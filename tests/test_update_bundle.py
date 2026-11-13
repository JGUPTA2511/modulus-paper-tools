"""Real Bash staging of the run_core four-file update, using local stubs.

This covers run_core's download/compile staging only. The outer .command
starter has its own earlier updater, so this is not a whole-launch transaction.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FILES = ('question_boundaries.py', 'source_layout.py', 'source_keys.py',
         'workspace_client.py')


class UpdateBundleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.install = self.base / 'Installed Mac tool with spaces'
        self.remote = self.base / 'Local download sources with spaces'
        self.saved = self.base / 'Installed originals with spaces'
        self.stubs = self.base / 'Stub helper programs with spaces'
        for folder in (self.install, self.remote, self.saved, self.stubs):
            folder.mkdir()
        self.marker = self.base / 'interactive marker.json'
        self.log = self.base / 'requested files.txt'
        self.old = {}
        self.new = {}
        for name in FILES:
            self.old[name] = self.module(name, 'old')
            self.new[name] = self.module(name, 'new')
            (self.install / name).write_text(self.old[name])
            (self.saved / name).write_text(self.old[name])
            (self.remote / name).write_text(self.new[name])
        runner = (ROOT / 'run_core.sh').read_text().replace(
            '/opt/homebrew/bin/brew /usr/local/bin/brew',
            '/missing-modulus-brew-a /missing-modulus-brew-b')
        (self.install / 'run_core.sh').write_text(runner)
        self.program(self.stubs / 'pdftoppm', '#!/bin/bash\nexit 0\n')
        self.program(self.stubs / 'tesseract', '#!/bin/bash\nexit 0\n')
        self.program(self.stubs / 'gh', '#!/bin/bash\nexit 97\n')
        self.program(self.stubs / 'brew', '#!/bin/bash\nexit 98\n')
        self.program(self.install / '.venv/bin/python3', '''#!/bin/bash
if [ "${1:-}" = -c ]; then exit 0; fi
exec "$MODULUS_TEST_REAL_PYTHON" "$@"
''')
        self.program(self.stubs / 'curl', '''#!/bin/bash
set -eu
file=''
destination=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) destination="$2"; shift 2;;
    https://*) file="${1##*/}"; shift;;
    *) shift;;
  esac
done
printf '%s\\n' "$file" >> "$MODULUS_TEST_REQUEST_LOG"
# Every request must still see the complete installed old set. This catches
# replacements performed before all downloads/compiles have staged.
for name in question_boundaries.py source_layout.py source_keys.py workspace_client.py; do
  if [ -f "$MODULUS_TEST_SAVED/$name" ]; then
    cmp -s "$MODULUS_TEST_SAVED/$name" "$MODULUS_TEST_INSTALL/$name" || exit 92
  elif [ -e "$MODULUS_TEST_INSTALL/$name" ]; then
    exit 93
  fi
done
if [ "$file" = "${MODULUS_TEST_FAIL_FILE:-}" ]; then exit 7; fi
cp "$MODULUS_TEST_REMOTE/$file" "$destination"
''')

    @staticmethod
    def program(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o755)

    @staticmethod
    def module(name, version):
        source = 'VERSION = ' + repr(version) + '\n'
        if name == 'workspace_client.py':
            source += '''if __name__ == '__main__':
    import json, os, sys
    from pathlib import Path
    assert sys.argv[1:] == ['--interactive']
    modules = ['question_boundaries', 'source_layout', 'source_keys']
    observed = {name: __import__(name).VERSION for name in modules}
    observed['workspace_client'] = VERSION
    Path(os.environ['MODULUS_TEST_MARKER']).write_text(json.dumps(observed))
'''
        return source

    def launch(self, *, fail_file='', invalid_file=''):
        if invalid_file:
            (self.remote / invalid_file).write_text('def invalid syntax(:\n')
        env = dict(os.environ,
                   PATH=str(self.stubs) + ':/usr/bin:/bin',
                   MODULUS_TEST_REAL_PYTHON=sys.executable,
                   MODULUS_TEST_MARKER=str(self.marker),
                   MODULUS_TEST_REQUEST_LOG=str(self.log),
                   MODULUS_TEST_SAVED=str(self.saved),
                   MODULUS_TEST_INSTALL=str(self.install),
                   MODULUS_TEST_REMOTE=str(self.remote),
                   MODULUS_TEST_FAIL_FILE=fail_file)
        return subprocess.run(['/bin/bash', str(self.install / 'run_core.sh')],
                              env=env, capture_output=True, text=True, timeout=15)

    def assert_old_retained(self):
        for name in FILES:
            with self.subTest(file=name):
                self.assertEqual((self.install / name).read_text(), self.old[name])
                self.assertFalse((self.install / (name + '.previous')).exists())

    def test_complete_staged_bundle_replaces_all_four_before_starting_client(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in FILES:
            self.assertEqual((self.install / name).read_text(), self.new[name])
            self.assertEqual((self.install / (name + '.previous')).read_text(), self.old[name])
        self.assertEqual(self.log.read_text().splitlines(), list(FILES))
        self.assertEqual(set(json.loads(self.marker.read_text()).values()), {'new'})

    def test_partial_network_failure_keeps_every_installed_file(self):
        result = self.launch(fail_file='source_keys.py')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_old_retained()
        self.assertEqual(set(json.loads(self.marker.read_text()).values()), {'old'})
        self.assertIn('complete update unavailable', result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), list(FILES[:3]))

    def test_invalid_python_late_in_bundle_keeps_every_installed_file(self):
        result = self.launch(invalid_file='workspace_client.py')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_old_retained()
        self.assertEqual(set(json.loads(self.marker.read_text()).values()), {'old'})
        self.assertIn('complete update unavailable', result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), list(FILES))

    def test_first_install_failure_exits_without_running_interactive(self):
        (self.install / 'workspace_client.py').unlink()
        (self.saved / 'workspace_client.py').unlink()
        result = self.launch(fail_file='source_layout.py')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.install / 'workspace_client.py').exists())
        for name in FILES[:3]:
            self.assertEqual((self.install / name).read_text(), self.old[name])
            self.assertFalse((self.install / (name + '.previous')).exists())
        self.assertIn('Could not download the complete tool update', result.stdout)


if __name__ == '__main__':
    unittest.main()
