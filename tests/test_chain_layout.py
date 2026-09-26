"""The chain layout fixes the clause-6 structure; pin the properties it must keep."""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'chain_layout', ROOT / 'tools/analysis/chain_layout.py')
chain_layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chain_layout)

import sys                                                    # noqa: E402
sys.path.insert(0, str(ROOT / 'tools/analysis'))
sys.path.insert(0, str(ROOT / 'src'))
import clause6_candidate as candidate                         # noqa: E402

REAL_PLAN = ROOT / 'runs/semi_merged_v4/result.json'


def scheme(rounds, orders=None):
    """A minimal scheme carrying just what the layout reads."""
    totals = {}
    for rnd in rounds:
        for oid, length in rnd.items():
            totals[oid] = totals.get(oid, 0.0) + length
    return {'orders': orders or sorted(totals),
            'length_scheme': rounds,
            'counts': [1] * len(rounds),
            'blank_type': 1,
            'blank_counts': [1] * len(rounds)}


class ChainLayoutTests(unittest.TestCase):
    def test_each_order_keeps_its_total_length(self):
        before = scheme([{'A': 80.0, 'B': 40.0, 'C': 60.0},
                         {'A': 60.0, 'B': 40.0, 'C': 60.0}])
        totals_before = {}
        for rnd in before['length_scheme']:
            for oid, length in rnd.items():
                totals_before[oid] = totals_before.get(oid, 0.0) + length

        rounds = chain_layout.chain_rounds(before)
        self.assertIsNotNone(rounds)
        totals_after = {}
        for rnd in rounds:
            for oid, length in rnd.items():
                totals_after[oid] = totals_after.get(oid, 0.0) + length
        self.assertEqual(totals_before, totals_after,
                         'the layout moves length between rounds, never repeats it')

    def test_every_round_is_inside_the_bed_band(self):
        before = scheme([{'A': 90.0, 'B': 90.0}, {'A': 90.0, 'B': 90.0}])
        rounds = chain_layout.chain_rounds(before)
        self.assertIsNotNone(rounds)
        for rnd in rounds:
            self.assertGreaterEqual(sum(rnd.values()), chain_layout.LO - 1e-9)
            self.assertLessEqual(sum(rnd.values()), chain_layout.HI + 1e-9)

    def test_layout_is_clean_under_the_candidate_predicate(self):
        """The whole point: at most one shared order, sitting at the seam."""
        before = scheme([{'A': 100.0, 'B': 100.0, 'C': 100.0},
                         {'A': 100.0, 'B': 100.0, 'C': 100.0}])
        self.assertGreater(candidate.count_boundaries([before]), 0,
                           'the input shape is what the platform rejected')
        after = dict(before, length_scheme=chain_layout.chain_rounds(before))
        self.assertEqual(candidate.count_boundaries([after]), 0)

    def test_a_boundary_never_carries_two_spanning_orders(self):
        before = scheme([{'A': 70.0, 'B': 70.0, 'C': 70.0, 'D': 70.0},
                         {'A': 70.0, 'B': 70.0, 'C': 70.0, 'D': 70.0}])
        rounds = chain_layout.chain_rounds(before)
        self.assertIsNotNone(rounds)
        for left, right in zip(rounds, rounds[1:]):
            shared = set(left) & set(right)
            self.assertLessEqual(len(shared), 1,
                                 f'two orders span one boundary: {sorted(shared)}')
            if shared:
                only = next(iter(shared))
                self.assertEqual(list(left)[-1], only, 'the spanning order must be last')
                self.assertEqual(list(right)[0], only, 'and first on the other side')

    def test_the_real_plan_lays_out_and_comes_out_clean(self):
        if not REAL_PLAN.exists():
            self.skipTest(f'no local run at {REAL_PLAN} (gitignored, regenerate with aic.py)')
        plan = json.loads(REAL_PLAN.read_text(encoding='utf-8'))
        self.assertGreater(candidate.count_boundaries(plan), 0)
        laid, _ = chain_layout.relayout(plan)
        self.assertEqual(candidate.count_boundaries(laid), 0,
                         'the chain layout must remove every seam violation')
        self.assertEqual(candidate.count_order_gaps(laid), 0,
                         'and must not introduce a skipped round')


if __name__ == '__main__':
    unittest.main()
