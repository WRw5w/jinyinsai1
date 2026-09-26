"""The chain layout fixes the clause-6 structure; pin the properties it must keep.

The real-plan case needs the 定尺 lengths, so it loads them the same way the
checkers do.  It skips when the gitignored run is absent rather than inventing a
fixture -- the numbers in the module docstring come from that run, and a
synthetic plan would not reproduce them.
"""
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
SIZE = 5.0            # a clean grid keeps the synthetic cases exact


def scheme(rounds):
    totals = {}
    for rnd in rounds:
        for oid, length in rnd.items():
            totals[oid] = totals.get(oid, 0.0) + length
    return {'orders': sorted(totals), 'length_scheme': rounds,
            'counts': [1] * len(rounds), 'blank_type': 1,
            'blank_counts': [1] * len(rounds)}


def sizes_for(scheme_, size=SIZE):
    return {oid: size for oid in scheme_['orders']}


class ChainLayoutTests(unittest.TestCase):
    def test_each_order_keeps_its_total_length(self):
        before = scheme([{'A': 80.0, 'B': 40.0, 'C': 60.0},
                         {'A': 60.0, 'B': 40.0, 'C': 60.0}])
        totals = lambda s: {o: sum(r.get(o, 0.0) for r in s['length_scheme'])  # noqa: E731
                            for o in s['orders']}
        rounds = chain_layout.layout(before, sizes_for(before))
        self.assertIsNotNone(rounds)
        after = totals({'orders': before['orders'], 'length_scheme': rounds})
        self.assertEqual(totals(before), after,
                         'the layout moves length between rounds, never repeats it')

    def test_every_allocation_stays_on_the_length_grid(self):
        before = scheme([{'A': 100.0, 'B': 100.0, 'C': 100.0},
                         {'A': 100.0, 'B': 100.0, 'C': 100.0}])
        rounds = chain_layout.layout(before, sizes_for(before))
        self.assertIsNotNone(rounds)
        for rnd in rounds:
            for length in rnd.values():
                self.assertAlmostEqual(length / SIZE, round(length / SIZE), places=9)

    def test_every_round_is_inside_the_bed_band(self):
        before = scheme([{'A': 90.0, 'B': 90.0}, {'A': 90.0, 'B': 90.0}])
        rounds = chain_layout.layout(before, sizes_for(before))
        self.assertIsNotNone(rounds)
        for rnd in rounds:
            self.assertGreaterEqual(sum(rnd.values()), chain_layout.LO - 1e-9)
            self.assertLessEqual(sum(rnd.values()), chain_layout.HI + 1e-9)

    def test_layout_is_clean_under_the_candidate_predicate(self):
        before = scheme([{'A': 100.0, 'B': 100.0, 'C': 100.0},
                         {'A': 100.0, 'B': 100.0, 'C': 100.0}])
        self.assertGreater(candidate.count_boundaries([before]), 0,
                           'the input shape is what the platform rejected')
        after = dict(before, length_scheme=chain_layout.layout(before, sizes_for(before)))
        self.assertEqual(candidate.count_boundaries([after]), 0)

    def test_a_boundary_never_carries_two_spanning_orders(self):
        before = scheme([{'A': 70.0, 'B': 70.0, 'C': 70.0, 'D': 70.0},
                         {'A': 70.0, 'B': 70.0, 'C': 70.0, 'D': 70.0}])
        rounds = chain_layout.layout(before, sizes_for(before))
        self.assertIsNotNone(rounds)
        for left, right in zip(rounds, rounds[1:]):
            shared = set(left) & set(right)
            self.assertLessEqual(len(shared), 1,
                                 f'two orders span one boundary: {sorted(shared)}')
            if shared:
                only = next(iter(shared))
                self.assertEqual(list(left)[-1], only, 'the spanning order must be last')
                self.assertEqual(list(right)[0], only, 'and first on the other side')

    def test_the_real_plan_lays_out_and_the_seam_count_collapses(self):
        if not REAL_PLAN.exists():
            self.skipTest(f'no local run at {REAL_PLAN} (gitignored; regenerate with aic.py)')
        plan = json.loads(REAL_PLAN.read_text(encoding='utf-8'))
        before = candidate.count_boundaries(plan)
        self.assertGreater(before, 0)
        sizes, linear = chain_layout.load_sizes(ROOT)
        required, diameter, blank_weights = chain_layout.load_mass_data(ROOT)
        laid, stats = chain_layout.relayout(
            plan, sizes, linear=linear, required=required,
            diameter=diameter, blank_weights=blank_weights)
        after = candidate.count_boundaries(laid)
        self.assertEqual(candidate.count_order_gaps(laid), 0,
                         'the layout must not introduce a skipped round')
        # Every remaining violation belongs to a scheme left alone -- either it
        # would not lay out, or the count it needs breaches the bed width or the
        # bed weight and it is not written out infeasible.  On this run that is
        # 1,173 of 3,200, so the seam count falls to about half.
        self.assertLess(after / before, 0.6,
                        'the layout must clear a large share of the seams')
        self.assertGreater(stats['laid_out'], 1900)


if __name__ == '__main__':
    unittest.main()
