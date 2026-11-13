"""Prepare validated paper imports for the existing private development portal.

This module neither crops nor deploys. Originals stay in paper-workspace; only
the immutable export and a hash-bound integration manifest reach development.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import zipfile

REPO = 'JGUPTA2511/modulus-practice-corner'
BRANCH = 'development'
SCHEMA = 'modulus_paper_integration_v1'
MAX_FILE = 24 * 1024 * 1024
ID = re.compile(r'^[A-Z0-9][A-Z0-9_-]{0,79}$')
RUN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$')
PATTERNS = {'jee-main', 'jee-advanced-1', 'jee-advanced-2', 'jee-advanced-both'}
SUBJECTS = {'Physics', 'Chemistry', 'Mathematics', 'Biology', 'Botany', 'Zoology'}
FORMATS = {'single_choice': 'choice', 'single_correct': 'choice', 'choice': 'choice',
           'multiple_correct': 'multi', 'multi': 'multi',
           'numerical': 'number', 'integer': 'number', 'number': 'number',
           'Single Correct (MCQ)': 'choice', 'Multiple Correct (MCQ)': 'multi',
           'Numerical / Integer': 'number'}


class PortalError(RuntimeError):
    pass


def digest(body):
    return hashlib.sha256(body).hexdigest()


def git_digest(body):
    return hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def safe_file(folder, relative, required=True):
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or '..' in parts or '\\' in relative:
        raise PortalError('Unsafe portal file path.')
    path = folder.joinpath(*parts)
    for candidate in (path, *path.parents):
        if candidate == folder.parent:
            break
        if candidate.is_symlink():
            raise PortalError('Symlink portal file refused.')
    if folder not in path.resolve().parents or (required and not path.is_file()):
        raise PortalError('Portal source file is absent or outside its job.')
    return path


def read_json(path):
    if path.stat().st_size > MAX_FILE:
        raise PortalError('Portal metadata exceeds the file limit.')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise PortalError('Portal metadata must be an object.')
    return value


def write_json(path, value, immutable=False):
    if path.is_symlink() or path.parent.is_symlink():
        raise PortalError('Unsafe portal metadata destination.')
    body = encoded(value)
    if path.exists() and immutable:
        if path.read_bytes() != body:
            raise PortalError('Integration manifest changed; original integration revision retained.')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    if temporary.is_symlink():
        raise PortalError('Unsafe temporary portal metadata path.')
    temporary.write_bytes(body)
    temporary.replace(path)


def _confirmation(basis, evidence):
    return {'status': 'confirmed', 'basis': basis, 'evidence': evidence}


def _confirmed(value):
    return (isinstance(value, dict) and value.get('status') == 'confirmed'
            and value.get('basis') in ('source', 'owner_selected')
            and isinstance(value.get('evidence'), (str, dict)) and bool(value['evidence']))


def _format(question):
    declarations = []
    for name in ('source_type', 'answer_format', 'response_type'):
        value = question.get(name)
        if value:
            if not isinstance(value, str):
                return None
            if value in ('passage', 'comprehension', 'Passage / Comprehension'):
                continue
            declarations.append(FORMATS.get(value))
    value = question.get('question_type')
    if isinstance(value, str) and value in FORMATS:
        declarations.append(FORMATS[value])
    return declarations[0] if declarations and None not in declarations and len(set(declarations)) == 1 else None


def _answer_valid(question, kind):
    value = question.get('answer')
    if value is None or isinstance(value, bool):
        return False
    if kind == 'number':
        return type(value) in (str, int, float) and bool(re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', str(value).strip()))
    values = value if isinstance(value, list) else list(value.strip().upper()) if kind == 'multi' and isinstance(value, str) else [value]
    if kind not in ('choice', 'multi') or not values or (kind == 'choice' and len(values) != 1):
        return False
    normalized = []
    for option in values:
        if type(option) not in (str, int):
            return False
        text = str(option).strip().upper()
        if text in ('1', '2', '3', '4') and question.get('option_key_basis') == 'one_based_options':
            text = 'ABCD'[int(text) - 1]
        if text not in ('A', 'B', 'C', 'D'):
            return False
        normalized.append(text)
    return len(normalized) == len(set(normalized))


def _rule(value, kind):
    if not isinstance(value, dict) or not all(type(value.get(k)) in (int, float)
            and math.isfinite(value[k]) for k in ('correct', 'incorrect', 'unanswered')):
        return False
    if value['correct'] <= 0 or value['incorrect'] > 0:
        return False
    if kind == 'multi':
        partial = value.get('partial')
        if not isinstance(partial, dict) or partial.get('type') not in (
                'none', 'per_correct_option', 'jee_advanced_2026', 'jee_advanced_2020_2025_standard'):
            return False
        if partial['type'] == 'per_correct_option' and not (type(partial.get('each')) in (int, float)
                and math.isfinite(partial['each']) and partial['each'] > 0):
            return False
    return True


def _source_coverage(archive, data):
    layout = data.get('source_layout', {})
    if (not isinstance(layout, dict) or layout.get('basis') != 'unique_complete_printed_single_column_sequence'
            or layout.get('expected_count') != data['count']
            or layout.get('question_count_detected') != data['count']):
        return None
    with zipfile.ZipFile(archive) as z:
        if 'boundaries.json' not in z.namelist():
            return None
        bounds = json.loads(z.read('boundaries.json'))
    if not isinstance(bounds, dict):
        return None
    rows, pages = bounds.get('questions'), bounds.get('pages')
    if not isinstance(rows, list) or not isinstance(pages, list) or len(rows) != data['count']:
        return None
    coverage = set()
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.get('number') != number or not isinstance(row.get('parts'), list) or not row['parts']:
            return None
        for part in row['parts']:
            if not isinstance(part, dict):
                return None
            pi, box = part.get('page'), part.get('box')
            if (type(pi) is not int or not 0 <= pi < len(pages) or not isinstance(box, list)
                    or len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                    or not 0 <= box[0] < box[2] <= pages[pi]['w']
                    or not 0 <= box[1] < box[3] <= pages[pi]['h']):
                return None
            coverage.add(pi)
    first, last = layout.get('first_page_index'), layout.get('last_page_index')
    if type(first) is not int or type(last) is not int or not 0 <= first <= last < len(pages):
        return None
    if not set(range(first, last + 1)).issubset(coverage):
        return None
    if not set(layout.get('continuation_page_indexes', [])).issubset(coverage):
        return None
    return {'layout': deepcopy(layout), 'body_pages_with_image_parts': sorted(coverage),
            'question_image_parts': len(rows), 'visual_review': 'not_independently_performed'}


def prepare_job(folder, metadata_override=None):
    """Create one immutable integration manifest, or a local hold report.

    Overrides are explicit source/owner metadata bound to this exact export.
    Missing marking, source type or coverage is never replaced by a guess.
    """
    from workspace_client import validate_export
    folder = Path(folder).expanduser()
    if folder.is_symlink():
        raise PortalError('Symlink paper job refused.')
    folder = folder.resolve()
    job_path = safe_file(folder, 'job.json')
    job_body, job = job_path.read_bytes(), read_json(job_path)
    status = read_json(safe_file(folder, 'status.json'))
    pid, rid = job.get('paper_id'), status.get('run_id')
    if not isinstance(pid, str) or not ID.fullmatch(pid) or folder.name != pid or status.get('paper_id') != pid:
        raise PortalError('Portal job/status identity mismatch.')
    if not isinstance(rid, str) or not RUN.fullmatch(rid):
        raise PortalError('Unsafe portal run ID.')
    run = safe_file(folder, 'runs/' + rid + '/run.json').parent
    run_record = read_json(run / 'run.json')
    archive = safe_file(folder, 'runs/' + rid + '/export.zip')
    if archive.stat().st_size > MAX_FILE:
        raise PortalError('Export exceeds the 24 MiB transfer limit; complete local images are retained.')
    archive_sha = digest(archive.read_bytes())
    if run_record.get('export_sha256') != archive_sha or run_record.get('input_manifest_sha256') != digest(job_body):
        raise PortalError('Portal export does not match the immutable source run.')
    saved_checks = read_json(safe_file(folder, 'runs/' + rid + '/checks.json'))
    if saved_checks.get('export_sha256') != archive_sha:
        raise PortalError('Saved source checks do not match the immutable export.')
    for role, record in job.get('inputs', {}).items():
        if role not in ('questions', 'answer_key', 'solutions') or not isinstance(record, dict):
            raise PortalError('Invalid original source record.')
        original_path = safe_file(folder, 'input/' + role + '.pdf')
        if original_path.stat().st_size > MAX_FILE:
            raise PortalError('Original source exceeds the file limit.')
        original_body = original_path.read_bytes()
        if (record.get('file') != 'input/' + role + '.pdf' or len(original_body) != record.get('size')
                or digest(original_body) != record.get('sha256')):
            raise PortalError('Original source changed; existing integration is retained.')
    processing = safe_file(folder, 'runs/' + rid + '/processing.json', required=False)
    if processing.exists():
        body, snapshot = processing.read_bytes(), read_json(processing)
        if (snapshot.get('schema') != 'modulus_run_processing_v1'
                or snapshot.get('paper_id') != pid or snapshot.get('input_manifest_sha256') != digest(job_body)
                or status.get('processing_sha256') != digest(body)
                or run_record.get('processing_sha256') != digest(body)):
            raise PortalError('Portal retry settings are not bound to the source run.')
        from workspace_client import run_processing_job
        job = run_processing_job(folder, status, job_body, run_record)
    elif run_record.get('processing_sha256') or status.get('processing_sha256'):
        raise PortalError('Portal retry settings are missing.')
    data, checks = validate_export(archive)
    if status.get('state') not in ('ready_for_integration', 'images_ready'):
        raise PortalError('Paper images have not completed local extraction.')
    override_path = safe_file(folder, 'integration_override.json', required=False)
    override = deepcopy(metadata_override if metadata_override is not None else
                        read_json(override_path) if override_path.exists() else {})
    if not isinstance(override, dict):
        raise PortalError('Integration override must be an object.')
    if override and override.get('export_sha256') != archive_sha:
        raise PortalError('Integration override is not bound to this export hash.')
    for key, value in (('paper_id', pid), ('run_id', rid), ('schema', SCHEMA)):
        if key in override and override[key] != value:
            raise PortalError('Integration override identity mismatch.')
    manifest = {'schema': SCHEMA, 'paper_id': pid, 'run_id': rid, 'export_sha256': archive_sha,
                'title': job.get('title') or pid + ' — uploaded paper',
                'exam_pattern': job.get('exam_intent', {}).get('exam_pattern'),
                'test_scope': 'Mixed Practice', 'publication': 'not_deployed',
                'allow_source_key_grading': True, 'validation': {}}
    coverage = _source_coverage(archive, data)
    if coverage:
        for key in ('source_alignment', 'source_completeness'):
            manifest['validation'][key] = _confirmation('source', coverage)
    if job.get('profile') == 'jee-main-mock' and manifest['exam_pattern'] == 'jee-main':
        scoring = job.get('requested_scoring', {})
        rule = {key: scoring.get(key) for key in ('correct', 'incorrect', 'unanswered')}
        if _rule(rule, 'choice'):
            manifest.update(marking_scheme=rule,
                            marking_basis={'basis': 'owner_selected', 'evidence': scoring},
                            duration_minutes=scoring.get('duration_minutes'),
                            duration_basis={'basis': 'owner_selected', 'evidence': scoring},
                            paper_origin='owner_selected_mock')
    manifest.update({key: value for key, value in override.items() if key != 'validation'})
    if override.get('target_branch', BRANCH) != BRANCH:
        raise PortalError('Integration override may target development only.')
    manifest['publication'] = 'not_deployed'
    if not isinstance(override.get('validation', {}), dict):
        raise PortalError('Integration validation override must be an object.')
    manifest['validation'].update(override.get('validation', {}))
    overrides = manifest.get('question_metadata', [])
    if not isinstance(overrides, list):
        raise PortalError('Question metadata override must be a list.')
    numbers = [row.get('number') for row in overrides if isinstance(row, dict)]
    if len(numbers) != len(overrides) or len(set(numbers)) != len(numbers) or any(type(n) is not int or not 1 <= n <= data['count'] for n in numbers):
        raise PortalError('Invalid or duplicate question metadata index.')
    lookup = {row['number']: row for row in overrides}
    questions = []
    for original in data['questions']:
        q = deepcopy(original)
        q.update({key: value for key, value in lookup.get(q['number'], {}).items() if key not in ('number', 'image', 'answer')})
        questions.append(q)
    if 'duration_minutes' not in manifest:
        durations = [q.get('paper_duration_minutes') for q in questions]
        bases = [q.get('duration_basis') for q in questions]
        if (durations and all(type(value) in (int, float) and math.isfinite(value) and 0 < value <= 1440 for value in durations)
                and len(set(durations)) == 1
                and all(isinstance(basis, dict) and basis.get('basis') == 'source' and basis.get('evidence') for basis in bases)):
            manifest.update(duration_minutes=durations[0], duration_basis=deepcopy(bases[0]))
    if str(manifest.get('exam_pattern', '')).startswith('jee-advanced') and coverage:
        manifest.setdefault('paper_origin', 'source_document')
    issues = []
    if not isinstance(manifest.get('title'), str) or not manifest['title'].strip() or len(manifest['title']) > 300:
        issues.append('Paper title is missing or invalid.')
    if manifest.get('test_scope') not in ('Chapter Practice', 'Part Test', 'Full Syllabus Test', 'Mixed Practice'):
        issues.append('Paper test scope is unresolved.')
    formats = [_format(q) for q in questions]
    if all(formats):
        manifest['validation'].setdefault('question_types', _confirmation('source', {
            'question_types': {str(q['number']): q.get('source_section', q.get('source_type')) for q in questions},
            'answers_used_for_type': False}))
    subjects = {q.get('subject', manifest.get('subject')) for q in questions}
    if subjects == {'Physics', 'Chemistry', 'Mathematics'}:
        manifest.setdefault('paper_combination', 'PCM')
    elif len(subjects) == 1 and subjects.issubset(SUBJECTS):
        manifest.setdefault('paper_combination', 'Single Subject')
    elif subjects == {'Physics', 'Chemistry', 'Biology'}:
        manifest.setdefault('paper_combination', 'PCB')
    if all(_answer_valid(q, kind) for q, kind in zip(questions, formats)) and not data.get('source_key', {}).get('unresolved'):
        manifest['validation'].setdefault('answers', _confirmation('owner_selected', {
            'decision': 'use_supplied_source_keys_for_provisional_scoring',
            'independently_math_verified': False, 'export_sha256': archive_sha,
            'source_key': deepcopy(data.get('source_key', {}))}))
    rules = [(q.get('marking_scheme', manifest.get('marking_scheme')),
              q.get('marking_basis', manifest.get('marking_basis'))) for q in questions]
    if all(_rule(rule, kind) and bool(basis) for (rule, basis), kind in zip(rules, formats)):
        basis_kind = 'owner_selected' if all(isinstance(b, dict) and b.get('basis') == 'owner_selected' for _, b in rules) else 'source'
        manifest['validation'].setdefault('marking', _confirmation(basis_kind, {
            'rules': [{'number': q['number'], 'scheme': rule, 'basis': basis}
                      for q, (rule, basis) in zip(questions, rules)]}))
    for field in ('source_alignment', 'source_completeness', 'question_types', 'marking', 'answers'):
        if not _confirmed(manifest['validation'].get(field)):
            issues.append(field + ': source or owner evidence is missing.')
    if manifest.get('exam_pattern') not in PATTERNS:
        issues.append('Existing Main/Advanced interface pattern is unresolved.')
    duration = manifest.get('duration_minutes')
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 1440 or not manifest.get('duration_basis'):
        issues.append('Source-backed or selected duration is missing.')
    if manifest.get('paper_origin') not in ('institutional_mock', 'official_past_paper', 'owner_selected_mock'):
        issues.append('Paper origin is unresolved.')
    if not manifest.get('paper_combination'):
        issues.append('Source subject combination is unresolved.')
    combinations = {'PCM': {'Physics', 'Chemistry', 'Mathematics'},
                    'PCB': {'Physics', 'Chemistry', 'Biology', 'Botany', 'Zoology'},
                    'PCMB': SUBJECTS}
    if manifest.get('paper_combination') in combinations and not subjects.issubset(combinations[manifest['paper_combination']]):
        issues.append('Source subjects conflict with the selected paper combination.')
    for q, kind, (rule, basis) in zip(questions, formats, rules):
        n = q['number']
        if q.get('subject', manifest.get('subject')) not in SUBJECTS:
            issues.append(f'Q{n}: source subject is missing.')
        if not kind:
            issues.append(f'Q{n}: source response type is unresolved.')
        if not _answer_valid(q, kind):
            issues.append(f'Q{n}: source answer is missing or conflicts with its explicit response type.')
        section = q.get('exam_section', q.get('section', q.get('source_section')))
        if isinstance(section, dict):
            section = section.get('text')
        if not isinstance(section, str) or not section.strip():
            issues.append(f'Q{n}: printed section is missing.')
        if not _rule(rule, kind) or not basis:
            issues.append(f'Q{n}: source or selected marking rules are missing.')
        if kind in ('choice', 'multi'):
            answer = q.get('answer')
            values = answer if isinstance(answer, list) else [answer]
            if any(str(value).strip() in ('1', '2', '3', '4') for value in values) and q.get('option_key_basis') != 'one_based_options':
                issues.append(f'Q{n}: source option-label basis is missing.')
    if data.get('source_key', {}).get('unresolved'):
        issues.append('Finalized source-key entries remain unresolved.')
    if data.get('grading_issues'):
        issues.append('Source extraction reports unresolved grading issues.')
    issues = list(dict.fromkeys(issues))
    portal = safe_file(folder, 'runs/' + rid + '/portal', required=False)
    portal.mkdir(exist_ok=True)
    manifest_path = portal / 'integration.json'
    report = {'schema': SCHEMA, 'paper_id': pid, 'run_id': rid,
              'status': 'held' if issues else 'ready_for_development',
              'blocking_requirements': issues, 'export_sha256': archive_sha,
              'manifest_path': str(manifest_path), 'export_path': str(archive),
              'publication': 'not_deployed', 'target_branch': BRANCH}
    if not issues:
        write_json(manifest_path, manifest, immutable=True)
        report['integration_manifest_sha256'] = digest(manifest_path.read_bytes())
    write_json(portal / 'status.json', report)
    return report


class DevelopmentUploader:
    """Write only immutable paper imports, without scanning the question bank."""
    def __init__(self, api=None, branch=BRANCH):
        if branch != BRANCH:
            raise PortalError('Paper integration may target development only.')
        if api is None:
            from workspace_client import GitHub
            api = GitHub()
        self.api = api

    def _snapshot(self, paths):
        head = self.api.call('git/ref/heads/' + BRANCH)['object']['sha']
        root = self.api.call('git/commits/' + head)['tree']['sha']
        cache = {}
        def entries(tree):
            if tree not in cache:
                response = self.api.call('git/trees/' + tree)
                if response.get('truncated'):
                    raise PortalError('Integration subtree is truncated; no upload made.')
                cache[tree] = {row['path']: row for row in response['tree']}
            return cache[tree]
        found = {}
        for path in paths:
            tree = root
            for i, part in enumerate(PurePosixPath(path).parts):
                row = entries(tree).get(part)
                if row is None:
                    break
                if i == len(PurePosixPath(path).parts) - 1:
                    if row['type'] != 'blob':
                        raise PortalError('Integration file collides with a directory.')
                    found[path] = row['sha']
                elif row['type'] != 'tree':
                    raise PortalError('Integration directory collides with a file.')
                else:
                    tree = row['sha']
        return head, root, found

    def upload(self, files):
        self.api.check_private()
        repository = self.api.call('')
        if (repository.get('full_name', '').lower() != REPO.lower()
                or repository.get('private') is not True
                or repository.get('permissions', {}).get('push') is not True):
            raise PortalError('Development target must be the private Modulus repository with write access.')
        for path, body in files.items():
            parts = PurePosixPath(path).parts
            if (len(parts) != 4 or parts[0] != 'paper_imports' or not ID.fullmatch(parts[1])
                    or not RUN.fullmatch(parts[2]) or parts[3] not in ('export.zip', 'integration.json')
                    or '\\' in path or len(body) > MAX_FILE):
                raise PortalError('Unsafe or oversized development integration file.')
        head, tree, original = self._snapshot(files)
        desired = {path: git_digest(body) for path, body in files.items()}
        changed = {path: body for path, body in files.items() if original.get(path) != desired[path]}
        if any(path in original for path in changed):
            raise PortalError('Existing integration revision differs; previous files retained.')
        if not changed:
            return {'commit': head, 'changed': 0, 'branch': BRANCH}
        import base64
        entries = []
        uploaded = set()
        for path, body in changed.items():
            if desired[path] not in uploaded:
                blob = self.api.call('git/blobs', 'POST', {'content': base64.b64encode(body).decode(), 'encoding': 'base64'})
                if blob.get('sha') != desired[path]:
                    raise PortalError('Development blob checksum mismatch.')
                uploaded.add(desired[path])
            entries.append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': desired[path]})
        for _ in range(3):
            new_tree = self.api.call('git/trees', 'POST', {'base_tree': tree, 'tree': entries})['sha']
            commit = self.api.call('git/commits', 'POST', {'tree': new_tree, 'parents': [head],
                'message': 'Stage validated image paper in development [skip ci] [skip netlify]'})['sha']
            try:
                self.api.call('git/refs/heads/' + BRANCH, 'PATCH', {'sha': commit, 'force': False})
                return {'commit': commit, 'changed': len(changed), 'branch': BRANCH}
            except Exception:
                new_head, new_base, current = self._snapshot(changed)
                if all(current.get(path) == desired[path] for path in changed):
                    return {'commit': new_head, 'changed': 0, 'branch': BRANCH}
                if new_head == head or any(current.get(path) != original.get(path) for path in changed):
                    raise PortalError('Concurrent integration edit; local paper retained for retry.') from None
                head, tree = new_head, new_base
        raise PortalError('Development kept changing; local paper retained for retry.')


def sync_job(folder, api=None):
    """Stage a ready import; raises on network failure so the local queue retries."""
    report = prepare_job(folder)
    if report['status'] != 'ready_for_development':
        return report
    prefix = 'paper_imports/' + report['paper_id'] + '/' + report['run_id'] + '/'
    files = {prefix + 'export.zip': Path(report['export_path']).read_bytes(),
             prefix + 'integration.json': Path(report['manifest_path']).read_bytes()}
    try:
        receipt = DevelopmentUploader(api).upload(files)
    except Exception as exc:
        report['status'] = 'upload_pending'
        write_json(Path(report['manifest_path']).parent / 'status.json', report)
        raise PortalError('Development import upload pending; saved images will be reused on retry.') from exc
    report.update(status='staged_in_development', receipt=receipt, publication='not_deployed')
    write_json(Path(report['manifest_path']).parent / 'status.json', report)
    return report
