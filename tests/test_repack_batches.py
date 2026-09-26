"""The re-batching pass: what it may change, and what it may not.

Nothing pins which same-steel, same-diameter orders share a batch -- rule 10 only
forbids an order appearing in two of them -- so `repack_batches` re-chooses the
composition and lays each batch anew.  These cases pin what has to survive that:
every order still lands in exactly one batch, every round keeps its chain and its
`net` inside `[48, 148]`, the price rule refuses a group that comes out dearer at
the score's own rate, and a synthetic pair of batches that could share one chain
really does buy a round.
"""
import importlib.util
import unittest
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'repack_batches', ROOT / 'tools/analysis/repack_batches.py')
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)

_spec_sc = importlib.util.spec_from_file_location(
    'shift_cuts', ROOT / 'tools/analysis/shift_cuts.py')
sc = importlib.util.module_from_spec(_spec_sc)
_spec_sc.loader.exec_module(sc)

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


def rounds_of(plan):
    return sum(len(b['length_scheme']) for b in plan)


class SyntheticCompositionTests(unittest.TestCase):
    """Two batches whose orders chain into one round when they share a batch.

    Each order wants 30 pieces of 4 m; at count 10 that is three pieces a bar and
    a 12 m run, so four orders chain into one 48 m round -- exactly the floor.  The
    plan carries them as two rounds (two pieces a bar, 15 units each), which the
    pass should fold into one.
    """

    def setUp(self):
        self.orders = {oid: order(4.0, 5.0, 30) for oid in 'ABCD'}
        self.plan = [batch('AB', [{'A': 60.0, 'B': 60.0}], [2]),
                     batch('CD', [{'C': 60.0, 'D': 60.0}], [2])]

    def test_sharing_a_batch_folds_two_rounds_into_one(self):
        before = rb.cost_of(self.plan, self.orders, BLANKS)
        out, stats = rb.repack(self.plan, self.orders, BLANKS)
        self.assertEqual(stats['groups_kept'], 0)
        self.assertEqual(rounds_of(out), 1)
        self.assertLess(rb.cost_of(out, self.orders, BLANKS), before)
        self.assertEqual(sorted(o for b in out for o in b['orders']), list('ABCD'))

    def test_every_order_lands_in_exactly_one_batch(self):
        out, _ = rb.repack(self.plan, self.orders, BLANKS)
        seen = [o for b in out for o in b['orders']]
        self.assertEqual(len(seen), len(set(seen)), 'an order may not be batched twice')
        self.assertEqual(set(seen), set(self.orders))
        for b in out:
            for scheme, count in zip(b['length_scheme'], b['counts']):
                net = sum((D(str(v)) for v in scheme.values()), D(0))
                self.assertTrue(sc.NET_MIN <= net <= sc.NET_MAX, f'round net {net}')
                self.assertLessEqual((net + 2) * count * D('5.0'), D('60000'))

    def test_a_dearer_group_keeps_the_batches_the_plan_had(self):
        # The gate itself: hand the pass a composition that comes out dearer at
        # the score's own rate and it must return the plan's own batches untouched.
        orders = {oid: order(4.0, 5.0, 30) for oid in 'AB'}
        plan = [batch('AB', [{'A': 60.0, 'B': 60.0}], [2])]
        dearer = [batch('AB', [{'A': 60.0, 'B': 60.0}], [40])]
        original = rb.cut_packed
        rb.cut_packed = lambda *args, **kwargs: dearer
        try:
            out, stats = rb.repack(plan, orders, BLANKS)
        finally:
            rb.cut_packed = original
        self.assertEqual(stats['groups_kept'], 1)
        self.assertEqual(out, plan)


class ScanTests(unittest.TestCase):
    def test_the_scan_keeps_the_count_inside_the_roller(self):
        pieces, sizes = [30, 30], [4.0, 4.0]
        for cost, count in rb.scan_counts(pieces, sizes, 7, 5.0, 5000.0, sc.KNIFE_KG):
            self.assertLessEqual(count, 7)
            self.assertGreater(cost, 0)

    def test_a_taller_count_is_offered_when_the_chain_still_fills_a_round(self):
        # 12 units of 4 m is 48 m: the tallest count whose chain still reaches the
        # floor is the one the scan should like best, since knives count per piece.
        pieces, sizes = [30] * 4, [4.0] * 4
        counts = [c for _, c in rb.scan_counts(pieces, sizes, 20, 5.0, 5000.0, sc.KNIFE_KG)]
        self.assertIn(10, counts)


class WorkingTreePlanTests(unittest.TestCase):
    """The real plan, when the (gitignored) run artifacts are still around."""

    def setUp(self):
        self.source = ROOT / 'runs/_rf3.json'
        if not self.source.is_file():
            self.skipTest('runs/_rf3.json missing (gitignored tree)')
        from platform_check import load_orders_and_blanks, read_plan
        self.orders, self.blanks, _, _ = load_orders_and_blanks(ROOT / 'data/semi', 'semi')
        self.plan = read_plan(self.source)

    def test_re_grouping_two_groups_keeps_them_legal_and_no_dearer(self):
        # Two of the smallest groups, so the case stays a test rather than a run.
        groups = rb.groups_of(self.plan, self.orders)
        by = {(str(self.orders[b['orders'][0]]['steel']),
               float(self.orders[b['orders'][0]]['dia'])): [] for b in self.plan}
        for b in self.plan:
            first = b['orders'][0]
            by[(str(self.orders[first]['steel']), float(self.orders[first]['dia']))].append(b)
        small = sorted(groups, key=lambda k: len(groups[k]))[:2]
        sub = [b for key in small for b in by[key]]
        before = rb.cost_of(sub, self.orders, self.blanks)
        out, _ = rb.repack(sub, self.orders, self.blanks)
        self.assertLessEqual(rb.cost_of(out, self.orders, self.blanks), before)
        self.assertEqual(sorted(o for b in out for o in b['orders']),
                         sorted(o for b in sub for o in b['orders']))
        for b in out:
            for scheme, count in zip(b['length_scheme'], b['counts']):
                net = sum((D(str(v)) for v in scheme.values()), D(0))
                self.assertTrue(sc.NET_MIN <= net <= sc.NET_MAX, f'round net {net}')
                lin = self.orders[b['orders'][0]]['linear']
                self.assertLessEqual((net + 2) * count * lin, D('60000'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
