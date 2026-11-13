"""Regression coverage for the owner's blocked startup and local processing path."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_batch import pdf
from test_workspace import FakeAPI, export, image, w


class LocalFirstTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'Original Batch'
        self.source.mkdir()
        self.root = self.base / 'Modulus Papers'
        self.console = io.StringIO()
        output = redirect_stdout(self.console)
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        self.crop_calls = []

    def crop(self, command, **kwargs):
        self.crop_calls.append(command)
        self.assertTrue(command[1].endswith('question_boundaries.py'))
        output = Path(command[command.index('--out') + 1])
        (output / 'crops').mkdir(parents=True)
        (output / 'crops/Q001.png').write_bytes(image())
        (output / 'exam.json').write_text(json.dumps({'count': 1, 'questions': [
            {'number': 1, 'image': 'crops/Q001.png', 'answer': 0, 'type': 'numerical'}]}))
        return subprocess.CompletedProcess(command, 0)

    def args(self):
        return argparse.Namespace(source=self.source, root=self.root, profile='unspecified',
                                  offline=False, keep_color=False, paper_profile=[], queue_uploads=True)

    def papers(self):
        for pid in ('A', 'B'):
            folder = self.source / pid
            folder.mkdir()
            pdf(folder / 'Original paper.pdf')

    def test_two_papers_finish_and_queue_without_any_foreground_github_call(self):
        self.papers()
        with patch.object(w.subprocess, 'run', side_effect=self.crop), \
             patch.object(w, 'GitHub', side_effect=AssertionError('No network during local batch')):
            rows = w.run_batch(self.args())
        self.assertEqual(len(self.crop_calls), 2)
        self.assertEqual([r['upload_state'] for r in rows], ['queued', 'queued'])
        self.assertEqual(sorted(p.stem for p in (self.root / '.upload_queue').glob('*.json')), ['A', 'B'])
        for pid in ('A', 'B'):
            self.assertEqual(json.loads((self.root / pid / 'status.json').read_text())['state'], 'ready_for_integration')
        api = FakeAPI()
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No recropping in upload worker')):
            self.assertEqual([r['outcome'] for r in w.drain_queue(self.root, api)], ['synced', 'synced'])
        self.assertEqual(len(api.commit_trees), 2)
        self.assertFalse(list((self.root / '.upload_queue').glob('*.json')))

    def test_repeated_local_batch_and_uploads_do_not_create_duplicate_crops_or_commits(self):
        self.papers()
        api = FakeAPI()
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            w.run_batch(self.args())
        w.drain_queue(self.root, api)
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No repeated crop')):
            rows = w.run_batch(self.args())
            w.drain_queue(self.root, api)
        self.assertEqual([row['outcome'] for row in rows], ['already processed', 'already processed'])
        self.assertEqual(len(api.commit_trees), 2)
        for pid in ('A', 'B'):
            self.assertEqual(len(list((self.root / pid / 'runs').iterdir())), 1)

    def test_unsafe_queue_is_reported_local_only_without_claiming_it_was_saved(self):
        self.papers()
        self.root.mkdir()
        external = self.base / 'outside queue'
        external.mkdir()
        (self.root / '.upload_queue').symlink_to(external, target_is_directory=True)
        with patch.object(w.subprocess, 'run', side_effect=self.crop), \
             patch.object(w, 'GitHub', side_effect=AssertionError('No upload on unsafe queue')):
            rows = w.run_batch(self.args())
        self.assertEqual(len(self.crop_calls), 2)
        self.assertTrue(all(row['outcome'] == 'upload failed/local-only' for row in rows))
        self.assertTrue(all('upload_state' not in row for row in rows))
        self.assertEqual(list(external.iterdir()), [])

    def test_interactive_existing_export_queues_without_starting_browser_login_or_sync(self):
        archive = export(self.base / 'My original result.zip')
        original = archive.read_bytes()
        with patch.object(w.Path, 'home', return_value=self.base), \
             patch('builtins.input', side_effect=[str(archive), 'A', '', 'q']), \
             patch.object(w.subprocess, 'run', side_effect=AssertionError('No browser, setup or crop')), \
             patch.object(w, 'GitHub', side_effect=AssertionError('No foreground API')):
            w._interactive_loop()
        self.assertTrue((self.root / '.upload_queue/A.json').is_file())
        self.assertEqual(next((self.root / 'A/runs').glob('*/export.zip')).read_bytes(), original)

    def test_refusal_reason_survives_an_unavailable_upload_queue(self):
        paper = pdf(self.base / 'Original paper.pdf')

        def refuse(args):
            folder, job = w.prepare_job(args.root, args.paper_id, source=args.source)
            run = w.new_run(folder)
            (run / 'REFUSED.txt').write_text('Original source key is missing')
            w.record_status(folder, job, 'held', run.name, reason='Original source key is missing')
            raise w.WorkspaceError('Original source key is missing')

        with patch.object(w.Path, 'home', return_value=self.base), \
             patch('builtins.input', side_effect=[str(paper), 'A', '', '', '', 'q']), \
             patch.object(w, 'process', side_effect=refuse), \
             patch.object(w, 'queue_sync', side_effect=w.WorkspaceError('Queue unavailable')):
            w._interactive_loop()
        self.assertIn('Original source key is missing', self.console.getvalue())
        self.assertIn('Queue unavailable', self.console.getvalue())

    def test_login_occurs_only_after_explicit_connect_choice(self):
        with patch('builtins.input', side_effect=['g', 'q']), \
             patch.object(w, 'connect_github', return_value=True) as connect:
            w._interactive_loop()
        self.assertEqual(connect.call_count, 1)
        with patch('builtins.input', side_effect=['q']), \
             patch.object(w, 'connect_github', side_effect=AssertionError('No automatic login')):
            w._interactive_loop()

    def test_explicit_browser_flow_cannot_ask_the_terminal_git_credentials_question(self):
        with patch.object(w.shutil, 'which', return_value='/usr/local/bin/gh'), \
             patch.object(w.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertTrue(w.connect_github())
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertEqual(command[:3], ['gh', 'auth', 'login'])
        self.assertIn('--web', command)
        self.assertNotIn('--with-token', command)
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(kwargs['env']['GH_PROMPT_DISABLED'], '1')

    def test_missing_github_tools_do_not_prevent_return_to_local_processing(self):
        with patch.object(w.shutil, 'which', return_value=None), \
             patch.object(w.subprocess, 'run', side_effect=AssertionError('No helper available')):
            self.assertFalse(w.connect_github())

    def test_newly_queued_work_wakes_the_idle_worker_without_waiting_an_hour(self):
        folder = self.source / 'A'
        folder.mkdir()
        archive = export(folder / 'Original result.zip')
        args = argparse.Namespace(source=archive, root=self.root, paper_id='A',
                                  profile='unspecified', offline=True)
        job = w.attach(args)
        w.queue_sync(job)
        pause = threading.Event()
        pause.set()
        checked = threading.Event()
        wake = threading.Event()
        invoked = threading.Event()

        class ObservedPause:
            def is_set(self):
                checked.set()
                return pause.is_set()

        with patch.object(w, 'drain_queue', side_effect=lambda *a, **kw: invoked.set()):
            stop, thread = w.start_queue_worker(self.root, interval=3600,
                                               pause_event=ObservedPause(), wake_event=wake)
            try:
                self.assertTrue(checked.wait(2))
                self.assertFalse(invoked.is_set())
                pause.clear()
                wake.set()
                self.assertTrue(invoked.wait(2))
            finally:
                stop.set()
                wake.set()
                thread.join(2)
        self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
