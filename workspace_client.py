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
    if not text or text.isspace():
        return None
    if '\0' in text:
        raise WorkspaceError('A file path cannot contain a null character.')
    # Finder may escape a literal trailing space as "\\ ". Do not strip input:
    # that would leave a dangling escape before shlex gets to read it.
    literal = Path(text).expanduser()
    try:
        if literal.exists():
            return literal.absolute()
    except OSError:
        pass  # A multi-file drag can be too long to be a single literal path.
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        # Recover an old, already-trimmed Finder path only when the filesystem
        # identifies exactly one destination. Ambiguous repairs stay unselected.
        matches = set()
        if text.endswith('\\'):
            for repair in (text + ' ', text[:-1]):
                try:
                    names = shlex.split(repair)
                    if len(names) == 1:
                        candidate = Path(names[0]).expanduser()
                        if candidate.exists():
                            matches.add(candidate.absolute())
                except (ValueError, OSError):
                    continue
        if len(matches) == 1:
            return matches.pop()
        raise WorkspaceError('Could not read the dragged path. Press Enter at the paper prompt to choose the batch folder.') from exc
    if len(parts) != 1:
        raise WorkspaceError('Drag ONE file or batch folder into each prompt, not several paths.')
    return Path(parts[0]).expanduser().absolute()


