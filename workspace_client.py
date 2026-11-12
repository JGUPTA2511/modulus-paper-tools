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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_bytes(encoded(obj))
    tmp.replace(path)


def paper_id(value):
    if not ID_RE.fullmatch(value):
        raise WorkspaceError('Paper ID: use 1-80 uppercase letters, digits, - or _.')
    return value


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
    if Path(text).expanduser().is_file():
        return Path(text).expanduser().resolve()
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        raise WorkspaceError('Could not read the dragged path.') from exc
    if len(parts) != 1:
        raise WorkspaceError('Drag ONE file into each prompt, not several.')
    return Path(parts[0]).expanduser().resolve()


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


def sync_job(folder, api=None):
    folder = Path(folder).expanduser().resolve()
    job = json.loads((folder / 'job.json').read_text())
    pid = paper_id(job['paper_id'])
    status_path = folder / 'status.json'
    status = json.loads(status_path.read_text())
    allowed = [folder / 'job.json', status_path]
    allowed += list((folder / 'input').glob('*.pdf'))
    if status.get('run_id'):
        run = folder / 'runs' / status['run_id']
        names = ('export.zip', 'exam.json', 'checks.json', 'run.json') if status['state'] == 'ready_for_integration' else ('REFUSED.txt',)
        allowed += [run / n for n in names if (run / n).is_file()]
    files = {}
    for p in allowed:
        if p.is_symlink() or folder not in p.resolve().parents:
            raise WorkspaceError('Symlink/outside path refused.')
        files[f'paper_jobs/{pid}/' + p.relative_to(folder).as_posix()] = p.read_bytes()
    result = (api or GitHub()).upload(files, 'Sync paper ' + pid + ': ' + status['state'])
    # Receipt is local only: no ever-changing timestamps to cause repeated commits.
    save_json(folder / 'sync_receipt.json', dict(result, synced_at=now(), branch=BRANCH))
    print('SYNCED: ' + pid + ' | commit ' + result['commit'][:10])
    print('Both assistants can read: ' + REPO + ' / ' + BRANCH + ' / paper_jobs/' + pid)
    return result


def prepare_job(root, pid, source=None, key=None, solutions=None, profile='unspecified'):
    pid = paper_id(pid)
    root = Path(root).expanduser().resolve()
    folder = root / pid
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
    if profile == 'jee-main-mock':
        job['requested_scoring'] = {'correct': 4, 'incorrect': -1, 'unanswered': 0,
                                    'duration_minutes': 180, 'basis': 'owner_selected_mock_rules'}
    path = folder / 'job.json'
    if path.exists() and json.loads(path.read_text()) != job:
        raise WorkspaceError('This paper ID already has different files or settings. Choose a new ID; previous work is preserved.')
    for role, value in roles.items():
        if value:
            dest = folder / inputs[role]['file']
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
    run = folder / 'runs' / state.get('run_id', 'none') / 'run.json'
    if state.get('state') != 'ready_for_integration' or not run.is_file():
        return False
    previous = json.loads(run.read_text())
    if export_sha:
        return previous.get('export_sha256') == export_sha
    return previous.get('input_manifest_sha256') == sha((folder / 'job.json').read_bytes()) and previous.get('cropper_sha256') == sha((HERE / 'question_boundaries.py').read_bytes())


def new_run(folder):
    rid = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
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
    # Validate all provided files before making a job or running the cropper.
    source = input_pdf(args.source)
    for p in (args.key, args.solutions):
        if p:
            input_pdf(p)
    folder, job = prepare_job(args.root, args.paper_id, source, args.key, args.solutions, args.profile)
    # One process per local paper. Process crash releases this lock automatically.
    import fcntl
    with (folder / '.run.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkspaceError('This paper is already running on this Mac.') from exc
        if cached_run(folder):
            print('Already processed with this cropper; reusing existing crops.')
            if not args.offline:
                sync_job(folder)
            return folder
        run = new_run(folder)
        record_status(folder, job, 'processing', run.name)
        if not args.offline:
            sync_job(folder)
        output = run / 'local_crop_output'
        command = [sys.executable, str(HERE / 'question_boundaries.py'),
                   str(folder / 'input/questions.pdf'), '--out', str(output)]
        if args.key:
            command += ['--key', str(folder / 'input/answer_key.pdf')]
        if args.keep_color:
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
            if not args.offline:
                sync_job(folder)
            raise WorkspaceError('Paper not accepted. Originals and report kept in ' + str(folder)) from exc
        if not args.offline:
            sync_job(folder)
        print('Local results: ' + str(output))
        if sys.platform == 'darwin' and (output / 'review.html').is_file():
            subprocess.run(['open', str(output / 'review.html')], check=False)
        return folder


def attach(args):
    # Existing accepted export: upload unchanged, do NOT recrop.
    validate_export(args.source)
    folder, job = prepare_job(args.root, args.paper_id, profile=args.profile)
    if cached_run(folder, sha(Path(args.source).read_bytes())):
        if not args.offline:
            sync_job(folder)
        print('This export is already stored; no duplicate run created.')
        return folder
    run = new_run(folder)
    shutil.copy2(args.source, run / 'export.zip')
    finish_run(folder, job, run, run / 'export.zip')
    if not args.offline:
        sync_job(folder)
    print('Existing crops preserved. Local copy: ' + str(folder))
    return folder


def interactive():
    print('\nMODULUS — one paper workspace, shared by Claude and ChatGPT')
    print('Your PDFs/results go ONLY to the private repository. No Netlify deployment.')
    print('Drag a question PDF OR an existing result ZIP. Type q to finish.')
    while True:
        raw = input('\nQuestion PDF / result ZIP > ').strip()
        if raw.lower() == 'q':
            return
        try:
            path = path_from_prompt(raw)
            if path is None or not path.is_file():
                raise WorkspaceError('File not found.')
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
