"""Existing cropper geometry with synthetic source text and PIL page pixels only."""
import argparse
import ast
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('source_compat_cropper', ROOT / 'question_boundaries.py')
cropper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cropper)


# AST hashes from the accepted cropper. Tests work without Git or a source checkout.
PRESERVED_FUNCTIONS = {
    'render_pdf': '2e99db83973fde1b4cad856d429c2a32bcae2e7fc08500016b013fceacbd9345',
    'clean_image': 'e4463465ee7e50d79b4f97f73bc3c1a0892ac3a06a096ad0d88f897a086e1cd4',
    'detect_bands': 'c6be00aca7ad4c251941fbd49c5a0269d8dbe0a70eec492593e2705f59acb46c',
    'ink_mask': '375588d21bcb155077a768dbbe9c04d02dcc100c0b4e315c2dd04f8479a43857',
    'ink_rows': '0a0f6f8f269156d3a4a68b6c2b1508c3c19ec116a4affad4d6e8c1cf30b949d7',
    'trim_box': '2db70b63d27ccaddac6a3857557949abd0729338890a8a921b3ccc51db81cbfd',
    'make_crops': '2c0ab0687cc78d7078622f7d6f10003a9645f87cdc08e4c989ab20618b90ca9b',
}


def word(text, x, y, width=25):
    return {'text': text, 'x0': x, 'x1': x + width,
            'top': y, 'bottom': y + 12}


