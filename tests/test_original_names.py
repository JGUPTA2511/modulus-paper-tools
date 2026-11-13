"""Synthetic original-filename association regressions; no real paper fixtures."""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_batch import pdf
from test_workspace import FakeAPI, export, image, w


class OriginalFilenameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.source = self.base / 'Batch original names'
        self.source.mkdir()
        self.root = self.base / 'Generated jobs'
        self.console = io.StringIO()
        self.redirect = redirect_stdout(self.console)
        self.redirect.__enter__()
        self.crops = []

    def tearDown(self):
        self.redirect.__exit__(None, None, None)
        self.tmp.cleanup()

    def folder(self, name='PAPER-A'):
        folder = self.source / name
        folder.mkdir()
        return folder

    def config(self, folder, files, ignore=None):
        settings = {'files': files}
        if ignore is not None:
            settings['ignore'] = ignore
        (folder / 'paper.json').write_text(json.dumps(settings))

    def args(self, offline=True):
        return argparse.Namespace(source=self.source, root=self.root,
            profile='unspecified', paper_profile=[], offline=offline, keep_color=False)

    def snapshot(self, folder):
        return {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file() and not p.is_symlink()}

    def cropper(self, command, **kwargs):
        self.crops.append(command)
        output = Path(command[command.index('--out') + 1])
        (output / 'crops').mkdir(parents=True)
        (output / 'crops/Q001.png').write_bytes(image())
        (output / 'exam.json').write_text(json.dumps({'count': 1, 'questions': [
            {'number': 1, 'image': 'crops/Q001.png', 'answer': 0,
             'type': 'numerical', 'passage': 'Synthetic original passage'}]}))
        return subprocess.CompletedProcess(command, 0)

    def test_single_pdf_keeps_spaces_non_ascii_and_original_bytes(self):
        folder = self.folder()
        original = pdf(folder / 'GTM 02 प्रश्न पत्र final.pdf')
        before = self.snapshot(folder)
        entries = w.discover_batch(self.source, self.root)
        self.assertIsNone(entries[0]['reason'])
        self.assertEqual(Path(entries[0]['files']['questions.pdf']), original)
        self.assertEqual(self.snapshot(folder), before)
        self.assertFalse(self.root.exists(), 'Unambiguous read-only discovery must not create jobs')
        with patch.object(w.subprocess, 'run', side_effect=self.cropper):
            rows = w.run_batch(self.args(), entries)
        self.assertEqual(rows[0]['outcome'], 'upload failed/local-only')
        job = json.loads((self.root / 'PAPER-A/job.json').read_text())
        self.assertEqual(job['inputs']['questions']['original_name'], original.name)
        self.assertEqual((self.root / 'PAPER-A/input/questions.pdf').read_bytes(), original.read_bytes())
        self.assertEqual(self.snapshot(folder), before)

    def test_single_unknown_question_with_canonical_key_and_solutions(self):
        folder = self.folder()
        question = pdf(folder / 'Unchanged downloaded paper.pdf')
        pdf(folder / 'answer_key.pdf', 'Synthetic key')
        pdf(folder / 'solutions.pdf', 'Synthetic solutions')
        entries = w.discover_batch(self.source, self.root)
        self.assertIsNone(entries[0]['reason'])
        self.assertEqual(Path(entries[0]['files']['questions.pdf']), question)
        self.assertEqual(set(entries[0]['files']), {'questions.pdf', 'answer_key.pdf', 'solutions.pdf'})

    def test_single_arbitrarily_named_export_is_validated_and_reused_unchanged(self):
        folder = self.folder()
        archive = export(folder / 'GTM 04 accepted result (original).zip', [
            {'number': 1, 'image': 'crops/Q001.png', 'answer': 0, 'type': 'numerical'}])
        before = self.snapshot(folder)
        entries = w.discover_batch(self.source, self.root)
        self.assertIsNone(entries[0]['reason'])
        self.assertEqual(Path(entries[0]['files']['export.zip']), archive)
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('Existing export must not crop')):
            first = w.run_batch(self.args(), entries)
            second = w.run_batch(self.args())
        self.assertEqual(first[0]['outcome'], 'upload failed/local-only')
        self.assertEqual(second[0]['outcome'], 'already processed')
        runs = list((self.root / 'PAPER-A/runs').iterdir())
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0] / 'export.zip').read_bytes(), archive.read_bytes())
        self.assertEqual(json.loads((runs[0] / 'exam.json').read_text())['questions'][0]['answer'], 0)
        self.assertEqual(self.snapshot(folder), before)

    def test_multiple_original_pdfs_are_held_without_heuristic_guessing(self):
        folder = self.folder()
        pdf(folder / 'Likely Question Paper.pdf')
        pdf(folder / 'Likely Answer Key.pdf')
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(entries[0]['reason'])
        self.assertFalse(self.root.exists())
        with patch.object(w, 'process', side_effect=AssertionError('Do not guess associations')):
            result = w.run_batch(self.args(), entries)
        self.assertEqual(result[0]['outcome'], 'held')

    def test_explicit_mapping_retains_every_original_role_and_numerical_zero(self):
        folder = self.folder()
        question = pdf(folder / '01 Original Paper.pdf', 'Question')
        key = pdf(folder / '02 Official Responses.pdf', 'Key')
        solutions = pdf(folder / '03 Worked Steps.pdf', 'Solutions')
        self.config(folder, {'questions': question.name, 'answer_key': key.name, 'solutions': solutions.name})
        before = self.snapshot(folder)
        entries = w.discover_batch(self.source, self.root)
        self.assertIsNone(entries[0]['reason'])
        with patch.object(w.subprocess, 'run', side_effect=self.cropper):
            w.run_batch(self.args(), entries)
        job = json.loads((self.root / 'PAPER-A/job.json').read_text())
        self.assertEqual({r: v['original_name'] for r, v in job['inputs'].items()}, {
            'questions': question.name, 'answer_key': key.name, 'solutions': solutions.name})
        for role, path in (('questions', question), ('answer_key', key), ('solutions', solutions)):
            self.assertEqual((self.root / 'PAPER-A/input' / (role + '.pdf')).read_bytes(), path.read_bytes())
        self.assertIn('--key', self.crops[0])
        exam = json.loads(next((self.root / 'PAPER-A/runs').glob('*/exam.json')).read_text())
        self.assertEqual(exam['questions'][0]['answer'], 0)
        self.assertEqual(exam['questions'][0]['type'], 'numerical')
        self.assertEqual(exam['questions'][0]['passage'], 'Synthetic original passage')
        state = json.loads((self.root / 'PAPER-A/status.json').read_text())
        self.assertEqual(state['solutions'], 'supplied_not_matched')
        self.assertEqual(self.snapshot(folder), before)

    def test_explicit_export_mapping_reuses_original_archive_beside_optional_pdfs(self):
        folder = self.folder()
        question = pdf(folder / 'Original qp.pdf')
        solutions = pdf(folder / 'Original worked solution.pdf')
        archive = export(folder / 'Owner accepted cleaned crops.zip')
        self.config(folder, {'questions': question.name, 'solutions': solutions.name, 'export': archive.name})
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No recropping')):
            w.run_batch(self.args())
        run = next((self.root / 'PAPER-A/runs').iterdir())
        self.assertEqual((run / 'export.zip').read_bytes(), archive.read_bytes())
        self.assertEqual((self.root / 'PAPER-A/input/solutions.pdf').read_bytes(), solutions.read_bytes())

    def test_unassigned_documents_need_explicit_ignore(self):
        folder = self.folder()
        question = pdf(folder / 'Question original.pdf')
        extra = pdf(folder / 'Unrelated insert.pdf')
        self.config(folder, {'questions': question.name})
        held = w.discover_batch(self.source, self.root)
        self.assertTrue(held[0]['reason'])
        self.config(folder, {'questions': question.name}, ignore=[extra.name])
        accepted = w.discover_batch(self.source, self.root)
        self.assertIsNone(accepted[0]['reason'])
        self.assertEqual(set(accepted[0]['files']), {'questions.pdf'})

    def test_unsafe_or_missing_filename_mapping_is_held(self):
        for index, value in enumerate(('../outside.pdf', '/tmp/outside.pdf', 'nested/file.pdf',
                                       'nested\\file.pdf', 'missing.pdf')):
            with self.subTest(value=value):
                folder = self.folder('PAPER-' + str(index))
                pdf(folder / 'Original.pdf')
                self.config(folder, {'questions': value})
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(all(e['reason'] for e in entries))
        self.assertFalse(self.root.exists())

    def test_invalid_role_or_value_types_are_held(self):
        cases = [({'other': 'Original.pdf'}, None),
                 ({'questions': ['Original.pdf']}, None),
                 ({'questions': 'Original.pdf'}, 'not-a-list'),
                 ({'questions': 'Original.pdf'}, [123]),
                 ({'questions': 'Original.pdf'}, ['../outside.pdf'])]
        for index, (mapping, ignored) in enumerate(cases):
            folder = self.folder('PAPER-' + str(index))
            pdf(folder / 'Original.pdf')
            self.config(folder, mapping, ignore=ignored)
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(all(e['reason'] for e in entries))

    def test_same_original_cannot_fill_two_roles_or_be_ignored(self):
        folder = self.folder('PAPER-A')
        question = pdf(folder / 'Original.pdf')
        self.config(folder, {'questions': question.name, 'answer_key': question.name})
        other = self.folder('PAPER-B')
        question2 = pdf(other / 'Original.pdf')
        self.config(other, {'questions': question2.name}, ignore=[question2.name])
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(all(e['reason'] for e in entries))

    def test_wrong_extension_role_and_symlink_mapping_are_held(self):
        folder = self.folder('PAPER-A')
        archive = export(folder / 'Accepted.zip')
        self.config(folder, {'questions': archive.name})
        other = self.folder('PAPER-B')
        question = pdf(other / 'Original.pdf')
        self.config(other, {'export': question.name})
        linked = self.folder('PAPER-C')
        external = pdf(self.base / 'outside.pdf')
        (linked / 'Linked.pdf').symlink_to(external)
        self.config(linked, {'questions': 'Linked.pdf'})
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(all(e['reason'] for e in entries))

    def test_corrupt_arbitrary_name_is_refused_before_processing(self):
        folder = self.folder('PAPER-A')
        (folder / 'Original corrupt.pdf').write_bytes(b'%PDF-1.7\ncorrupt data')
        other = self.folder('PAPER-B')
        (other / 'Original corrupt result.zip').write_bytes(b'bad zip')
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(all(e['reason'] for e in entries))
        self.assertFalse(self.root.exists())

    def test_original_name_upload_retry_reuses_crop_and_does_not_duplicate(self):
        folder = self.folder()
        question = pdf(folder / 'Question Paper Version 3.pdf')
        key = pdf(folder / 'Correct Responses Version 3.pdf')
        self.config(folder, {'questions': question.name, 'answer_key': key.name})
        original = self.snapshot(folder)

        class OfflineAPI(FakeAPI):
            def upload(self, files, message):
                raise w.WorkspaceError('Synthetic upload failure')

        with patch.object(w.subprocess, 'run', side_effect=self.cropper), \
                patch.object(w, 'GitHub', return_value=OfflineAPI()):
            first = w.run_batch(self.args(offline=False))
        self.assertEqual(first[0]['outcome'], 'upload failed/local-only')
        self.assertEqual(len(self.crops), 1)
        api = FakeAPI()
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No repeat cropping')), \
                patch.object(w, 'GitHub', return_value=api):
            second = w.run_batch(self.args(offline=False))
            third = w.run_batch(self.args(offline=False))
        self.assertEqual(second[0]['outcome'], 'synced')
        self.assertEqual(third[0]['outcome'], 'already processed')
        self.assertEqual(len(api.commit_trees), 1)
        self.assertEqual(len(list((self.root / 'PAPER-A/runs').iterdir())), 1)
        self.assertEqual(self.snapshot(folder), original)

    def test_actual_offline_cli_accepts_original_export_filename(self):
        folder = self.folder()
        archive = export(folder / 'Unrenamed result with spaces.zip')
        client = Path(w.__file__)
        result = subprocess.run([sys.executable, str(client), 'batch', str(self.source),
            '--root', str(self.root), '--offline'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('BATCH COMPLETE', result.stdout)
        saved = next((self.root / 'PAPER-A/runs').glob('*/export.zip'))
        self.assertEqual(saved.read_bytes(), archive.read_bytes())

    def test_interactive_choices_are_saved_outside_inputs_and_reused_without_prompts(self):
        folder = self.folder()
        question = pdf(folder / 'A Download original.pdf', 'Question')
        key = pdf(folder / 'B Download original.pdf', 'Key')
        solutions = pdf(folder / 'C Download original.pdf', 'Solutions')
        before = self.snapshot(folder)
        with patch('builtins.input', side_effect=['1', '2', '3']) as choose:
            selected = w.discover_batch(self.source, self.root, select_files=True)
        self.assertIsNone(selected[0]['reason'])
        self.assertEqual(choose.call_count, 3)
        self.assertEqual({role: Path(path).name for role, path in selected[0]['files'].items()}, {
            'questions.pdf': question.name, 'answer_key.pdf': key.name, 'solutions.pdf': solutions.name})
        self.assertEqual(self.snapshot(folder), before)
        self.assertFalse((self.root / 'PAPER-A').exists())
        self.assertTrue(any((self.root / '.batch_selections').glob('*.json')))
        with patch('builtins.input', side_effect=AssertionError('Saved selections must not reprompt')):
            repeated = w.discover_batch(self.source, self.root, select_files=True)
            headless = w.discover_batch(self.source, self.root)
        self.assertEqual(repeated[0]['files'], selected[0]['files'])
        self.assertEqual(headless[0]['files'], selected[0]['files'])
        self.assertEqual(self.snapshot(folder), before)

    def test_changed_saved_input_fingerprint_is_held_or_explicitly_reselected(self):
        folder = self.folder()
        pdf(folder / 'A Download.pdf', 'Question')
        key = pdf(folder / 'B Download.pdf', 'Key')
        pdf(folder / 'C Download.pdf', 'Solutions')
        with patch('builtins.input', side_effect=['1', '2', '3']):
            first = w.discover_batch(self.source, self.root, select_files=True)
        self.assertIsNone(first[0]['reason'])
        pdf(key, 'Changed key contents')
        stale = w.discover_batch(self.source, self.root)
        self.assertTrue(stale[0]['reason'])
        with patch('builtins.input', side_effect=['1', '2', '3']) as choose:
            refreshed = w.discover_batch(self.source, self.root, select_files=True)
        self.assertIsNone(refreshed[0]['reason'])
        self.assertEqual(choose.call_count, 3)
        self.assertEqual(refreshed[0]['hashes']['answer_key.pdf'], w.sha(key.read_bytes()))

    def test_added_document_invalidates_saved_association_inventory(self):
        folder = self.folder()
        pdf(folder / 'A Download.pdf')
        pdf(folder / 'B Download.pdf')
        with patch('builtins.input', side_effect=['1', '2', '']):
            first = w.discover_batch(self.source, self.root, select_files=True)
        self.assertIsNone(first[0]['reason'])
        pdf(folder / 'C New document.pdf')
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(entries[0]['reason'])

    def test_interactive_leftover_requires_explicit_ignore_confirmation(self):
        folder = self.folder()
        pdf(folder / 'A Download.pdf')
        pdf(folder / 'B Download.pdf')
        pdf(folder / 'C Download.pdf')
        before = self.snapshot(folder)
        with patch('builtins.input', side_effect=['1', '2', '', 'y']):
            entries = w.discover_batch(self.source, self.root, select_files=True)
        self.assertIsNone(entries[0]['reason'])
        self.assertEqual(set(entries[0]['files']), {'questions.pdf', 'answer_key.pdf'})
        self.assertEqual(self.snapshot(folder), before)
        with patch('builtins.input', side_effect=AssertionError('Remember confirmed ignored document')):
            repeated = w.discover_batch(self.source, self.root, select_files=True)
        self.assertIsNone(repeated[0]['reason'])

    def test_interactive_declined_ignore_holds_paper(self):
        folder = self.folder()
        pdf(folder / 'A Download.pdf')
        pdf(folder / 'B Download.pdf')
        pdf(folder / 'C Download.pdf')
        with patch('builtins.input', side_effect=['1', '2', '', '']):
            entries = w.discover_batch(self.source, self.root, select_files=True)
        self.assertTrue(entries[0]['reason'])
        self.assertFalse((self.root / 'PAPER-A').exists())

    def test_selection_directory_symlink_cannot_receive_saved_choices(self):
        folder = self.folder()
        pdf(folder / 'A Download.pdf')
        pdf(folder / 'B Download.pdf')
        outside = self.base / 'Outside cache'
        outside.mkdir()
        self.root.mkdir()
        (self.root / '.batch_selections').symlink_to(outside, target_is_directory=True)
        with patch('builtins.input', side_effect=['1', '2', '']):
            try:
                entries = w.discover_batch(self.source, self.root, select_files=True)
            except w.WorkspaceError:
                entries = None
        if entries is not None:
            self.assertTrue(entries[0]['reason'])
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.root / 'PAPER-A').exists())

    def test_word_only_paper_is_held_but_word_original_beside_pdf_is_untouched(self):
        only_word = self.folder('PAPER-A')
        (only_word / 'Paper downloaded from Word.docx').write_bytes(b'Synthetic unsupported DOCX')
        folder = self.folder('PAPER-B')
        pdf(folder / 'questions.pdf')
        (folder / 'Paper downloaded from Word.docx').write_bytes(b'Synthetic Word original')
        before = self.snapshot(folder)
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(entries[0]['reason'])
        self.assertIn('docx', entries[0]['reason'].lower())
        self.assertIsNone(entries[1]['reason'])
        self.assertEqual(set(entries[1]['files']), {'questions.pdf'})
        self.assertEqual(self.snapshot(folder), before)
        self.assertFalse(self.root.exists())
    def test_multiple_arbitrary_exports_are_held_without_selection(self):
        folder = self.folder()
        export(folder / 'A accepted result.zip')
        export(folder / 'B accepted result.zip')
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(entries[0]['reason'])
        self.assertFalse(self.root.exists())


    def test_special_file_is_refused_without_attempting_to_read_it(self):
        folder = self.folder()
        fifo = folder / 'Original named pipe.pdf'
        os.mkfifo(fifo)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Never read special input files')):
            entries = w.discover_batch(self.source, self.root)
        self.assertTrue(entries[0]['reason'])
        self.assertFalse(self.root.exists())

    def test_saved_selection_file_symlink_is_refused_without_reprompt(self):
        folder = self.folder()
        pdf(folder / 'A Download.pdf')
        pdf(folder / 'B Download.pdf')
        with patch('builtins.input', side_effect=['1', '2', '']):
            selected = w.discover_batch(self.source, self.root, select_files=True)
        self.assertIsNone(selected[0]['reason'])
        cache = next((self.root / '.batch_selections').glob('*.json'))
        saved = cache.read_bytes()
        external = self.base / 'external selection.json'
        external.write_bytes(saved)
        cache.unlink()
        cache.symlink_to(external)
        with patch('builtins.input', side_effect=AssertionError('Unsafe cache cannot prompt/write')):
            entries = w.discover_batch(self.source, self.root, select_files=True)
        self.assertTrue(entries[0]['reason'])
        self.assertEqual(external.read_bytes(), saved)



if __name__ == '__main__':
    unittest.main()
