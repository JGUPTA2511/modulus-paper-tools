"""Synthetic inventory tests for an explicitly declared complete-paper collection."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_batch import pdf
from test_workspace import export, w


class AllPaperInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'Mixed Papers With Spaces '
        self.source.mkdir()
        self.root = self.base / 'Generated Modulus Papers'

    def scan(self):
        return w.discover_all_papers(self.source, self.root)

    def test_flat_and_nested_papers_all_accounted_without_creating_jobs(self):
        pdf(self.source / 'Original Main paper.pdf')
        nested = self.source / 'Advanced'
        nested.mkdir()
        pdf(nested / 'Original Advanced paper.pdf')
        archive = export(nested / 'Existing result.zip')
        original = archive.read_bytes()
        rows = self.scan()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row['reason'] is None for row in rows))
        self.assertEqual({row['source_relative_path'] for row in rows}, {
            'Original Main paper.pdf', 'Advanced/Original Advanced paper.pdf',
            'Advanced/Existing result.zip'})
        self.assertEqual({row['profile'] for row in rows}, {'unspecified'})
        self.assertFalse(self.root.exists())
        self.assertEqual(archive.read_bytes(), original)
        self.assertTrue(all(row['files'] for row in rows))
        self.assertTrue(all(row['hashes'] for row in rows))

    def test_duplicate_normalized_ids_hold_every_colliding_source(self):
        pdf(self.source / 'Paper A.pdf')
        pdf(self.source / 'PAPER-A.pdf')
        rows = self.scan()
        self.assertEqual({row['paper_id'] for row in rows}, {'PAPER-A'})
        self.assertTrue(all('Duplicate paper ID' in row['reason'] for row in rows))

    def test_same_filename_in_different_directories_has_stable_distinct_id(self):
        for name in ('Main', 'Advanced'):
            folder = self.source / name
            folder.mkdir()
            pdf(folder / 'Paper.pdf')
        rows = self.scan()
        self.assertEqual({row['paper_id'] for row in rows}, {'MAIN-PAPER', 'ADVANCED-PAPER'})
        self.assertTrue(all(row['reason'] is None for row in rows))

    def test_changed_original_keeps_id_and_changes_hash_for_immutable_job_check(self):
        path = pdf(self.source / 'Paper 1.pdf', 'First source')
        first = self.scan()[0]
        pdf(path, 'Changed source')
        second = self.scan()[0]
        self.assertEqual(first['paper_id'], second['paper_id'])
        self.assertNotEqual(first['hashes'], second['hashes'])

    def test_same_stem_pdf_and_export_are_held_instead_of_recropping_or_guessing(self):
        pdf(self.source / 'Paper 1.pdf')
        export(self.source / 'Paper 1.zip')
        rows = self.scan()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all('associate them explicitly' in row['reason'] for row in rows))
        self.assertFalse(self.root.exists())

    def test_word_original_beside_same_name_pdf_does_not_hold_the_pdf(self):
        paper = pdf(self.source / 'Original paper.pdf')
        original_pdf = paper.read_bytes()
        word = self.source / 'Original paper.docx'
        word.write_bytes(b'untouched Word original')
        legacy_word = self.source / 'Original paper.doc'
        legacy_word.write_bytes(b'untouched legacy Word original')
        rows = {row['source_relative_path']: row for row in self.scan()}
        self.assertIsNone(rows['Original paper.pdf']['reason'])
        self.assertEqual(rows['Original paper.pdf']['paper_id'], 'ORIGINAL-PAPER')
        self.assertEqual(rows['Original paper.docx']['paper_id'], 'ORIGINAL-PAPER-DOCX')
        self.assertEqual(rows['Original paper.doc']['paper_id'], 'ORIGINAL-PAPER-DOC')
        self.assertIn('Word documents need a PDF copy', rows['Original paper.docx']['reason'])
        self.assertIn('Word documents need a PDF copy', rows['Original paper.doc']['reason'])
        self.assertEqual(paper.read_bytes(), original_pdf)
        self.assertEqual(word.read_bytes(), b'untouched Word original')
        self.assertEqual(legacy_word.read_bytes(), b'untouched legacy Word original')

    def test_unicode_and_long_names_produce_safe_stable_bounded_ids(self):
        folder = self.source / ('A' * 90)
        folder.mkdir()
        pdf(folder / 'Original paper.pdf')
        pdf(self.source / '\u6570\u5b66\u8bd5\u5377.pdf')
        rows = self.scan()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertLessEqual(len(row['paper_id']), 80)
            self.assertEqual(w.paper_id(row['paper_id']), row['paper_id'])
        self.assertEqual([row['paper_id'] for row in rows], [row['paper_id'] for row in self.scan()])

    def test_real_hidden_papers_in_hidden_folders_are_not_silently_omitted(self):
        folder = self.source / '.originals'
        folder.mkdir()
        pdf(self.source / '.Paper.pdf')
        pdf(folder / '.Nested paper.pdf')
        rows = self.scan()
        self.assertEqual({row['source_relative_path'] for row in rows}, {
            '.Paper.pdf', '.originals/.Nested paper.pdf'})
        self.assertTrue(all(row['reason'] is None for row in rows))

    def test_known_metadata_directories_are_excluded(self):
        pdf(self.source / 'Actual paper.pdf')
        for name in ('.git', '.venv', '__pycache__', '__MACOSX'):
            folder = self.source / name
            folder.mkdir()
            (folder / 'metadata.pdf').write_bytes(b'not a paper')
        (self.source / '.DS_Store').write_bytes(b'Finder metadata')
        rows = self.scan()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source_relative_path'], 'Actual paper.pdf')

    def test_symlink_file_and_directory_are_visible_held_and_never_followed(self):
        external = self.base / 'External'
        external.mkdir()
        path = pdf(external / 'Private source.pdf')
        (self.source / 'Linked paper.pdf').symlink_to(path)
        (self.source / 'Linked folder').symlink_to(external, target_is_directory=True)
        with patch.object(w, 'strict_pdf', side_effect=AssertionError('Never read linked sources')):
            rows = self.scan()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all('Symlink' in row['reason'] for row in rows))
        self.assertFalse(any('Private source' in row['source_relative_path'] for row in rows))
        self.assertFalse(self.root.exists())

    def test_corrupt_oversized_and_word_inputs_hold_without_stopping_valid_papers(self):
        pdf(self.source / 'Valid.pdf')
        (self.source / 'Corrupt.pdf').write_bytes(b'%PDF-1.7\ninvalid')
        with (self.source / 'Oversized.pdf').open('wb') as stream:
            stream.truncate(w.MAX_FILE + 1)
        (self.source / 'Broken export.zip').write_bytes(b'not a zip')
        (self.source / 'Original Word.docx').write_bytes(b'original untouched')
        rows = {row['source_relative_path']: row for row in self.scan()}
        self.assertEqual(len(rows), 5)
        self.assertIsNone(rows['Valid.pdf']['reason'])
        self.assertIn('corrupt', rows['Corrupt.pdf']['reason'])
        self.assertIn('24 MiB', rows['Oversized.pdf']['reason'])
        self.assertIn('Invalid export', rows['Broken export.zip']['reason'])
        self.assertIn('Word documents need a PDF copy', rows['Original Word.docx']['reason'])
        self.assertEqual((self.source / 'Original Word.docx').read_bytes(), b'original untouched')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO test')
    def test_special_file_is_held_before_any_read(self):
        os.mkfifo(self.source / 'Paper.pdf')
        with patch.object(w.Path, 'read_bytes', side_effect=AssertionError('No FIFO read')):
            row = self.scan()[0]
        self.assertIn('special', row['reason'])

    def test_source_and_output_overlap_in_either_direction_are_refused(self):
        pdf(self.source / 'Paper.pdf')
        for root in (self.source, self.source / 'Generated', self.base):
            with self.assertRaises(w.WorkspaceError):
                w.discover_all_papers(self.source, root)
        alias = self.base / 'Alias'
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(w.WorkspaceError):
            w.discover_all_papers(alias, self.root)

    def test_parent_inventory_matches_existing_batch_change_check(self):
        pdf(self.source / 'Paper.pdf')
        (self.source / 'notes.txt').write_text('retained notes')
        (self.source / '.DS_Store').write_text('metadata')
        row = self.scan()[0]
        self.assertEqual(row['folder'], str(self.source.resolve()))
        self.assertEqual(row['inventory'], ['Paper.pdf', 'notes.txt'])
        self.assertEqual(row['discovery_mode'], 'complete_paper_files')

    def test_no_source_candidates_has_clear_error(self):
        (self.source / 'notes.txt').write_text('not a source paper')
        with self.assertRaisesRegex(w.WorkspaceError, 'No PDF'):
            self.scan()


if __name__ == '__main__':
    unittest.main()
