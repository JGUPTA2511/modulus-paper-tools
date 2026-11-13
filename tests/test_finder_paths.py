"""Synthetic Finder input/chooser regressions; no real Mac dialogs or papers."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_workspace import w


def finder_escape(path):
    """The relevant Finder/Terminal escaping, retaining literal final spaces."""
    return str(path).replace('\\', '\\\\').replace(' ', '\\ ').replace("'", "\\'")


class FinderPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.console = io.StringIO()
        output = redirect_stdout(self.console)
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def folder(self, name):
        path = self.base / name
        path.mkdir()
        return path

    def test_finder_drag_preserves_a_literal_trailing_space(self):
        folder = self.folder('Modulus Batch ')
        self.assertEqual(w.path_from_prompt(finder_escape(folder)), folder)
        self.assertTrue(folder.is_dir())
        self.assertEqual(folder.name, 'Modulus Batch ')

    def test_finder_drag_preserves_multiple_final_spaces_and_terminal_separator(self):
        folder = self.folder('Modulus Batch   ')
        self.assertEqual(w.path_from_prompt(finder_escape(folder) + ' '), folder)

    def test_unescaped_existing_path_preserves_its_trailing_space(self):
        folder = self.folder('Original Folder ')
        self.assertEqual(w.path_from_prompt(str(folder)), folder)

    def test_quoted_path_preserves_spaces_unicode_and_apostrophes(self):
        folder = self.folder("  Original student's प्रश्न folder  ")
        self.assertEqual(w.path_from_prompt(shlex.quote(str(folder))), folder)
        self.assertEqual(w.path_from_prompt('"' + str(folder) + '"'), folder)

    def test_backslashes_in_original_names_are_preserved(self):
        folder = self.folder('Original \\ folder ')
        self.assertEqual(w.path_from_prompt(finder_escape(folder)), folder)
        self.assertEqual(w.path_from_prompt(shlex.quote(str(folder))), folder)

    def test_multiple_dragged_paths_remain_rejected(self):
        first = self.folder('First Paper ')
        second = self.folder('Second Paper ')
        dragged = finder_escape(first) + ' ' + finder_escape(second) + ' '
        with self.assertRaises(w.WorkspaceError):
            w.path_from_prompt(dragged)

    def test_path_text_never_executes_shell_syntax(self):
        folder = self.folder('Original $(touch DO_NOT_RUN) `command` folder ')
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No shell execution')):
            self.assertEqual(w.path_from_prompt(shlex.quote(str(folder))), folder)
        self.assertFalse((self.base / 'DO_NOT_RUN').exists())

    def test_path_reader_keeps_symlink_identity_for_batch_safety_checks(self):
        target = self.folder('Target Original ')
        link = self.base / 'Link Original '
        link.symlink_to(target, target_is_directory=True)
        self.assertEqual(w.path_from_prompt(finder_escape(link)), link)
        self.assertTrue(link.is_symlink())

    def test_legacy_clipped_final_escape_recovers_the_unique_existing_space_ended_folder(self):
        folder = self.folder('Legacy Batch ')
        clipped = finder_escape(folder).rstrip()
        self.assertTrue(clipped.endswith('\\'))
        self.assertEqual(w.path_from_prompt(clipped), folder)

    def test_stray_final_escape_recovers_only_the_unique_existing_nonspace_folder(self):
        folder = self.folder('Legacy Batch')
        self.assertEqual(w.path_from_prompt(finder_escape(folder) + '\\'), folder)

    def test_legacy_recovery_refuses_to_guess_between_two_existing_folders(self):
        folder = self.folder('Legacy Batch')
        self.folder('Legacy Batch ')
        with self.assertRaises(w.WorkspaceError):
            w.path_from_prompt(finder_escape(folder) + '\\')

    def test_invalid_legacy_escape_without_existing_candidate_remains_rejected(self):
        with self.assertRaises(w.WorkspaceError):
            w.path_from_prompt(str(self.base / 'Missing Folder') + '\\')

    def test_legacy_recovery_still_refuses_multiple_dragged_paths(self):
        first = self.folder('First Legacy')
        second = self.folder('Second Legacy ')
        clipped = (finder_escape(first) + ' ' + finder_escape(second)).rstrip()
        with self.assertRaises(w.WorkspaceError):
            w.path_from_prompt(clipped)

    def test_empty_input_remains_optional_at_file_prompts(self):
        self.assertIsNone(w.path_from_prompt(''))
        self.assertIsNone(w.path_from_prompt('   '))

    def test_interactive_loop_does_not_strip_the_finder_path_before_parsing(self):
        folder = self.folder('Batch Folder ')
        dragged = finder_escape(folder) + ' '
        actual = w.path_from_prompt
        with patch('builtins.input', side_effect=[dragged, 'q']), \
             patch.object(w, 'path_from_prompt', wraps=actual) as parse, \
             patch.object(w, 'interactive_batch', return_value=[]) as batch:
            w._interactive_loop()
        self.assertEqual(parse.call_args.args[0], dragged)
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(batch.call_args.args[0], folder)
        self.assertNotIn('STOPPED:', self.console.getvalue())

    def test_native_folder_chooser_returns_path_without_trimming_folder_whitespace(self):
        folder = self.folder('Selected Folder ')
        result = subprocess.CompletedProcess([], 0, stdout=str(folder) + '/\n', stderr='')
        with patch.object(w.sys, 'platform', 'darwin'), \
             patch.object(w.subprocess, 'run', return_value=result) as run:
            chosen = w.choose_batch_folder()
        self.assertEqual(chosen, folder)
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertIsInstance(command, list)
        self.assertEqual(command[0], 'osascript')
        self.assertIn('choose folder', ' '.join(command))
        self.assertFalse(kwargs.get('shell', False))
        self.assertTrue(folder.is_dir())

    def test_cancelled_native_folder_chooser_returns_without_processing(self):
        result = subprocess.CompletedProcess([], 1, stdout='', stderr='User canceled. (-128)')
        with patch.object(w.sys, 'platform', 'darwin'), \
             patch.object(w.subprocess, 'run', return_value=result):
            self.assertIsNone(w.choose_batch_folder())

    def test_enter_selects_a_folder_through_the_native_dialog_on_mac(self):
        folder = self.folder('Picked Original Folder ')
        with patch.object(w.sys, 'platform', 'darwin'), \
             patch('builtins.input', side_effect=['', 'q']), \
             patch.object(w, 'choose_batch_folder', return_value=folder) as choose, \
             patch.object(w, 'interactive_batch', return_value=[]) as batch:
            w._interactive_loop()
        self.assertEqual(choose.call_count, 1)
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(batch.call_args.args[0], folder)
        self.assertNotIn('STOPPED:', self.console.getvalue())

    def test_cancelled_folder_selection_returns_to_prompt_quietly(self):
        with patch.object(w.sys, 'platform', 'darwin'), \
             patch('builtins.input', side_effect=['', 'q']), \
             patch.object(w, 'choose_batch_folder', return_value=None), \
             patch.object(w, 'interactive_batch', side_effect=AssertionError('Cancelled dialog')):
            w._interactive_loop()
        self.assertNotIn('STOPPED:', self.console.getvalue())

    def test_enter_on_non_mac_never_attempts_an_applescript_process(self):
        with patch.object(w.sys, 'platform', 'linux'), \
             patch('builtins.input', side_effect=['', 'q']), \
             patch.object(w.subprocess, 'run', side_effect=AssertionError('No AppleScript on Linux')), \
             patch.object(w, 'interactive_batch', side_effect=AssertionError('No selected folder')):
            w._interactive_loop()


if __name__ == '__main__':
    unittest.main()
