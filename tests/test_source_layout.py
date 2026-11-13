"""Synthetic layout calibration; no source papers, crops or answer keys."""
from copy import deepcopy
import re
import unittest

from source_layout import (calibrate_single_column, option_prefix_label,
                           source_question_types, trusted_candidates)


def word(text, x, y):
    return {'text': text, 'x0': x, 'x1': x + 12, 'top': y, 'bottom': y + 10}


def candidates(words):
    result = []
    for item in words:
        match = re.fullmatch(r'(\d{1,3})[.)]', item['text'])
        if match:
            result.append({'n': int(match.group(1)), 'x': item['x0'],
                           'y': item['top'], 'h': item['bottom'] - item['top']})
    return result


def page(numbers=(), *, width=600, height=800, margin=50, text=''):
    words = []
    for i, number in enumerate(numbers):
        y = 100 + i * 100
        words.extend([word(str(number) + '.', margin, y),
                      word('Synthetic question statement', margin + 30, y)])
        # Frequent option-label columns must not outweigh the genuine margin.
        for label in range(1, 5):
            words.append(word(str(label) + ')', 100 if label % 2 else 350,
                              y + 15 + label * 5))
    return {'width': width, 'height': height, 'words': words, 'text': text}


def source():
    cover = page(range(1, 6), text=(
        '_JEE_Main_Practice_\nIMPORTANT INSTRUCTIONS\n'
        'The Test Booklet consists of 12 Questions.'))
    first = page(range(1, 5), text='SECTION-I\n(SINGLE CORRECT ANSWER TYPE)')
    middle = page(range(5, 9), text='Synthetic Main practice continuation')
    last = page(range(9, 13), text='Synthetic Main practice continuation')
    return [cover, first, middle, last]


