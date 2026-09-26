"""Discriminating examples for the candidate, without claiming platform parity."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "clause6_candidate", Path(__file__).resolve().parents[1] /
    "tools/analysis/clause6_candidate.py")
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


class CandidateTests(unittest.TestCase):
    def plan(self, *rounds):
        return [{"length_scheme": [dict.fromkeys(orders, 1) for orders in rounds]}]

    def test_shared_order_hidden_by_matching_tail_and_head(self):
        self.assertEqual(candidate.count_boundaries(self.plan("AB", "BA")), 1)

    def test_multiple_bad_shared_orders_count_as_one_boundary(self):
        self.assertEqual(candidate.count_boundaries(self.plan("ABC", "ABC")), 1)

    def test_three_consecutive_rounds_can_be_legal(self):
        plan = self.plan("AB", "B", "BC")
        self.assertEqual(candidate.count_boundaries(plan), 0)
        self.assertEqual(candidate.count_order_gaps(plan), 0)

    def test_gap_is_reported_separately(self):
        plan = self.plan("A", "B", "A")
        self.assertEqual(candidate.count_boundaries(plan), 0)
        self.assertEqual(candidate.count_order_gaps(plan), 1)

    def test_empty_or_disjoint_rounds(self):
        self.assertEqual(candidate.count_boundaries(self.plan("", "A", "B")), 0)
