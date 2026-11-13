"""Execute the shell launcher with synthetic tools; never run network/install commands."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LauncherQueueTests(unittest.TestCase):
    def test_offline_browser_login_does_not_block_local_interactive_processing(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'Mac Tool With Spaces'
            folder.mkdir()
            stubs = folder / 'stub-tools'
            stubs.mkdir()
            env = dict(os.environ, PATH=str(stubs) + ':/usr/bin:/bin',
                       MODULUS_TEST_MARKER=str(folder / 'interactive-started'),
                       MODULUS_TEST_AUTH_LOG=str(folder / 'auth-attempts'))
            commands = {
                'brew': '#!/bin/bash\nexit 0\n',
                'pdftoppm': '#!/bin/bash\nexit 0\n',
                'tesseract': '#!/bin/bash\nexit 0\n',
                'curl': '#!/bin/bash\nexit 7\n',
                'gh': '#!/bin/bash\nprintf "%s\\n" "$*" >> "$MODULUS_TEST_AUTH_LOG"\nexit 1\n',
                'python3': '#!/bin/bash\n'
                    'if [ "${1:-}" = -c ]; then exit 0; fi\n'
                    'if [ "${2:-}" = --interactive ]; then\n'
                    '  printf "local processing started\\n" > "$MODULUS_TEST_MARKER"\n'
                    '  exit 0\n'
                    'fi\n'
                    'exit 42\n'
            }
            for name, source in commands.items():
                path = stubs / name
                path.write_text(source)
                path.chmod(0o755)
            # Stub only host Homebrew discovery paths, preserving the actual
            # dependency, update, login and interactive-launch control flow.
            source = (ROOT / 'run_core.sh').read_text()
            source = source.replace('/opt/homebrew/bin/brew /usr/local/bin/brew',
                                    '"' + str(stubs / 'brew') + '"')
            (folder / 'run_core.sh').write_text(source)
            (folder / '.venv/bin').mkdir(parents=True)
            (folder / '.venv/bin/activate').write_text('')
            (folder / 'workspace_client.py').write_text('# Existing installed client\n')
            result = subprocess.run(['/bin/bash', str(folder / 'run_core.sh')],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((folder / 'interactive-started').read_text(),
                             'local processing started\n')
            auth = (folder / 'auth-attempts').read_text()
            self.assertIn('auth status --hostname github.com', auth)
            self.assertIn('auth login --hostname github.com --web --git-protocol https', auth)
            self.assertIn('Using the installed workspace client', result.stdout)
            self.assertIn('queue', result.stdout.lower())


if __name__ == '__main__':
    unittest.main()
