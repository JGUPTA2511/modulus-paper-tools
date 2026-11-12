import argparse
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workspace', ROOT / 'workspace_client.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def image():
    b = io.BytesIO()
    Image.new('RGB', (40, 40), 'white').save(b, 'PNG')
    return b.getvalue()


def export(path, rows=None, extra=None):
    rows = rows or [{'number': 1, 'image': 'crops/Q001.png', 'answer': '0'}]
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('exam.json', json.dumps({'count': len(rows), 'questions': rows}))
        for q in rows:
            z.writestr(q['image'], image())
        if extra:
            for k, v in extra.items():
                z.writestr(k, v)
    return path


class FakeAPI(w.GitHub):
    def __init__(self, private=True, conflict=None):
        self.private = private
        self.files = {}
        self.blobs = {}
        self.head = 'head0'
        self.commit_trees = {}
        self.trees = {'tree0': {}}
        self.calls = []
        self.conflict = conflict
    def check_private(self):
        self.calls.append('privacy')
        if not self.private:
            raise w.WorkspaceError('Public repo blocked')
    def snapshot(self):
        return self.head, 'tree0', dict(self.files)
    def call(self, endpoint, method='GET', body=None):
        self.calls.append((endpoint, method))
        if endpoint == 'git/blobs':
            data = w.base64.b64decode(body['content'])
            h = w.gitsha(data)
            self.blobs[h] = data
            return {'sha': h}
        if endpoint == 'git/trees':
            t = dict(self.files)
            t.update({x['path']: x['sha'] for x in body['tree']})
            name = 'tree' + str(len(self.trees))
            self.trees[name] = t
            return {'sha': name}
        if endpoint == 'git/commits':
            assert '[skip ci]' in body['message'] and '[skip netlify]' in body['message']
            name = 'commit' + str(len(self.commit_trees))
            self.commit_trees[name] = body['tree']
            return {'sha': name}
        if endpoint.startswith('git/refs/'):
            assert body['force'] is False
            if self.conflict:
                target = self.conflict
                self.conflict = None
                self.files[target] = 'someone-elses-version'
                self.head = 'concurrent'
                raise w.WorkspaceError('Concurrent update')
            self.files = self.trees[self.commit_trees[body['sha']]]
            self.head = body['sha']
            return {}
        raise AssertionError(endpoint)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def test_zero_answer_preserved(self):
        data, checks = w.validate_export(export(self.p / 'a.zip'))
        self.assertEqual(data['questions'][0]['answer'], '0')
        self.assertEqual(checks['questions'], 1)
    def test_duplicate_number_rejected(self):
        rows = [{'number': 1, 'image': 'crops/Q001.png', 'answer': '2'},
                {'number': 1, 'image': 'crops/Q002.png', 'answer': '3'}]
        with self.assertRaises(w.WorkspaceError):
            w.validate_export(export(self.p / 'a.zip', rows))
    def test_missing_answer_rejected(self):
        with self.assertRaises(w.WorkspaceError):
            w.validate_export(export(self.p / 'a.zip', [{'number': 1, 'image': 'crops/Q001.png', 'answer': None}]))
    def test_missing_image_rejected(self):
        with zipfile.ZipFile(self.p / 'a.zip', 'w') as z:
            z.writestr('exam.json', json.dumps({'count': 1, 'questions': [{'number': 1, 'image': 'crops/Q001.png', 'answer': '1'}]}))
        with self.assertRaises(w.WorkspaceError):
            w.validate_export(self.p / 'a.zip')
    def test_extra_crop_rejected(self):
        with self.assertRaises(w.WorkspaceError):
            w.validate_export(export(self.p / 'a.zip', extra={'crops/Q002.png': image()}))
    def test_traversal_rejected(self):
        with self.assertRaises(w.WorkspaceError):
            w.validate_export(export(self.p / 'a.zip', extra={'../escape.txt': b'x'}))
    def test_refused_result_rejected(self):
        with self.assertRaises(w.WorkspaceError):
            w.validate_export(export(self.p / 'a.zip', extra={'REFUSED.txt': b'No key'}))
    def test_corrupt_png_rejected(self):
        with zipfile.ZipFile(self.p / 'a.zip', 'w') as z:
            z.writestr('exam.json', json.dumps({'count': 1, 'questions': [{'number': 1, 'image': 'crops/Q001.png', 'answer': '1'}]}))
            z.writestr('crops/Q001.png', b'\x89PNG\r\n\x1a\nbad')
        with self.assertRaises(w.WorkspaceError):
            w.validate_export(self.p / 'a.zip')
    def test_bool_count_rejected(self):
        with zipfile.ZipFile(self.p / 'a.zip', 'w') as z:
            z.writestr('exam.json', json.dumps({'count': True, 'questions': []}))
        with self.assertRaises(w.WorkspaceError):
            w.validate_export(self.p / 'a.zip')
    def test_nan_answer_rejected(self):
        with self.assertRaises(w.WorkspaceError):
            w.validate_export(export(self.p / 'a.zip', [{'number': 1, 'image': 'crops/Q001.png', 'answer': float('nan')}]))
    def test_three_separate_pdfs_preserved(self):
        args = []
        for n in ('qp', 'key', 'solutions'):
            p = self.p / (n + '.pdf')
            p.write_bytes(b'%PDF-1.7\n' + n.encode())
            args.append(p)
        folder, job = w.prepare_job(self.p / 'local', 'TEST-01', *args, profile='jee-main-mock')
        self.assertEqual(set(job['inputs']), {'questions', 'answer_key', 'solutions'})
        self.assertEqual((folder / 'input/answer_key.pdf').read_bytes(), args[1].read_bytes())
        self.assertEqual(job['requested_scoring']['correct'], 4)
        self.assertTrue(all(p.is_file() for p in args))
    def test_inputs_immutable(self):
        p = self.p / 'a.pdf'; p.write_bytes(b'%PDF-1.7 first')
        w.prepare_job(self.p / 'local', 'A', p)
        p.write_bytes(b'%PDF-1.7 second')
        with self.assertRaises(w.WorkspaceError):
            w.prepare_job(self.p / 'local', 'A', p)
        self.assertEqual((self.p / 'local/A/input/questions.pdf').read_bytes(), b'%PDF-1.7 first')
    def test_no_inferred_profile(self):
        _, job = w.prepare_job(self.p / 'local', 'A')
        self.assertNotIn('requested_scoring', job)
    def test_public_upload_blocked_before_blob(self):
        api = FakeAPI(private=False)
        with self.assertRaises(w.WorkspaceError):
            api.upload({'paper_jobs/A/input/questions.pdf': b'private'}, 'Sync')
        self.assertEqual(api.calls, ['privacy'])
    def test_writes_outside_workspace_blocked(self):
        api = FakeAPI()
        with self.assertRaises(w.WorkspaceError):
            api.upload({'index.html': b'oops'}, 'Sync')
        self.assertFalse(api.blobs)
    def test_sync_is_idempotent(self):
        api = FakeAPI()
        files = {'paper_jobs/A/job.json': b'{}'}
        first = api.upload(files, 'Sync')
        second = api.upload(files, 'Sync')
        self.assertEqual(first['changed'], 1)
        self.assertEqual(second['changed'], 0)
        self.assertEqual(len(api.commit_trees), 1)
    def test_immutable_remote_collision(self):
        api = FakeAPI()
        api.files['paper_jobs/A/job.json'] = 'different'
        with self.assertRaises(w.WorkspaceError):
            api.upload({'paper_jobs/A/job.json': b'{}'}, 'Sync')
        self.assertFalse(api.blobs)
    def test_unrelated_concurrent_work_kept(self):
        api = FakeAPI(conflict='paper_jobs/B/status.json')
        api.upload({'paper_jobs/A/job.json': b'{}'}, 'Sync')
        self.assertEqual(api.files['paper_jobs/B/status.json'], 'someone-elses-version')
    def test_same_path_concurrent_edit_not_overwritten(self):
        api = FakeAPI(conflict='paper_jobs/A/status.json')
        with self.assertRaises(w.WorkspaceError):
            api.upload({'paper_jobs/A/status.json': b'new'}, 'Sync')
        self.assertEqual(api.files['paper_jobs/A/status.json'], 'someone-elses-version')
    def test_held_output_not_uploaded(self):
        folder, job = w.prepare_job(self.p / 'local', 'A')
        run = w.new_run(folder)
        (run / 'export.zip').write_bytes(b'bad data')
        (run / 'REFUSED.txt').write_text('Bad key')
        w.record_status(folder, job, 'held', run.name)
        api = FakeAPI()
        w.sync_job(folder, api)
        self.assertFalse(any(k.endswith('export.zip') for k in api.files))
        self.assertTrue(any(k.endswith('REFUSED.txt') for k in api.files))
    def test_existing_export_not_recropped_or_duplicated(self):
        p = export(self.p / 'a.zip')
        args = argparse.Namespace(source=p, root=self.p / 'local', paper_id='A', profile='unspecified', offline=True)
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No cropping allowed')):
            folder = w.attach(args)
            w.attach(args)
        self.assertEqual(len(list((folder / 'runs').iterdir())), 1)
        saved = next((folder / 'runs').glob('*/export.zip'))
        self.assertEqual(saved.read_bytes(), p.read_bytes())
    def test_dragged_path_spaces(self):
        p = self.p / "a b.pdf"; p.write_bytes(b'%PDF-')
        self.assertEqual(w.path_from_prompt(str(p).replace(' ', '\\ ')), p)
        self.assertEqual(w.path_from_prompt('"' + str(p) + '"'), p)
    def test_multiple_dragged_files_rejected(self):
        with self.assertRaises(w.WorkspaceError):
            w.path_from_prompt('/a.pdf /b.pdf')
    def test_ids_do_not_allow_escape(self):
        with self.assertRaises(w.WorkspaceError):
            w.paper_id('../SECRET')

if __name__ == '__main__':
    unittest.main()
