"""Explicit same-ID companion associations; fixtures contain no real papers."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from test_batch import pdf
from test_workspace import export, w


class CompanionGroupingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'Mixed Original Papers '
        self.source.mkdir()
        self.root = self.base / 'Generated Jobs'

    def paper(self, name, title='Synthetic fixture', directory=None):
        return pdf((directory or self.source) / name, title)

    def scan(self):
        return w.discover_companion_papers(self.source, self.root)

    def by_id(self):
        return {item['paper_id']: item for item in self.scan()}

    def test_exact_prefixes_pair_question_with_combined_key_and_solutions(self):
        for pid in ('GTM-2', 'GTM-20', 'GTM-28'):
            self.paper(pid + '_QP.PDF')
            self.paper(pid + '_KEY-&-SOLS.PDF')
        self.assertTrue(w.has_companion_papers(self.source))
        rows = self.by_id()
        self.assertEqual(set(rows), {'GTM-2', 'GTM-20', 'GTM-28'})
        for pid, row in rows.items():
            self.assertIsNone(row['reason'])
            self.assertEqual(Path(row['files']['questions.pdf']).name, pid + '_QP.PDF')
            self.assertEqual(Path(row['files']['answer_key.pdf']).name, pid + '_KEY-&-SOLS.PDF')
            self.assertEqual(row['files']['answer_key.pdf'], row['files']['solutions.pdf'])
            self.assertEqual(row['hashes']['answer_key.pdf'], row['hashes']['solutions.pdf'])
            self.assertEqual(row['source_association']['answer_key_basis'], 'combined_key_solutions')
        self.assertFalse(self.root.exists())

    def test_final_key_is_selected_and_combined_original_remains_solutions(self):
        self.paper('GTM-29_QP.PDF')
        final = self.paper('GTM-29_FINAL-KEY.PDF')
        combined = self.paper('GTM-29_KEY-&-SOLS.PDF')
        row = self.scan()[0]
        self.assertIsNone(row['reason'])
        self.assertEqual(row['files']['answer_key.pdf'], str(final))
        self.assertEqual(row['files']['solutions.pdf'], str(combined))
        self.assertEqual(row['source_association']['answer_key_basis'], 'final_key')
        self.assertEqual(row['source_association']['final_key_filename'], final.name)
        self.assertEqual(row['source_association']['combined_key_solutions_filename'], combined.name)
        self.assertEqual(row['source_association']['filenames']['answer_key.pdf'], final.name)

    def test_case_underscore_roles_and_unicode_paper_dashes_keep_original_names(self):
        question = self.paper('gtm–28_qp.pdf')
        final = self.paper('GTM-28_final_key.PdF')
        combined = self.paper('GTM‑28_key_&_sols.pdf')
        row = self.scan()[0]
        self.assertEqual(row['paper_id'], 'GTM-28')
        self.assertIsNone(row['reason'])
        self.assertEqual(row['files']['questions.pdf'], str(question))
        self.assertEqual(row['files']['answer_key.pdf'], str(final))
        self.assertEqual(row['files']['solutions.pdf'], str(combined))
        self.assertTrue(all(path.exists() for path in (question, final, combined)))

    def test_canonical_only_tree_does_not_trigger_companion_detection(self):
        leaf = self.source / 'PAPER-01'
        leaf.mkdir()
        for name in ('questions.pdf', 'answer_key.pdf', 'solutions.pdf'):
            self.paper(name, directory=leaf)
        export(leaf / 'export.zip')
        self.assertFalse(w.has_companion_papers(self.source))

    def test_duplicate_final_or_question_role_holds_entire_group(self):
        self.paper('GTM-28_QP.pdf')
        self.paper('gtm-28_qp.PDF')
        self.paper('GTM-28_KEY-&-SOLS.pdf')
        self.paper('GTM-29_QP.pdf')
        self.paper('GTM-29_FINAL_KEY.pdf')
        self.paper('GTM-29_FINAL-KEY.pdf')
        rows = self.by_id()
        self.assertTrue(all('Duplicate/ambiguous' in row['reason'] for row in rows.values()))
        self.assertFalse(self.root.exists())

    def test_orphan_companions_are_not_treated_as_question_papers(self):
        self.paper('GTM-28_KEY-&-SOLS.pdf')
        self.paper('GTM-29_FINAL-KEY.pdf')
        rows = self.scan()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all('no same-ID QP' in row['reason'] for row in rows))

    def test_duplicate_ids_across_folders_are_held_without_cross_association(self):
        for dirname in ('One', 'Two'):
            folder = self.source / dirname
            folder.mkdir()
            self.paper('GTM-28_QP.pdf', directory=folder)
            self.paper('GTM-28_KEY-&-SOLS.pdf', directory=folder)
        rows = self.scan()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all('Duplicate paper ID' in row['reason'] for row in rows))

    def test_qp_and_key_in_different_folders_never_pair(self):
        one, two = self.source / 'One', self.source / 'Two'
        one.mkdir(); two.mkdir()
        self.paper('GTM-28_QP.pdf', directory=one)
        self.paper('GTM-28_FINAL-KEY.pdf', directory=two)
        rows = self.scan()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all('Duplicate paper ID' in row['reason'] for row in rows))

    def test_unknown_loose_pdf_and_word_are_visible_held_beside_valid_paper(self):
        self.paper('GTM-28_QP.pdf')
        self.paper('GTM-28_KEY-&-SOLS.pdf')
        self.paper('Unlabelled source.pdf')
        (self.source / 'Original Word.docx').write_bytes(b'Word original')
        rows = self.scan()
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(row['reason'] is None for row in rows), 1)
        self.assertTrue(any('no question/key/solutions role' in (row['reason'] or '') for row in rows))
        self.assertTrue(any('Word documents' in (row['reason'] or '') for row in rows))

    def test_export_group_uses_original_archive_and_optional_qp_without_recropping(self):
        question = self.paper('GTM-28_QP.pdf')
        archive = export(self.source / 'GTM-28_RESULT.zip')
        original = archive.read_bytes()
        row = self.scan()[0]
        self.assertIsNone(row['reason'])
        self.assertEqual(row['files']['export.zip'], str(archive))
        self.assertEqual(row['files']['questions.pdf'], str(question))
        self.assertEqual(archive.read_bytes(), original)
        self.assertFalse(self.root.exists())

    def test_archive_can_be_a_standalone_named_source(self):
        archive = export(self.source / 'GTM-28_EXPORT.zip')
        row = self.scan()[0]
        self.assertIsNone(row['reason'])
        self.assertEqual(row['files'], {'export.zip': str(archive)})

    def test_corrupt_and_oversized_companions_hold_only_their_paper(self):
        self.paper('GTM-28_QP.pdf')
        (self.source / 'GTM-28_FINAL-KEY.pdf').write_bytes(b'%PDF-1.7\ncorrupt')
        self.paper('GTM-29_QP.pdf')
        with (self.source / 'GTM-29_KEY-&-SOLS.pdf').open('wb') as stream:
            stream.truncate(w.MAX_FILE + 1)
        self.paper('GTM-30_QP.pdf')
        rows = self.by_id()
        self.assertIn('corrupt', rows['GTM-28']['reason'])
        self.assertIn('24 MiB', rows['GTM-29']['reason'])
        self.assertIsNone(rows['GTM-30']['reason'])

    def test_symlink_sources_and_directories_are_visible_and_never_followed(self):
        outside = self.base / 'Outside'
        outside.mkdir()
        target = self.paper('Actual.pdf', directory=outside)
        (self.source / 'GTM-28_QP.pdf').symlink_to(target)
        (self.source / 'Linked folder').symlink_to(outside, target_is_directory=True)
        rows = self.scan()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all('Symlink' in row['reason'] for row in rows))
        self.assertFalse(self.root.exists())

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'FIFO unavailable')
    def test_fifo_named_qp_is_held_without_reading(self):
        os.mkfifo(self.source / 'GTM-28_QP.pdf')
        row = self.scan()[0]
        self.assertIn('special source file', row['reason'])
        self.assertFalse(self.root.exists())

    def test_independent_unused_key_is_held_instead_of_dropped(self):
        self.paper('GTM-28_QP.pdf')
        self.paper('GTM-28_FINAL-KEY.pdf')
        self.paper('GTM-28_ANSWER_KEY.pdf')
        row = self.scan()[0]
        self.assertIn('Additional independent key', row['reason'])

    def test_unneeded_combined_solution_with_standalone_solutions_is_held(self):
        for name in ('GTM-28_QP.pdf','GTM-28_FINAL-KEY.pdf','GTM-28_KEY-&-SOLS.pdf','GTM-28_SOLUTIONS.pdf'):
            self.paper(name)
        row = self.scan()[0]
        self.assertIn('Additional independent key/solutions', row['reason'])

    def test_combined_key_and_standalone_solutions_preserve_both_originals(self):
        self.paper('GTM-28_QP.pdf')
        combined = self.paper('GTM-28_KEY-&-SOLS.pdf')
        solutions = self.paper('GTM-28_SOLUTIONS.pdf')
        row = self.scan()[0]
        self.assertIsNone(row['reason'])
        self.assertEqual(row['files']['answer_key.pdf'], str(combined))
        self.assertEqual(row['files']['solutions.pdf'], str(solutions))

    def test_canonical_leaf_groups_and_config_remain_supported_alongside_flat_groups(self):
        self.paper('GTM-28_QP.pdf')
        self.paper('GTM-28_KEY-&-SOLS.pdf')
        leaf = self.source / 'Other Paper'
        leaf.mkdir()
        original = self.paper('Original question.pdf', directory=leaf)
        (leaf / 'paper.json').write_text(json.dumps({'paper_id': 'OTHER-01', 'profile':'jee-advanced-source',
                                                   'files':{'questions':original.name}}))
        rows = self.by_id()
        self.assertEqual(set(rows), {'GTM-28','OTHER-01'})
        self.assertIsNone(rows['OTHER-01']['reason'])
        self.assertTrue(rows['OTHER-01']['profile_explicit'])
        self.assertEqual(rows['OTHER-01']['profile'], 'jee-advanced-source')
        self.assertEqual(rows['OTHER-01']['files']['questions.pdf'], str(original))

    def test_explicit_mapping_precedes_arbitrary_role_shaped_filenames(self):
        self.paper('GTM-28_QP.pdf')
        leaf = self.source / 'PAPER-01'
        leaf.mkdir()
        question = self.paper('My original.pdf', directory=leaf)
        key = self.paper('FINAL_KEY.pdf', directory=leaf)
        (leaf / 'paper.json').write_text(json.dumps({'paper_id':'CUSTOM-01',
            'files':{'questions':question.name,'answer_key':key.name}}))
        rows = self.by_id()
        self.assertEqual(set(rows), {'GTM-28','CUSTOM-01'})
        self.assertIsNone(rows['CUSTOM-01']['reason'])
        self.assertEqual(rows['CUSTOM-01']['files']['answer_key.pdf'], str(key))

    def test_single_prefixed_group_retains_explicit_paper_id_and_profile(self):
        self.paper('GTM-28_QP.pdf')
        self.paper('GTM-28_KEY-&-SOLS.pdf')
        (self.source / 'paper.json').write_text(json.dumps({'paper_id':'OWNER-28','profile':'unspecified'}))
        row = self.scan()[0]
        self.assertEqual(row['paper_id'], 'OWNER-28')
        self.assertEqual(row['source_association']['paper_id'], 'OWNER-28')
        self.assertTrue(row['profile_explicit'])
        self.assertIsNone(row['reason'])

    def test_canonical_question_key_export_leaf_without_config_is_preserved(self):
        self.paper('GTM-28_QP.pdf')
        leaf = self.source / 'OTHER-01'
        leaf.mkdir()
        for name in ('questions.pdf','answer_key.pdf','solutions.pdf'):
            self.paper(name, directory=leaf)
        archive = export(leaf / 'export.zip')
        rows = self.by_id()
        self.assertIsNone(rows['OTHER-01']['reason'])
        self.assertEqual(set(rows['OTHER-01']['files']), {'questions.pdf','answer_key.pdf','solutions.pdf','export.zip'})
        self.assertEqual(rows['OTHER-01']['files']['export.zip'], str(archive))

    def test_discovery_is_read_only_and_preserves_every_original_byte(self):
        for name in ('GTM-28_QP.pdf','GTM-28_KEY-&-SOLS.pdf','GTM-29_QP.pdf','GTM-29_FINAL-KEY.pdf','GTM-29_KEY-&-SOLS.pdf'):
            self.paper(name)
        before = {path.name:path.read_bytes() for path in self.source.iterdir()}
        self.scan()
        self.assertEqual({path.name:path.read_bytes() for path in self.source.iterdir()}, before)
        self.assertFalse(self.root.exists())

    def test_wrong_format_and_invalid_identifier_are_held(self):
        export(self.source / 'GTM-28_QP.zip')
        self.paper('bad!id_QP.pdf')
        rows = self.scan()
        self.assertTrue(all(row['reason'] for row in rows))
        self.assertTrue(any('Wrong file format' in row['reason'] for row in rows))
        self.assertTrue(any('Invalid paper identifier' in row['reason'] for row in rows))

    def test_overlapping_input_output_and_root_symlink_are_rejected(self):
        self.paper('GTM-28_QP.pdf')
        with self.assertRaises(w.WorkspaceError):
            w.discover_companion_papers(self.source, self.source / 'Generated')
        link = self.base / 'Source Alias'
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(w.WorkspaceError):
            w.discover_companion_papers(link, self.root)


if __name__ == '__main__':
    unittest.main()
