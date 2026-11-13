"""Durable local upload queue tests. All papers and GitHub writes are synthetic."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_workspace import FakeAPI, export, w


class UploadQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / 'Local Jobs With Spaces'
        self.output = redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.tmp.cleanup()

    def job(self, pid='A'):
        source = export(self.base / (pid + ' original.zip'))
        args = argparse.Namespace(source=source, root=self.root, paper_id=pid,
                                  profile='unspecified', offline=True)
        return w.attach(args)

    def record(self, pid='A'):
        return self.root / '.upload_queue' / (pid + '.json')

    def test_saved_queue_survives_network_failure_then_uploads_without_recropping(self):
        folder = self.job()
        w.queue_sync(folder, 'Connection unavailable')
        snapshot = (folder / 'status.json').read_bytes()
        with patch.object(w, 'sync_job', side_effect=w.WorkspaceError('Offline')):
            failed = w.drain_queue(self.root)
        self.assertEqual(failed[0]['outcome'], 'queued')
        record = json.loads(self.record().read_text())
        self.assertEqual(record['attempts'], 1)
        self.assertEqual(record['last_error'], 'Offline')
        api = FakeAPI()
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No crop/auth subprocess expected')):
            succeeded = w.drain_queue(self.root, api)
        self.assertEqual(succeeded[0]['outcome'], 'synced')
        self.assertFalse(self.record().exists())
        self.assertEqual((folder / 'status.json').read_bytes(), snapshot)
        self.assertEqual(len(list((folder / 'runs').iterdir())), 1)
        self.assertEqual(len(api.commit_trees), 1)
        self.assertEqual(w.drain_queue(self.root, api), [])
        self.assertEqual(len(api.commit_trees), 1)

    def test_one_failed_upload_keeps_it_queued_and_does_not_stop_other_papers(self):
        for pid in ('A', 'B'):
            w.queue_sync(self.job(pid))
        api = FakeAPI()
        actual_sync = w.sync_job
        def selective(folder, supplied_api=None, **kwargs):
            if Path(folder).name == 'A':
                raise w.WorkspaceError('Network unavailable for this transfer')
            return actual_sync(folder, supplied_api, **kwargs)
        with patch.object(w, 'sync_job', side_effect=selective):
            rows = w.drain_queue(self.root, api)
        self.assertEqual([r['outcome'] for r in rows], ['queued', 'synced'])
        self.assertTrue(self.record('A').exists())
        self.assertFalse(self.record('B').exists())
        self.assertIn('paper_jobs/B/job.json', api.files)

    def test_existing_record_is_all_that_restart_needs(self):
        folder = self.job()
        w.queue_sync(folder)
        stored = self.record().read_bytes()
        api = FakeAPI()
        # No queue object or in-memory task is retained; reload from disk only.
        self.assertEqual(json.loads(stored)['paper_id'], 'A')
        self.assertEqual(w.drain_queue(Path(str(self.root)), api)[0]['outcome'], 'synced')
        self.assertFalse(self.record().exists())

    def test_newer_enqueue_during_upload_is_not_lost(self):
        folder = self.job()
        first = w.queue_sync(folder)
        actual_sync = w.sync_job
        api = FakeAPI()
        def enqueue_again(path, supplied_api=None, **kwargs):
            result = actual_sync(path, supplied_api, **kwargs)
            w.queue_sync(folder, 'New retry requested concurrently')
            return result
        with patch.object(w, 'sync_job', side_effect=enqueue_again):
            w.drain_queue(self.root, api)
        current = json.loads(self.record().read_text())
        self.assertNotEqual(first['revision'], current['revision'])
        self.assertEqual(current['last_error'], 'New retry requested concurrently')
        self.assertEqual(w.drain_queue(self.root, api)[0]['outcome'], 'synced')
        self.assertFalse(self.record().exists())
        self.assertEqual(len(api.commit_trees), 1, 'Repeating a successful upload must not duplicate commits')

    def test_newer_enqueue_during_failed_request_keeps_its_details(self):
        folder = self.job()
        w.queue_sync(folder)
        def enqueue_again(path, supplied_api=None, **kwargs):
            w.queue_sync(folder, 'Newer queue entry')
            raise w.WorkspaceError('Older failure')
        with patch.object(w, 'sync_job', side_effect=enqueue_again):
            w.drain_queue(self.root)
        current = json.loads(self.record().read_text())
        self.assertEqual(current['last_error'], 'Newer queue entry')
        self.assertEqual(current['attempts'], 0)

    def test_public_destination_stays_queued_before_private_bytes_are_sent(self):
        w.queue_sync(self.job())
        api = FakeAPI(private=False)
        rows = w.drain_queue(self.root, api)
        self.assertEqual(rows[0]['outcome'], 'queued')
        self.assertEqual(api.calls, ['privacy'])
        self.assertFalse(api.blobs)
        self.assertTrue(self.record().exists())

    def test_symlink_queue_directory_cannot_receive_records(self):
        folder = self.job()
        outside = self.base / 'outside'
        outside.mkdir()
        (self.root / '.upload_queue').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(w.WorkspaceError, 'Unsafe local upload queue'):
            w.queue_sync(folder)
        with self.assertRaisesRegex(w.WorkspaceError, 'Unsafe local upload queue'):
            w.drain_queue(self.root, FakeAPI())
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_record_is_never_followed_or_replaced(self):
        folder = self.job()
        directory = self.root / '.upload_queue'
        directory.mkdir()
        outside = self.base / 'outside.json'
        outside.write_text('Do not touch')
        self.record().symlink_to(outside)
        with self.assertRaisesRegex(w.WorkspaceError, 'Unsafe'):
            w.queue_sync(folder)
        rows = w.drain_queue(self.root, FakeAPI())
        self.assertEqual(rows[0]['outcome'], 'queued')
        self.assertEqual(outside.read_text(), 'Do not touch')
        self.assertTrue(self.record().is_symlink())

    def test_queue_record_cannot_redirect_upload_to_another_folder(self):
        folder = self.job()
        record = w.queue_sync(folder)
        record.update(paper_id='../OUTSIDE', folder=str(self.base / 'outside'))
        self.record().write_text(json.dumps(record))
        api = FakeAPI()
        rows = w.drain_queue(self.root, api)
        self.assertEqual(rows[0]['outcome'], 'queued')
        self.assertEqual(api.calls, [])
        self.assertTrue(self.record().exists())

    def test_symlink_job_is_rejected_on_enqueue_and_drain(self):
        folder = self.job()
        actual = self.base / 'actual paper'
        folder.rename(actual)
        folder.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(w.WorkspaceError, 'Unsafe queued paper folder'):
            w.queue_sync(folder)
        folder.unlink()
        actual.rename(folder)
        w.queue_sync(folder)
        folder.rename(actual)
        folder.symlink_to(actual, target_is_directory=True)
        api = FakeAPI()
        rows = w.drain_queue(self.root, api)
        self.assertEqual(rows[0]['outcome'], 'queued')
        self.assertFalse(api.calls)
        self.assertTrue(self.record().exists())

    def test_status_must_identify_the_same_paper_before_enqueuing(self):
        folder = self.job()
        status = json.loads((folder / 'status.json').read_text())
        status['paper_id'] = 'B'
        (folder / 'status.json').write_text(json.dumps(status))
        with self.assertRaisesRegex(w.WorkspaceError, 'does not match'):
            w.queue_sync(folder)
        self.assertFalse(self.record().exists())

    def test_oversized_or_invalid_json_records_do_not_stop_other_uploads(self):
        w.queue_sync(self.job('B'))
        path = self.root / '.upload_queue' / 'A.json'
        path.write_bytes(b'x' * 17000)
        api = FakeAPI()
        rows = w.drain_queue(self.root, api)
        self.assertEqual([r['outcome'] for r in rows], ['queued', 'synced'])
        self.assertEqual(path.stat().st_size, 17000)
        self.assertFalse(self.record('B').exists())

    def test_worker_resumes_pending_queue_and_stops_without_waiting_a_minute(self):
        w.queue_sync(self.job())
        invoked = threading.Event()
        with patch.object(w, 'drain_queue', side_effect=lambda root, **kwargs: invoked.set()):
            stop, thread = w.start_queue_worker(self.root)
            try:
                self.assertTrue(invoked.wait(2), 'Worker should inspect saved pending tasks immediately')
            finally:
                stop.set()
                thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.record().exists(), 'Closing the worker must preserve unsent work')

    def test_idle_worker_makes_no_github_requests_and_creates_no_queue(self):
        with patch.object(w, 'drain_queue') as drain:
            stop, thread = w.start_queue_worker(self.root)
            try:
                # Thread may not have started; joining after stop still guarantees no API call.
                stop.set()
                thread.join(2)
            finally:
                stop.set()
        drain.assert_not_called()
        self.assertFalse((self.root / '.upload_queue').exists())


    def test_actual_upload_failure_enqueues_then_retry_clears_record(self):
        folder = self.job()
        class OfflineAPI(FakeAPI):
            def upload(self, files, message):
                raise w.WorkspaceError('Connection unavailable')
        with self.assertRaisesRegex(w.WorkspaceError, 'Connection unavailable'):
            w.sync_job(folder, OfflineAPI())
        self.assertTrue(self.record().exists())
        record = json.loads(self.record().read_text())
        self.assertEqual(record['last_error'], 'Connection unavailable')
        self.assertEqual(w.drain_queue(self.root, FakeAPI())[0]['outcome'], 'synced')
        self.assertFalse(self.record().exists())

    def test_background_retry_backoff_is_persisted_but_explicit_retry_is_immediate(self):
        folder = self.job()
        w.queue_sync(folder)
        with patch.object(w, 'sync_job', side_effect=w.WorkspaceError('Offline')) as failed:
            w.drain_queue(self.root, force=False)
            current = json.loads(self.record().read_text())
            self.assertEqual(current['attempts'], 1)
            self.assertGreater(current['next_retry_at'], w.dt.datetime.now(w.dt.timezone.utc).timestamp())
            w.drain_queue(self.root, force=False)
            self.assertEqual(failed.call_count, 1)
            self.assertEqual(json.loads(self.record().read_text())['attempts'], 1)
            w.drain_queue(self.root)  # Explicit user retry ignores the future deadline.
            self.assertEqual(failed.call_count, 2)
        self.assertEqual(json.loads(self.record().read_text())['attempts'], 2)
        self.assertEqual(w.drain_queue(self.root, FakeAPI())[0]['outcome'], 'synced')

    def test_paused_worker_waits_for_local_work_to_finish(self):
        w.queue_sync(self.job())
        pause = threading.Event()
        pause.set()
        checked = threading.Event()
        invoked = threading.Event()
        class ObservedPause:
            def is_set(self):
                checked.set()
                return pause.is_set()
        with patch.object(w, 'drain_queue', side_effect=lambda root, **kwargs: invoked.set()):
            stop, thread = w.start_queue_worker(self.root, interval=0.01, pause_event=ObservedPause())
            try:
                self.assertTrue(checked.wait(2))
                self.assertFalse(invoked.is_set())
                pause.clear()
                self.assertTrue(invoked.wait(2))
            finally:
                stop.set()
                thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_existing_drainer_prevents_duplicate_simultaneous_attempts(self):
        import fcntl
        w.queue_sync(self.job())
        directory = self.root / '.upload_queue'
        with (directory / '.drain.lock').open('a') as guard:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(w, 'sync_job') as sync:
                self.assertEqual(w.drain_queue(self.root), [])
                sync.assert_not_called()
        self.assertTrue(self.record().exists())

    def test_queue_locks_cannot_follow_symlinks(self):
        folder = self.job()
        directory = self.root / '.upload_queue'
        directory.mkdir()
        outside = self.base / 'valuable lock target'
        outside.write_text('original')
        (directory / '.lock').symlink_to(outside)
        with self.assertRaisesRegex(w.WorkspaceError, 'Unsafe local upload queue lock'):
            w.queue_sync(folder)
        (directory / '.lock').unlink()
        w.queue_sync(folder)
        (directory / '.drain.lock').symlink_to(outside)
        with self.assertRaisesRegex(w.WorkspaceError, 'Unsafe local queue worker lock'):
            w.drain_queue(self.root, FakeAPI())
        self.assertEqual(outside.read_text(), 'original')


if __name__ == '__main__':
    unittest.main()
