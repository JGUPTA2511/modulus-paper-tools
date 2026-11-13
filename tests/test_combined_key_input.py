"""Synthetic probes of the unchanged combined-key reader and source headings."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from test_workspace import w


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'unchanged_cropper_combined_key_regression', ROOT / 'question_boundaries.py')
CROPPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CROPPER)


def words(tokens):
    return [{'text': token, 'x0': index * 25, 'x1': index * 25 + 20,
             'top': 10, 'bottom': 20} for index, token in enumerate(tokens)]


def key_words(count=15, answer='2', first_zero=False):
    tokens = []
    for number in range(1, count + 1):
        tokens.extend([str(number) + ')', '0' if first_zero and number == 1 else answer])
    # Keep the text-layer sample above the cropper's OCR fallback threshold.
    tokens.extend(['Synthetic', 'source', 'answer', 'key', 'header', 'label', 'text'])
    return words(tokens)


class CombinedKeyInputTests(unittest.TestCase):
    def read_pages(self, pages):
        synthetic_images = [Path('/tmp/synthetic-key-page-' + str(i) + '.png')
                            for i in range(len(pages))]
        with patch.object(CROPPER, 'render_pdf', return_value=synthetic_images), \
             patch.object(CROPPER, 'text_layer_words', side_effect=pages), \
             patch.object(CROPPER, 'ocr_words', side_effect=AssertionError('No OCR in this text-layer test')):
            return CROPPER.key_from_file('/tmp/synthetic-combined-input.pdf', 120)

    def test_combined_pdf_reads_key_preserves_zero_and_skips_solution_only_page(self):
        printed_key = key_words(first_zero=True)
        solution_only = words(['Worked', 'solution', 'explanation'] * 12)
        result = self.read_pages([printed_key, solution_only])
        self.assertEqual(len(result), 15)
        self.assertEqual(result[1], '0')
        self.assertEqual(result, CROPPER.parse_key(printed_key))

    def test_later_qualifying_key_grid_overwrites_earlier_values(self):
        # Documents why a supplied FINAL-KEY must be selected explicitly rather
        # than mixed with a potentially older key grid in the solutions PDF.
        result = self.read_pages([key_words(first_zero=True), key_words(answer='4')])
        self.assertEqual(len(result), 15)
        self.assertEqual(result[1], '4')
        self.assertTrue(all(answer == '4' for answer in result.values()))

    def test_combined_reader_requires_at_least_ten_parsed_entries_on_a_page(self):
        self.assertEqual(self.read_pages([key_words(count=9)]), {})
        self.assertEqual(len(self.read_pages([key_words(count=10)])), 10)

    def test_parenthesized_main_title_selects_existing_main_pattern(self):
        for heading in ('JEE (Main) 2026', 'JEE(Main) MOCK', 'J.E.E. (MAIN)'):
            with self.subTest(heading=heading):
                family, pattern, part, evidence = w._source_title_intent(heading)
                self.assertEqual((family, pattern, part), ('jee-main', 'jee-main', None))
                self.assertEqual(evidence, [heading])

    def test_parenthesized_advanced_title_keeps_explicit_paper_two(self):
        for heading in ('JEE (Advanced) 2026\nPaper II', 'JEE(Advanced)\nPaperII'):
            with self.subTest(heading=heading):
                family, pattern, part, _ = w._source_title_intent(heading)
                self.assertEqual((family, pattern, part),
                                 ('jee-advanced', 'jee-advanced-2', 'paper_2'))

    def test_slash_combined_family_title_is_unresolved(self):
        family, pattern, part, _ = w._source_title_intent('JEE (Main/Advanced) practice')
        self.assertEqual((family, pattern, part), ('unknown', None, None))

    def test_mixed_main_and_advanced_title_is_unresolved(self):
        for heading in ('JEE Main & Advanced practice', 'JEE (Main) and JEE (Advanced)'):
            with self.subTest(heading=heading):
                family, pattern, part, _ = w._source_title_intent(heading)
                self.assertEqual((family, pattern, part), ('unknown', None, None))


if __name__ == '__main__':
    unittest.main()