def choose_batch_folder():
    """Native macOS folder selection, without shell escaping or renamed inputs."""
    if sys.platform != 'darwin':
        raise WorkspaceError('The folder chooser is available on macOS. Drag or paste a folder path here.')
    script = 'POSIX path of (choose folder with prompt "Choose the folder containing your paper subfolders")'
    result = subprocess.run(['osascript', '-e', script], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, check=False)
    if result.returncode:
        if '-128' in result.stderr:  # The owner cancelled the standard dialog.
            return None
        raise WorkspaceError('The folder chooser could not open. You can still drag or paste the folder path.')
    # osascript adds a protocol newline; spaces inside the returned path are data.
    selected = result.stdout.rstrip('\r\n')
    if not selected:
        return None
    path = Path(selected).expanduser().absolute()
    if not path.is_dir():
        raise WorkspaceError('The selected folder was not found.')
    return path


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
        uploaded = set()
        for p, body in changed.items():
            blob_sha = desired[p]
            if blob_sha not in uploaded:
                blob = self.call('git/blobs', 'POST', {'content': base64.b64encode(body).decode(), 'encoding': 'base64'})
                if blob.get('sha') != blob_sha:
                    raise WorkspaceError('Uploaded blob hash mismatch: ' + p)
                uploaded.add(blob_sha)
            entries.append({'path': p, 'mode': '100644', 'type': 'blob', 'sha': blob_sha})
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
                export=None, keep_color=False, exam_intent=None, source_association=None):
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
    if profile == 'jee-advanced-source':
        job['profile_basis'] = 'source_required'
    if exam_intent is not None:
        if not isinstance(exam_intent, dict) or exam_intent.get('exam_family') not in ('jee-main', 'jee-advanced', 'unknown'):
            raise WorkspaceError('Invalid per-paper exam family metadata.')
        if profile == 'jee-main-mock' and exam_intent['exam_family'] == 'jee-advanced':
            raise WorkspaceError('Main marking cannot be applied to an Advanced source paper.')
        if profile == 'jee-advanced-source' and exam_intent['exam_family'] == 'jee-main':
            raise WorkspaceError('Advanced interface cannot be assigned to a Main source paper.')
        job['exam_intent'] = exam_intent
    if source_association is not None:
        if not isinstance(source_association, dict):
            raise WorkspaceError('Invalid source-file association metadata.')
        job['source_association'] = source_association
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
        identity, old_identity = dict(job), dict(old)
        for annotation in ('exam_intent', 'source_association'):
            identity.pop(annotation, None)
            old_identity.pop(annotation, None)
        if identity == old_identity:
            job = old  # Keep existing annotations and immutable bytes for the same inputs/settings.
        # Legacy attach manifests did not contain the export identity. Reuse an
        # exact completed export without rewriting that immutable manifest.
        legacy = dict(job)
        legacy.pop('source_export', None)
        if 'exam_intent' not in old:
            legacy.pop('exam_intent', None)
        if 'source_association' not in old:
            legacy.pop('source_association', None)
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
    if not path.exists():
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
    intent = job.get('exam_intent')
    if intent:
        check['exam_intent'] = intent
        check['exam_launch_ready'] = False
        check['source_completeness'] = 'unverified_requires_source_scoped_question_counts'
        check['marking_state'] = 'owner_selected_main_rules' if job['profile'] == 'jee-main-mock' else 'source_validation_required'
    save_json(run / 'exam.json', data)
    save_json(run / 'checks.json', check)
    save_json(run / 'run.json', {'run_id': run.name, 'completed_at': now(),
        'cropper_sha256': sha((HERE / 'question_boundaries.py').read_bytes()) if (HERE / 'question_boundaries.py').is_file() else None,
        'export_sha256': check['export_sha256'],
        'key_semantics': 'preserved verbatim; a numeric answer is NOT assumed to be an option',
        'input_manifest_sha256': sha((folder / 'job.json').read_bytes())})
    extra = {}
    if intent:
        extra = {'exam_family': intent['exam_family'], 'required_exam_pattern': intent.get('exam_pattern'),
                 'integration_state': 'held_pending_source_type_marking_and_completeness_validation',
                 'exam_launch_ready': False}
    record_status(folder, job, 'ready_for_integration', run.name, questions=data['count'], **extra,
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
    intent = getattr(args, 'exam_intent', None)
    if intent is None and args.profile in ('jee-main-mock', 'jee-advanced-source'):
        intent = inspect_exam_source(source=source)
    folder, job = prepare_job(args.root, args.paper_id, source, key, solutions,
                              args.profile, keep_color=keep_color, exam_intent=intent,
                              source_association=getattr(args, 'source_association', None))
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
    intent = getattr(args, 'exam_intent', None)
    if intent is None and args.profile in ('jee-main-mock', 'jee-advanced-source'):
        intent = inspect_exam_source(source=getattr(args, 'source_pdf', None), export=args.source)
    folder, job = prepare_job(args.root, args.paper_id,
        source=getattr(args, 'source_pdf', None), key=getattr(args, 'key', None),
        solutions=getattr(args, 'solutions', None), profile=args.profile,
        export=args.source, exam_intent=intent,
        source_association=getattr(args, 'source_association', None))
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


def start_queue_worker(root, interval=60, pause_event=None, wake_event=None):
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
            if wake_event is not None:
                wake_event.wait(interval)
                wake_event.clear()
            elif stop.wait(interval):
                break

    thread = threading.Thread(target=retry, name='modulus-upload-queue', daemon=True)
    thread.start()
    return stop, thread


def _all_paper_id(relative):
    """Stable identity from the source path, independent of its current bytes."""
    path = PurePosixPath(relative)
    value = path.with_suffix('').as_posix()
    # Word originals may sit beside their PDF copies. Keep the PDF identity
    # stable while giving each unsupported Word candidate its own held record.
    if path.suffix.lower() in ('.doc', '.docx'):
        value += '-' + path.suffix[1:].upper()
    clean = re.sub(r'[^A-Z0-9_-]+', '-', value.upper()).strip('-_')
    if not clean:
        clean = 'PAPER-' + sha(value.encode())[:12].upper()
    if len(clean) > 80:
        clean = clean[:67].rstrip('-_') + '-' + sha(value.encode())[:12].upper()
    return paper_id(clean)


def discover_all_papers(source, root):
    """Inventory an explicitly declared collection of complete paper files.

    This mode never associates similarly named keys/solutions automatically.
    Every PDF/ZIP is one source candidate; Word/unsafe inputs are held visibly.
    The earlier one-subfolder-per-paper discovery remains separate and intact.
    """
    source = Path(source).expanduser()
    if source.is_symlink() or not source.is_dir():
        raise WorkspaceError('Batch input must be a real folder, not a symlink.')
    source = source.resolve()
    root = Path(root).expanduser().resolve()
    if source == root or source in root.parents or root in source.parents:
        raise WorkspaceError('Keep batch input and generated job folders separate; neither may contain the other.')
    entries = []
    ignored_directories = {'.git', '.venv', '__pycache__', '__MACOSX'}
    extensions = {'.pdf', '.zip', '.doc', '.docx'}

    def record(path, reason=None):
        relative = path.relative_to(source).as_posix()
        item = {'folder': str(path.parent), 'paper_id': _all_paper_id(relative),
                'profile': 'unspecified', 'files': {}, 'hashes': {},
                'reason': reason, 'source_relative_path': relative,
                'source_file': str(path), 'discovery_mode': 'complete_paper_files'}
        entries.append(item)
        try:
            item['inventory'] = sorted(p.name for p in path.parent.iterdir() if not p.name.startswith('.'))
            if reason:
                return item
            if path.is_symlink() or not path.is_file():
                raise WorkspaceError('Symlink/special source file refused: ' + path.name)
            if path.stat().st_size > MAX_FILE:
                raise WorkspaceError('Input exceeds the 24 MiB per-file limit: ' + path.name)
            if path.suffix.lower() in ('.doc', '.docx'):
                raise WorkspaceError('Word documents need a PDF copy; original names/files remain unchanged: ' + path.name)
            canonical = 'export.zip' if path.suffix.lower() == '.zip' else 'questions.pdf'
            item['files'][canonical] = str(path)
            if canonical == 'export.zip':
                validate_export(path)
            else:
                strict_pdf(path)
            item['hashes'][canonical] = sha(path.read_bytes())
        except (WorkspaceError, OSError, ValueError, TypeError) as exc:
            item['reason'] = str(exc)
        return item

    def walk_error(exc):
        # Permission errors must be visible; never silently omit a subtree.
        path = Path(exc.filename) if exc.filename else source
        if path == source:
            raise WorkspaceError('Could not read the batch input folder.') from exc
        record(path, 'Folder could not be inspected: ' + path.name)

    for current, directories, filenames in os.walk(source, followlinks=False, onerror=walk_error):
        parent = Path(current)
        for name in sorted(list(directories), key=lambda n: (n.casefold(), n)):
            path = parent / name
            if name in ignored_directories:
                directories.remove(name)
            elif path.is_symlink():
                directories.remove(name)
                record(path, 'Symlink folder refused; its contents were not followed: ' + name)
        directories.sort(key=lambda n: (n.casefold(), n))
        for name in sorted(filenames, key=lambda n: (n.casefold(), n)):
            if name == '.DS_Store':
                continue
            path = parent / name
            if path.suffix.lower() in extensions:
                record(path)
    if not entries:
        raise WorkspaceError('No PDF, export ZIP or Word source candidates found in the selected folder.')
    entries.sort(key=lambda item: (item['source_relative_path'].casefold(), item['source_relative_path']))

    groups = {}
    ids = {}
    for item in entries:
        path = PurePosixPath(item['source_relative_path'])
        groups.setdefault((str(path.parent).casefold(), path.stem.casefold()), []).append(item)
        ids.setdefault(item['paper_id'], []).append(item)
    for members in groups.values():
        suffixes = {PurePosixPath(item['source_relative_path']).suffix.lower() for item in members}
        if {'.pdf', '.zip'} <= suffixes:
            for item in members:
                message = 'PDF and export ZIP share a paper name; associate them explicitly in one paper subfolder to reuse the export without recropping.'
                item['reason'] = (item['reason'] + ' ' if item['reason'] else '') + message
    for pid, duplicates in ids.items():
        if len(duplicates) > 1:
            for item in duplicates:
                message = 'Duplicate paper ID ' + pid + '; all candidates with this ID are held.'
                item['reason'] = (item['reason'] + ' ' if item['reason'] else '') + message
    return entries


SOURCE_EVIDENCE_MAX_PAGES = 200
SOURCE_EVIDENCE_MAX_SNIPPETS = 80
SOURCE_EVIDENCE_MAX_CHARS = 50000
SOURCE_EVIDENCE_TITLE_LINES = 12


def _source_title_intent(text):
    """Read explicit first-page headings; filenames and answer values are irrelevant."""
    lines = [line for line in text.splitlines() if line.strip()][:SOURCE_EVIDENCE_TITLE_LINES]
    # Some printed titles use underscores as separators. Match their labels as
    # words, while retaining the original extracted lines as source evidence.
    labels = [line.replace('_', ' ') for line in lines]
    label = re.compile(r'\bJ\.?\s*E\.?\s*E\.?\s*[-:]?\s*(?:\(\s*(Main|Advanced)\s*\)|(Main|Advanced)\b)', re.I)
    evidence = [line for line, normalized in zip(lines, labels) if label.search(normalized)]
    matched = [line for line in labels if label.search(line)]
    families = {'jee-' + (match.group(1) or match.group(2)).lower() for line in matched for match in label.finditer(line)}
    if any(re.search(r'\bMain\b', line, re.I) and re.search(r'\bAdvanced\b', line, re.I) for line in matched):
        families = {'jee-main', 'jee-advanced'}
    if len(families) != 1:
        return 'unknown', None, None, evidence
    family = families.pop()
    if family == 'jee-main':
        return family, 'jee-main', None, evidence
    # Paper identity must be in a title/standalone label. General instructions
    # such as "Both papers are compulsory" do not turn Paper I into a pair.
    standalone = re.compile(r'^\s*(?:(?:Combined\s+)?Both\s+Papers|Papers?\s*[-:]?\s*(?:1|2|I|II)(?:\s*(?:and|&)\s*(?:Paper\s*)?(?:2|II))?)(?:\s*\([^)]*\))?\s*$', re.I)
    heading = '\n'.join(line for line in labels if label.search(line) or standalone.fullmatch(line))
    one = bool(re.search(r'\bPaper\s*[-:]?\s*(?:1|I)\b', heading, re.I))
    two = bool(re.search(r'\bPaper\s*[-:]?\s*(?:2|II)\b', heading, re.I))
    both = bool(re.search(r'\bBoth\s+Papers\b|\bPapers?\s*(?:1|I)\s*(?:and|&)\s*(?:Paper\s*)?(?:2|II)\b', heading, re.I))
    if both:
        return family, 'jee-advanced-both', 'both', evidence
    if one != two:
        part = 'paper_1' if one else 'paper_2'
        return family, 'jee-advanced-1' if one else 'jee-advanced-2', part, evidence
    return family, None, None, evidence


def _source_export_intent(data, document_sha):
    """Use explicit existing portal metadata only, preserving all source answers."""
    known = {'jee-main': ('jee-main', 'jee-main', None),
             'jee_main': ('jee-main', 'jee-main', None),
             'jee-advanced': ('jee-advanced', None, None),
             'jee_advanced': ('jee-advanced', None, None),
             'jee-advanced-1': ('jee-advanced', 'jee-advanced-1', 'paper_1'),
             'jee-advanced-2': ('jee-advanced', 'jee-advanced-2', 'paper_2'),
             'jee-advanced-both': ('jee-advanced', 'jee-advanced-both', 'both')}
    declarations = []
    for key in ('exam_pattern', 'exam_family'):
        if key in data:
            declarations.append((key, data[key]))
    rows = data.get('questions', [])
    if isinstance(rows, list):
        for i, row in enumerate(rows):
            if isinstance(row, dict) and 'exam_pattern' in row:
                declarations.append((f'questions[{i}].exam_pattern', row['exam_pattern']))
    evidence, intents, invalid = [], [], False
    for location, value in declarations:
        evidence.append({'origin': 'export_metadata', 'location': location,
                         'text': value if isinstance(value, str) else '[invalid declaration]',
                         'document_sha256': document_sha})
        if not isinstance(value, str) or value not in known:
            invalid = True
        else:
            intents.append(known[value])
    families = {item[0] for item in intents}
    if invalid or len(families) != 1:
        return 'unknown', None, None, evidence
    family = families.pop()
    if family == 'jee-main':
        return family, 'jee-main', None, evidence
    parts = {item[2] for item in intents if item[2] is not None}
    explicit = data.get('advanced_paper')
    if explicit in ('paper_1', 'paper_2', 'both'):
        parts.add(explicit)
        evidence.append({'origin': 'export_metadata', 'location': 'advanced_paper',
                         'text': explicit, 'document_sha256': document_sha})
    # A combined exam needs an explicit top-level combined declaration.
    if data.get('exam_pattern') == 'jee-advanced-both' or explicit == 'both':
        return family, 'jee-advanced-both', 'both', evidence
    if len(parts) == 1:
        part = parts.pop()
        return family, {'paper_1': 'jee-advanced-1', 'paper_2': 'jee-advanced-2',
                        'both': 'jee-advanced-both'}[part], part, evidence
    return family, None, None, evidence


def inspect_exam_source(source=None, export=None):
    """Collect source evidence for later validation; never invent scoring or types.

    Text-only inspection deliberately leaves scanned sources unresolved. This is
    neither OCR nor a mathematical check, and never changes the accepted cropper.
    """
    result = {'schema': 'modulus_source_evidence_v1', 'exam_family': 'unknown',
              'exam_pattern': None, 'advanced_paper': None, 'source_sha256': None,
              'source_pdf_sha256': None, 'source_export_sha256': None,
              'evidence_type': 'none', 'family_evidence': [], 'instruction_evidence': [],
              'total_pages': None, 'pages_examined': 0, 'truncated': False,
              'limits': {'pages': SOURCE_EVIDENCE_MAX_PAGES,
                         'snippets': SOURCE_EVIDENCE_MAX_SNIPPETS,
                         'characters': SOURCE_EVIDENCE_MAX_CHARS,
                         'title_lines': SOURCE_EVIDENCE_TITLE_LINES},
              'truncation_reasons': [], 'verified_rules': None,
              'marking_state': 'source_validation_required', 'launch_ready': False,
              'status': 'evidence_pending', 'errors': []}
    intents = []
    if source is not None:
        try:
            import pdfplumber
            if Path(source).expanduser().is_symlink():
                raise WorkspaceError('Symlink source refused')
            path = input_pdf(source)
            result['source_pdf_sha256'] = sha(path.read_bytes())
            result['source_sha256'] = result['source_pdf_sha256']
            result['evidence_type'] = 'pdf_text_unverified'
            words = re.compile(r'\b(?:marks?|marking|partial|correct|incorrect|negative|unanswered|unattempted|section|duration|minutes?|hours?|time\s+allowed)\b', re.I)
            chars = 0
            with pdfplumber.open(path) as doc:
                result['total_pages'] = len(doc.pages)
                if not doc.pages:
                    raise ValueError('no pages')
                if len(doc.pages) > SOURCE_EVIDENCE_MAX_PAGES:
                    result['truncation_reasons'].append('pages_limit')
                for page_number, page in enumerate(doc.pages[:SOURCE_EVIDENCE_MAX_PAGES], 1):
                    text = page.extract_text() or ''
                    result['pages_examined'] += 1
                    if page_number == 1:
                        family, pattern, part, title = _source_title_intent(text)
                        if family != 'unknown':
                            intents.append((family, pattern, part))
                        result['family_evidence'] += [
                            {'origin': 'pdf_text_unverified', 'page': 1, 'text': line,
                             'document_sha256': result['source_pdf_sha256']} for line in title]
                        if title and family == 'unknown':
                            result['errors'].append('First-page exam family is ambiguous.')
                    lines = text.splitlines(keepends=True)
                    relevant = {i for i, line in enumerate(lines) if words.search(line)}
                    # Preserve complete extracted lines with their immediate context.
                    selected = set()
                    for i in relevant:
                        selected.update(range(max(0, i - 1), min(len(lines), i + 2)))
                    groups = []
                    for i in sorted(selected):
                        if not groups or i != groups[-1][-1] + 1:
                            groups.append([i])
                        else:
                            groups[-1].append(i)
                    for group in groups:
                        if len(result['instruction_evidence']) >= SOURCE_EVIDENCE_MAX_SNIPPETS:
                            if 'snippets_limit' not in result['truncation_reasons']:
                                result['truncation_reasons'].append('snippets_limit')
                            break
                        snippet = ''.join(lines[i] for i in group)
                        remaining = SOURCE_EVIDENCE_MAX_CHARS - chars
                        if len(snippet) > remaining:
                            if 'characters_limit' not in result['truncation_reasons']:
                                result['truncation_reasons'].append('characters_limit')
                            # Do not present a cut mathematical instruction as verbatim.
                            continue
                        result['instruction_evidence'].append(
                            {'page': page_number, 'text': snippet,
                             'document_sha256': result['source_pdf_sha256'],
                             'evidence_type': 'pdf_text_unverified'})
                        chars += len(snippet)
            if not result['family_evidence'] and not result['instruction_evidence']:
                result['evidence_type'] = 'pdf_text_unavailable'
                result['errors'].append('No usable title/instruction text; scanned sources need local OCR or source review.')
        except Exception as exc:
            result['errors'].append('Could not inspect source PDF (' + type(exc).__name__ + ').')
    if export is not None:
        try:
            path = Path(export)
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE:
                raise WorkspaceError('Unsafe/oversized export')
            export_sha = sha(path.read_bytes())
            result['source_export_sha256'] = export_sha
            if result['source_sha256'] is None:
                result['source_sha256'] = export_sha
                result['evidence_type'] = 'export_metadata_unverified'
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len({item.filename for item in entries}) != len(entries):
                    raise WorkspaceError('Duplicate ZIP entries')
                if sum(item.file_size for item in entries) > MAX_EXPANDED:
                    raise WorkspaceError('Oversized expanded archive')
                for item in entries:
                    safe_member(item.filename)
                    if stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                        raise WorkspaceError('Unsafe ZIP entry')
                info = archive.getinfo('exam.json')
                if info.file_size > MAX_FILE:
                    raise WorkspaceError('Oversized exam metadata')
                data = json.loads(archive.read(info))
            if not isinstance(data, dict):
                raise WorkspaceError('Malformed exam metadata')
            family, pattern, part, evidence = _source_export_intent(data, export_sha)
            result['family_evidence'] += evidence
            if family != 'unknown':
                intents.append((family, pattern, part))
            elif evidence:
                result['errors'].append('Export exam family metadata is ambiguous or unsupported.')
        except Exception as exc:
            result['errors'].append('Could not inspect export metadata (' + type(exc).__name__ + ').')
    families = {item[0] for item in intents}
    if len(families) == 1 and not any('family' in error for error in result['errors']):
        family = families.pop()
        result['exam_family'] = family
        patterns = {item[1] for item in intents if item[1] is not None}
        parts = {item[2] for item in intents if item[2] is not None}
        if family == 'jee-main':
            result['exam_pattern'] = 'jee-main'
        elif len(patterns) == 1 and len(parts) <= 1:
            result['exam_pattern'] = patterns.pop()
            result['advanced_paper'] = parts.pop() if parts else None
        elif len(patterns) > 1:
            result['errors'].append('Advanced paper-part declarations conflict; part remains unresolved.')
    elif len(families) > 1:
        result['errors'].append('PDF and export exam families conflict; no interface selected.')
    result['truncated'] = bool(result['truncation_reasons'])
    if result['family_evidence'] or result['instruction_evidence']:
        result['status'] = 'evidence_collected_requires_validation'
    return result


_COMPANION_DASHES = str.maketrans({c: '-' for c in '\u2010\u2011\u2012\u2013\u2014\u2212'})
_COMPANION_ROLE_PATTERNS = (
    ('final_key', r'FINAL[-_ ]+KEY', '.pdf'),
    ('combined', r'KEY[-_ ]*(?:&|AND)[-_ ]*SOLS', '.pdf'),
    ('answer_key', r'ANSWER[-_ ]+KEY', '.pdf'),
    ('questions', r'QP', '.pdf'),
    ('solutions', r'(?:SOLUTIONS|SOLS)', '.pdf'),
    ('export', r'(?:EXPORT|RESULTS?)', '.zip'),
    ('answer_key', r'KEY', '.pdf'),
)


def _companion_filename(path):
    """Parse explicit role suffixes; no substring, fuzzy-ID or source-type guess."""
    path = Path(path)
    if path.name.lower() in ('questions.pdf', 'answer_key.pdf', 'solutions.pdf', 'export.zip'):
        return None  # Existing per-paper role names have no paper-ID prefix.
    if path.suffix.lower() not in ('.pdf', '.zip'):
        return None
    stem = path.stem.translate(_COMPANION_DASHES)
    for role, pattern, extension in _COMPANION_ROLE_PATTERNS:
        match = re.fullmatch(r'(.+)_(?:' + pattern + r')', stem, re.I)
        if match:
            raw_id = match.group(1)
            pid = raw_id.strip().upper().replace(' ', '-')
            error = None
            try:
                paper_id(pid)
            except WorkspaceError:
                error = 'Invalid paper identifier before the role suffix: ' + raw_id
            if path.suffix.lower() != extension:
                error = 'Wrong file format for named ' + role + ' role: ' + path.name
            return {'paper_id': pid, 'raw_paper_id': raw_id, 'role': role, 'reason': error}
    return None


def has_companion_papers(source):
    """Detect the explicit QP/key/solutions naming contract without reading PDFs."""
    source = Path(source).expanduser()
    if source.is_symlink() or not source.is_dir():
        return False
    ignored = {'.git', '.venv', '__pycache__', '__MACOSX'}
    for current, directories, filenames in os.walk(source, followlinks=False):
        directories[:] = [name for name in directories if name not in ignored and
                          not (Path(current) / name).is_symlink()]
        if any(_companion_filename(name) for name in filenames):
            return True
    return False


def discover_companion_papers(source, root):
    """Group explicit same-ID companions in place; never rename or move sources."""
    source = Path(source).expanduser()
    if source.is_symlink() or not source.is_dir():
        raise WorkspaceError('Batch input must be a real folder, not a symlink.')
    source = source.resolve()
    root = Path(root).expanduser().resolve()
    if source == root or source in root.parents or root in source.parents:
        raise WorkspaceError('Keep batch input and generated job folders separate; neither may contain the other.')
    entries = []
    ignored = {'.git', '.venv', '__pycache__', '__MACOSX'}
    extensions = {'.pdf', '.zip', '.doc', '.docx'}

    def item_for(parent, pid, paths, reason=None):
        try:
            inventory = sorted(p.name for p in parent.iterdir() if not p.name.startswith('.'))
        except OSError:
            inventory = []
            reason = reason or 'Could not inspect source folder inventory.'
        item = {'folder': str(parent), 'paper_id': pid, 'profile': 'unspecified',
                'files': {}, 'hashes': {}, 'inventory': inventory, 'reason': reason,
                'discovery_mode': 'explicit_companion_filenames',
                'source_association': {'method': 'explicit_companion_filenames',
                    'paper_id': pid, 'filenames': {},
                    'answer_key_basis': 'embedded_or_export',
                    'candidates': sorted(path.name for path in paths)}}
        entries.append(item)
        return item

    def validate(item):
        bodies = {}
        for canonical, name in item['files'].items():
            path = Path(name)
            if path.is_symlink() or not path.is_file():
                raise WorkspaceError('Symlink/special source file refused: ' + path.name)
            if path.stat().st_size > MAX_FILE:
                raise WorkspaceError('Input exceeds the 24 MiB per-file limit: ' + path.name)
            if path not in bodies:
                validate_export(path) if canonical == 'export.zip' else strict_pdf(path)
                bodies[path] = sha(path.read_bytes())
            item['hashes'][canonical] = bodies[path]

    def held(path, reason):
        return item_for(path.parent, _all_paper_id(path.relative_to(source).as_posix()), [path], reason)

    def walk_error(exc):
        path = Path(exc.filename) if exc.filename else source
        if path == source:
            raise WorkspaceError('Could not read the batch input folder.') from exc
        held(path, 'Folder could not be inspected; its sources were not skipped silently.')

    for current, directories, filenames in os.walk(source, followlinks=False, onerror=walk_error):
        parent = Path(current)
        for name in sorted(list(directories), key=lambda name: (name.casefold(), name)):
            path = parent / name
            if name in ignored:
                directories.remove(name)
            elif path.is_symlink():
                directories.remove(name)
                held(path, 'Symlink folder refused; its contents were not followed.')
        directories.sort(key=lambda name: (name.casefold(), name))
        paths = [parent / name for name in sorted(filenames, key=lambda name: (name.casefold(), name))
                 if Path(name).suffix.lower() in extensions]
        config = parent / 'paper.json'
        declared, config_error = {}, None
        if config.exists() or config.is_symlink():
            try:
                if config.is_symlink() or not config.is_file() or config.stat().st_size > 16384:
                    raise WorkspaceError('Unsafe or oversized paper.json.')
                declared = json.loads(config.read_text())
                if not isinstance(declared, dict) or set(declared) - {'paper_id', 'profile', 'files', 'ignore'}:
                    raise WorkspaceError('Invalid paper.json settings.')
                if 'ignore' in declared and 'files' not in declared:
                    raise WorkspaceError('paper.json ignore requires explicit files selections.')
            except (WorkspaceError, OSError, ValueError, TypeError) as exc:
                config_error = str(exc)
                declared = {}
        groups, unknown = {}, []
        for path in paths:
            # An owner's explicit mapping has precedence over role-shaped
            # arbitrary names such as FINAL_KEY.pdf inside that paper folder.
            parsed = None if 'files' in declared else _companion_filename(path)
            if parsed is None:
                unknown.append(path)
                continue
            key = parsed['paper_id']
            group = groups.setdefault(key, {'paths': [], 'roles': {}, 'errors': []})
            group['paths'].append(path)
            group['roles'].setdefault(parsed['role'], []).append(path)
            if parsed['reason']:
                group['errors'].append(parsed['reason'])
        for pid, group in sorted(groups.items()):
            safe_pid = pid if ID_RE.fullmatch(pid) else _all_paper_id(group['paths'][0].relative_to(source).as_posix())
            item = item_for(parent, safe_pid, group['paths'])
            try:
                if config_error:
                    raise WorkspaceError(config_error)
                if declared:
                    if len(groups) != 1:
                        raise WorkspaceError('paper.json covers multiple companion groups; explicit per-paper settings are required.')
                    item['paper_id'] = paper_id(declared.get('paper_id', item['paper_id']))
                    item['profile'] = batch_profile(declared.get('profile', 'unspecified'))
                    item['profile_explicit'] = 'profile' in declared
                    item['source_association']['paper_id'] = item['paper_id']
                if group['errors']:
                    raise WorkspaceError('; '.join(group['errors']))
                roles = group['roles']
                duplicates = [role for role, matches in roles.items() if len(matches) != 1]
                if duplicates:
                    raise WorkspaceError('Duplicate/ambiguous named source role: ' + ', '.join(duplicates))
                selected = {role: matches[0] for role, matches in roles.items()}
                if 'questions' not in selected and 'export' not in selected:
                    raise WorkspaceError('Companion files have no same-ID QP or existing export; no question paper was guessed.')
                files = {}
                for role, canonical in (('questions', 'questions.pdf'), ('export', 'export.zip')):
                    if role in selected:
                        files[canonical] = selected[role]
                key_role = next((role for role in ('final_key', 'answer_key', 'combined') if role in selected), None)
                if key_role:
                    files['answer_key.pdf'] = selected[key_role]
                solution = selected.get('solutions') or selected.get('combined')
                if solution:
                    files['solutions.pdf'] = solution
                unused = set(group['paths']) - set(files.values())
                if unused:
                    raise WorkspaceError('Additional independent key/solutions sources require explicit selection: ' +
                                         ', '.join(sorted(path.name for path in unused)))
                association = item['source_association']
                association['answer_key_basis'] = {'final_key': 'final_key', 'answer_key': 'answer_key',
                                                   'combined': 'combined_key_solutions'}.get(key_role, 'embedded_or_export')
                if 'combined' in selected:
                    association['combined_key_solutions_filename'] = selected['combined'].name
                if 'final_key' in selected:
                    association['final_key_filename'] = selected['final_key'].name
                association['filenames'] = {canonical: path.name for canonical, path in files.items()}
                item['files'] = {canonical: str(path) for canonical, path in files.items()}
                validate(item)
            except (WorkspaceError, OSError, ValueError, TypeError) as exc:
                item['reason'] = str(exc)

        canonical = any(path.name.lower() in ('questions.pdf', 'export.zip') for path in unknown)
        if not groups and (canonical or config.exists() or config.is_symlink()):
            item = item_for(parent, _all_paper_id(parent.name), unknown)
            try:
                settings = {}
                if config.exists() or config.is_symlink():
                    if config.is_symlink() or not config.is_file() or config.stat().st_size > 16384:
                        raise WorkspaceError('Unsafe or oversized paper.json.')
                    settings = json.loads(config.read_text())
                    if not isinstance(settings, dict) or set(settings) - {'paper_id', 'profile', 'files', 'ignore'}:
                        raise WorkspaceError('Invalid paper.json settings.')
                item['paper_id'] = paper_id(settings.get('paper_id', parent.name.upper().replace(' ', '-').translate(_COMPANION_DASHES)))
                item['profile'] = batch_profile(settings.get('profile', 'unspecified'))
                item['profile_explicit'] = 'profile' in settings
                for path in unknown:
                    if path.is_symlink() or not path.is_file():
                        raise WorkspaceError('Symlink/special source file refused: ' + path.name)
                candidates = {path.name: path for path in unknown if path.suffix.lower() in ('.pdf', '.zip')}
                names = associate_files(source, root, parent, candidates, settings, select_files=False)
                item['files'] = {canonical: str(path) for canonical, path in names.items()}
                item['source_association'] = {'method': 'explicit_grouped_folder',
                    'paper_id': item['paper_id'], 'filenames': {canonical: path.name for canonical, path in names.items()},
                    'answer_key_basis': 'answer_key' if 'answer_key.pdf' in names else 'embedded_or_export',
                    'candidates': sorted(path.name for path in unknown)}
                item['unprocessed_documents'] = [path.name for path in unknown if path.suffix.lower() in ('.doc', '.docx')]
                validate(item)
            except (WorkspaceError, OSError, ValueError, TypeError) as exc:
                item['reason'] = str(exc)
            unknown = []
        for path in unknown:
            reason = ('Word documents need a PDF copy; original remains unchanged.' if path.suffix.lower() in ('.doc', '.docx') else
                      'Unrecognised source association; no question/key/solutions role was guessed for ' + path.name)
            held(path, reason)
    if not entries:
        raise WorkspaceError('No paper sources found in the selected folder.')
    ids = {}
    for item in entries:
        ids.setdefault(item['paper_id'], []).append(item)
    for pid, matches in ids.items():
        if len(matches) > 1:
            for item in matches:
                item['reason'] = 'Duplicate paper ID ' + pid + ' in different source groups; explicit disambiguation required.'
    return entries


PROFILES = ('unspecified', 'jee-main-mock', 'jee-advanced-source')


def strict_pdf(path):
    """Batch preflight: parse every PDF before any cropper starts."""
    path = input_pdf(path)
    try:
        import pdfplumber
        from pdfminer.pdftypes import PDFStream, resolve1
        with pdfplumber.open(path) as doc:
            if not doc.pages:
                raise ValueError('no pages')
            for page in doc.pages:
                if page.width <= 0 or page.height <= 0:
                    raise ValueError('invalid page size')
                # Resolve content streams too, without OCR or rendering.
                for stream in page.page_obj.contents:
                    resolved = resolve1(stream)
                    if not isinstance(resolved, PDFStream):
                        raise ValueError('invalid page content stream')
                    resolved.get_data()
                page.objects
    except ImportError as exc:
        raise WorkspaceError('PDF validation needs pdfplumber. Run the Mac launcher to install it.') from exc
    except Exception as exc:
        # A parser/programming failure is not evidence that a viewable source is
        # corrupt or encrypted. Name the actual failure class for diagnostics.
        from pdfminer.pdfdocument import PDFPasswordIncorrect
        password_required = isinstance(exc, PDFPasswordIncorrect)
        # Recent pdfplumber wraps PDFDocument failures. Inspect only its known
        # wrapper, without treating unrelated parser failures as encryption.
        if not password_required:
            try:
                from pdfplumber.utils.exceptions import PdfminerException
            except ImportError:
                pass  # Older pdfplumber versions raise the original exception.
            else:
                if isinstance(exc, PdfminerException):
                    wrapped = (exc.__cause__, exc.__context__, *exc.args)
                    password_required = any(isinstance(error, PDFPasswordIncorrect)
                                            for error in wrapped)
        if password_required:
            raise WorkspaceError('PDF requires a password before processing: ' + path.name) from exc
        raise WorkspaceError('PDF preflight check failed (' + type(exc).__name__ + '): ' +
                             path.name + '. Original file kept unchanged.') from exc
    return path


def batch_profile(value):
    if value not in PROFILES:
        raise WorkspaceError('Profile must be unspecified, jee-main-mock or jee-advanced-source.')
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
    if has_companion_papers(source):
        entries = discover_companion_papers(source, root)
        for item in entries:
            if not item.get('profile_explicit'):
                item['profile'] = profile
            if item['paper_id'] in overrides:
                item['profile'] = batch_profile(overrides[item['paper_id']])
                item['profile_explicit'] = True
        if set(overrides) - {item['paper_id'] for item in entries}:
            raise WorkspaceError('Profile override references an unknown paper ID.')
        return entries
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
                item['profile_explicit'] = 'profile' in settings
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
                item['profile_explicit'] = True
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


def apply_batch_exam_policy(entries, policy=None):
    """Keep source-backed UI intent separate from unverified grading metadata."""
    if policy not in (None, 'main-standard-advanced-source'):
        raise WorkspaceError('Unsupported batch exam policy.')
    for item in entries:
        if item['reason'] or (policy is None and item['profile'] not in ('jee-main-mock', 'jee-advanced-source')):
            continue
        files = item['files']
        intent = inspect_exam_source(source=files.get('questions.pdf'), export=files.get('export.zip'))
        family = intent['exam_family']
        if policy and not item.get('profile_explicit'):
            item['profile'] = {'jee-main': 'jee-main-mock', 'jee-advanced': 'jee-advanced-source'}.get(family, 'unspecified')
        if family == 'jee-advanced' and item['profile'] == 'jee-main-mock':
            item['reason'] = 'Advanced source conflicts with selected Main marking; original/settings preserved.'
        if family == 'jee-main' and item['profile'] == 'jee-advanced-source':
            item['reason'] = 'Main source conflicts with selected Advanced interface; original/settings preserved.'
        item['exam_intent'] = intent
        item['exam_policy'] = policy or 'source_required'
    return entries


def _batch_entries(args, overrides):
    if has_companion_papers(args.source):
        entries = discover_batch(args.source, args.root, args.profile, overrides,
                                 select_files=getattr(args, 'select_files', False))
    elif getattr(args, 'all_papers', False):
        entries = discover_all_papers(args.source, args.root)
        for item in entries:
            item['profile'] = args.profile
            if item['paper_id'] in overrides:
                item['profile'] = overrides[item['paper_id']]
                item['profile_explicit'] = True
        if set(overrides) - {item['paper_id'] for item in entries}:
            raise WorkspaceError('Profile override references an unknown paper ID.')
    else:
        entries = discover_batch(args.source, args.root, args.profile, overrides,
                                 select_files=getattr(args, 'select_files', False))
    return apply_batch_exam_policy(entries, getattr(args, 'exam_policy', None))


def run_batch(args, entries=None):
    overrides = {}
    for value in getattr(args, 'paper_profile', []) or []:
        pid, sep, profile = value.partition('=')
        if not sep or paper_id(pid) in overrides:
            raise WorkspaceError('Use each --paper-profile ID=PROFILE once.')
        overrides[pid] = batch_profile(profile)
    if entries is None:
        entries = _batch_entries(args, overrides)
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
                keep_color=args.keep_color, no_review=True, exam_intent=item.get('exam_intent'),
                source_association=item.get('source_association'))
            previous = json.loads((folder / 'status.json').read_text()) if (folder / 'status.json').is_file() else {}
            if 'export.zip' in files:
                attach(run_args)
            else:
                process(run_args)
            current = json.loads((folder / 'status.json').read_text())
            reused = previous.get('run_id') == current.get('run_id')
            row['run_id'] = current['run_id']
            if item.get('exam_intent'):
                row['exam_family'] = item['exam_intent']['exam_family']
                row['required_exam_pattern'] = item['exam_intent'].get('exam_pattern')
                row['integration_state'] = 'held_pending_source_type_marking_and_completeness_validation'
                row['exam_launch_ready'] = False
            if args.offline:
                row.update(outcome='already processed' if reused else 'upload failed/local-only',
                           reason='Local results retained; offline mode selected.')
            elif getattr(args, 'queue_uploads', False):
                row.update(outcome='upload failed/local-only', reason='Local results kept; upload queue could not yet be saved.')
                queue_sync(folder)
                row.update(outcome='already processed' if reused else 'upload failed/local-only',
                           reason='Local processing complete; upload saved for background sync.', upload_state='queued')
            else:
                try:
                    receipt = sync_job(folder)
                    row.update(outcome='already processed' if reused and receipt['changed'] == 0 else 'synced',
                               reason=None, commit=receipt['commit'])
                except (WorkspaceError, OSError, ValueError, subprocess.SubprocessError) as exc:
                    row.update(outcome='upload failed/local-only', reason=str(exc) + ' Upload queued; crops are saved and retry without recropping.')
            if row.get('integration_state'):
                row['reason'] = (row['reason'] + ' ' if row['reason'] else '') + 'Exam launch held: validate source sections, completeness and marking in the required Main/Advanced flow.'
        except Exception as exc:
            row['reason'] = str(exc)
            # A refusal is useful to collaborators, but never hide it behind an upload error.
            try:
                state = json.loads((folder / 'status.json').read_text())
                if state.get('state') == 'held' and not args.offline:
                    if getattr(args, 'queue_uploads', False):
                        queue_sync(folder, state.get('reason', 'Paper held'))
                    else:
                        sync_job(folder)
            except Exception as upload_exc:
                row['upload_note'] = str(upload_exc)
        print('BATCH: ' + pid + ' | ' + ('local-only / upload queued' if row.get('upload_state') == 'queued' else row['outcome']) + (' | ' + row['reason'] if row['reason'] else ''))
    if report_dir.is_symlink():
        raise WorkspaceError('Batch results kept, but report directory became a symlink; report write refused.')
    report = report_dir / (dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8] + '.json')
    save_json(report, {'schema': 'modulus_batch_report_v1', 'completed_at': now(),
        'input_folder': str(Path(args.source).expanduser().resolve()), 'publication': 'not_deployed', 'papers': results})
    print('\nBATCH COMPLETE — private workspace handoff, not a deployed examination')
    for row in results:
        print('  ' + row['paper_id'] + ': ' + ('local-only / upload queued' if row.get('upload_state') == 'queued' else row['outcome']) + (' — ' + row['reason'] if row['reason'] else ''))
    print('Batch report: ' + str(report))
    return results


