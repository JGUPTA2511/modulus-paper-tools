"""Simulated workspace/development handoff through the existing client queue."""
import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import portal_bridge
from test_workspace import FakeAPI, export, w


class PortalClientHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = export(self.root / 'Existing crop export.zip')
        self.folder = w.attach(argparse.Namespace(source=self.archive, root=self.root / 'Jobs',
            paper_id='PAPER-02', profile='unspecified', offline=True, keep_color=False,
            source_pdf=None, key=None, solutions=None, no_review=True))

    def test_existing_workspace_sync_stages_development_without_recropping(self):
        api = FakeAPI()
        with patch.object(portal_bridge, 'sync_job', return_value={'status': 'staged_in_development',
                          'publication': 'not_deployed'}) as stage, \
             patch.object(w.subprocess, 'run', side_effect=AssertionError('No crop or browser')):
            result = w.sync_job(self.folder, api)
        stage.assert_called_once_with(self.folder, api=api)
        self.assertEqual(result['portal']['status'], 'staged_in_development')
        self.assertEqual(len(api.commit_trees), 1)
        self.assertEqual(len(list((self.folder / 'runs').iterdir())), 1)

    def test_development_failure_retries_without_duplicate_workspace_upload_or_crop(self):
        api = FakeAPI()
        original = self.archive.read_bytes()
        with patch.object(portal_bridge, 'sync_job', side_effect=portal_bridge.PortalError('Temporary outage')):
            with self.assertRaisesRegex(w.WorkspaceError, 'without recropping'):
                w.sync_job(self.folder, api)
        self.assertTrue((self.folder.parent / '.upload_queue/PAPER-02.json').is_file())
        with patch.object(portal_bridge, 'sync_job', return_value={'status': 'staged_in_development'}), \
             patch.object(w.subprocess, 'run', side_effect=AssertionError('No crop')):
            w.sync_job(self.folder, api)
        self.assertEqual(len(api.commit_trees), 1)
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertEqual(len(list((self.folder / 'runs').iterdir())), 1)
