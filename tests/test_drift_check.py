"""The zip/json drift check must compare the plans, not a summary of them.

It used to compare only the reading-B violation counts, so any two plans that
happened to share a count passed -- including two entirely different ones.  A
check that certifies agreement it never looked at is the same failure this
project keeps meeting, so it gets its own case.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'verify_clause6_readings', ROOT / 'tools/analysis/verify_clause6_readings.py')
vcr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vcr)


def plan(rounds, orders=('A', 'B')):
    return [{'orders': list(orders), 'length_scheme': rounds,
             'counts': [1] * len(rounds), 'blank_type': 1,
             'blank_counts': [1] * len(rounds)}]


class DriftComparisonTests(unittest.TestCase):
    def test_same_seam_count_but_different_plan_is_not_equal(self):
        a = plan([{'A': 10.0, 'B': 5.0}, {'A': 10.0, 'B': 5.0}])
        b = plan([{'A': 99.0, 'B': 5.0}, {'A': 10.0, 'B': 5.0}])
        self.assertEqual(vcr.reading_B(a), vcr.reading_B(b),
                         'precondition: the old comparison could not tell these apart')
        self.assertNotEqual(vcr._shape(a), vcr._shape(b),
                            'the check must compare the plans themselves')

    def test_key_order_alone_is_a_difference(self):
        """Key order is what the seam predicate reads, so two copies that differ
        only there are NOT interchangeable -- and `dict.__eq__` cannot see it."""
        a = plan([{'A': 10.0, 'B': 5.0}])
        b = plan([{'B': 5.0, 'A': 10.0}])
        self.assertEqual(a, b, 'precondition: plain equality ignores key order')
        self.assertNotEqual(vcr._shape(a), vcr._shape(b))

    def test_identical_plans_compare_equal(self):
        a = plan([{'A': 10.0, 'B': 5.0}, {'A': 10.0, 'B': 5.0}])
        self.assertEqual(vcr._shape(a), vcr._shape(plan(a[0]['length_scheme'])))


if __name__ == '__main__':
    unittest.main()