def page(numbers=(), *, text='', continuation=False):
    words = []
    pixels = Image.new('RGB', (600, 800), 'white')
    draw = ImageDraw.Draw(pixels)
    for offset, number in enumerate(numbers):
        y = 100 + offset * 140
        words += [word(str(number) + '.', 50, y, 20),
                  word('Synthetic question statement', 85, y, 180)]
        draw.rectangle((50, y, 70, y + 11), fill='black')
        # Character-like strokes span the line. A single solid long rectangle
        # would be correctly stripped as a page rule by the original ink mask.
        for x in range(85, 550, 24):
            draw.rectangle((x, y + 2, min(x + 13, 555), y + 10), fill='black')
        for label in range(1, 5):
            # These attached labels are source text; no token/box is invented.
            x = 105 if label % 2 else 365
            oy = y + 25 + 14 * ((label - 1) // 2)
            words.append(word(str(label) + ')value', x, oy, 70))
            draw.rectangle((x, oy, x + 70, oy + 10), fill='black')
            # A distinct isolated option population competes with singleton
            # number columns in the old per-page column selection.
            words.append(word(str(label) + ')', x + 12, oy + 55, 16))
    if continuation:
        words.append(word('Synthetic continued diagram question statement', 80, 70, 300))
        for label in range(1, 5):
            words.append(word(str(label) + ')diagram', 85 if label % 2 else 365,
                              180 + label * 40, 80))
        draw.rectangle((65, 70, 560, 700), outline='black', width=4)
        draw.line((75, 75, 550, 685), fill='black', width=3)
    # Keep these synthetic text-bearing pages out of OCR, as actual digital
    # sources use text directly. This filler never resembles a number anchor.
    for i in range(30):
        words.append(word('synthetic', 80 + i * 4, 740, 20))
    return {'width': 600, 'height': 800, 'words': words,
            'text': text, 'pixels': pixels}


def source():
    return [
        page(range(1, 5), text=(
            '_JEE_Main_Practice_\nIMPORTANT INSTRUCTIONS\n'
            'The Test Booklet consists of 12 Questions.')),
        page(range(1, 5), text='SECTION-I\nSINGLE CORRECT ANSWER TYPE'),
        page([5], text='Synthetic singleton question page'),
        page(range(6, 9), text='Synthetic question page'),
        page(text='Synthetic continued question diagrams', continuation=True),
        page(range(9, 13), text='Synthetic question page'),
    ]


class CropperSourceCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def detect(self, pages, *, enabled=True):
        files = []
        fake_pages = []
        for i, item in enumerate(pages):
            path = self.base / f'p{i + 1:02d}.png'
            item['pixels'].save(path)
            files.append(path)
            fake_pages.append(SimpleNamespace(
                width=item['width'], height=item['height'],
                extract_text=lambda item=item: item['text'],
                extract_words=lambda item=item, **kwargs: deepcopy(item['words'])))
        args = argparse.Namespace(source_compat=enabled, dpi=72,
                                  skip_top=0.04, skip_bottom=0.04, pad=14)
        with patch('pdfplumber.open', return_value=nullcontext(SimpleNamespace(pages=fake_pages))), \
             patch.object(cropper, 'ocr_words', side_effect=AssertionError('Synthetic source has text')), \
             patch.object(cropper, 'recover_margin_numbers', side_effect=AssertionError('Keep proved source anchors')):
            result = cropper.detect(self.base / 'Synthetic original.pdf', files, args)
        return args, result

    def test_proven_margin_keeps_singleton_glued_options_and_whole_continuation(self):
        args, (pages, questions, key) = self.detect(source())
        self.assertIsNotNone(args._source_layout)
        self.assertEqual([q['number'] for q in questions], list(range(1, 13)))
        self.assertEqual(args._source_layout['continuation_page_indexes'], [4])
        self.assertEqual(pages[2]['ncols'], 1)
        self.assertEqual(pages[4]['ncols'], 1)
        self.assertTrue(all(q['options_found'] == 4 for q in questions))
        self.assertTrue(all(not any('missing_numbers_before' in f for f in q['flags'])
                            for q in questions))
        eighth = questions[7]
        continued = [part for part in eighth['parts'] if part['page'] == 4]
        self.assertEqual(len(continued), 1)
        self.assertTrue(continued[0]['continuation'])
        self.assertLessEqual(continued[0]['box'][0], 65)
        self.assertGreaterEqual(continued[0]['box'][2], 560)
        self.assertGreaterEqual(continued[0]['box'][3] - continued[0]['box'][1], 600)
        # Full-width prose ink remains inside every first question part rather
        # than being cut into competing option-label columns.
        self.assertTrue(all(q['parts'][0]['box'][2] >= 550 for q in questions))
        self.assertEqual(cropper.gate(pages, questions, {n: '0' for n in range(1, 13)}), [])
        self.assertEqual(key, {})

    def test_short_proven_source_pages_keep_original_anchors_without_ocr_recovery(self):
        pages = source()
        for index in (2, 4):
            pages[index]['words'] = [w for w in pages[index]['words'] if w['text'] != 'synthetic']
            self.assertLess(len(pages[index]['words']), 25)
        args, (detected_pages, questions, _) = self.detect(pages)
        self.assertIsNotNone(args._source_layout)
        self.assertEqual([q['number'] for q in questions], list(range(1, 13)))
        self.assertEqual(detected_pages[2]['source'], 'text')
        self.assertEqual(detected_pages[4]['source'], 'text')
        self.assertEqual([p['page'] for p in questions[7]['parts'] if p['continuation']], [4])

    def test_missing_source_number_is_not_invented_to_reach_declared_count(self):
        pages = source()
        pages[2] = page(text='Synthetic page with no original question anchor')
        args, (_, questions, _) = self.detect(pages)
        self.assertIsNone(args._source_layout)
        self.assertNotIn(5, [q['number'] for q in questions])
        self.assertNotEqual([q['number'] for q in questions], list(range(1, 13)))

    def test_optional_source_parser_failure_retains_original_local_ocr_fallback(self):
        item = page([1, 2], text='Synthetic unreadable text layer')
        item['words'] = [w for w in item['words'] if ')' not in w['text']]
        path = self.base / 'Synthetic scan.png'
        item['pixels'].save(path)
        args = argparse.Namespace(source_compat=True, dpi=72,
                                  skip_top=0.04, skip_bottom=0.04, pad=14)
        with patch('pdfplumber.open', side_effect=ValueError('Synthetic text parser failure')) as opened, \
             patch.object(cropper, 'ocr_words', return_value=deepcopy(item['words'])) as ocr, \
             patch.object(cropper, 'recover_margin_numbers', return_value=[]):
            pages, questions, _ = cropper.detect(self.base / 'Synthetic scan.pdf', [path], args)
        self.assertIsNone(args._source_layout)
        self.assertEqual(args._source_inspection_error, 'ValueError')
        self.assertEqual(opened.call_count, 2)
        ocr.assert_called_once()
        self.assertEqual(pages[0]['source'], 'ocr')
        self.assertEqual([q['number'] for q in questions], [1, 2])

    def test_clean_render_mask_trim_and_crop_functions_match_accepted_cropper(self):
        tree = ast.parse((ROOT / 'question_boundaries.py').read_text())
        actual = {node.name: hashlib.sha256(
            ast.dump(node, include_attributes=False).encode()).hexdigest()
                  for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name in PRESERVED_FUNCTIONS}
        self.assertEqual(actual, PRESERVED_FUNCTIONS)


if __name__ == '__main__':
    unittest.main()
