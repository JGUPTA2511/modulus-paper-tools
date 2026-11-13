"""Synthetic source-key grids and correction sheets; no real paper fixtures."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('source_keys', ROOT / 'source_keys.py')
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)


def word(text, center, top, width=None):
    width = width if width is not None else max(5, len(text) * 4)
    return {'text': text, 'x0': center - width / 2, 'x1': center + width / 2,
            'top': top, 'bottom': top + 8}


def grid_page(*, paper='SYN-24', day='10-02-2026', answers=None):
    answers = answers or {n: str((n % 4) + 1) for n in range(1, 21)}
    words = []
    for index, (number, answer) in enumerate(answers.items()):
        row, column = divmod(index, 5)
        words += [word(str(number), 60 + column * 90, 70 + row * 16),
                  word(answer, 90 + column * 90, 70 + row * 16)]
    return {'page': 1, 'words': words,
            'text': f'10-02-26 JEE Main {paper}_KEY\nDate: {day}\nKEY SHEET'}


def correction_page(rows=((5, '2', '3'),), *, paper='SYN-24', day='10-02-2026'):
    words = [word('S.NO', 25, 50), word('SUB', 55, 50), word('Q.NO', 91, 50),
             word('GIVEN KEY', 150, 50), word('FINALIZED KEY', 245, 50),
             word('EXPLANATION', 410, 50)]
    for index, (number, given, finalized) in enumerate(rows, 1):
        top = 50 + index * 15
        words.extend([word(str(index), 25, top), word('SUB', 55, top),
                      word(str(number), 91, top), word(given, 150, top),
                      word(finalized, 245, top), word('Source explanation', 410, top)])
    return {'page': 1, 'words': words,
            'text': f'{day} JEE Main {paper}_Final Key\nS.NO SUB Q.NO GIVEN KEY FINALIZED KEY EXPLANATION'}


def pdf_from_words(path, page):
    """One-page proper-xref PDF with generic positioned source text."""
    stream = []
    for w in page['words']:
        escaped = w['text'].replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        stream.append(f"BT /F1 8 Tf {w['x0']} {760 - w['top']} Td ({escaped}) Tj ET\n")
    for line_no, line in enumerate(page.get('text', '').splitlines()[:2]):
        escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        stream.append(f'BT /F1 8 Tf 30 {790 - line_no * 10} Td ({escaped}) Tj ET\n')
    data = ''.join(stream).encode('ascii')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 820] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(data)).encode() + b' >>\nstream\n' + data + b'endstream']
    body = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for n, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body.extend(str(n).encode() + b' 0 obj\n' + obj + b'\nendobj\n')
    xref = len(body)
    body.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        body.extend(f'{offset:010d} 00000 n \n'.encode())
    body.extend(f'trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    path.write_bytes(body)
    return path


class SourceKeyTests(unittest.TestCase):
    def setUp(self):
        self.base = k.read_key_pages([grid_page()], source_sha256='base-hash')

    def corrections(self, *rows, **kwargs):
        return k.read_key_pages([correction_page(rows, **kwargs)], source_sha256='final-hash')

    def test_complete_legacy_grid_keeps_all_answers_and_provenance(self):
        self.assertEqual(len(self.base['key']), 20)
        self.assertFalse(self.base['correction_sheet'])
        self.assertEqual(self.base['key'][1], '2')
        self.assertEqual(self.base['key_evidence'][1], {'page': 1, 'source_sha256': 'base-hash'})
        self.assertFalse(self.base['unresolved'])

    def test_correction_columns_cannot_become_an_alternating_complete_key(self):
        def forbidden(_words):
            raise AssertionError('Never parse correction serial/given/final cells as a key grid')
        result = k.read_key_pages([correction_page()], forbidden)
        self.assertTrue(result['correction_sheet'])
        self.assertEqual(result['key'], {})
        self.assertEqual(result['corrections'][0]['number'], 5)
        self.assertEqual(result['corrections'][0]['given'], '2')
        self.assertEqual(result['corrections'][0]['finalized'], '3')

    def test_sparse_final_correction_requires_matching_base_then_applies(self):
        final = self.corrections((5, '2', '3'))
        before_base, before_final = deepcopy(self.base), deepcopy(final)
        result = k.combine_keys(self.base, final, expected_paper_id='SYN-24', expected_numbers=range(1, 21))
        self.assertEqual(result['key'][5], '3')
        self.assertEqual(len(result['key']), 20)
        self.assertFalse(result['unresolved'])
        self.assertEqual(result['corrections'][0]['page'], 1)
        self.assertEqual(result['corrections'][0]['source_sha256'], 'final-hash')
        self.assertEqual(self.base, before_base)
        self.assertEqual(final, before_final)

    def test_sparse_final_without_base_cannot_manufacture_other_answers(self):
        final = self.corrections((5, '2', '3'))
        for base, chosen in [(None, final), (final, None)]:
            result = k.combine_keys(base, chosen)
            self.assertEqual(result['key'], {})
            self.assertTrue(any('requires its complete base' in issue for issue in result['unresolved']))

    def test_finalized_zero_and_numeric_answer_strings_are_preserved(self):
        final = self.corrections((5, '2', '0'), (6, '3', '1.25'))
        result = k.combine_keys(self.base, final)
        self.assertEqual(result['key'][5], '0')
        self.assertEqual(result['key'][6], '1.25')
        self.assertFalse(result['unresolved'])
        self.assertNotIn('question_type', result)

    def test_unknown_finalized_marker_never_falls_back_to_old_answer(self):
        final = self.corrections((5, '2', 'Add'))
        result = k.combine_keys(self.base, final)
        self.assertEqual(result['key'][5], 'Add')
        self.assertTrue(any('unresolved finalized source marker' in issue for issue in result['unresolved']))
        self.assertEqual(result['corrections'][0]['finalized'], 'Add')
        self.assertNotIn('bonus', result)

    def test_other_unsupported_finalized_values_remain_unresolved(self):
        final = self.corrections((5, '2', '1,2'))
        result = k.combine_keys(self.base, final)
        self.assertEqual(result['key'][5], '1,2')
        self.assertTrue(result['unresolved'])

    def test_owner_original_policy_only_adjudicates_exact_add_marker(self):
        final = self.corrections((5, '2', 'Add'), (6, '3', 'ADD'), (7, '4', '0'))
        original = deepcopy(final)
        result = k.combine_keys(self.base, final, add_policy='original')
        self.assertEqual(result['key'][5], '2')
        self.assertEqual(result['key'][6], '3')
        self.assertEqual(result['key'][7], '0')
        self.assertFalse(result['unresolved'])
        self.assertEqual(result['adjudication_policy'],
                         {'add': 'original', 'basis': 'owner_selected_original_for_add'})
        row = result['corrections'][0]
        self.assertEqual(row['finalized'], 'Add')
        self.assertEqual(row['base_answer'], '2')
        self.assertEqual(row['effective_answer'], '2')
        self.assertEqual(row['adjudication_basis'], 'owner_selected_original_for_add')
        self.assertEqual(row['source_sha256'], 'final-hash')
        self.assertEqual(row['page'], 1)
        self.assertEqual(final, original)
        self.assertEqual(result['corrections'][2]['adjudication_basis'], 'explicit_finalized_source_value')

    def test_owner_original_policy_preserves_original_numerical_zero(self):
        base = deepcopy(self.base)
        base['key'][5] = '0'
        final = self.corrections((5, '0', 'add'))
        result = k.combine_keys(base, final, add_policy='original')
        self.assertEqual(result['key'][5], '0')
        self.assertEqual(result['corrections'][0]['effective_answer'], '0')
        self.assertFalse(result['unresolved'])

    def test_owner_original_policy_cannot_override_mismatched_or_duplicate_rows(self):
        for final in (self.corrections((5, '4', 'Add')),
                      self.corrections((5, '2', 'Add'), (5, '2', 'Add'))):
            result = k.combine_keys(self.base, final, add_policy='original')
            self.assertNotIn(5, result['key'])
            self.assertTrue(result['unresolved'])

    def test_owner_original_policy_does_not_reinterpret_other_source_markers(self):
        result = k.combine_keys(self.base, self.corrections((5, '2', 'Added')), add_policy='original')
        self.assertEqual(result['key'][5], 'Added')
        self.assertTrue(result['unresolved'])
        self.assertIsNone(result['corrections'][0]['effective_answer'])

    def test_unsupported_owner_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            k.combine_keys(self.base, add_policy='bonus')

    def test_given_mismatch_removes_stale_base_answer(self):
        result = k.combine_keys(self.base, self.corrections((5, '4', '3')))
        self.assertNotIn(5, result['key'])
        self.assertTrue(any('given key does not match' in issue for issue in result['unresolved']))

    def test_correction_for_absent_question_is_held(self):
        result = k.combine_keys(self.base, self.corrections((90, '1', '2')))
        self.assertNotIn(90, result['key'])
        self.assertTrue(any('absent from the base key' in issue for issue in result['unresolved']))

    def test_duplicate_identical_or_conflicting_corrections_are_held(self):
        for replacement in ('3', '4'):
            final = self.corrections((5, '2', '3'), (5, '2', replacement))
            result = k.combine_keys(self.base, final)
            self.assertNotIn(5, result['key'])
            self.assertTrue(any('duplicate correction' in issue for issue in result['unresolved']))

    def test_incomplete_correction_cells_are_held_without_old_answer(self):
        result = k.combine_keys(self.base, self.corrections((5, '2', '')))
        self.assertNotIn(5, result['key'])
        self.assertTrue(any('incomplete correction' in issue for issue in result['unresolved']))

    def test_complete_final_key_is_preferred_independently_of_base(self):
        final = k.read_key_pages([grid_page(answers={n: '0' for n in range(1, 21)})])
        for base in (self.base, None):
            result = k.combine_keys(base, final)
            self.assertEqual(result['key'], {n: '0' for n in range(1, 21)})
            self.assertFalse(result['unresolved'])

    def test_expected_numbers_prevent_short_or_extra_keys_from_passing(self):
        for numbers in (range(1, 22), range(1, 20)):
            result = k.combine_keys(self.base, expected_numbers=numbers)
            self.assertTrue(any('expected question set' in issue for issue in result['unresolved']))

    def test_printed_paper_id_date_and_family_conflicts_are_held(self):
        for final in (self.corrections((5, '2', '3'), paper='SYN-25'),
                      self.corrections((5, '2', '3'), day='11-02-2026')):
            result = k.combine_keys(self.base, final)
            self.assertTrue(any('mismatch' in issue for issue in result['unresolved']))
            self.assertEqual(result['key'], {})
        final = self.corrections((5, '2', '3'))
        final['identity']['exam_families'] = ['jee-advanced']
        self.assertTrue(any('exam family mismatch' in issue for issue in k.combine_keys(self.base, final)['unresolved']))

    def test_printed_question_identity_is_checked_independently_of_job_version(self):
        identity = deepcopy(self.base['identity'])
        result = k.combine_keys(self.base, expected_identity=identity)
        self.assertEqual(result['key'], self.base['key'])
        self.assertFalse(result['identity_issues'])
        identity['paper_ids'] = ['SYN-25']
        result = k.combine_keys(self.base, expected_identity=identity)
        self.assertEqual(result['key'], {})
        self.assertTrue(result['identity_issues'])

    def test_expected_paper_id_is_exact_and_never_fuzzy(self):
        for expected in ('SYN-2', 'SYN-240'):
            result = k.combine_keys(self.base, expected_paper_id=expected)
            self.assertTrue(any('selected paper ID' in issue for issue in result['unresolved']))

    def test_missing_identity_does_not_invent_identity_from_filename(self):
        page = grid_page()
        page['text'] = 'KEY SHEET'
        base = k.read_key_pages([page], source_name='SYN-99_Final Key.pdf')
        self.assertEqual(base['identity']['paper_ids'], [])
        self.assertFalse(k.combine_keys(base, expected_paper_id='SYN-24')['unresolved'])

    def test_date_normalization_and_cohort_codes_do_not_change_paper_identity(self):
        page = grid_page()
        page['text'] = '10-02-26_Cohort_AB-500_JEE-Main_SYN-24_KEY\nDate: 10-02-2026'
        identity = k.identity_from_pages([page])
        self.assertEqual(identity['paper_ids'], ['SYN-24'])
        self.assertEqual(identity['dates'], ['2026-02-10'])
        self.assertEqual(identity['exam_families'], ['jee-main'])

    def test_split_correction_header_tokens_and_letter_wrappers(self):
        page = correction_page(((5, '(B)', '(C)'),))
        page['words'] = [w for w in page['words'] if w['top'] != 50] + [
            word('S.NO', 25, 50), word('Q.', 84, 50), word('NO', 99, 50),
            word('GIVEN', 140, 50), word('KEY', 162, 50),
            word('FINALISED', 230, 50), word('KEY', 264, 50)]
        final = k.read_key_pages([page])
        base = deepcopy(self.base)
        base['key'][5] = 'B'
        result = k.combine_keys(base, final)
        self.assertEqual(result['key'][5], '(C)')
        self.assertFalse(result['unresolved'])

    def test_duplicate_base_pages_are_held(self):
        base = k.read_key_pages([grid_page(), dict(grid_page(), page=2)])
        self.assertFalse(base['key'])
        self.assertTrue(any('duplicate base key' in issue for issue in base['unresolved']))

    def test_legacy_ocr_fallback_is_only_for_same_unparsed_noncorrection_source(self):
        unread = k.read_key_pages([{'words': [], 'text': ''}])
        result = k.combine_keys(final=unread)
        self.assertTrue(result['legacy_fallback_allowed'])
        self.assertEqual(unread['textless_pages'], [1])
        sparse = k.combine_keys(self.base, self.corrections((5, '2', 'Add')))
        self.assertFalse(sparse['legacy_fallback_allowed'])
        unread['identity']['paper_ids'] = ['SYN-25']
        result = k.combine_keys(self.base, unread)
        self.assertFalse(result['legacy_fallback_allowed'])

    def test_unsupported_nonfinite_base_values_cannot_be_complete_keys(self):
        def invalid(_words):
            return {n: (float('nan') if n == 5 else '1') for n in range(1, 21)}
        base = k.read_key_pages([grid_page()], invalid)
        self.assertNotIn(5, base['key'])
        self.assertTrue(any('unsupported base key' in issue for issue in base['unresolved']))

    def test_synthetic_pdfs_are_read_unchanged_without_ocr_or_rendering(self):
        with tempfile.TemporaryDirectory() as temp:
            base_path = pdf_from_words(Path(temp) / 'Any base name.pdf', grid_page())
            final_path = pdf_from_words(Path(temp) / 'Any final name.pdf', correction_page())
            before = [path.read_bytes() for path in (base_path, final_path)]
            result = k.read_key_sources(base_path, final_path, expected_paper_id='SYN-24', expected_numbers=range(1, 21))
            self.assertEqual(result['key'][5], '3')
            self.assertFalse(result['unresolved'])
            self.assertEqual(before, [path.read_bytes() for path in (base_path, final_path)])
            self.assertTrue(all(source['sha256'] for source in result['sources']))


if __name__ == '__main__':
    unittest.main()
