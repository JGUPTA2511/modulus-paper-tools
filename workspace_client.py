#!/usr/bin/env python3
"""Mac-to-private-GitHub handoff. No cloning, Actions, API AI, or deployment.

Use the existing Run_Question_Boundaries.command launcher. CLI examples:
  python workspace_client.py run paper.pdf --key key.pdf --paper-id TEST-02
  python workspace_client.py attach GTM-01_result.zip --paper-id GTM-01
  python workspace_client.py sync "$HOME/Modulus Papers/GTM-01"

Claude's question_boundaries.py is executed unchanged. Only structural checks
are made here; source answers and crop completeness are not independently verified.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import contextmanager, ExitStack
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile

REPO = 'JGUPTA2511/modulus-practice-corner'
BRANCH = 'paper-workspace'
MAX_FILE = 24 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
SKIP = '[skip ci] [skip netlify]'
HERE = Path(__file__).resolve().parent
ID_RE = re.compile(r'^[A-Z0-9][A-Z0-9_-]{0,79}$')

class WorkspaceError(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def gitsha(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def encoded(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode()


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique, exclusively created file cannot follow a stale *.tmp symlink.
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.',
                                         suffix='.tmp', delete=False) as stream:
            tmp = Path(stream.name)
            stream.write(encoded(obj))
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def paper_id(value):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise WorkspaceError('Paper ID: use 1-80 uppercase letters, digits, - or _.')
    return value


@contextmanager
def job_lock(root, pid):
    """One lock for preparing, cropping, attaching and syncing a local paper."""
    import fcntl
    pid = paper_id(pid)
    root = Path(root).expanduser().resolve()
    lock_dir = root / '.locks'
    if lock_dir.is_symlink() or (root / pid).is_symlink():
        raise WorkspaceError('Symlink job/lock directory refused.')
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / (pid + '.lock')
    paths = [path]
    # Older installed clients lock inside an existing job directory. Keep that
    # protection while moving preparation under the shared root lock.
    if (root / pid).is_dir():
        paths.append(root / pid / '.run.lock')
    with ExitStack() as opened:
        for path in paths:
            if path.is_symlink():
                raise WorkspaceError('Symlink lock refused.')
            lock = opened.enter_context(path.open('a'))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkspaceError('This paper is already running or syncing on this Mac.') from exc
        yield  # Closing the file descriptors releases both locks, even on error.


def safe_member(name):
    p = PurePosixPath(name)
    if not name or '\\' in name or p.is_absolute() or '..' in p.parts or ':' in name:
        raise WorkspaceError('Unsafe archive path: ' + name[:100])
    return p


def path_from_prompt(text):
    text = text.strip()
    if not text:
        return None
    # Handle Finder's escaped spaces, single quotes and double quotes; no eval.
    if Path(text).expanduser().exists():
        return Path(text).expanduser().absolute()
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        raise WorkspaceError('Could not read the dragged path.') from exc
    if len(parts) != 1:
        raise WorkspaceError('Drag ONE file or batch folder into each prompt, not several paths.')
    return Path(parts[0]).expanduser().absolute()


def input_pdf(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != '.pdf':
        raise WorkspaceError('Not a PDF file: ' + str(path))
    if path.stat().st_size > MAX_FILE:
        raise WorkspaceError('PDF exceeds the 24 MiB per-file workspace limit: ' + path.name)
    if b'%PDF-' not in path.read_bytes()[:1024]:
        raise WorkspaceError('File does not have a PDF header: ' + path.name)
    return path


def validate_export(source):
    """Verify references and readable PNGs, not mathematical/cropping correctness."""
    from PIL import Image
    source = Path(source)
    if not source.is_file() or source.stat().st_size > MAX_FILE:
        raise WorkspaceError('Export missing or exceeds the 24 MiB per-file limit.')
    try:
        with zipfile.ZipFile(source) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            if len(names) != len(set(names)):
                raise WorkspaceError('Duplicate ZIP entries.')
            if sum(i.file_size for i in infos) > MAX_EXPANDED:
                raise WorkspaceError('Archive expands beyond the safety limit.')
            for item in infos:
                safe_member(item.filename)
                if stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                    raise WorkspaceError('Symlinks/encrypted ZIP entries are not accepted.')
            if 'REFUSED.txt' in names:
                raise WorkspaceError('This cropper result was refused; it is not an exam.')
            data = json.loads(z.read('exam.json'))
            rows, count = data.get('questions'), data.get('count')
            if type(count) is not int or not 1 <= count <= 1000 or not isinstance(rows, list) or len(rows) != count:
                raise WorkspaceError('Question count mismatch.')
            if any(not isinstance(q, dict) for q in rows):
                raise WorkspaceError('Malformed question records.')
            if any(type(q.get('number')) is not int for q in rows) or [q['number'] for q in rows] != list(range(1, count + 1)):
                raise WorkspaceError('Missing, duplicate or out-of-order question numbers.')
            expected = set()
            for q in rows:
                n = q['number']
                name = f'crops/Q{n:03d}.png'
                if q.get('image') != name or name not in names:
                    raise WorkspaceError(f'Q{n}: missing or mismatched image.')
                answer = q.get('answer')
                if not isinstance(answer, (str, int, float)) or isinstance(answer, bool) or (isinstance(answer, float) and not math.isfinite(answer)) or not str(answer).strip():
                    raise WorkspaceError(f'Q{n}: missing/invalid source answer.')
                body = z.read(name)
                if not body.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise WorkspaceError(f'Q{n}: not a PNG.')
                with Image.open(io.BytesIO(body)) as im:
                    im.verify()
                with Image.open(io.BytesIO(body)) as im:
                    im.load()
                    if im.width < 2 or im.height < 2:
                        raise WorkspaceError(f'Q{n}: empty-sized crop.')
                expected.add(name)
            actual = {n for n in names if n.startswith('crops/') and n.lower().endswith('.png')}
            if actual != expected:
                raise WorkspaceError('Unexpected or unused crop images.')
            if z.testzip() is not None:
                raise WorkspaceError('ZIP checksum error.')
            report = z.read('REPORT.txt').decode('utf-8', errors='replace') if 'REPORT.txt' in names else ''
            return data, {'structural_check': 'passed', 'questions': count,
                'images': count, 'answers': count, 'export_sha256': sha(source.read_bytes()),
                'answer_status': 'source_transcribed_unverified',
                'crop_visual_review': 'not_performed_by_sync_tool',
                'cropper_report': report[:20000]}
    except WorkspaceError:
        raise
    except Exception as exc:
        raise WorkspaceError('Invalid export: ' + str(exc)[:240]) from exc


class GitHub:
    """Use gh's existing login; never read, print or store the access token."""
    def __init__(self):
        if shutil.which('gh') is None:
            raise WorkspaceError('GitHub CLI is missing. Run the Mac launcher to set it up.')
        self.prefix = 'repos/' + REPO

    def call(self, endpoint, method='GET', body=None):
        args = ['gh', 'api', '--hostname', 'github.com', self.prefix + ('/' + endpoint if endpoint else ''),
                '--method', method, '-H', 'Accept: application/vnd.github+json']
        if body is not None:
            args += ['--input', '-']
        result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                                text=True, capture_output=True, timeout=180)
        if result.returncode:
            # Do not echo arbitrary environment/credential data or full request payloads.
            raise WorkspaceError('GitHub request failed (' + method + ' ' + endpoint.split('?')[0] +
                                 '). Check browser login, connection and repository access.')
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def check_private(self):
        repo = self.call('')
        if repo.get('full_name', '').lower() != REPO.lower() or repo.get('private') is not True:
            raise WorkspaceError('Uploads stopped: target must be the private Modulus repository.')
        if not repo.get('permissions', {}).get('push', False):
            raise WorkspaceError('Your GitHub login lacks write access to the private workspace.')

    def snapshot(self):
        head = self.call('git/ref/heads/' + BRANCH)['object']['sha']
        tree = self.call('git/commits/' + head)['tree']['sha']
        response = self.call('git/trees/' + tree + '?recursive=1')
        if response.get('truncated'):
            raise WorkspaceError('Workspace tree is truncated; upload stopped.')
        entries = {x['path']: x['sha'] for x in response['tree'] if x['type'] == 'blob'}
        return head, tree, entries

    def upload(self, files, message):
        self.check_private()  # Must run BEFORE any private bytes are sent.
        for path, body in files.items():
            safe_member(path)
            parts = PurePosixPath(path).parts
            if len(parts) < 3 or parts[0] != 'paper_jobs':
                raise WorkspaceError('Upload outside paper_jobs is blocked.')
            paper_id(parts[1])
            if len(body) > MAX_FILE:
                raise WorkspaceError('File exceeds 24 MiB: ' + path)
        head, tree, baseline = self.snapshot()
        desired = {p: gitsha(b) for p, b in files.items()}
        changed = {p: b for p, b in files.items() if baseline.get(p) != desired[p]}
        # Manifest and completed run paths are immutable. Never overwrite another run.
        immutable = [p for p in changed if '/runs/' in p or p.endswith('/job.json') or '/input/' in p]
        if any(p in baseline for p in immutable):
            raise WorkspaceError('Paper/input/run already exists with different contents. Use a new paper ID; nothing overwritten.')
        if not changed:
            return {'commit': head, 'changed': 0}
        entries = []
        for p, body in changed.items():
            blob = self.call('git/blobs', 'POST', {'content': base64.b64encode(body).decode(), 'encoding': 'base64'})
            if blob.get('sha') != desired[p]:
                raise WorkspaceError('Uploaded blob hash mismatch: ' + p)
            entries.append({'path': p, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
        for attempt in range(3):
            new_tree = self.call('git/trees', 'POST', {'base_tree': tree, 'tree': entries})['sha']
            commit = self.call('git/commits', 'POST', {'message': message + ' ' + SKIP,
                              'tree': new_tree, 'parents': [head]})['sha']
            try:
                self.call('git/refs/heads/' + BRANCH, 'PATCH', {'sha': commit, 'force': False})
                return {'commit': commit, 'changed': len(changed)}
            except WorkspaceError:
                new_head, new_base, current = self.snapshot()
                if all(current.get(p) == desired[p] for p in changed):
                    return {'commit': new_head, 'changed': 0}
                if new_head == head or any(current.get(p) != baseline.get(p) for p in changed):
                    raise WorkspaceError('Concurrent edit or upload error. Local files kept; no force-push. Retry sync after resolving the conflict.')
                head, tree = new_head, new_base  # An unrelated paper changed; safely rebase.
        raise WorkspaceError('Workspace kept changing. Local files kept; retry sync.')


def sync_job(folder, api=None, queue_on_error=True):
    folder = Path(folder).expanduser()
    if folder.is_symlink():
        raise WorkspaceError('Symlink job folder refused.')
    folder = folder.resolve()
    with job_lock(folder.parent, folder.name):
        return _sync_job(folder, api, queue_on_error=queue_on_error)


def _sync_job(folder, api=None, queue_on_error=True):
    folder = Path(folder).expanduser().resolve()
    manifest_path = folder / 'job.json'
    status_path = folder / 'status.json'
    if manifest_path.is_symlink() or status_path.is_symlink():
        raise WorkspaceError('Symlink manifest/status refused.')
    manifest_body, status_body = manifest_path.read_bytes(), status_path.read_bytes()
    job = json.loads(manifest_body)
    status = json.loads(status_body)
    validated = {manifest_path: manifest_body, status_path: status_body}
    if not isinstance(job, dict) or not isinstance(status, dict):
        raise WorkspaceError('Malformed job/status: expected objects.')
    pid = paper_id(job.get('paper_id'))
    if pid != folder.name or status.get('paper_id') != pid:
        raise WorkspaceError('Job/status paper ID does not match its local folder.')
    if status.get('state') not in ('processing', 'held', 'ready_for_integration'):
        raise WorkspaceError('Invalid Mac processing state.')
    inputs = job.get('inputs')
    if not isinstance(inputs, dict) or set(inputs) - {'questions', 'answer_key', 'solutions'}:
        raise WorkspaceError('Invalid source input manifest.')
    allowed = [manifest_path, status_path]
    for role, item in inputs.items():
        name = 'input/' + role + '.pdf'
        if not isinstance(item, dict) or item.get('file') != name or type(item.get('size')) is not int or not isinstance(item.get('sha256'), str):
            raise WorkspaceError('Malformed original input record: ' + role)
        path = folder / name
        if (folder / 'input').is_symlink() or path.is_symlink() or folder not in path.resolve().parents or not path.is_file():
            raise WorkspaceError('Original input missing or unsafe: ' + role)
        if not 0 <= path.stat().st_size <= MAX_FILE:
            raise WorkspaceError('Original input exceeds the 24 MiB limit: ' + role)
        body = path.read_bytes()
        if len(body) != item['size'] or sha(body) != item['sha256']:
            raise WorkspaceError('Original input changed; restore the immutable source before syncing: ' + role)
        validated[path] = body
        allowed.append(path)
    if status.get('state') == 'ready_for_integration':
        cached_run(folder)  # Refuse missing or changed completed artifacts.
        run_record = json.loads((folder / 'runs' / status['run_id'] / 'run.json').read_text())
        if run_record.get('input_manifest_sha256') != sha(manifest_body):
            raise WorkspaceError('Completed run does not match its immutable input manifest; restore it before syncing.')
    if status.get('run_id'):
        rid = status['run_id']
        if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,119}', rid):
            raise WorkspaceError('Invalid saved run ID.')
        if (folder / 'runs').is_symlink():
            raise WorkspaceError('Symlink runs directory refused.')
        run = folder / 'runs' / rid
        if run.is_symlink():
            raise WorkspaceError('Symlink run directory refused.')
        names = ('export.zip', 'exam.json', 'checks.json', 'run.json') if status['state'] == 'ready_for_integration' else ('REFUSED.txt',)
        allowed += [run / n for n in names if (run / n).is_file()]
    files = {}
    for p in allowed:
        if p.is_symlink() or folder not in p.resolve().parents:
            raise WorkspaceError('Symlink/outside path refused.')
        files[f'paper_jobs/{pid}/' + p.relative_to(folder).as_posix()] = validated[p] if p in validated else p.read_bytes()
    try:
        result = (api or GitHub()).upload(files, 'Sync paper ' + pid + ': ' + status['state'])
    except Exception as exc:
        if queue_on_error:
            try:
                queue_sync(folder, str(exc))
                print('QUEUED: ' + pid + ' — upload will retry while the launcher is open or on its next launch.')
            except (WorkspaceError, OSError, ValueError) as queue_exc:
                print('Local results kept; upload queue could not be saved: ' + str(queue_exc))
        raise
    # Receipt is local only: no ever-changing timestamps to cause repeated commits.
    save_json(folder / 'sync_receipt.json', dict(result, synced_at=now(), branch=BRANCH))
    print('SYNCED: ' + pid + ' | commit ' + result['commit'][:10])
    print('Both assistants can read: ' + REPO + ' / ' + BRANCH + ' / paper_jobs/' + pid)
    return result


def prepare_job(root, pid, source=None, key=None, solutions=None, profile='unspecified',
                export=None, keep_color=False):
    pid = paper_id(pid)
    root = Path(root).expanduser().resolve()
    folder = root / pid
    if folder.is_symlink():
        raise WorkspaceError('Symlink job folder refused.')
    folder.mkdir(parents=True, exist_ok=True)
    roles = {'questions': source, 'answer_key': key, 'solutions': solutions}
    inputs = {}
    for role, value in roles.items():
        if value:
            p = input_pdf(value)
            inputs[role] = {'file': 'input/' + role + '.pdf', 'original_name': p.name,
                            'sha256': sha(p.read_bytes()), 'size': p.stat().st_size}
    job = {'schema': 'modulus_shared_paper_v1', 'paper_id': pid, 'inputs': inputs,
           'profile': profile, 'profile_basis': 'owner_selected' if profile != 'unspecified' else 'not_selected'}
    if export:
        p = Path(export)
        job['source_export'] = {'original_name': p.name, 'sha256': sha(p.read_bytes()),
                                'size': p.stat().st_size}
    if keep_color:
        job['crop_settings'] = {'keep_color': True}
    if profile == 'jee-main-mock':
        job['requested_scoring'] = {'correct': 4, 'incorrect': -1, 'unanswered': 0,
                                    'duration_minutes': 180, 'basis': 'owner_selected_mock_rules'}
    path = folder / 'job.json'
    if path.is_symlink() or (folder / 'input').is_symlink():
        raise WorkspaceError('Symlink manifest/input directory refused.')
    if path.exists():
        old = json.loads(path.read_text())
        # Legacy attach manifests did not contain the export identity. Reuse an
        # exact completed export without rewriting that immutable manifest.
        legacy = dict(job)
        legacy.pop('source_export', None)
        if export and 'source_export' not in old and old == legacy and cached_run(folder, job['source_export']['sha256']):
            job = old
        if old != job:
            raise WorkspaceError('This paper ID already has different files or settings. Choose a new ID; previous work is preserved.')
    for role, value in roles.items():
        if value:
            dest = folder / inputs[role]['file']
            if dest.is_symlink():
                raise WorkspaceError('Symlink input refused.')
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and sha(dest.read_bytes()) != inputs[role]['sha256']:
                raise WorkspaceError('Input collision; original retained.')
            if not dest.exists():
                shutil.copy2(input_pdf(value), dest)
    save_json(path, job)
    return folder, job


def record_status(folder, job, state, run_id, **extra):
    save_json(folder / 'status.json', {'schema': 'modulus_shared_status_v1',
        'paper_id': job['paper_id'], 'state': state, 'run_id': run_id,
        'updated_at': now(), 'updated_by': 'mac_runner',
        'solutions': 'supplied_not_matched' if 'solutions' in job['inputs'] else 'not_supplied',
        'publication': 'not_deployed', **extra})


def finish_run(folder, job, run, archive):
    data, check = validate_export(archive)
    save_json(run / 'exam.json', data)
    save_json(run / 'checks.json', check)
    save_json(run / 'run.json', {'run_id': run.name, 'completed_at': now(),
        'cropper_sha256': sha((HERE / 'question_boundaries.py').read_bytes()) if (HERE / 'question_boundaries.py').is_file() else None,
        'export_sha256': check['export_sha256'],
        'key_semantics': 'preserved verbatim; a numeric answer is NOT assumed to be an option',
        'input_manifest_sha256': sha((folder / 'job.json').read_bytes())})
    record_status(folder, job, 'ready_for_integration', run.name, questions=data['count'],
        next_action='Assistant: identify source sections/types, map supplied solutions, validate selected marking, test portal integration.')


def cached_run(folder, export_sha=None):
    status = folder / 'status.json'
    if not status.is_file():
        return False
    state = json.loads(status.read_text())
    if state.get('state') != 'ready_for_integration':
        return False
    rid = state.get('run_id')
    if not isinstance(rid, str) or not rid or PurePosixPath(rid).name != rid or rid in ('.', '..') or '\\' in rid:
        raise WorkspaceError('Invalid saved run ID.')
    run = folder / 'runs' / rid / 'run.json'
    required = ('run.json', 'exam.json', 'checks.json', 'export.zip')
    for name in required:
        path = run.parent / name
        if path.is_symlink() or folder not in path.resolve().parents or not path.is_file():
            raise WorkspaceError('Completed run is missing/unsafe; original run retained. Restore it before retrying.')
    previous = json.loads(run.read_text())
    if sha((run.parent / 'export.zip').read_bytes()) != previous.get('export_sha256'):
        raise WorkspaceError('Completed export changed; original run retained. Restore it before retrying.')
    data, checks = validate_export(run.parent / 'export.zip')
    if json.loads((run.parent / 'exam.json').read_text()) != data or json.loads((run.parent / 'checks.json').read_text()).get('export_sha256') != checks['export_sha256']:
        raise WorkspaceError('Completed run metadata changed; restore it before retrying.')
    if export_sha:
        return previous.get('export_sha256') == export_sha
    return previous.get('input_manifest_sha256') == sha((folder / 'job.json').read_bytes()) and previous.get('cropper_sha256') == sha((HERE / 'question_boundaries.py').read_bytes())


def new_run(folder):
    rid = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    if (folder / 'runs').is_symlink():
        raise WorkspaceError('Symlink runs directory refused.')
    run = folder / 'runs' / rid
    run.mkdir(parents=True)
    return run


def package_output(output, destination):
    selected = [output / 'exam.json', output / 'REPORT.txt', output / 'boundaries.json']
    selected += sorted((output / 'crops').glob('*.png'))
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in selected:
            if p.is_file():
                z.write(p, p.relative_to(output).as_posix())


def process(args):
    with job_lock(args.root, args.paper_id):
        return _process(args)


def _process(args):
    # The lock covers immutable input preparation as well as cropper execution.
    source = input_pdf(args.source)
    key, solutions = getattr(args, 'key', None), getattr(args, 'solutions', None)
    keep_color = getattr(args, 'keep_color', False)
    for p in (key, solutions):
        if p:
            input_pdf(p)
    folder, job = prepare_job(args.root, args.paper_id, source, key, solutions,
                              args.profile, keep_color=keep_color)
    if cached_run(folder):
        print('Already processed with this cropper; reusing existing crops.')
        if not args.offline:
            _sync_job(folder)
        return folder
    run = new_run(folder)
    record_status(folder, job, 'processing', run.name)
    # Local work must continue even when GitHub is unavailable. Sync once the
    # result or refusal exists, so upload retry never requires recropping.
    output = run / 'local_crop_output'
    command = [sys.executable, str(HERE / 'question_boundaries.py'),
               str(folder / 'input/questions.pdf'), '--out', str(output)]
    if key:
        command += ['--key', str(folder / 'input/answer_key.pdf')]
    if keep_color:
        command += ['--keep-color']
    try:
        result = subprocess.run(command, check=False)
        if result.returncode:
            reason = (output / 'REFUSED.txt').read_text() if (output / 'REFUSED.txt').is_file() else 'Cropper exited with code ' + str(result.returncode)
            raise WorkspaceError(reason)
        package_output(output, run / 'export.zip')
        finish_run(folder, job, run, run / 'export.zip')
    except Exception as exc:
        (run / 'REFUSED.txt').write_text(str(exc)[:20000])
        record_status(folder, job, 'held', run.name, reason=str(exc)[:2000])
        upload_error = ''
        if not args.offline:
            try:
                _sync_job(folder)
            except (WorkspaceError, OSError, ValueError, subprocess.SubprocessError) as sync_exc:
                upload_error = ' Upload also failed: ' + str(sync_exc)
        raise WorkspaceError('Paper held: ' + str(exc)[:2000] + '. Originals/report kept in ' + str(folder) + upload_error) from exc
    if not args.offline:
        _sync_job(folder)
    print('Local results: ' + str(output))
    if not getattr(args, 'no_review', False) and sys.platform == 'darwin' and (output / 'review.html').is_file():
        subprocess.run(['open', str(output / 'review.html')], check=False)
    return folder


def attach(args):
    with job_lock(args.root, args.paper_id):
        return _attach(args)


def _attach(args):
    # Existing accepted export: upload unchanged, do NOT recrop.
    validate_export(args.source)
    folder, job = prepare_job(args.root, args.paper_id,
        source=getattr(args, 'source_pdf', None), key=getattr(args, 'key', None),
        solutions=getattr(args, 'solutions', None), profile=args.profile,
        export=args.source)
    if cached_run(folder, sha(Path(args.source).read_bytes())):
        if not args.offline:
            _sync_job(folder)
        print('This export is already stored; no duplicate run created.')
        return folder
    run = new_run(folder)
    shutil.copy2(args.source, run / 'export.zip')
    finish_run(folder, job, run, run / 'export.zip')
    if not args.offline:
        _sync_job(folder)
    print('Existing crops preserved. Local copy: ' + str(folder))
    return folder


def _upload_queue_dir(root, create=False):
    root = Path(root).expanduser().resolve()
    directory = root / '.upload_queue'
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise WorkspaceError('Unsafe local upload queue directory.')
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


@contextmanager
def _upload_queue_lock(root):
    """Hold only for local record changes, never during a network request."""
    import fcntl
    directory = _upload_queue_dir(root, create=True)
    path = directory / '.lock'
    if path.is_symlink():
        raise WorkspaceError('Unsafe local upload queue lock.')
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags, 0o600), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield directory


