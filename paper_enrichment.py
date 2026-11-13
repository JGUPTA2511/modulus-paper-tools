"""Immutable key/solution supplements for existing local question images.

No cropper, network request or deployment is invoked. A new run retains each
crop byte while recording the exact supplemental sources and effective rules.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile


_FILES = {'answer_key': 'answer_key.pdf', 'solutions': 'solutions.pdf',
          'base_key': 'base_key.pdf'}
_RUN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$')


def _client():
    main = sys.modules.get('__main__')
    if getattr(main, '__file__', None) and Path(main.__file__).resolve() == Path(__file__).with_name('workspace_client.py').resolve():
        return main
    import workspace_client
    return workspace_client


def _pdf(value):
    w = _client()
    path = Path(value).expanduser()
    if path.is_symlink():
        raise w.WorkspaceError('Symlink supplemental PDF refused.')
    return w.strict_pdf(path)


def supplement_payload(folder, run):
    """Validate a supplement and return the manifest/PDF bytes for safe sync."""
    w = _client()
    folder, run = Path(folder), Path(run)
    if (folder.is_symlink() or (folder / 'runs').is_symlink() or run.is_symlink()
            or run.parent.resolve() != (folder / 'runs').resolve()):
        raise w.WorkspaceError('Supplement run is outside the saved paper job.')
    path = run / 'supplement.json'
    if path.is_symlink() or run.is_symlink() or (run / 'supplements').is_symlink():
        raise w.WorkspaceError('Symlink supplement refused.')
    if not path.is_file():
        return {}
    body = path.read_bytes()
    if len(body) > w.MAX_FILE:
        raise w.WorkspaceError('Supplement manifest exceeds the file limit.')
    record = json.loads(body)
    if (not isinstance(record, dict) or record.get('schema') != 'modulus_paper_supplement_v1'
            or record.get('paper_id') != folder.name or record.get('run_id') != run.name
            or record.get('input_manifest_sha256') != w.sha((folder / 'job.json').read_bytes())
            or not isinstance(record.get('parent_run_id'), str)
            or not _RUN.fullmatch(record['parent_run_id'])):
        raise w.WorkspaceError('Supplement does not match its immutable job/run.')
    parent = folder / 'runs' / record['parent_run_id'] / 'export.zip'
    if (parent.is_symlink() or parent.parent.is_symlink() or not parent.is_file()
            or w.sha(parent.read_bytes()) != record.get('parent_export_sha256')):
        raise w.WorkspaceError('Supplement parent export changed or is missing.')
    sources = record.get('sources')
    if not isinstance(sources, dict) or set(sources) - set(_FILES):
        raise w.WorkspaceError('Malformed supplemental source roles.')
    payload = {path: body}
    for role, item in sources.items():
        expected = 'supplements/' + _FILES[role]
        if not isinstance(item, dict) or item.get('file') != expected:
            raise w.WorkspaceError('Unsafe supplemental source path.')
        source = run / expected
        if source.is_symlink() or not source.is_file() or run not in source.resolve().parents:
            raise w.WorkspaceError('Supplemental source is missing or unsafe.')
        if not 0 <= source.stat().st_size <= w.MAX_FILE:
            raise w.WorkspaceError('Supplemental source exceeds the file limit.')
        content = source.read_bytes()
        if len(content) != item.get('size') or w.sha(content) != item.get('sha256'):
            raise w.WorkspaceError('Supplemental source changed: ' + role)
        payload[source] = content
    return payload


def _current(folder):
    w = _client()
    raw = Path(folder).expanduser()
    if raw.is_symlink():
        raise w.WorkspaceError('Symlink job folder refused.')
    folder = raw.resolve()
    for name in ('job.json', 'status.json'):
        if (folder / name).is_symlink():
            raise w.WorkspaceError('Symlink job/status refused.')
    body = (folder / 'job.json').read_bytes()
    job, status = json.loads(body), json.loads((folder / 'status.json').read_text())
    if not isinstance(job, dict) or not isinstance(status, dict) or job.get('paper_id') != folder.name or status.get('paper_id') != folder.name:
        raise w.WorkspaceError('Paper identity mismatch.')
    w.verify_saved_inputs(folder, job)
    if not w.cached_run(folder):
        raise w.WorkspaceError('A complete unchanged image run is required before enrichment.')
    run = folder / 'runs' / status['run_id']
    effective = w.run_processing_job(folder, status, body,
                                    json.loads((run / 'run.json').read_text()))
    payload = supplement_payload(folder, run)
    if payload and status.get('supplement_sha256') != w.sha(payload[run / 'supplement.json']):
        raise w.WorkspaceError('Saved supplement manifest changed.')
    data, _ = w.validate_export(run / 'export.zip')
    return w, folder, job, effective, status, run, data


def can_enrich(folder, source, key=None, solutions=None, profile=None):
    """Read-only eligibility check; enrich revalidates under the shared lock."""
    try:
        w, folder, job, effective, status, run, data = _current(folder)
        original = job.get('inputs', {}).get('questions')
        incoming = _pdf(source)
        if not original or w.sha(incoming.read_bytes()) != original['sha256']:
            return {'eligible': False, 'reason': 'Question original differs; saved images were kept.'}
        changed = []
        if profile is not None:
            w.batch_profile(profile)
            if profile != effective['profile']:
                changed.append('selected_profile')
        supplements = json.loads((run / 'supplement.json').read_text()).get('sources', {}) if (run / 'supplement.json').is_file() else {}
        for role, value in (('answer_key', key), ('solutions', solutions)):
            if value is None:
                continue
            p = _pdf(value)
            saved = supplements.get(role) or job.get('inputs', {}).get(role, {})
            if w.sha(p.read_bytes()) != saved.get('sha256'):
                changed.append(role)
        if key is not None and any(q.get('answer') is None for q in data['questions']):
            changed.append('missing_source_answers')
        return {'eligible': bool(changed), 'source_matches': True, 'changes': changed,
                'reason': ', '.join(changed) if changed else 'Supplemental sources are unchanged.'}
    except (OSError, ValueError, TypeError, KeyError, _client().WorkspaceError) as exc:
        return {'eligible': False, 'reason': str(exc)}


def enrich(folder, key=None, solutions=None, base_key=None, profile=None, *, _locked=False):
    """Attach source metadata in a fresh run; preserve all existing crop bytes."""
    w = _client()
    raw = Path(folder).expanduser()
    if raw.is_symlink():
        raise w.WorkspaceError('Symlink job folder refused.')
    folder = raw.resolve()
    if not _locked:
        with w.job_lock(folder.parent, folder.name):
            return enrich(folder, key, solutions, base_key, profile, _locked=True)
    w, folder, original, job, status, parent, data = _current(folder)
    job = deepcopy(job)
    from source_keys import read_key_sources, read_pdf_identity
    question = folder / original['inputs']['questions']['file'] if 'questions' in original['inputs'] else None
    identity = read_pdf_identity(question) if question else None
    if profile is not None:
        w.batch_profile(profile)
        family = job.get('exam_intent', {}).get('exam_family')
        if ((profile == 'jee-main-mock' and family == 'jee-advanced') or
                (profile == 'jee-advanced-source' and family == 'jee-main')):
            raise w.WorkspaceError('Selected marking profile conflicts with the source family.')
        job.update(profile=profile, profile_basis='source_required' if profile == 'jee-advanced-source'
                   else 'not_selected' if profile == 'unspecified' else 'owner_selected')
        job.pop('requested_scoring', None)
        if profile == 'jee-main-mock':
            job['requested_scoring'] = {'correct': 4, 'incorrect': -1, 'unanswered': 0,
                                        'duration_minutes': 180, 'basis': 'owner_selected_mock_rules'}
    families = (identity or {}).get('exam_families', [])
    if ((job['profile'] == 'jee-main-mock' and families == ['jee-advanced']) or
            (job['profile'] == 'jee-advanced-source' and families == ['jee-main'])):
        raise w.WorkspaceError('Selected marking profile conflicts with the printed source family.')
    supplied = {role: _pdf(value) for role, value in
                (('answer_key', key), ('solutions', solutions), ('base_key', base_key)) if value is not None}
    if base_key is not None and key is None:
        raise w.WorkspaceError('A base key requires its explicitly selected final key.')
    previous = json.loads((parent / 'supplement.json').read_text()) if (parent / 'supplement.json').is_file() else {}
    sources = {role: (parent / item['file'], item['original_name'])
               for role, item in previous.get('sources', {}).items()}
    sources.update({role: (p, p.name) for role, p in supplied.items()})
    source_hashes = {role: w.sha(p.read_bytes()) for role, (p, _) in sources.items()}
    fingerprint = w.sha(json.dumps({'sources': source_hashes,
                                    'profile': job['profile'], 'add_policy': 'original'}, sort_keys=True).encode())
    if previous.get('request_sha256') == fingerprint:
        return {'paper_id': folder.name, 'run_id': parent.name, 'run': str(parent), 'reused': True,
                'state': status['state']}
    result = None
    if key is not None:
        result = read_key_sources(base_path=sources.get('base_key', (None,))[0], final_path=supplied['answer_key'],
            expected_identity=identity,
            expected_numbers=range(1, data['count'] + 1), add_policy='original')
        if set(result['key']) != set(range(1, data['count'] + 1)) or result.get('identity_issues'):
            raise w.WorkspaceError('Supplemental key is incomplete or mismatched; current run was kept unchanged.')
        for row in data['questions']:
            row['answer'] = result['key'][row['number']]
        old_issues = data.get('source_key', {}).get('unresolved', [])
        data['source_key'] = {name: value for name, value in result.items() if name != 'key'}
        if not result.get('unresolved'):
            if data.get('schema') == 'modulus_image_exam_v1':
                data.pop('schema')
            if data.get('extraction_state') == 'images_ready':
                data.pop('extraction_state')
            data.pop('grading_ready', None)
            data['grading_issues'] = [issue for issue in data.get('grading_issues', [])
                if issue not in old_issues and not str(issue).startswith((
                    'Source answers missing for question numbers:',
                    'Source key has unassociated question numbers:',
                    'Source answers remain unavailable.'))]
    elif not supplied and profile is None:
        return {'paper_id': folder.name, 'run_id': parent.name, 'run': str(parent), 'reused': True,
                'state': status['state']}
    run = w.new_run(folder)
    records = {}
    for role, (p, name) in sources.items():
        target = run / 'supplements' / _FILES[role]
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(p, target)
        content = target.read_bytes()
        if w.sha(content) != source_hashes[role]:
            raise w.WorkspaceError('Supplemental source changed during enrichment; saved images were kept.')
        records[role] = {'file': target.relative_to(run).as_posix(), 'original_name': name,
                         'size': len(content), 'sha256': w.sha(content)}
    supplement = {'schema': 'modulus_paper_supplement_v1', 'paper_id': folder.name, 'run_id': run.name,
                  'parent_run_id': parent.name, 'parent_export_sha256': w.sha((parent / 'export.zip').read_bytes()),
                  'input_manifest_sha256': w.sha((folder / 'job.json').read_bytes()),
                  'sources': records, 'request_sha256': fingerprint, 'profile': job['profile'],
                  'solutions': 'supplied_not_matched' if 'solutions' in records else status.get('solutions', 'not_supplied')}
    w.save_json(run / 'supplement.json', supplement)
    w.save_processing_job(folder, job, run)
    with zipfile.ZipFile(parent / 'export.zip') as old, zipfile.ZipFile(run / 'export.zip', 'w', zipfile.ZIP_DEFLATED) as new:
        for info in old.infolist():
            if info.filename != 'exam.json' and not (result is not None and info.filename == 'source_key.json'):
                new.writestr(info.filename, old.read(info.filename))
        new.writestr('exam.json', json.dumps(data, ensure_ascii=False))
        if result is not None:
            new.writestr('source_key.json', json.dumps(result, ensure_ascii=False))
    w.finish_run(folder, job, run, run / 'export.zip')
    current = json.loads((folder / 'status.json').read_text())
    current.update(supplement_sha256=w.sha((run / 'supplement.json').read_bytes()), solutions=supplement['solutions'])
    w.save_json(folder / 'status.json', current)
    return {'paper_id': folder.name, 'run_id': run.name, 'run': str(run), 'reused': False,
            'state': current['state'], 'supplement_sha256': current['supplement_sha256']}