class SourceLayoutTests(unittest.TestCase):
    def test_unique_complete_margin_beats_frequent_option_columns(self):
        pages = source()
        original = deepcopy(pages)
        result = calibrate_single_column(pages, candidates)
        self.assertIsNotNone(result)
        self.assertEqual(result['expected_count'], 12)
        self.assertEqual(result['exam_family'], 'jee-main')
        self.assertEqual(result['first_page_index'], 1)
        self.assertEqual(result['last_page_index'], 3)
        self.assertEqual([a['n'] for items in result['anchors_by_page'] for a in items],
                         list(range(1, 13)))
        self.assertEqual(result['anchors_by_page'][0], [])
        self.assertAlmostEqual(result['margin_ratio'], 50 / 600)
        self.assertEqual(pages, original)
        self.assertEqual(result['declaration_evidence'][0]['text'],
                         'The Test Booklet consists of 12 Questions.')

    def test_singleton_question_page_is_retained_with_its_original_coordinates(self):
        pages = source()
        pages[2:3] = [page([5]), page(range(6, 9))]
        result = calibrate_single_column(pages, candidates)
        self.assertEqual(trusted_candidates(result, 2),
                         [{'n': 5, 'x': 50, 'y': 100, 'h': 10}])
        copied = trusted_candidates(result, 2)
        copied[0]['n'] = 999
        self.assertEqual(trusted_candidates(result, 2)[0]['n'], 5)

    def test_explicit_option_continuation_between_real_question_pages_is_retained(self):
        pages = source()
        continuation = page(text='Synthetic continued answer diagrams')
        continuation['words'] = [word(str(n) + ')', 100, n * 50) for n in range(1, 5)]
        pages.insert(3, continuation)
        result = calibrate_single_column(pages, candidates)
        self.assertIsNotNone(result)
        self.assertEqual(result['continuation_page_indexes'], [3])
        self.assertEqual(result['anchors_by_page'][3], [])
        self.assertEqual(result['continuation_evidence'][3]['option_labels'],
                         ['1', '2', '3', '4'])
        self.assertFalse(result['tail_pages_verified'])

    def test_missing_question_is_not_synthesized(self):
        pages = source()
        pages[2] = page([5, 6, 8])
        self.assertIsNone(calibrate_single_column(pages, candidates))

    def test_duplicate_and_restarted_numbering_are_not_accepted(self):
        for numbers in ([5, 6, 6, 7, 8], [1, 2, 3, 4]):
            with self.subTest(numbers=numbers):
                pages = source()
                pages[2] = page(numbers)
                self.assertIsNone(calibrate_single_column(pages, candidates))

    def test_extra_question_beyond_declared_total_is_held(self):
        pages = source()
        pages[-1] = page([9, 10, 11, 12, 13])
        self.assertIsNone(calibrate_single_column(pages, candidates))

    def test_two_complete_margins_are_ambiguous(self):
        pages = source()
        for p in pages[1:]:
            p['words'].extend({**w, 'x0': w['x0'] + 200, 'x1': w['x1'] + 200}
                              for w in list(p['words']) if w['text'].endswith('.'))
        self.assertIsNone(calibrate_single_column(pages, candidates))

    def test_shifted_margin_and_changed_page_size_fall_back(self):
        shifted = source()
        shifted[2] = page(range(5, 9), margin=70)
        widened = source()
        widened[2] = page(range(5, 9), width=700, margin=50 * 700 / 600)
        for pages in (shifted, widened):
            self.assertIsNone(calibrate_single_column(pages, candidates))

    def test_no_source_count_or_conflicting_count_falls_back(self):
        pages = source()
        pages[0]['text'] = '_JEE_Main_Practice_'
        self.assertIsNone(calibrate_single_column(pages, candidates))
        pages = source()
        pages[0]['text'] += '\nTotal questions: 15'
        self.assertIsNone(calibrate_single_column(pages, candidates))

    def test_unknown_and_conflicting_family_are_not_calibrated(self):
        for family in ('Unlabelled practice', 'JEE Main and JEE Advanced',
                       'JEE Main & Advanced', 'JEE (Main/Advanced)'):
            pages = source()
            pages[0]['text'] = family + '\nIMPORTANT INSTRUCTIONS\nTotal Questions: 12'
            self.assertIsNone(calibrate_single_column(pages, candidates))

    def test_advanced_numerical_sequence_is_source_proven_without_option_labels(self):
        pages = source()
        pages[0]['text'] = 'JEE (Advanced)\nIMPORTANT INSTRUCTIONS\nTotal Questions: 12'
        pages[1]['text'] = 'SECTION-I\nINTEGER ANSWER TYPE'
        for p in pages[1:]:
            p['words'] = [w for w in p['words'] if not re.fullmatch(r'[1-4]\)', w['text'])]
        self.heading(pages[1], '(INTEGER ANSWER TYPE)')
        original = deepcopy(pages)
        layout = calibrate_single_column(pages, candidates)
        self.assertIsNotNone(layout)
        self.assertEqual(layout['exam_family'], 'jee-advanced')
        self.assertEqual([a['n'] for items in layout['anchors_by_page'] for a in items], list(range(1, 13)))
        types = source_question_types(pages, layout)
        self.assertEqual([types[n]['source_type'] for n in range(1, 13)], ['numerical'] * 12)
        self.assertEqual(types[1]['source_section']['text'], '(INTEGER ANSWER TYPE)')
        self.assertFalse(any(field in layout for field in ('duration_minutes', 'marks', 'advanced_paper')))
        self.assertEqual(pages, original)

    def test_advanced_multi_correct_and_integer_headings_preserve_response_metadata(self):
        for multi_heading in ('MULTIPLE CORRECT ANSWER TYPE', 'MULTIPLE CORRECT TYPE'):
            with self.subTest(heading=multi_heading):
                pages = source()
                pages[0]['text'] = 'JEE Advanced Paper II\nIMPORTANT INSTRUCTIONS\nTotal Questions: 12'
                pages[1]['text'] = 'SECTION-I\n' + multi_heading
                self.heading(pages[1], multi_heading)
                self.heading(pages[3], 'INTEGER TYPE')
                layout = calibrate_single_column(pages, candidates)
                self.assertEqual(layout['exam_family'], 'jee-advanced')
                types = source_question_types(pages, layout)
                self.assertEqual([types[n]['source_type'] for n in range(1, 13)],
                                 ['multiple_correct'] * 8 + ['numerical'] * 4)
                self.assertEqual(types[1]['source_section']['text'], multi_heading)
                for i, p in enumerate(pages):
                    p['answer'] = [i % 4 + 1, (i + 1) % 4 + 1]
                self.assertEqual(source_question_types(pages, layout), types)

    def test_advanced_restarted_numbers_and_unsupported_first_section_remain_held(self):
        pages = source()
        pages[0]['text'] = 'JEE Advanced\nIMPORTANT INSTRUCTIONS\nTotal Questions: 12'
        pages[1]['text'] = 'SECTION-I\nNUMERICAL VALUE TYPE'
        pages[2] = page([1, 2, 3, 4])
        self.assertIsNone(calibrate_single_column(pages, candidates))
        pages = source()
        pages[0]['text'] = 'JEE Advanced\nIMPORTANT INSTRUCTIONS\nTotal Questions: 12'
        pages[1]['text'] = 'SECTION-I\nMATRIX MATCH TYPE'
        self.assertIsNone(calibrate_single_column(pages, candidates))

    def test_paired_main_heading_is_source_evidence(self):
        pages = source()
        pages[0]['text'] = pages[0]['text'].replace('_JEE_Main_Practice_', 'JEE (Main) Practice')
        self.assertIsNotNone(calibrate_single_column(pages, candidates))

    def test_answer_grid_and_same_row_sequences_are_not_question_margins(self):
        pages = source()
        pages[1]['text'] = 'FINAL ANSWER KEY\nSINGLE CORRECT ANSWER TYPE'
        self.assertIsNone(calibrate_single_column(pages, candidates))
        pages = source()
        for item in pages[2]['words']:
            if item['text'].endswith('.'):
                item['top'] = 100
                item['bottom'] = 110
        self.assertIsNone(calibrate_single_column(pages, candidates))

    def test_unlabelled_vertical_key_values_are_not_positive_question_content(self):
        pages = source()
        for p in pages[1:]:
            for w in p['words']:
                if w['text'] == 'Synthetic question statement':
                    w['text'] = 'Ans 2'
        self.assertIsNone(calibrate_single_column(pages, candidates))

    def test_equation_only_questions_remain_supported_without_english_prose(self):
        pages = source()
        for p in pages[1:]:
            for w in p['words']:
                if w['text'] == 'Synthetic question statement':
                    w['text'] = 'x\u00b2+y\u00b2=4'
        result = calibrate_single_column(pages, candidates)
        self.assertIsNotNone(result)
        self.assertEqual(sum(len(items) for items in result['anchors_by_page']), 12)

    def test_unproved_anchorless_or_unrelated_interior_pages_fall_back(self):
        for text, labels in [('Blank page', True), ('ROUGH WORK', True),
                             ('SOLUTIONS', True), ('Answer Key', True),
                             ('SECTION-II', True), ('Unlabelled image page', False)]:
            with self.subTest(text=text):
                pages = source()
                extra = page(text=text)
                if labels:
                    extra['words'] = [word(str(n) + ')', 100, n * 50) for n in range(1, 5)]
                pages.insert(3, extra)
                self.assertIsNone(calibrate_single_column(pages, candidates))

    def test_tail_marketing_family_does_not_reclassify_source_or_become_continuation(self):
        pages = source() + [page(text='JEE Advanced marketing')]
        result = calibrate_single_column(pages, candidates)
        self.assertEqual(result['exam_family'], 'jee-main')
        self.assertEqual(result['last_page_index'], 3)
        self.assertEqual(result['continuation_page_indexes'], [])
        self.assertFalse(result['tail_pages_verified'])

    def test_explicit_glued_option_prefixes_only(self):
        for text, label in [('1)10', '1'), ('(2)3', '2'), ('A)statement', 'A'),
                            ('d)diagram', 'D')]:
            self.assertEqual(option_prefix_label(text), label)
        for text in ('0', '1', '2', '3', '4', '1.25', '-1', '1)', '(1)',
                     '11)4', '0)0', 'E)text', 'ordinary text', None):
            self.assertIsNone(option_prefix_label(text))

    def test_invalid_dimensions_coordinates_and_page_indexes_are_not_trusted(self):
        for field, value in [('width', 0), ('height', float('nan'))]:
            pages = source()
            pages[2][field] = value
            self.assertIsNone(calibrate_single_column(pages, candidates))
        pages = source()
        pages[2]['words'][0]['x0'] = float('inf')
        self.assertIsNone(calibrate_single_column(pages, candidates))
        self.assertEqual(trusted_candidates(None, 0), [])
        self.assertEqual(trusted_candidates({}, -1), [])
        self.assertEqual(trusted_candidates({}, True), [])

    @staticmethod
    def heading(p, text, y=50):
        x = 50
        for token in text.split():
            p['words'].append(word(token, x, y))
            x += len(token) * 8 + 6

    def test_question_types_follow_printed_headings_without_using_answers_or_fixed_ranges(self):
        pages = source()
        self.heading(pages[0], '(NUMERICAL VALUE TYPE)')  # Outside body; ignored.
        self.heading(pages[1], '(SINGLE CORRECT ANSWER TYPE)')
        self.heading(pages[3], '(NUMERICAL VALUE TYPE)')
        layout = calibrate_single_column(pages, candidates)
        original = deepcopy(pages)
        types = source_question_types(pages, layout)
        self.assertEqual([types[n]['source_type'] for n in range(1, 13)],
                         ['single_choice'] * 8 + ['numerical'] * 4)
        self.assertEqual(types[9]['source_section'], {
            'page': 4, 'text': '(NUMERICAL VALUE TYPE)',
            'basis': 'explicit_printed_section_heading'})
        for index, p in enumerate(pages):
            p['answer'] = index % 5  # Answer 0/1/2/3/4 cannot influence controls.
        self.assertEqual(source_question_types(pages, layout), types)
        self.assertEqual([{k: v for k, v in p.items() if k != 'answer'} for p in pages], original)

    def test_clear_wrapped_type_headings_keep_raw_word_evidence(self):
        pages = source()
        self.heading(pages[1], '(Single correct', 40)
        self.heading(pages[1], 'answer type)', 55)
        self.heading(pages[3], '(Numerical value', 40)
        self.heading(pages[3], 'type)', 55)
        types = source_question_types(pages, calibrate_single_column(pages, candidates))
        self.assertEqual(types[1]['source_type'], 'single_choice')
        self.assertEqual(types[1]['source_section']['text'], '(Single correct\nanswer type)')
        self.assertEqual(types[12]['source_type'], 'numerical')

    def test_absent_and_unsupported_type_headings_remain_unassigned(self):
        pages = source()
        layout = calibrate_single_column(pages, candidates)
        self.assertEqual(source_question_types(pages, layout), {})
        self.heading(pages[1], '(SINGLE CORRECT ANSWER TYPE)')
        self.heading(pages[2], 'SECTION-II', 40)
        self.heading(pages[2], '(MATRIX MATCH TYPE)', 55)
        self.heading(pages[3], '(NUMERICAL VALUE TYPE)')
        types = source_question_types(pages, layout)
        self.assertEqual(set(types), set(range(1, 5)) | set(range(9, 13)))
        self.assertEqual(source_question_types(pages, None), {})

    def test_subjects_come_from_printed_headers_and_new_subject_resets_unknown_type(self):
        pages = source()
        self.heading(pages[1], 'PHYSICS Max Marks: 80', 20)
        self.heading(pages[1], '(SINGLE CORRECT ANSWER TYPE)', 50)
        self.heading(pages[2], 'CHEMISTRY', 20)
        self.heading(pages[3], '(NUMERICAL VALUE TYPE)', 50)
        result = source_question_types(pages, calibrate_single_column(pages, candidates))
        self.assertEqual(result[1]['subject'], 'Physics')
        self.assertEqual(result[1]['subject_source']['text'], 'PHYSICS Max Marks: 80')
        self.assertNotIn(5, result)
        self.assertEqual(result[9]['subject'], 'Chemistry')
        self.assertEqual(result[9]['source_type'], 'numerical')

    def test_printed_option_convention_is_preserved_without_using_stem_letters_or_keys(self):
        pages = source()
        self.heading(pages[1], '(SINGLE CORRECT ANSWER TYPE)', 40)
        self.heading(pages[1], 'Each question has 4 options (1), (2), (3) and (4).', 60)
        types = source_question_types(pages, calibrate_single_column(pages, candidates))
        self.assertEqual(types[1]['option_key_basis'], 'one_based_options')
        self.assertEqual(types[1]['option_key_source']['basis'], 'explicit_printed_option_convention')
        for page in pages:
            page['answer'] = 'A'
        self.assertEqual(source_question_types(pages, calibrate_single_column(pages, candidates)), types)

    def test_advanced_rules_and_duration_require_explicit_source_numbers(self):
        pages = source()
        pages[0]['text'] = 'JEE Advanced\nIMPORTANT INSTRUCTIONS\nTotal Questions: 12\nDuration: 3 hours'
        pages[1]['text'] = 'SECTION-I\nINTEGER ANSWER TYPE'
        self.heading(pages[1], 'INTEGER ANSWER TYPE', 20)
        self.heading(pages[1], 'Correct: +3', 40)
        self.heading(pages[1], 'Incorrect: -2', 52)
        self.heading(pages[1], 'Unattempted: 0', 64)
        types = source_question_types(pages, calibrate_single_column(pages, candidates))
        self.assertEqual(types[1]['marking_scheme'], {'correct': 3, 'incorrect': -2, 'unanswered': 0})
        self.assertEqual(types[1]['marking_basis']['basis'], 'source')
        self.assertIn('Unattempted: 0', types[1]['marking_basis']['evidence']['text'])
        self.assertEqual(types[12]['paper_duration_minutes'], 180)
        self.assertEqual(types[12]['duration_basis']['basis'], 'source')
        pages[0]['text'] += '\nTime Allowed: 120 minutes'
        result = source_question_types(pages, calibrate_single_column(pages, candidates))
        self.assertNotIn('paper_duration_minutes', result[1])

    def test_multiple_correct_partial_policy_is_never_assumed(self):
        for partial, expected in ((None, None), ('No partial credit', {'type': 'none'}),
                                  ('Partial Marks: +1 per correct option', {'type': 'per_correct_option', 'each': 1.0})):
            with self.subTest(partial=partial):
                pages = source()
                pages[0]['text'] = 'JEE Advanced\nIMPORTANT INSTRUCTIONS\nTotal Questions: 12'
                pages[1]['text'] = 'MULTIPLE CORRECT ANSWER TYPE'
                self.heading(pages[1], 'MULTIPLE CORRECT ANSWER TYPE', 20)
                self.heading(pages[1], 'Full Marks: +4', 40)
                self.heading(pages[1], 'Negative Marks: -2', 52)
                self.heading(pages[1], 'Zero Marks: 0', 64)
                if partial:
                    self.heading(pages[1], partial, 76)
                types = source_question_types(pages, calibrate_single_column(pages, candidates))
                if expected is None:
                    self.assertNotIn('marking_scheme', types[1])
                else:
                    self.assertEqual(types[1]['marking_scheme']['partial'], expected)
                    self.assertEqual(types[1]['marking_scheme']['incorrect'], -2)

    def test_unknown_advanced_rules_never_inherit_main_or_question_text_values(self):
        pages = source()
        pages[0]['text'] = 'JEE Advanced\nIMPORTANT INSTRUCTIONS\nTotal Questions: 12'
        self.heading(pages[1], 'SINGLE CORRECT ANSWER TYPE', 20)
        self.heading(pages[1], 'Correct: +4', 40)
        self.heading(pages[1], 'Unattempted: 0', 64)
        # This is after the first source anchor, so it is question content,
        # not section-level evidence for the missing incorrect rule.
        self.heading(pages[1], 'Incorrect: -1', 115)
        result = source_question_types(pages, calibrate_single_column(pages, candidates))
        self.assertNotIn('marking_scheme', result[1])
        self.assertNotIn('paper_duration_minutes', result[1])

    def test_custom_numerical_partial_credit_is_not_silently_discarded(self):
        pages = source()
        self.heading(pages[1], 'NUMERICAL VALUE TYPE', 20)
        self.heading(pages[1], 'Correct: +3', 40)
        self.heading(pages[1], 'Incorrect: -1', 52)
        self.heading(pages[1], 'Unattempted: 0', 64)
        self.heading(pages[1], 'Partial credit depends on rounding', 76)
        result = source_question_types(pages, calibrate_single_column(pages, candidates))
        self.assertNotIn('marking_scheme', result[1])


if __name__ == '__main__':
    unittest.main()