def _read_upload_record(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise WorkspaceError('Unsafe or oversized local upload queue record.')
    item = json.loads(path.read_text())
    pid = paper_id(path.stem)
    if (not isinstance(item, dict) or item.get('schema') != 'modulus_upload_queue_v1'
            or item.get('paper_id') != pid
            or not isinstance(item.get('revision'), str)
            or not re.fullmatch(r'[0-9a-f]{32}', item['revision'])
            or type(item.get('attempts')) is not int or item['attempts'] < 0
            or type(item.get('next_retry_at', 0)) not in (int, float)
            or not math.isfinite(item.get('next_retry_at', 0)) or item.get('next_retry_at', 0) < 0):
        raise WorkspaceError('Malformed local upload queue record.')
    return item


def queue_sync(folder, reason=''):
    """Persist a pending API upload; no cropping, hosted processing or deployment."""
    folder = Path(folder).expanduser()
    if folder.is_symlink() or not folder.is_dir():
        raise WorkspaceError('Unsafe queued paper folder.')
    folder = folder.resolve()
    pid = paper_id(folder.name)
    for name in ('job.json', 'status.json'):
        path = folder / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE:
            raise WorkspaceError('Queued paper is missing a safe job/status record.')
        item = json.loads(path.read_text())
        if not isinstance(item, dict) or item.get('paper_id') != pid:
            raise WorkspaceError('Queued paper ID does not match its folder.')
    with _upload_queue_lock(folder.parent) as directory:
        path = directory / (pid + '.json')
        previous = _read_upload_record(path) if path.exists() or path.is_symlink() else {}
        record = {'schema': 'modulus_upload_queue_v1', 'paper_id': pid,
                  'revision': uuid.uuid4().hex, 'created_at': previous.get('created_at', now()),
                  'updated_at': now(), 'attempts': previous.get('attempts', 0),
                  'last_error': str(reason)[:1000], 'next_retry_at': 0, 'state': 'pending'}
        save_json(path, record)
    return record


def drain_queue(root, api=None, force=True):
    """Try each saved upload once; retain failures and never rerun the cropper."""
    root = Path(root).expanduser().resolve()
    directory = _upload_queue_dir(root)
    if not directory.exists():
        return []
    # Multiple launchers may be open. Only one drainer sends queued uploads;
    # enqueue operations still proceed under the separate brief record lock.
    import fcntl
    guard = directory / '.drain.lock'
    if guard.is_symlink():
        raise WorkspaceError('Unsafe local queue worker lock.')
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    results = []
    with os.fdopen(os.open(guard, flags, 0o600), 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return []
        for path in sorted(directory.glob('*.json')):
            row = {'paper_id': path.stem, 'outcome': 'queued'}
            results.append(row)
            record = None
            try:
                with _upload_queue_lock(root):
                    record = _read_upload_record(path)
                pid = record['paper_id']
                next_retry = record.get('next_retry_at', 0)
                if not force and isinstance(next_retry, (int, float)) and next_retry > dt.datetime.now(dt.timezone.utc).timestamp():
                    row['next_retry_at'] = next_retry
                    continue
                # Resolve the target from validated ID, never from queue-provided paths.
                folder = root / pid
                if folder.is_symlink() or not folder.is_dir():
                    raise WorkspaceError('Queued paper folder is missing or unsafe.')
                result = sync_job(folder, api, queue_on_error=False)
                with _upload_queue_lock(root):
                    # A newer enqueue may have arrived while this request ran.
                    # Remove only the exact revision that has now been uploaded.
                    current = _read_upload_record(path) if path.exists() or path.is_symlink() else None
                    if current and current['revision'] == record['revision']:
                        path.unlink()
                row.update(outcome='synced', commit=result['commit'])
            except Exception as exc:
                row['reason'] = str(exc)[:1000]
                if record is not None:
                    try:
                        with _upload_queue_lock(root):
                            current = _read_upload_record(path) if path.exists() or path.is_symlink() else None
                            if current and current['revision'] == record['revision']:
                                attempts = current['attempts'] + 1
                                delay = min(60 * (2 ** min(attempts - 1, 6)), 3600)
                                current.update(attempts=attempts, updated_at=now(),
                                               last_error=str(exc)[:1000],
                                               next_retry_at=dt.datetime.now(dt.timezone.utc).timestamp() + delay)
                                save_json(path, current)
                    except (WorkspaceError, OSError, ValueError):
                        pass  # Never replace an unsafe/concurrently edited record.
    return results


def start_queue_worker(root, interval=60, pause_event=None):
    """Resume saved uploads while this launcher is open; closing keeps the queue."""
    import threading
    stop = threading.Event()
    root = Path(root).expanduser().resolve()

    def retry():
        while not stop.is_set():
            try:
                # Avoid authentication/API requests when there is no pending work.
                directory = _upload_queue_dir(root)
                if (pause_event is None or not pause_event.is_set()) and directory.is_dir() and any(directory.glob('*.json')):
                    drain_queue(root, force=False)
            except Exception:
                # The interactive/local processing flow must keep working.
                # Existing queue records retain the actionable upload error.
                pass
            if stop.wait(interval):
                break

    thread = threading.Thread(target=retry, name='modulus-upload-queue', daemon=True)
    thread.start()
    return stop, thread


PROFILES = ('unspecified', 'jee-main-mock')


def strict_pdf(path):
    """Batch preflight: parse every PDF before any cropper starts."""
    path = input_pdf(path)
    try:
        import pdfplumber
        with pdfplumber.open(path) as doc:
            if not doc.pages:
                raise ValueError('no pages')
            for page in doc.pages:
                if page.width <= 0 or page.height <= 0:
                    raise ValueError('invalid page size')
                # Resolve content streams too, without OCR or rendering.
                for stream in page.page_obj.contents:
                    stream.get_data()
                page.objects
    except ImportError as exc:
        raise WorkspaceError('PDF validation needs pdfplumber. Run the Mac launcher to install it.') from exc
    except Exception as exc:
        raise WorkspaceError('Unreadable/corrupt or password-protected PDF: ' + path.name) from exc
    return path


def batch_profile(value):
    if value not in PROFILES:
        raise WorkspaceError('Profile must be unspecified or jee-main-mock (explicit owner selection).')
    return value


FILE_ROLES = {'questions': 'questions.pdf', 'answer_key': 'answer_key.pdf',
              'solutions': 'solutions.pdf', 'export': 'export.zip'}


def selection_path(source, root):
    directory = Path(root).expanduser().resolve() / '.batch_selections'
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise WorkspaceError('Unsafe saved-selection directory.')
    path = directory / (sha(str(Path(source).resolve()).encode()) + '.json')
    if path.is_symlink():
        raise WorkspaceError('Unsafe saved-selection file.')
    return path


def read_selections(source, root):
    path = selection_path(source, root)
    if not path.exists():
        return {}
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise WorkspaceError('Invalid/oversized saved file selections.')
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('schema') != 'modulus_batch_selections_v1' or not isinstance(data.get('folders'), dict):
        raise WorkspaceError('Invalid saved file selections.')
    return data['folders']


def remember_selection(source, root, directory, fingerprint, mapping, ignored):
    # Merge after acquiring the lock, so selections for another paper are kept.
    with job_lock(root, 'BATCH-SELECTIONS'):
        selections = read_selections(source, root)
        selections[directory.name] = {'fingerprint': fingerprint, 'files': mapping, 'ignore': ignored}
        save_json(selection_path(source, root), {'schema': 'modulus_batch_selections_v1', 'folders': selections})


def checked_selection(candidates, mapping, ignored):
    if not isinstance(mapping, dict) or set(mapping) - set(FILE_ROLES):
        raise WorkspaceError('File selection roles must be questions, answer_key, solutions or export.')
    if not isinstance(ignored, list) or any(not isinstance(name, str) for name in ignored):
        raise WorkspaceError('Ignored files must be a list of filenames.')
    used = []
    names = {}
    for role, name in mapping.items():
        if not isinstance(name, str) or not name or PurePosixPath(name).name != name or '\\' in name or ':' in name:
            raise WorkspaceError('Unsafe selected filename; choose a file inside this paper folder.')
        if name not in candidates or candidates[name].is_symlink():
            raise WorkspaceError('Selected file is missing or unsafe: ' + name)
        expected = '.zip' if role == 'export' else '.pdf'
        if candidates[name].suffix.lower() != expected:
            raise WorkspaceError('Wrong file format for ' + role + ': ' + name)
        used.append(name)
        names[FILE_ROLES[role]] = candidates[name]
    if len(used + ignored) != len(set(used + ignored)):
        raise WorkspaceError('A file cannot have multiple roles or also be ignored.')
    if set(used + ignored) != set(candidates):
        raise WorkspaceError('Ambiguous input: every PDF/ZIP must be selected or explicitly ignored. Keep original filenames; select files in the Mac launcher.')
    if not set(mapping) & {'questions', 'export'}:
        raise WorkspaceError('A question PDF or existing export must be selected.')
    return names


def ask_file_roles(directory, candidates, initial):
    print('\nSelect files for ' + directory.name + ' — original filenames stay unchanged.')
    numbered = sorted(candidates, key=lambda name: (name.casefold(), name))
    for index, name in enumerate(numbered, 1):
        print('  ' + str(index) + ': ' + repr(name))
    mapping = dict(initial)
    for role in ('export', 'questions', 'answer_key', 'solutions'):
        if role in mapping:
            continue
        suffix = '.zip' if role == 'export' else '.pdf'
        choices = {str(i): name for i, name in enumerate(numbered, 1)
                   if name not in mapping.values() and candidates[name].suffix.lower() == suffix}
        if not choices:
            continue
        required = role == 'questions' and 'export' not in mapping
        while True:
            label = {'export': 'Existing cropped EXPORT ZIP', 'questions': 'QUESTION paper PDF',
                     'answer_key': 'Separate ANSWER KEY PDF', 'solutions': 'Separate SOLUTIONS PDF'}[role]
            answer = input(label + ' number (' + ', '.join(choices) +
                           ('; Enter holds this paper' if required else '; Enter skips') + ') > ').strip()
            if not answer:
                if required:
                    raise WorkspaceError('Paper held: no question PDF selected. Original filenames kept.')
                break
            if answer in choices:
                mapping[role] = choices[answer]
                break
            print('Choose one of the listed numbers, or press Enter.')
    ignored = [name for name in numbered if name not in mapping.values()]
    if ignored:
        print('Unselected files: ' + ', '.join(repr(name) for name in ignored))
        if input('Leave these files unused? y / Enter holds this paper > ').strip().lower() != 'y':
            raise WorkspaceError('Paper held: file associations were not selected; original files kept.')
    checked_selection(candidates, mapping, ignored)
    return mapping, ignored


def associate_files(source, root, directory, candidates, settings, select_files=False):
    # Fingerprints invalidate saved choices even when filenames are unchanged.
    fingerprint = {}
    for name, path in candidates.items():
        if path.stat().st_size > MAX_FILE:
            raise WorkspaceError('Input exceeds the 24 MiB per-file limit: ' + name)
        fingerprint[name] = sha(path.read_bytes())
    if 'files' in settings:
        return checked_selection(candidates, settings['files'], settings.get('ignore', []))
    if 'ignore' in settings:
        raise WorkspaceError('paper.json ignore requires explicit files selections.')
    saved = read_selections(source, root).get(directory.name)
    if saved is not None:
        if not isinstance(saved, dict):
            raise WorkspaceError('Malformed saved file selection.')
        if saved.get('fingerprint') == fingerprint:
            return checked_selection(candidates, saved.get('files'), saved.get('ignore', []))
        if not select_files:
            raise WorkspaceError('Saved file selections changed. Use the Mac launcher to select again; changed originals need a new paper ID.')
    initial = {}
    for role, canonical in FILE_ROLES.items():
        matches = [name for name in candidates if name.lower() == canonical]
        if len(matches) > 1:
            raise WorkspaceError('Duplicate input role: ' + canonical)
        if matches:
            initial[role] = matches[0]
    # A single ZIP has a verifiable export structure, irrespective of its name.
    zips = [name for name in candidates if candidates[name].suffix.lower() == '.zip']
    if 'export' not in initial and len(zips) == 1:
        validate_export(candidates[zips[0]])
        initial['export'] = zips[0]
    remaining = [name for name in candidates if candidates[name].suffix.lower() == '.pdf' and name not in initial.values()]
    if 'questions' not in initial and 'export' not in initial and len(remaining) == 1:
        initial['questions'] = remaining[0]
    if saved is None and set(initial.values()) == set(candidates):
        return checked_selection(candidates, initial, [])
    if not select_files:
        raise WorkspaceError('Ambiguous input: select question/key/solutions files in the Mac launcher, or set paper.json files; no renaming needed.')
    mapping, ignored = ask_file_roles(directory, candidates, initial)
    # Validate the actual selected contents before remembering a selection.
    names = checked_selection(candidates, mapping, ignored)
    for canonical, path in names.items():
        validate_export(path) if canonical == 'export.zip' else strict_pdf(path)
    remember_selection(source, root, directory, fingerprint, mapping, ignored)
    return names


def discover_batch(source, root, profile='unspecified', overrides=None, select_files=False):
    """Read-only discovery. No guessed associations, output directories or cropping."""
    source = Path(source).expanduser()
    if source.is_symlink() or not source.is_dir():
        raise WorkspaceError('Batch input must be a real folder, not a symlink.')
    source = source.resolve()
    root = Path(root).expanduser().resolve()
    if source == root or source in root.parents or root in source.parents:
        raise WorkspaceError('Keep batch input and generated job folders separate; neither may contain the other.')
    batch_profile(profile)
    overrides = overrides or {}
    entries = []
    for directory in sorted(source.iterdir(), key=lambda p: p.name.casefold()):
        if directory.name.startswith('.'):
            continue  # Finder metadata, never an input paper.
        item = {'folder': str(directory), 'paper_id': directory.name.upper().replace(' ', '-'),
                'profile': profile, 'files': {}, 'hashes': {}, 'reason': None}
        entries.append(item)
        try:
            if directory.is_symlink() or not directory.is_dir():
                raise WorkspaceError('Expected one real subfolder per paper; loose files/symlinks are not processed.')
            settings = {}
            config = directory / 'paper.json'
            if config.exists() or config.is_symlink():
                if config.is_symlink() or not config.is_file() or config.stat().st_size > 16384:
                    raise WorkspaceError('Unsafe or oversized paper.json.')
                settings = json.loads(config.read_text())
                if not isinstance(settings, dict) or set(settings) - {'paper_id', 'profile', 'files', 'ignore'}:
                    raise WorkspaceError('paper.json accepts only paper_id, profile, files and ignore.')
                item['paper_id'] = paper_id(settings.get('paper_id', item['paper_id']))
                item['profile'] = batch_profile(settings.get('profile', profile))
            paper_id(item['paper_id'])
            item['inventory'] = sorted(p.name for p in directory.iterdir() if not p.name.startswith('.'))
            candidates = {}
            documents = []
            for p in directory.iterdir():
                if p.name.startswith('.') or p.name == 'paper.json':
                    continue
                if p.is_symlink() or p.is_dir():
                    raise WorkspaceError('Symlinks/nested input folders are not accepted: ' + p.name)
                if not p.is_file():
                    raise WorkspaceError('Special/nonregular input files are not accepted: ' + p.name)
                if p.suffix.lower() in ('.doc', '.docx'):
                    documents.append(p.name)
                    continue
                if p.suffix.lower() in ('.pdf', '.zip'):
                    candidates[p.name] = p
            if not candidates:
                if documents:
                    raise WorkspaceError('Word documents need a PDF copy before processing; originals stay unchanged: ' + ', '.join(documents))
                raise WorkspaceError('Missing question PDF or existing export ZIP.')
            item['unprocessed_documents'] = documents
            names = associate_files(source, root, directory, candidates, settings, select_files)
            for name, p in names.items():
                if name == 'export.zip':
                    validate_export(p)
                else:
                    strict_pdf(p)
                item['files'][name] = str(p)
                item['hashes'][name] = sha(p.read_bytes())
            if item['paper_id'] in overrides:
                item['profile'] = batch_profile(overrides[item['paper_id']])
        except (WorkspaceError, OSError, ValueError, TypeError) as exc:
            item['reason'] = str(exc)
    if not entries:
        raise WorkspaceError('No paper subfolders found in the batch input folder.')
    ids = {}
    for item in entries:
        ids.setdefault(item['paper_id'], []).append(item)
    for pid, duplicates in ids.items():
        if len(duplicates) > 1:
            for item in duplicates:
                item['reason'] = 'Duplicate paper ID ' + pid + '; all folders with this ID are held.'
    if set(overrides) - set(ids):
        raise WorkspaceError('Profile override references an unknown paper ID: ' + ', '.join(sorted(set(overrides) - set(ids))))
    return entries


def show_batch(entries):
    print('\nBATCH PREFLIGHT — ' + str(len(entries)) + ' paper folders (processed sequentially)')
    for item in entries:
        if item['reason']:
            print('  ' + item['paper_id'] + ' | HELD: ' + item['reason'])
        else:
            files = item['files']
            mode = 'reuse export unchanged' if 'export.zip' in files else 'crop question PDF'
            key = 'separate key' if 'answer_key.pdf' in files else 'key from existing export' if 'export.zip' in files else 'embedded key (if present)'
            solutions = 'solutions stored, not matched' if 'solutions.pdf' in files else 'no solutions supplied'
            print('  ' + item['paper_id'] + ' | ' + ', '.join(role.rsplit('.', 1)[0] + ': ' + Path(path).name for role, path in files.items()) + ' | ' + mode + ' | ' + key + ' | ' + solutions + ' | profile=' + item['profile'])
            if item.get('unprocessed_documents'):
                print('    Word originals left unused and unchanged: ' + ', '.join(item['unprocessed_documents']))


def run_batch(args, entries=None):
    overrides = {}
    for value in getattr(args, 'paper_profile', []) or []:
        pid, sep, profile = value.partition('=')
        if not sep or paper_id(pid) in overrides:
            raise WorkspaceError('Use each --paper-profile ID=PROFILE once.')
        overrides[pid] = batch_profile(profile)
    if entries is None:
        entries = discover_batch(args.source, args.root, args.profile, overrides,
                                 select_files=getattr(args, 'select_files', False))
        show_batch(entries)
    report_dir = Path(args.root).expanduser().resolve() / '.batch_reports'
    if report_dir.is_symlink() or (report_dir.exists() and not report_dir.is_dir()):
        raise WorkspaceError('Unsafe batch report directory; no papers processed.')
    results = []
    for item in entries:
        pid = item['paper_id']
        row = {'paper_id': pid, 'input_folder': item['folder'], 'outcome': 'held', 'reason': item['reason']}
        results.append(row)
        if item['reason']:
            continue
        folder = Path(args.root).expanduser().resolve() / pid
        files = item['files']
        # Check the snapshot again; do not process a changed folder after preflight.
        try:
            directory = Path(item['folder'])
            if directory.is_symlink() or sorted(p.name for p in directory.iterdir() if not p.name.startswith('.')) != item['inventory']:
                raise WorkspaceError('Input folder changed after preflight. Rerun the batch.')
            for name, path in files.items():
                p = Path(path)
                if p.is_symlink() or not p.is_file() or sha(p.read_bytes()) != item['hashes'][name]:
                    raise WorkspaceError('Input changed after preflight: ' + name + '. Rerun the batch.')
            run_args = argparse.Namespace(source=Path(files.get('export.zip', files.get('questions.pdf'))),
                source_pdf=Path(files['questions.pdf']) if 'export.zip' in files and 'questions.pdf' in files else None,
                key=files.get('answer_key.pdf'), solutions=files.get('solutions.pdf'),
                root=args.root, paper_id=pid, profile=item['profile'], offline=True,
                keep_color=args.keep_color, no_review=True)
            previous = json.loads((folder / 'status.json').read_text()) if (folder / 'status.json').is_file() else {}
            if 'export.zip' in files:
                attach(run_args)
            else:
                process(run_args)
            current = json.loads((folder / 'status.json').read_text())
            reused = previous.get('run_id') == current.get('run_id')
            row['run_id'] = current['run_id']
            if args.offline:
                row.update(outcome='already processed' if reused else 'upload failed/local-only',
                           reason='Local results retained; offline mode selected.')
            else:
                try:
                    receipt = sync_job(folder)
                    row.update(outcome='already processed' if reused and receipt['changed'] == 0 else 'synced',
                               reason=None, commit=receipt['commit'])
                except (WorkspaceError, OSError, ValueError, subprocess.SubprocessError) as exc:
                    row.update(outcome='upload failed/local-only', reason=str(exc) + ' Upload queued; crops are saved and retry without recropping.')
        except Exception as exc:
            row['reason'] = str(exc)
            # A refusal is useful to collaborators, but never hide it behind an upload error.
            try:
                state = json.loads((folder / 'status.json').read_text())
                if state.get('state') == 'held' and not args.offline:
                    sync_job(folder)
            except Exception as upload_exc:
                row['upload_note'] = str(upload_exc)
        print('BATCH: ' + pid + ' | ' + row['outcome'] + (' | ' + row['reason'] if row['reason'] else ''))
    if report_dir.is_symlink():
        raise WorkspaceError('Batch results kept, but report directory became a symlink; report write refused.')
    report = report_dir / (dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8] + '.json')
    save_json(report, {'schema': 'modulus_batch_report_v1', 'completed_at': now(),
        'input_folder': str(Path(args.source).expanduser().resolve()), 'publication': 'not_deployed', 'papers': results})
    print('\nBATCH COMPLETE — private workspace handoff, not a deployed examination')
    for row in results:
        print('  ' + row['paper_id'] + ': ' + row['outcome'] + (' — ' + row['reason'] if row['reason'] else ''))
    print('Batch report: ' + str(report))
    return results


def interactive_batch(path):
    root = Path.home() / 'Modulus Papers'
    # Discover associations before requesting shared settings; paper.json overrides remain explicit.
    entries = discover_batch(path, root, select_files=True)
    show_batch(entries)
    choice = input('\nMarking: y = JEE Main MOCK (+4/-1/0, 180 min); m = mixed; Enter = unspecified\n(per-paper paper.json profiles take precedence) > ').strip().lower()
    profile = 'jee-main-mock' if choice == 'y' else 'unspecified'
    overrides = {}
    if choice == 'm':
        for item in entries:
            if not item['reason']:
                answer = input(item['paper_id'] + ': y = JEE mock, n = unspecified, Enter = keep ' + item['profile'] + ' > ').strip().lower()
                if answer in ('y', 'n'):
                    overrides[item['paper_id']] = 'jee-main-mock' if answer == 'y' else 'unspecified'
    # No files have been processed yet. Refresh the plan with selected settings.
    entries = discover_batch(path, root, profile, overrides)
    print('Selected marking: ' + ', '.join(item['paper_id'] + '=' + item['profile'] for item in entries if not item['reason']))
    args = argparse.Namespace(source=path, root=root, profile=profile, offline=False,
                              keep_color=False, paper_profile=[])
    return run_batch(args, entries)


def interactive():
    import threading
    pause = threading.Event()
    stop, worker = start_queue_worker(Path.home() / 'Modulus Papers', pause_event=pause)
    try:
        _interactive_loop(pause)
    finally:
        stop.set()  # Pending jobs stay on disk; never wait on a network request here.


def _interactive_loop(pause_uploads=None):
    print('\nMODULUS — one paper workspace, shared by Claude and ChatGPT')
    print('Local Mac processing and file uploads do not use GitHub Actions minutes.')
    print('Pending uploads retry while this window is open; closing keeps the saved queue.')
    print('Your PDFs/results go ONLY to the private repository. No Netlify deployment.')
    print('Drag a BATCH FOLDER, a question PDF OR an existing result ZIP. Type q to finish.')
    while True:
        if pause_uploads is not None:
            pause_uploads.clear()
        raw = input('\nBatch folder / question PDF / result ZIP > ').strip()
        if pause_uploads is not None:
            pause_uploads.set()
        if raw.lower() == 'q':
            return
        try:
            path = path_from_prompt(raw)
            if path is not None and path.is_dir():
                interactive_batch(path)
                continue
            if path is None or not path.is_file():
                raise WorkspaceError('File not found.')
            if path.suffix.lower() not in ('.pdf', '.zip'):
                raise WorkspaceError('Choose a PDF or existing export ZIP. Word documents need a PDF copy; keep the original unchanged.')
            default = re.sub(r'[^A-Z0-9_-]+', '-', path.stem.upper()).strip('-')[:70] or 'PAPER'
            default = re.sub(r'[_-]RESULT$', '', default)
            pid = paper_id(input('Paper name/code [' + default + '] > ').strip().upper() or default)
            key = solutions = None
            if path.suffix.lower() == '.pdf':
                key = path_from_prompt(input('Separate ANSWER KEY PDF (Enter if inside the question paper) > '))
                solutions = path_from_prompt(input('Separate SOLUTIONS PDF (optional; Enter to skip) > '))
            choice = input('Standard JEE Main mock marking (+4/-1/0, 180 min)? y / Enter for unspecified > ').strip().lower()
            profile = 'jee-main-mock' if choice == 'y' else 'unspecified'
            args = argparse.Namespace(source=path, paper_id=pid, root=Path.home() / 'Modulus Papers',
                    key=key, solutions=solutions, profile=profile, offline=False, keep_color=False)
            if path.suffix.lower() == '.zip':
                attach(args)
            else:
                process(args)
        except (WorkspaceError, OSError, ValueError, subprocess.SubprocessError) as exc:
            print('\nSTOPPED: ' + str(exc))
            print('Nothing was published. Local originals/results are retained.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--interactive', action='store_true')
    subs = p.add_subparsers(dest='action')
    for name in ('run', 'attach'):
        sub = subs.add_parser(name)
        sub.add_argument('source', type=Path)
        sub.add_argument('--paper-id', required=True)
        sub.add_argument('--root', type=Path, default=Path.home() / 'Modulus Papers')
        sub.add_argument('--profile', choices=('unspecified', 'jee-main-mock'), default='unspecified')
        sub.add_argument('--offline', action='store_true', help='Keep results local; sync later.')
        if name == 'run':
            sub.add_argument('--key', type=Path)
            sub.add_argument('--solutions', type=Path)
            sub.add_argument('--keep-color', action='store_true')
    sub = subs.add_parser('batch', help='Process one input subfolder per paper, sequentially.')
    sub.add_argument('source', type=Path)
    sub.add_argument('--root', type=Path, default=Path.home() / 'Modulus Papers')
    sub.add_argument('--profile', choices=PROFILES, default='unspecified')
    sub.add_argument('--select-files', action='store_true', help='Select ambiguous file roles without renaming originals.')
    sub.add_argument('--paper-profile', action='append', default=[], metavar='ID=PROFILE')
    sub.add_argument('--offline', action='store_true')
    sub.add_argument('--keep-color', action='store_true')
    sub = subs.add_parser('sync')
    sub.add_argument('folder', type=Path)
    args = p.parse_args()
    try:
        if args.interactive:
            interactive()
        elif args.action == 'run':
            process(args)
        elif args.action == 'attach':
            attach(args)
        elif args.action == 'batch':
            results = run_batch(args)
            return int(any(r['outcome'] in ('held', 'upload failed/local-only') for r in results))
        elif args.action == 'sync':
            sync_job(args.folder)
        else:
            p.print_help()
    except (WorkspaceError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print('STOPPED: ' + str(exc), file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
