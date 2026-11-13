"""Synthetic retries preserve original manifests, source bytes and old attempts."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_workspace import FakeAPI, export, w


class HeldRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'Original question paper.pdf'
        self.source.write_bytes(b'%PDF-1.7\nSynthetic unchanged source')
        self.root = self.base / 'Generated jobs'
        self.fixture = export(self.base / 'Synthetic export.zip')
        self.args = argparse.Namespace(source=self.source, root=self.root, paper_id='A',
            key=None, solutions=None, profile='jee-main-mock', offline=True,
            keep_color=False, no_review=True)
        self.calls = []

    def held(self, key=None):
        self.args.key = key
        folder, job = w.prepare_job(self.root, 'A', self.source, key=key)
        old = w.new_run(folder)
        (old / 'REFUSED.txt').write_text('Synthetic old detector refusal')
        w.record_status(folder, job, 'held', old.name, reason='Synthetic old detector refusal')
        return folder, old, (folder / 'job.json').read_bytes()

    def crop(self, command, **kwargs):
        self.calls.append(command)
        out = Path(command[command.index('--out') + 1])
        out.mkdir(parents=True)
        with zipfile.ZipFile(self.fixture) as z:
            z.extractall(out)
        return SimpleNamespace(returncode=0)

    def process(self):
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            return w.process(self.args)

    def test_held_unspecified_job_retries_same_id_with_explicit_main_settings(self):
        folder, old, original = self.held()
        self.process()
        status = json.loads((folder / 'status.json').read_text())
        run = folder / 'runs' / status['run_id']
        processing = json.loads((run / 'processing.json').read_text())
        self.assertEqual((folder / 'job.json').read_bytes(), original)
        self.assertEqual((folder / 'input/questions.pdf').read_bytes(), self.source.read_bytes())
        self.assertEqual(json.loads(original)['profile'], 'unspecified')
        self.assertEqual(processing['job']['profile'], 'jee-main-mock')
        self.assertEqual(processing['job']['requested_scoring']['correct'], 4)
        self.assertEqual(processing['input_manifest_sha256'], w.sha(original))
        self.assertEqual(status['profile'], 'jee-main-mock')
        self.assertEqual(status['state'], 'ready_for_integration')
        self.assertEqual(status['publication'], 'not_deployed')
        self.assertEqual((old / 'REFUSED.txt').read_text(), 'Synthetic old detector refusal')
        self.assertEqual(len(list((folder / 'runs').iterdir())), 2)

    def test_repeat_selected_settings_reuses_success_without_recropping(self):
        folder, old, original = self.held()
        self.process()
        snapshot = {p.relative_to(folder).as_posix(): p.read_bytes()
                    for p in folder.rglob('*') if p.is_file()}
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No recropping')):
            w.process(self.args)
        self.assertEqual(snapshot, {p.relative_to(folder).as_posix(): p.read_bytes()
                                   for p in folder.rglob('*') if p.is_file()})
        self.assertEqual(len(self.calls), 1)

    def test_byte_identical_filename_alias_is_not_a_new_original(self):
        folder, _, original = self.held()
        alias = self.base / 'Same bytes with another Finder name.pdf'
        alias.write_bytes(self.source.read_bytes())
        self.args.source = alias
        self.process()
        self.assertEqual((folder / 'job.json').read_bytes(), original)
        self.assertEqual((folder / 'input/questions.pdf').read_bytes(), alias.read_bytes())

    def test_changed_question_bytes_are_held_with_the_role_and_no_new_run(self):
        folder, old, original = self.held()
        self.source.write_bytes(b'%PDF-1.7\nChanged question source')
        with self.assertRaisesRegex(w.WorkspaceError, 'questions original bytes or roles changed'):
            self.process()
        self.assertEqual((folder / 'job.json').read_bytes(), original)
        self.assertEqual(list((folder / 'runs').iterdir()), [old])
        self.assertFalse(self.calls)

    def test_changed_key_and_added_roles_are_not_silently_accepted(self):
        key = self.base / 'Source key.pdf'
        key.write_bytes(b'%PDF-1.7\nOriginal key')
        folder, old, original = self.held(key)
        key.write_bytes(b'%PDF-1.7\nChanged key')
        with self.assertRaisesRegex(w.WorkspaceError, 'answer_key original bytes or roles changed'):
            self.process()
        self.args.key = None
        with self.assertRaisesRegex(w.WorkspaceError, 'answer_key original bytes or roles changed'):
            self.process()
        self.assertEqual((folder / 'job.json').read_bytes(), original)
        self.assertEqual(list((folder / 'runs').iterdir()), [old])

    def test_tampered_saved_original_is_rejected_before_retry(self):
        folder, old, original = self.held()
        (folder / 'input/questions.pdf').write_bytes(b'%PDF-1.7\nTampered local original')
        with self.assertRaisesRegex(w.WorkspaceError, 'Saved original changed: questions'):
            self.process()
        self.assertEqual((folder / 'job.json').read_bytes(), original)
        self.assertEqual(list((folder / 'runs').iterdir()), [old])

    def test_symlink_saved_input_is_rejected_without_touching_external_file(self):
        folder, old, original = self.held()
        path = folder / 'input/questions.pdf'
        path.unlink()
        path.symlink_to(self.source)
        with self.assertRaisesRegex(w.WorkspaceError, 'missing or unsafe'):
            self.process()
        self.assertEqual((folder / 'job.json').read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), b'%PDF-1.7\nSynthetic unchanged source')

    def test_retry_still_obeys_the_shared_job_lock(self):
        folder, old, original = self.held()
        with w.job_lock(self.root, 'A'):
            with self.assertRaisesRegex(w.WorkspaceError, 'already running'):
                self.process()
        self.assertEqual((folder / 'job.json').read_bytes(), original)
        self.assertEqual(list((folder / 'runs').iterdir()), [old])

    def test_sync_adds_effective_settings_without_overwriting_remote_manifest(self):
        folder, old, original = self.held()
        api = FakeAPI()
        w.sync_job(folder, api)
        self.process()
        status = json.loads((folder / 'status.json').read_text())
        w.sync_job(folder, api)
        prefix = 'paper_jobs/A/'
        self.assertEqual(api.files[prefix + 'job.json'], w.gitsha(original))
        self.assertIn(prefix + 'runs/' + old.name + '/REFUSED.txt', api.files)
        self.assertIn(prefix + 'runs/' + status['run_id'] + '/processing.json', api.files)
        commits = len(api.commit_trees)
        w.sync_job(folder, api)
        self.assertEqual(len(api.commit_trees), commits)

    def test_modified_processing_snapshot_cannot_be_synced_or_reused(self):
        folder, _, _ = self.held()
        self.process()
        status = json.loads((folder / 'status.json').read_text())
        path = folder / 'runs' / status['run_id'] / 'processing.json'
        value = json.loads(path.read_text())
        value['job']['requested_scoring']['correct'] = 999
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(w.WorkspaceError, 'Retry settings changed'):
            w.sync_job(folder, FakeAPI())
        with self.assertRaisesRegex(w.WorkspaceError, 'Retry settings changed'):
            w.cached_run(folder)

    def test_completed_settings_changes_do_not_recrop_or_rewrite(self):
        folder, _, original = self.held()
        self.process()
        status = (folder / 'status.json').read_bytes()
        self.args.keep_color = True
        with self.assertRaisesRegex(w.WorkspaceError, 'completed run settings differ'):
            self.process()
        self.assertEqual((folder / 'job.json').read_bytes(), original)
        self.assertEqual((folder / 'status.json').read_bytes(), status)
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
