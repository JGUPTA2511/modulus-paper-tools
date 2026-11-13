"""Synthetic local-job regression tests; GitHub calls are simulated."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_workspace import w, export, FakeAPI


class JobSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)
        self.source = self.p / 'question source.pdf'
        self.source.write_bytes(b'%PDF-1.7\nsynthetic header fixture')
        self.args = argparse.Namespace(source=self.source, root=self.p / 'local jobs',
            paper_id='A', profile='unspecified', key=None, solutions=None,
            offline=True, keep_color=False, no_review=True)
        self.crops = export(self.p / 'fixture.zip')

    def tearDown(self):
        self.tmp.cleanup()

    def crop(self, command, **kwargs):
        self.assertTrue(command[1].endswith('question_boundaries.py'))
        out = Path(command[command.index('--out') + 1])
        out.mkdir(parents=True)
        with zipfile.ZipFile(self.crops) as z:
            z.extractall(out)
        (out / 'review.html').write_text('synthetic review')
        return SimpleNamespace(returncode=0)

    def test_upload_failure_leaves_ready_output_and_retry_never_recrops(self):
        class OfflineAPI(FakeAPI):
            def upload(self, files, message):
                raise w.WorkspaceError('Network unavailable')
        self.args.offline = False
        with patch.object(w.subprocess, 'run', side_effect=self.crop) as crop, \
                patch.object(w, 'GitHub', return_value=OfflineAPI()):
            with self.assertRaisesRegex(w.WorkspaceError, 'Network unavailable'):
                w.process(self.args)
            self.assertEqual(crop.call_count, 1)
        folder = self.args.root / 'A'
        original_status = (folder / 'status.json').read_bytes()
        self.assertEqual(json.loads(original_status)['state'], 'ready_for_integration')
        api = FakeAPI()
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No recropping')), \
                patch.object(w, 'GitHub', return_value=api):
            w.process(self.args)
            w.process(self.args)
        self.assertEqual((folder / 'status.json').read_bytes(), original_status)
        self.assertEqual(len(list((folder / 'runs').iterdir())), 1)
        self.assertEqual(len(api.commit_trees), 1)

    def test_all_entry_points_share_lock_before_preparing(self):
        with w.job_lock(self.args.root, 'A'):
            for action in (w.process, w.attach):
                with self.assertRaisesRegex(w.WorkspaceError, 'already running'):
                    action(self.args)
            with self.assertRaisesRegex(w.WorkspaceError, 'already running'):
                w.sync_job(self.args.root / 'A', FakeAPI())
        self.assertFalse((self.args.root / 'A').exists())

    def test_changed_export_needs_new_paper_id(self):
        self.args.source = self.crops
        folder = w.attach(self.args)
        original = (folder / 'job.json').read_bytes()
        export(self.crops, [{'number': 1, 'image': 'crops/Q001.png', 'answer': '4'}])
        with self.assertRaisesRegex(w.WorkspaceError, 'different files or settings'):
            w.attach(self.args)
        self.assertEqual((folder / 'job.json').read_bytes(), original)
        self.assertEqual(len(list((folder / 'runs').iterdir())), 1)

    def test_legacy_attached_export_reuses_manifest_unchanged(self):
        self.args.source = self.crops
        folder = w.attach(self.args)
        job = json.loads((folder / 'job.json').read_text())
        job.pop('source_export')
        w.save_json(folder / 'job.json', job)
        original = (folder / 'job.json').read_bytes()
        w.attach(self.args)
        self.assertEqual((folder / 'job.json').read_bytes(), original)
        self.assertEqual(len(list((folder / 'runs').iterdir())), 1)

    def test_attach_keeps_separate_originals_and_solutions_unmatched(self):
        self.args.source = self.crops
        self.args.source_pdf = self.source
        self.args.key = self.p / 'answer key.pdf'
        self.args.key.write_bytes(b'%PDF-1.7\nkey')
        self.args.solutions = self.p / 'worked solutions.pdf'
        self.args.solutions.write_bytes(b'%PDF-1.7\nsolutions')
        folder = w.attach(self.args)
        job = json.loads((folder / 'job.json').read_text())
        self.assertEqual(set(job['inputs']), {'questions', 'answer_key', 'solutions'})
        self.assertEqual((folder / 'input/solutions.pdf').read_bytes(), self.args.solutions.read_bytes())
        self.assertEqual(json.loads((folder / 'status.json').read_text())['solutions'], 'supplied_not_matched')

    def test_changed_cached_export_is_held_without_overwriting(self):
        self.args.source = self.crops
        folder = w.attach(self.args)
        saved = next((folder / 'runs').glob('*/export.zip'))
        saved.write_bytes(b'corrupt')
        with self.assertRaisesRegex(w.WorkspaceError, 'Completed export changed'):
            w.attach(self.args)
        with self.assertRaisesRegex(w.WorkspaceError, 'Completed export changed'):
            w.sync_job(folder, FakeAPI())
        self.assertEqual(saved.read_bytes(), b'corrupt')
        self.assertEqual(len(list((folder / 'runs').iterdir())), 1)

    def test_color_choice_changes_manifest_instead_of_reusing_wrong_output(self):
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            folder = w.process(self.args)
        self.assertNotIn('crop_settings', json.loads((folder / 'job.json').read_text()))
        self.args.keep_color = True
        with self.assertRaisesRegex(w.WorkspaceError, 'different files or settings'):
            w.process(self.args)

    def test_batch_no_review_does_not_open_browser_on_mac(self):
        with patch.object(w.subprocess, 'run', side_effect=self.crop) as run, \
                patch.object(w.sys, 'platform', 'darwin'):
            w.process(self.args)
        self.assertEqual(run.call_count, 1)

    def test_held_reason_survives_upload_failure(self):
        class OfflineAPI(FakeAPI):
            def upload(self, files, message):
                raise w.WorkspaceError('Upload unavailable')
        self.args.offline = False
        with patch.object(w.subprocess, 'run', return_value=SimpleNamespace(returncode=7)), \
                patch.object(w, 'GitHub', return_value=OfflineAPI()):
            with self.assertRaisesRegex(w.WorkspaceError, 'Cropper exited with code 7.*Upload also failed'):
                w.process(self.args)
        status = json.loads((self.args.root / 'A/status.json').read_text())
        self.assertEqual(status['state'], 'held')
        self.assertIn('Cropper exited with code 7', status['reason'])

    def test_integer_zero_and_question_type_remain_unchanged(self):
        export(self.crops, [{'number': 1, 'image': 'crops/Q001.png', 'answer': 0,
                            'type': 'numerical', 'passage': 'Synthetic passage'}])
        self.args.source = self.crops
        folder = w.attach(self.args)
        saved = next((folder / 'runs').glob('*/export.zip'))
        self.assertEqual(saved.read_bytes(), self.crops.read_bytes())
        row = json.loads(next((folder / 'runs').glob('*/exam.json')).read_text())['questions'][0]
        self.assertEqual(row['answer'], 0)
        self.assertIs(type(row['answer']), int)
        self.assertEqual(row['type'], 'numerical')
        self.assertEqual(row['passage'], 'Synthetic passage')

    def test_symlink_job_folder_is_refused(self):
        self.args.root.mkdir()
        outside = self.p / 'elsewhere'
        outside.mkdir()
        (self.args.root / 'A').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(w.WorkspaceError, 'Symlink'):
            w.process(self.args)
        self.assertEqual(list(outside.iterdir()), [])

    def test_legacy_running_client_blocks_every_entry_point(self):
        import fcntl
        folder = self.args.root / 'A'
        folder.mkdir(parents=True)
        with (folder / '.run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for action in (w.process, w.attach):
                with self.assertRaisesRegex(w.WorkspaceError, 'already running'):
                    action(self.args)
            with self.assertRaisesRegex(w.WorkspaceError, 'already running'):
                w.sync_job(folder, FakeAPI())
        self.assertFalse((folder / 'job.json').exists())

    def test_corrupted_original_is_blocked_before_first_upload(self):
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            folder = w.process(self.args)
        original = folder / 'input/questions.pdf'
        body = original.read_bytes()
        original.write_bytes(body[:-1] + b'X')
        api = FakeAPI()
        with self.assertRaisesRegex(w.WorkspaceError, 'Original input changed'):
            w.sync_job(folder, api)
        self.assertEqual(api.calls, [])
        self.assertEqual(api.blobs, {})

    def test_extra_unmanifested_pdf_is_never_uploaded(self):
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            folder = w.process(self.args)
        (folder / 'input/unrelated.pdf').write_bytes(b'%PDF-1.7\nprivate unrelated file')
        api = FakeAPI()
        w.sync_job(folder, api)
        self.assertIn('paper_jobs/A/input/questions.pdf', api.files)
        self.assertNotIn('paper_jobs/A/input/unrelated.pdf', api.files)

    def test_symlink_runs_directory_cannot_receive_crop_output(self):
        folder = self.args.root / 'A'
        folder.mkdir(parents=True)
        outside = self.p / 'outside runs'
        outside.mkdir()
        (folder / 'runs').symlink_to(outside, target_is_directory=True)
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No crop execution')):
            with self.assertRaisesRegex(w.WorkspaceError, 'Symlink runs'):
                w.process(self.args)
        self.assertEqual(list(outside.iterdir()), [])

    def test_stale_temp_symlink_does_not_modify_target(self):
        target = self.p / 'valuable.txt'
        target.write_text('original')
        path = self.p / 'status.json'
        path.with_name('status.json.tmp').symlink_to(target)
        w.save_json(path, {'state': 'held'})
        self.assertEqual(json.loads(path.read_text()), {'state': 'held'})
        self.assertEqual(target.read_text(), 'original')

    def test_status_id_must_match_manifest_and_folder(self):
        self.args.source = self.crops
        folder = w.attach(self.args)
        status = json.loads((folder / 'status.json').read_text())
        status['paper_id'] = 'B'
        w.save_json(folder / 'status.json', status)
        api = FakeAPI()
        with self.assertRaisesRegex(w.WorkspaceError, 'paper ID does not match'):
            w.sync_job(folder, api)
        self.assertEqual(api.calls, [])

    def test_held_run_id_cannot_escape_job(self):
        folder, job = w.prepare_job(self.args.root, 'A')
        w.record_status(folder, job, 'held', '../../outside')
        api = FakeAPI()
        with self.assertRaisesRegex(w.WorkspaceError, 'Invalid saved run ID'):
            w.sync_job(folder, api)
        self.assertEqual(api.calls, [])

    def test_completed_run_must_match_manifest_before_first_upload(self):
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            folder = w.process(self.args)
        path = folder / 'job.json'
        job = json.loads(path.read_text())
        job['profile'] = 'jee-main-mock'
        w.save_json(path, job)
        api = FakeAPI()
        with self.assertRaisesRegex(w.WorkspaceError, 'does not match its immutable input manifest'):
            w.sync_job(folder, api)
        self.assertEqual(api.calls, [])


if __name__ == '__main__':
    unittest.main()