def interactive_batch(path):
    root = Path.home() / 'Modulus Papers'
    all_papers = False
    loose = any(p.suffix.lower() in ('.pdf', '.zip', '.doc', '.docx') for p in path.iterdir() if p.is_file())
    nested = any(p.is_dir() and not p.is_symlink() and not p.name.startswith('.') and
                 any(child.is_dir() and not child.name.startswith('.') for child in p.iterdir()) for p in path.iterdir())
    companions = has_companion_papers(path)
    if companions:
        print('Matched paper IDs in QP / FINAL-KEY / KEY-&-SOLS filenames. No moving or renaming is needed.')
    elif loose or nested:
        print('This folder has loose files or nested folders. File associations will not be guessed.')
        choice = input('a = every PDF/ZIP is a complete paper (scan all subfolders); Enter = use one grouped subfolder per paper > ').strip().lower()
        all_papers = choice == 'a'
    # Discover associations before requesting shared settings; paper.json overrides remain explicit.
    entries = discover_all_papers(path, root) if all_papers else discover_batch(path, root, select_files=True)
    show_batch(entries)
    choice = input('\nMarking: x = Main standard / Advanced from source; y = Main-only mock (+4/-1/0, 180 min); m = manual overrides; Enter = unspecified\n(per-paper paper.json profiles take precedence) > ').strip().lower()
    profile = 'jee-main-mock' if choice == 'y' else 'unspecified'
    overrides = {}
    if choice == 'm':
        for item in entries:
            if not item['reason']:
                answer = input(item['paper_id'] + ': y = Main mock, a = Advanced source rules, n = unspecified, Enter = keep ' + item['profile'] + ' > ').strip().lower()
                if answer in ('y', 'a', 'n'):
                    overrides[item['paper_id']] = {'y': 'jee-main-mock', 'a': 'jee-advanced-source', 'n': 'unspecified'}[answer]
    # No files have been processed yet. Refresh the plan with selected settings.
    args = argparse.Namespace(source=path, root=root, profile=profile, offline=False,
                              keep_color=False, paper_profile=[], queue_uploads=True, all_papers=all_papers,
                              exam_policy='main-standard-advanced-source' if choice == 'x' else None)
    entries = _batch_entries(args, overrides)
    print('Selected marking: ' + ', '.join(item['paper_id'] + '=' + item['profile'] for item in entries if not item['reason']))
    if choice == 'x':
        print('Source text determines Main/Advanced intent. Scanned/unknown types stay unresolved; no filename/answer-based guesses.')
        print('Advanced source instructions are collected for validation, not assigned invented marks. Existing cropper may refuse unsupported keys/layouts.')
    return run_batch(args, entries)


