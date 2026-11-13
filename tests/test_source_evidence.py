"""Synthetic source evidence; no OCR, source-answer edits or verified scoring."""
from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_batch import pdf
from test_workspace import export, w


class SourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def source(self, title, name='Original paper.pdf'):
        return pdf(self.base / name, title)

    def archive(self, **fields):
        path = self.base / 'Original export.zip'
        data = {'count': 1, 'questions': [
            {'number': 1, 'image': 'crops/Q001.png', 'answer': 0}]}
        data.update(fields)
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('exam.json', json.dumps(data))
        return path

    def pages(self, texts):
        return nullcontext(SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda value=text: value)
                                                  for text in texts]))

    def test_main_title_identifies_existing_main_pattern_without_inventing_rules(self):
        path = self.source('JEE Main MOCK')
        original = path.read_bytes()
        result = w.inspect_exam_source(source=path)
        self.assertEqual(result['exam_family'], 'jee-main')
        self.assertEqual(result['exam_pattern'], 'jee-main')
        self.assertEqual(result['source_pdf_sha256'], w.sha(original))
        self.assertEqual(result['family_evidence'][0]['page'], 1)
        self.assertIsNone(result['verified_rules'])
        self.assertFalse(result['launch_ready'])
        self.assertNotIn('requested_scoring', result)
        self.assertEqual(path.read_bytes(), original)

    def test_advanced_source_parts_and_both_use_existing_patterns(self):
        for i, (title, pattern, part) in enumerate([
            ('JEE Advanced Paper I', 'jee-advanced-1', 'paper_1'),
            ('JEE Advanced Paper II', 'jee-advanced-2', 'paper_2'),
            ('JEE Advanced Paper 1', 'jee-advanced-1', 'paper_1'),
            ('JEE Advanced Paper 2', 'jee-advanced-2', 'paper_2'),
            ('JEE Advanced Both Papers', 'jee-advanced-both', 'both'),
            ('JEE Advanced Paper I and Paper II', 'jee-advanced-both', 'both'),
        ]):
            with self.subTest(title=title):
                result = w.inspect_exam_source(source=self.source(title, str(i) + '.pdf'))
                self.assertEqual(result['exam_family'], 'jee-advanced')
                self.assertEqual(result['exam_pattern'], pattern)
                self.assertEqual(result['advanced_paper'], part)
                self.assertEqual(result['marking_state'], 'source_validation_required')
                self.assertIsNone(result['verified_rules'])

    def test_advanced_without_source_part_never_defaults_to_paper_one(self):
        result = w.inspect_exam_source(source=self.source('JEE Advanced MOCK'))
        self.assertEqual(result['exam_family'], 'jee-advanced')
        self.assertIsNone(result['advanced_paper'])
        self.assertIsNone(result['exam_pattern'])
        self.assertFalse(result['launch_ready'])

    def test_general_both_papers_instruction_does_not_override_single_paper_title(self):
        source = self.source('Synthetic backing PDF')
        text = 'JEE Advanced Paper I\nGeneral instructions\nBoth papers are compulsory\n'
        with patch('pdfplumber.open', return_value=self.pages([text])):
            result = w.inspect_exam_source(source=source)
        self.assertEqual(result['exam_pattern'], 'jee-advanced-1')
        self.assertEqual(result['advanced_paper'], 'paper_1')

    def test_filename_and_numeric_answers_never_select_family(self):
        path = self.source('Synthetic unlabeled source', 'JEE Advanced Paper 1.pdf')
        self.assertEqual(w.inspect_exam_source(source=path)['exam_family'], 'unknown')
        archive = self.archive(questions=[
            {'number': 1, 'image': 'crops/Q001.png', 'answer': 0},
            {'number': 2, 'image': 'crops/Q002.png', 'answer': '1'},
            {'number': 3, 'image': 'crops/Q003.png', 'answer': 'A,C'}])
        original = archive.read_bytes()
        result = w.inspect_exam_source(export=archive)
        self.assertEqual(result['exam_family'], 'unknown')
        self.assertEqual(archive.read_bytes(), original)
        with zipfile.ZipFile(archive) as z:
            self.assertEqual([q['answer'] for q in json.loads(z.read('exam.json'))['questions']], [0, '1', 'A,C'])

    def test_mixed_main_advanced_title_remains_unclassified(self):
        for i, title in enumerate(('JEE Main and JEE Advanced', 'J.E.E. Main and J.E.E. Advanced')):
            result = w.inspect_exam_source(source=self.source(title, str(i) + '.pdf'))
            self.assertEqual(result['exam_family'], 'unknown')
            self.assertIsNone(result['exam_pattern'])
            self.assertTrue(result['errors'])

    def test_export_explicit_patterns_and_vault_part_mapping(self):
        for fields, family, pattern in [
            ({'exam_pattern': 'jee-main'}, 'jee-main', 'jee-main'),
            ({'exam_pattern': 'jee_main'}, 'jee-main', 'jee-main'),
            ({'exam_pattern': 'jee-advanced-2'}, 'jee-advanced', 'jee-advanced-2'),
            ({'exam_pattern': 'jee_advanced', 'advanced_paper': 'paper_1'}, 'jee-advanced', 'jee-advanced-1'),
            ({'exam_pattern': 'jee_advanced', 'advanced_paper': 'single'}, 'jee-advanced', None),
        ]:
            with self.subTest(fields=fields):
                result = w.inspect_exam_source(export=self.archive(**fields))
                self.assertEqual(result['exam_family'], family)
                self.assertEqual(result['exam_pattern'], pattern)
                self.assertEqual(result['evidence_type'], 'export_metadata_unverified')
                self.assertIsNone(result['verified_rules'])

    def test_question_metadata_can_supply_family_but_mixed_families_do_not(self):
        result = w.inspect_exam_source(export=self.archive(questions=[
            {'number': 1, 'answer': 0, 'exam_pattern': 'jee-advanced-1'}]))
        self.assertEqual(result['exam_pattern'], 'jee-advanced-1')
        result = w.inspect_exam_source(export=self.archive(questions=[
            {'number': 1, 'answer': 0, 'exam_pattern': 'jee-main'},
            {'number': 2, 'answer': 'A,C', 'exam_pattern': 'jee-advanced-2'}]))
        self.assertEqual(result['exam_family'], 'unknown')
        self.assertIsNone(result['exam_pattern'])
        self.assertTrue(result['errors'])

    def test_two_advanced_parts_need_explicit_combined_metadata(self):
        rows = [{'number': 1, 'answer': 'A,C', 'exam_pattern': 'jee-advanced-1'},
                {'number': 2, 'answer': 0, 'exam_pattern': 'jee-advanced-2'}]
        result = w.inspect_exam_source(export=self.archive(questions=rows))
        self.assertEqual(result['exam_family'], 'jee-advanced')
        self.assertIsNone(result['exam_pattern'])
        result = w.inspect_exam_source(export=self.archive(exam_pattern='jee-advanced-both', questions=rows))
        self.assertEqual(result['exam_pattern'], 'jee-advanced-both')

    def test_pdf_export_family_or_part_conflicts_remain_held(self):
        source = self.source('JEE Main MOCK')
        archive = self.archive(exam_pattern='jee-advanced-2')
        result = w.inspect_exam_source(source=source, export=archive)
        self.assertEqual(result['exam_family'], 'unknown')
        self.assertTrue(any('conflict' in reason for reason in result['errors']))
        source = self.source('JEE Advanced Paper 1')
        result = w.inspect_exam_source(source=source, export=archive)
        self.assertEqual(result['exam_family'], 'jee-advanced')
        self.assertIsNone(result['exam_pattern'])
        self.assertIsNone(result['advanced_paper'])
        self.assertFalse(result['launch_ready'])

    def test_source_instruction_text_is_preserved_with_page_and_hash(self):
        source = self.source('Synthetic backing PDF')
        text = ('JEE Advanced Paper II\nSECTION 1\nFull marks: +4; incorrect: -2; unattempted: 0\n'
                'Partial credit: +1 per correct option; 0 if no choice.\nTime allowed: 3 hours\n')
        with patch('pdfplumber.open', return_value=self.pages([text])):
            result = w.inspect_exam_source(source=source)
        self.assertEqual(result['instruction_evidence'][0]['text'], text)
        self.assertEqual(result['instruction_evidence'][0]['page'], 1)
        self.assertEqual(result['instruction_evidence'][0]['document_sha256'], w.sha(source.read_bytes()))
        self.assertIsNone(result['verified_rules'])
        self.assertFalse(result['launch_ready'])

    def test_all_inspected_pages_contribute_instruction_evidence(self):
        source = self.source('Synthetic backing PDF')
        with patch('pdfplumber.open', return_value=self.pages([
            'JEE Advanced Paper I\nSECTION 1\nFull marks +3\n',
            'SECTION 2\nPartial marks +1; negative marks -2\n'])):
            result = w.inspect_exam_source(source=source)
        self.assertEqual(result['total_pages'], 2)
        self.assertEqual(result['pages_examined'], 2)
        self.assertEqual([item['page'] for item in result['instruction_evidence']], [1, 2])

    def test_scanned_source_without_text_stays_pending_without_ocr(self):
        source = self.source('Synthetic backing PDF')
        with patch('pdfplumber.open', return_value=self.pages([None])), \
             patch.object(w.subprocess, 'run', side_effect=AssertionError('No OCR/render calls')):
            result = w.inspect_exam_source(source=source)
        self.assertEqual(result['exam_family'], 'unknown')
        self.assertEqual(result['evidence_type'], 'pdf_text_unavailable')
        self.assertEqual(result['status'], 'evidence_pending')
        self.assertFalse(result['launch_ready'])

    def test_evidence_limits_are_explicit_and_never_cut_a_marking_instruction(self):
        source = self.source('Synthetic backing PDF')
        with patch('pdfplumber.open', return_value=self.pages([
            'JEE Main\nCorrect +4; incorrect -1\n', 'SECTION 2\nCorrect +4\n', 'SECTION 3\nCorrect +4\n'])), \
             patch.object(w, 'SOURCE_EVIDENCE_MAX_PAGES', 2), \
             patch.object(w, 'SOURCE_EVIDENCE_MAX_SNIPPETS', 1):
            result = w.inspect_exam_source(source=source)
        self.assertEqual(result['total_pages'], 3)
        self.assertEqual(result['pages_examined'], 2)
        self.assertEqual(len(result['instruction_evidence']), 1)
        self.assertIn('pages_limit', result['truncation_reasons'])
        self.assertIn('snippets_limit', result['truncation_reasons'])
        self.assertTrue(result['truncated'])
        with patch('pdfplumber.open', return_value=self.pages(['JEE Main\nCorrect +4; incorrect -1\n'])), \
             patch.object(w, 'SOURCE_EVIDENCE_MAX_CHARS', 5):
            result = w.inspect_exam_source(source=source)
        self.assertEqual(result['instruction_evidence'], [])
        self.assertIn('characters_limit', result['truncation_reasons'])

    def test_corrupt_or_oversized_sources_return_pending_error_without_raising(self):
        source = self.base / 'corrupt.pdf'
        source.write_bytes(b'%PDF-1.7\nnot a real PDF')
        result = w.inspect_exam_source(source=source)
        self.assertTrue(result['errors'])
        self.assertFalse(result['launch_ready'])
        with patch.object(w, 'MAX_FILE', 1):
            result = w.inspect_exam_source(source=source)
        self.assertIsNone(result['source_pdf_sha256'])
        self.assertTrue(result['errors'])

    def test_source_symlink_is_not_followed(self):
        source = self.source('JEE Main')
        link = self.base / 'linked.pdf'
        link.symlink_to(source)
        with patch.object(w, 'input_pdf', side_effect=AssertionError('Do not follow symlinks')) as validate:
            result = w.inspect_exam_source(source=link)
        self.assertEqual(validate.call_count, 0)
        self.assertEqual(result['exam_family'], 'unknown')
        self.assertIsNone(result['source_pdf_sha256'])
        self.assertTrue(result['errors'])

    def test_unsafe_or_corrupt_exports_do_not_select_a_pattern(self):
        path = self.base / 'broken.zip'
        path.write_bytes(b'broken')
        result = w.inspect_exam_source(export=path)
        self.assertEqual(result['exam_family'], 'unknown')
        self.assertTrue(result['errors'])
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('exam.json', json.dumps({'exam_pattern': 'jee-main'}))
            z.writestr('../unsafe', 'not extracted')
        result = w.inspect_exam_source(export=path)
        self.assertEqual(result['exam_family'], 'unknown')
        self.assertTrue(result['errors'])

    def test_no_source_never_invents_default_interface_or_marking(self):
        result = w.inspect_exam_source()
        self.assertEqual(result['exam_family'], 'unknown')
        self.assertIsNone(result['exam_pattern'])
        self.assertIsNone(result['verified_rules'])
        self.assertFalse(result['launch_ready'])


if __name__ == '__main__':
    unittest.main()
