"""Actual Bash startup with synthetic programs; no login, installs or network."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LauncherQueueTests(unittest.TestCase):
    def launch(self, *, venv=True, system_python=True, brew=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name) / 'Mac Tool With Spaces'
        stubs = folder / 'stub-tools'
        stubs.mkdir(parents=True)
        env = dict(os.environ, PATH=str(stubs) + ':/usr/bin:/bin',
                   MODULUS_TEST_MARKER=str(folder / 'interactive-started'),
                   MODULUS_TEST_COMMAND_LOG=str(folder / 'commands'))
        python = '''#!/bin/bash
printf 'python %s\\n' "$*" >> "$MODULUS_TEST_COMMAND_LOG"
if [ "${1:-}" = -c ]; then exit 0; fi
if [ "${2:-}" = --interactive ]; then
  IFS= read -r source
  printf '%s\\n' "$source" > "$MODULUS_TEST_MARKER"
  exit 0
fi
exit 42
'''
        commands = {
            'pdftoppm': '#!/bin/bash\nexit 0\n',
            'tesseract': '#!/bin/bash\nexit 0\n',
            'curl': '#!/bin/bash\nexit 7\n',
            # This reproduces a credential prompt consuming stdin if invoked.
            'gh': '#!/bin/bash\nprintf "gh %s\\n" "$*" >> "$MODULUS_TEST_COMMAND_LOG"\nread -r answer\nexit 91\n',
        }
        if brew:
            commands['brew'] = '#!/bin/bash\nprintf "brew %s\\n" "$*" >> "$MODULUS_TEST_COMMAND_LOG"\nexit 92\n'
        if system_python:
            commands['python3'] = python
        for name, source in commands.items():
            path = stubs / name
            path.write_text(source)
            path.chmod(0o755)
        if venv:
            path = folder / '.venv/bin/python3'
            path.parent.mkdir(parents=True)
            path.write_text(python)
            path.chmod(0o755)
        source = (ROOT / 'run_core.sh').read_text().replace(
            '/opt/homebrew/bin/brew /usr/local/bin/brew', '/missing-modulus-brew-a /missing-modulus-brew-b')
        (folder / 'run_core.sh').write_text(source)
        (folder / 'workspace_client.py').write_text('# Existing installed client\n')
        result = subprocess.run(['/bin/bash', str(folder / 'run_core.sh')], env=env,
                                input='My original question paper.pdf\n', capture_output=True,
                                text=True, timeout=10)
        return folder, result

    def test_startup_never_calls_github_or_consumes_the_first_paper_as_login_input(self):
        folder, result = self.launch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((folder / 'interactive-started').read_text(), 'My original question paper.pdf\n')
        log = (folder / 'commands').read_text()
        self.assertNotIn('gh ', log)
        self.assertNotIn('brew ', log)
        self.assertNotIn('-m pip', log)
        self.assertNotIn('-m venv', log)
        self.assertNotIn('ONE-TIME CONNECTION', result.stdout)

    def test_existing_venv_runs_without_homebrew_or_a_working_system_python(self):
        folder, result = self.launch(system_python=False, brew=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        log = (folder / 'commands').read_text()
        self.assertIn('--interactive', log)
        self.assertNotIn('-m pip', log)
        self.assertEqual((folder / 'interactive-started').read_text(), 'My original question paper.pdf\n')

    def test_existing_python_packages_are_used_without_creating_another_venv(self):
        folder, result = self.launch(venv=False, brew=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((folder / '.venv').exists())
        log = (folder / 'commands').read_text()
        self.assertNotIn('-m pip', log)
        self.assertNotIn('-m venv', log)
        self.assertNotIn('gh ', log)


if __name__ == '__main__':
    unittest.main()
