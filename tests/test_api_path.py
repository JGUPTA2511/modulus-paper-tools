"""Regression: the live repository metadata endpoint rejects a trailing slash."""
import argparse
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workspace_transport', ROOT / 'workspace_client.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

class APIPathTests(unittest.TestCase):
    def test_repository_endpoint_has_no_trailing_slash(self):
        with patch.object(w.shutil, 'which', return_value='/opt/homebrew/bin/gh'):
            api = w.GitHub()
        result = argparse.Namespace(returncode=0, stdout='{}', stderr='')
        with patch.object(w.subprocess, 'run', return_value=result) as call:
            api.call('')
        self.assertIn('repos/JGUPTA2511/modulus-practice-corner', call.call_args.args[0])
        self.assertNotIn('repos/JGUPTA2511/modulus-practice-corner/', call.call_args.args[0])

if __name__ == '__main__':
    unittest.main()
