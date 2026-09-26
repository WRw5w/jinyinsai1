"""The sharing pass: what it buys, and what it must not sell.

Coverage counts an order only when it stands on a round with another order, so
an order cut alone is worth `COVER_BONUS_KG` less than one cut beside someone --
`share_orders` re-cuts a lone order's batch and merges it with partners of the
same steel and diameter until every share that pays for itself has been bought.
These cases pin the two prices (`KNIFE_KG` and `COVER_BONUS_KG`), the search
that finds a merge, and the gate that refuses one that comes out dearer.
"""
import copy
import importlib.util
import unittest
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'share_orders', ROOT / 'tools/analysis/share_orders.py')
so = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(so)

sc = so.sc
rb = so.rb

BLANKS = {1: D('5000')}


def order(size, lin, pieces, dia=20.0, steel='GC-3'):
    return dict(size=D(str(size)), dia=D(str(dia)), linear=D(str(lin)),
                linear_int=D(str(lin)), steel=steel, pieces=pieces,
                weight=D(str(pieces)) * D(str(size)) * D(str(lin)))


def batch(orders, schemes, counts, weight='5000.0'):
    return {'orders': list(orders), 'length_scheme': schemes, 'counts': list(counts),
            'blank_type': 1,
            'blank_counts': [
                int(((sum((D(str(v)) for v in s.values()), D(0)) + 2) * c * D('5.0')
                     / D(weight)).to_integral_value(rounding='ROUND_CEILING'))
                for s, c in zip(schemes, counts)]}


class LoneOrderTests(unittest.TestCase):
    """Thirty pieces of 4 m at count 2 is 15 units, a 60 m round -- one order alone."""

    def setUp(self):
        self.orders = {oid: order(4.0, 5.0, 30) for oid in 'ABC'}

    def test_lone_orders_are_exactly_the_orders_that_never_share(self):
        plan = [batch('AB', [{'A': 60.0, 'B': 60.0}], [2]),
                batch('C', [{'C': 60.0}], [2])]
        self.assertEqual(so.lone_orders(plan, self.orders), ['C'])

    def test_a_lone_pair_merges_and_buys_both_shares(self):
        # A and B each hold their own round; at count 5 six units apiece make one
        # 48 m round carrying both, which is the floor and the whole win: two
        # orders stop being alone and the round count does not rise.
        plan = [batch('A', [{'A': 60.0}], [2]), batch('B', [{'B': 60.0}], [2])]
        before = sum(len(b['length_scheme']) for b in plan)
        out, stats = so.share(plan, self.orders, BLANKS)
        self.assertEqual(stats['merges'], 1)
        self.assertEqual(stats['recuts'], 0)
        self.assertEqual(stats['orders_newly_shared'], 2)
        self.assertEqual(len(out), 1, 'the pair folds into one batch')
        self.assertEqual(sorted(out[0]['orders']), ['A', 'B'])
        self.assertLessEqual(sum(len(b['length_scheme']) for b in out), before)
        self.assertGreater(stats['gain_kg'], 0)
        for scheme, count in zip(out[0]['length_scheme'], out[0]['counts']):
            net = sum((D(str(v)) for v in scheme.values()), D(0))
            self.assertTrue(sc.NET_MIN <= net <= sc.NET_MAX, f'round net {net}')
            self.assertLessEqual((net + 2) * count * D('5.0'), D('60000'))

    def test_a_share_is_worth_exactly_the_coverage_bonus(self):
        # Same orders, same steel, one layout that shares a round and one that
        # does not.  The lone layout costs one more round, one more knife and one
        # more blank -- and the two shares, priced at the bonus.
        shared = [batch('AB', [{'A': 60.0, 'B': 60.0}], [2])]
        lone = [batch('AB', [{'A': 60.0}, {'B': 60.0}], [2, 2])]
        gap = (rb.cost_of(lone, self.orders, BLANKS)
               - rb.cost_of(shared, self.orders, BLANKS))
        self.assertAlmostEqual(
            float(gap), sc.KNIFE_KG + 5000.0 + 2 * sc.COVER_BONUS_KG, delta=1e-6)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.orders = {oid: order(4.0, 5.0, 30) for oid in 'ABCD'}

    def test_a_dearer_candidate_is_refused_and_the_plan_kept(self):
        # The gate itself: hand the pass a laying that comes out dearer at the
        # score's own rate and it must leave the plan's own batches untouched.
        plan = [batch('A', [{'A': 60.0}], [2]), batch('B', [{'B': 60.0}], [2]),
                batch('C', [{'C': 60.0}], [2]), batch('D', [{'D': 60.0}], [2])]
        frozen = copy.deepcopy(plan)
        dearer = batch('ABCD', [{'A': 60.0}, {'B': 60.0}, {'C': 60.0}, {'D': 60.0}], [2] * 4)
        original = rb.cut_batch
        rb.cut_batch = lambda *args, **kwargs: dearer
        try:
            out, stats = so.share(plan, self.orders, BLANKS)
        finally:
            rb.cut_batch = original
        self.assertEqual(stats['merges'] + stats['recuts'], 0)
        self.assertEqual(out, frozen)

    def test_no_lone_order_means_no_moves(self):
        plan = [batch('AB', [{'A': 60.0, 'B': 60.0}], [2]),
                batch('CD', [{'C': 60.0, 'D': 60.0}], [2])]
        out, stats = so.share(plan, self.orders, BLANKS)
        self.assertEqual(stats['sweeps'], 0)
        self.assertEqual(out, plan)


class WorkingTreePlanTests(unittest.TestCase):
    """The real plan, when the (gitignored) run artifacts are still around."""

    def setUp(self):
        self.source = ROOT / 'runs/_rp4.json'
        if not self.source.is_file():
            self.skipTest('runs/_rp4.json missing (gitignored tree)')
        from platform_check import load_orders_and_blanks, read_plan
        self.read_plan = read_plan
        self.orders, self.blanks, _, _ = load_orders_and_blanks(ROOT / 'data/semi', 'semi')
        self.plan = read_plan(self.source)

    def test_sharing_the_hands_plan_never_loses_a_share_or_an_order(self):
        before = self.read_plan(self.source)
        covered_before = len(so.lone_orders(before, self.orders))
        out, stats = so.share(self.plan, self.orders, self.blanks, partners=3)
        self.assertEqual(sorted(o for b in out for o in b['orders']),
                         sorted(o for b in before for o in b['orders']))
        self.assertLessEqual(len(so.lone_orders(out, self.orders)), covered_before)
        self.assertLessEqual(rb.cost_of(out, self.orders, self.blanks),
                             rb.cost_of(before, self.orders, self.blanks))
        for b in out:
            lin = self.orders[b['orders'][0]]['linear']
            for scheme, count in zip(b['length_scheme'], b['counts']):
                net = sum((D(str(v)) for v in scheme.values()), D(0))
                self.assertTrue(sc.NET_MIN <= net <= sc.NET_MAX, f'round net {net}')
                self.assertLessEqual((net + 2) * count * lin, D('60000'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
