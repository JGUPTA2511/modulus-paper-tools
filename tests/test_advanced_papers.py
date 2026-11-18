import unittest
import question_boundaries as qb


def row(y, cells):
    return [dict(text=t, x0=x, x1=x + 8 * len(t), top=y, bottom=y + 10) for x, t in cells]


class AdvancedKeyTests(unittest.TestCase):
    def test_multi_letter_and_range_answers(self):
        words = []
        words += row(100, [(50, '1'), (90, 'B'), (130, '2'), (170, 'BCD'), (210, '3'), (250, 'AD'), (290, '4'), (330, '12'), (370, '5'), (410, '0.5')])
        words += row(130, [(50, '6'), (90, 'A'), (130, '7'), (170, '30'), (210, '8'), (250, '-'), (290, '9'), (330, 'ACD'), (370, '10'), (410, '7')])
        words += row(118, [(250, '29.6')])
        words += row(142, [(250, '30.5')])
        key = qb.parse_key(words)
        self.assertEqual(sorted(key), list(range(1, 11)))
        self.assertEqual(key[2], 'BCD')
        self.assertEqual(key[8], '29.6-30.5')

    def test_trailing_dash_range(self):
        words = row(100, [(50, '1'), (90, 'B'), (130, '2'), (170, '1.29-'), (210, '3'), (250, 'AD'), (290, '4'), (330, '12'), (370, '5'), (410, '2')])
        words += row(112, [(170, '1.34')])
        key = qb.parse_key(words)
        self.assertEqual(key[2], '1.29-1.34')

    def test_option_style_rows_are_not_a_key(self):
        words = row(100, [(50, 'A)'), (90, '82'), (130, 'B)'), (170, '271'), (210, 'C)'), (250, '352')])
        self.assertEqual(qb.parse_key(words), {})

    def test_gate_threshold_depends_on_paper_kind(self):
        qs = [dict(number=i, options_found=(4 if i <= 20 else 0)) for i in range(1, 52)]
        key = {i: 'A' for i in range(1, 52)}
        self.assertTrue(any('answer options' in w for w in qb.gate([], qs, key, 'main')))
        self.assertFalse(any('answer options' in w for w in qb.gate([], qs, key, 'advanced')))


if __name__ == '__main__':
    unittest.main()