def connect_github():
    """Explicit browser login only; never read/paste a token or configure Git interactively."""
    if shutil.which('gh') is None:
        brew = shutil.which('brew')
        if brew is None:
            print('GitHub connection is unavailable; local paper work and saved uploads remain available.')
            return False
        print('Installing the free GitHub upload helper...')
        if subprocess.run([brew, 'install', 'gh'], check=False).returncode:
            print('Upload helper installation failed; pending uploads remain saved.')
            return False
    print('Approve the connection in your GitHub browser window. Local results stay saved.')
    # Non-interactive stdin suppresses gh's unrelated Git-credential setup question.
    # Explicit --web keeps the owner-approved browser authorization flow.
    env = dict(os.environ, GH_PROMPT_DISABLED='1')
    result = subprocess.run(['gh', 'auth', 'login', '--hostname', 'github.com',
                             '--web', '--git-protocol', 'https'],
                            stdin=subprocess.DEVNULL, env=env, check=False)
    if result.returncode:
        print('Connection was not completed. You can keep processing papers; uploads remain queued.')
        return False
    print('GitHub connected. Pending uploads will resume automatically.')
    return True


def interactive():
    import threading
    pause = threading.Event()
    wake = threading.Event()
    stop, worker = start_queue_worker(Path.home() / 'Modulus Papers', pause_event=pause, wake_event=wake)
    try:
        _interactive_loop(pause, wake)
    finally:
        stop.set()
        wake.set()  # Pending jobs stay on disk; never wait on a network request here.


def _interactive_loop(pause_uploads=None, wake_uploads=None):
    print('\nMODULUS — one paper workspace, shared by Claude and ChatGPT')
    print('Local Mac processing and file uploads do not use GitHub Actions minutes.')
    print('Pending uploads retry while this window is open; closing keeps the saved queue.')
    print('Your PDFs/results go ONLY to the private repository. No Netlify deployment.')
    print('Drag a BATCH FOLDER, a question PDF OR an existing result ZIP.')
    print('Or press Enter to choose the batch folder in a normal folder dialog.')
    print('Type g to connect GitHub when convenient, or q to finish. Login is not needed to process papers.')
    while True:
        if pause_uploads is not None:
            pause_uploads.clear()
        if wake_uploads is not None:
            wake_uploads.set()
        raw = input('\nBatch folder / question PDF / result ZIP (Enter: Choose Folder) > ')
        if pause_uploads is not None:
            pause_uploads.set()
        if raw.strip().lower() == 'q':
            return
        try:
            if raw.strip().lower() == 'g':
                connect_github()
                continue
            path = choose_batch_folder() if not raw.strip() else path_from_prompt(raw)
            if path is None:
                continue
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
                    key=key, solutions=solutions, profile=profile, offline=True, keep_color=False)
            try:
                if path.suffix.lower() == '.zip':
                    folder = attach(args)
                else:
                    folder = process(args)
            except (WorkspaceError, OSError, ValueError, subprocess.SubprocessError):
                held_folder = Path(args.root) / pid
                state = held_folder / 'status.json'
                try:
                    if state.is_file() and json.loads(state.read_text()).get('state') == 'held':
                        queue_sync(held_folder, 'Paper held; source report retained')
                except (WorkspaceError, OSError, ValueError) as queue_exc:
                    print('Source report kept locally; queue unavailable: ' + str(queue_exc))
                raise
            queue_sync(folder)
            print('Local processing complete. Upload queued; you can process the next paper.')
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
        sub.add_argument('--profile', choices=PROFILES, default='unspecified')
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
    sub.add_argument('--all-papers', action='store_true', help='Explicitly treat each PDF/ZIP as a complete paper and scan nested folders.')
    sub.add_argument('--exam-policy', choices=('main-standard-advanced-source',))
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
