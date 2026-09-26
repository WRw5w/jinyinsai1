"""The mixed-count re-cut: its delivery floor, its price rule, its coverage guard.

`coarsen` may only re-cut a scheme whose rounds share one count, because only then
does a moved cut leave every delivery untouched.  The trim passes leave most
schemes with per-round counts -- that is where the remaining rounds are -- and
`coarsen_mixed` re-cuts those too, at each round's delivery floor.  The floor is
what makes the extension legal: with every round at or above the floor of every
order on it, an order's delivery `sum k_r * c_r` lands on or above `pieces` no
matter where the cuts moved.  These cases pin that invariant, the price rule (a
round bought must beat the bill it adds) and the coverage guard, on a synthetic
scheme first and then on the working tree's own plan when it is there.
"""
import importlib.util
import unittest
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'shift_cuts', ROOT / 'tools/analysis/shift_cuts.py')
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def order(size, lin, weight, steel='GC-3', dia=20.0):
    return dict(size=D(str(size)), dia=D(str(dia)), linear=D(str(lin)),
                linear_int=D(str(lin)), weight=D(str(weight)), steel=steel,
                pieces=int((D(str(weight)) / (D(str(lin)) * D(str(size))))
                           .to_integral_value(rounding='ROUND_CEILING')))


def batch(orders, schemes, counts, weight=5000.0):
    return {'orders': list(orders), 'length_scheme': schemes, 'counts': list(counts),
            'blank_type': 1,
            'blank_counts': [int(sc.blank_bill((sum(D(str(v)) for v in s.values()) + 2)
                                               * c * D('5.0'), D(str(weight))) / D(str(weight)))
                             for s, c in zip(schemes, counts)]}


def panel_score(plan, orders, blanks):
    """The three scored terms, computed the way the semi rule reads them."""
    segments = rounds = 0
    declared = D(0)
    delivered = {}
    covered = set()
    for b in plan:
        for scheme, count, stock in zip(b['length_scheme'], b['counts'], b['blank_counts']):
            rounds += 1
            if len(scheme) > 1:
                covered.update(scheme)
            for oid, length in scheme.items():
                k = sc.pieces_of(length, orders[oid]['size'])
                segments += k
                delivered[oid] = delivered.get(oid, D(0)) + (k * count * orders[oid]['size']
                                                             * orders[oid]['linear'])
            declared += stock * blanks[b['blank_type']]
    numerator = sum(min(mass, orders[oid]['weight']) for oid, mass in delivered.items())
    knives = segments + rounds
    y = 100 * float(numerator) / float(declared)
    c = 100 * len(covered) / len(orders)
    return 0.4 * (100 * 160000 / knives) + 0.4 * y + 0.2 * c, knives


def invariants(case, plan, orders, blanks, before_score):
    """Delivery floor, coverage monotone, rounds not up, score not down."""
    delivered = {}
    segments = {}
    for b in plan:
        for scheme, count in zip(b['length_scheme'], b['counts']):
            for oid, length in scheme.items():
                k = sc.pieces_of(length, orders[oid]['size'])
                delivered[oid] = delivered.get(oid, 0) + k * count
                segments[oid] = segments.get(oid, 0) + k
    for oid, count in delivered.items():
        case.assertGreaterEqual(count, orders[oid]['pieces'], f'{oid} fell below its floor')
    case.assertLessEqual(sum(len(b['length_scheme']) for b in plan), case.rounds_before)
    case.assertGreaterEqual(panel_score(plan, orders, blanks)[0], before_score - 1e-9)
    return segments


class RangeMaxTests(unittest.TestCase):
    def test_range_max_matches_the_naive_reading(self):
        values = [3, 1, 4, 1, 5, 9, 2, 6]
        table = sc._range_max_table(values)
        for lo in range(len(values)):
            for hi in range(lo + 1, len(values) + 1):
                self.assertEqual(sc._range_max(table, lo, hi), max(values[lo:hi]),
                                 f'window [{lo}, {hi})')


class CoveredOrdersTests(unittest.TestCase):
    def test_a_round_of_one_contributes_nothing(self):
        self.assertEqual(sc.covered_orders([{'A': 4.0}, {'B': 4.0}]), set())
        self.assertEqual(sc.covered_orders([{'A': 4.0}, {'A': 4.0, 'B': 4.0}]), {'A', 'B'})


class SyntheticRewriteTests(unittest.TestCase):
    """A two-order scheme the coarsen pass cannot touch, run through the mixed one."""

    def setUp(self):
        self.orders = {'A': order(4.0, 5.0, 960.0), 'B': order(4.0, 5.0, 1440.0)}
        # A needs 48 pieces, B 72; A rides four rounds, B the last four, sharing
        # rounds 3 and 4 -- a chain, so piece_index accepts it.
        schemes = [{'A': 32.0}, {'A': 32.0}, {'A': 16.0, 'B': 40.0},
                   {'B': 32.0}, {'B': 32.0}, {'B': 32.0}]
        self.plan = [batch(['A', 'B'], schemes, [4, 4, 4, 4, 5, 5])]
        self.blanks = {1: D('5000')}
        self.rounds_before = 6

    def test_the_rewrite_keeps_every_floor_and_never_scores_less(self):
        before, _ = panel_score(self.plan, self.orders, self.blanks)
        sc.coarsen_mixed(self.plan, self.orders, self.blanks)
        invariants(self, self.plan, self.orders, self.blanks, before)
        self.assertLessEqual(len(self.plan[0]['length_scheme']), 6)


class RequantizeTests(unittest.TestCase):
    """Counts the trim pushed down are not always the cheapest ones left.

    One round per order at count 4 is 111 knives; the same demand at count 9 --
    the fewest segments the width cap allows that still fill a bed -- is 13.  The
    pass has to find the taller count and the merged round on its own, and the
    delivery floor has to survive it (`4 pieces * 9 = 36` pieces, exactly demand).
    """

    def setUp(self):
        self.orders = {oid: order(4.0, 5.0, 720.0) for oid in 'ABC'}
        self.plan = [batch('ABC', [{'A': 144.0}, {'B': 144.0}, {'C': 144.0}], [4, 4, 4])]
        self.blanks = {1: D('5000')}
        self.rounds_before = 3

    def test_a_taller_count_trades_rounds_it_does_not_need_for_knives(self):
        before, knives_before = panel_score(self.plan, self.orders, self.blanks)
        self.assertEqual(sc.requantize_cuts(self.plan, self.orders, self.blanks), 1)
        _, knives_after = panel_score(self.plan, self.orders, self.blanks)
        self.assertLess(knives_after, knives_before)
        invariants(self, self.plan, self.orders, self.blanks, before)
        for b in self.plan:
            self.assertTrue(all(c == b['counts'][0] for c in b['counts']), b['counts'])
            for scheme in b['length_scheme']:
                net = sum((D(str(v)) for v in scheme.values()), D(0))
                self.assertTrue(sc.NET_MIN <= net <= sc.NET_MAX, f'round net {net}')


class RecountRoundTests(unittest.TestCase):
    """A round should not pay for bars the orders on it do not need.

    `A` wants 444 pieces and keeps 74 of them on one round, so six bars per piece
    reach its demand and seven are one bar too many: `(148 + 2) * 7 * 5` lands just
    over the second 5 t blank where `... * 6` stays inside the first.  The round has
    to come down to 6 on its own, exactly on the delivery floor, without the pieces
    or the knife count moving.
    """

    def setUp(self):
        self.orders = {'A': order(2.0, 5.0, 4440.0)}
        self.plan = [batch(['A'], [{'A': 148.0}], [7])]
        self.blanks = {1: D('5000')}
        self.rounds_before = 1

    def test_the_count_comes_down_to_the_delivery_floor_and_no_further(self):
        before, knives_before = panel_score(self.plan, self.orders, self.blanks)
        self.assertEqual(sc.recount_rounds(self.plan, self.orders, self.blanks), 1)
        self.assertEqual(self.plan[0]['counts'], [6])
        self.assertEqual(self.plan[0]['blank_counts'], [1])
        _, knives_after = panel_score(self.plan, self.orders, self.blanks)
        self.assertEqual(knives_after, knives_before, 'pieces and rounds are not this pass\'s to move')
        invariants(self, self.plan, self.orders, self.blanks, before)
        delivered = 74 * self.plan[0]['counts'][0]
        self.assertEqual(delivered, self.orders['A']['pieces'],
                         'the count must land on the floor, not under it')


class SplitSchemeTests(unittest.TestCase):
    """One blank weight per scheme is a billing choice, not a rule.

    Round one's mass sits just over the second 5 t blank but inside the 8 t one;
    round two wants the 5 t blank.  Together they bill 15,000 kg on the 5 t blank;
    cut apart where no order straddles, they bill 8,000 + 5,000.  Nothing else may
    move: same rounds, same counts, same knives.
    """

    def setUp(self):
        self.orders = {'A': order(4.0, 5.0, 5180.0), 'B': order(4.0, 5.0, 3500.0)}
        self.blanks = {1: D('5000'), 2: D('8000')}
        self.plan = [batch(['A', 'B'], [{'A': 148.0}, {'B': 100.0}], [7, 7])]
        self.rounds_before = 2

    def test_each_stretch_takes_the_blank_that_bills_less(self):
        before, knives_before = panel_score(self.plan, self.orders, self.blanks)
        self.assertEqual(sc.split_schemes(self.plan, self.orders, self.blanks), 1)
        self.assertEqual(len(self.plan), 2)
        self.assertEqual([b['blank_type'] for b in self.plan], [2, 1])
        self.assertEqual([b['blank_counts'] for b in self.plan], [[1], [1]])
        self.assertEqual([b['orders'] for b in self.plan], [['A'], ['B']])
        after, knives_after = panel_score(self.plan, self.orders, self.blanks)
        self.assertEqual(knives_after, knives_before, 'a split may not move knives')
        self.assertGreater(after, before, 'a split must bill less, so the score rises')


class WorkingTreePlanTests(unittest.TestCase):
    """The real plan, when the (gitignored) run artifacts are still around."""

    def test_the_rewrite_improves_the_plan_and_passes_the_strict_checker(self):
        source = ROOT / 'runs/_iter1.json'
        if not source.is_file():
            self.skipTest('runs/_iter1.json missing (gitignored tree)')
        from platform_check import check, load_orders_and_blanks, read_plan
        orders, blanks, _, _ = load_orders_and_blanks(ROOT / 'data/semi', 'semi')
        plan = read_plan(source)
        before, knives_before = panel_score(plan, orders, blanks)
        rewritten = sc.coarsen_mixed(plan, orders, blanks)
        self.assertGreater(rewritten, 0)
        self.rounds_before = sum(len(b['length_scheme']) for b in plan)
        invariants(self, plan, orders, blanks, before)
        _, knives_after = panel_score(plan, orders, blanks)
        self.assertLess(knives_after, knives_before, 'the rewrite must buy rounds')
        report = check(plan, data=ROOT / 'data/semi', weight_mode='strict', round='semi')
        self.assertTrue(report['passed'], report['error_counts'])
        for b in plan:
            for scheme in b['length_scheme']:
                net = sum((D(str(v)) for v in scheme.values()), D(0))
                self.assertTrue(sc.NET_MIN <= net <= sc.NET_MAX, f'round net {net}')

    def test_requantizing_the_counts_also_improves_that_plan(self):
        source = ROOT / 'runs/_iter11.json'
        if not source.is_file():
            self.skipTest('runs/_iter11.json missing (gitignored tree)')
        from platform_check import check, load_orders_and_blanks, read_plan
        orders, blanks, _, _ = load_orders_and_blanks(ROOT / 'data/semi', 'semi')
        plan = read_plan(source)
        before, knives_before = panel_score(plan, orders, blanks)
        rewritten = sc.requantize_cuts(plan, orders, blanks)
        self.assertGreater(rewritten, 0)
        self.rounds_before = sum(len(b['length_scheme']) for b in plan)
        invariants(self, plan, orders, blanks, before)
        _, knives_after = panel_score(plan, orders, blanks)
        self.assertLess(knives_after, knives_before, 'the rescan must buy knives')
        report = check(plan, data=ROOT / 'data/semi', weight_mode='strict', round='semi')
        self.assertTrue(report['passed'], report['error_counts'])

    def test_splitting_the_plan_at_order_boundaries_also_improves_it(self):
        source = ROOT / 'runs/_rc3.json'
        if not source.is_file():
            self.skipTest('runs/_rc3.json missing (gitignored tree)')
        from platform_check import check, load_orders_and_blanks, read_plan
        orders, blanks, _, _ = load_orders_and_blanks(ROOT / 'data/semi', 'semi')
        plan = read_plan(source)
        before, knives_before = panel_score(plan, orders, blanks)
        self.assertGreater(sc.split_schemes(plan, orders, blanks), 0)
        after, knives_after = panel_score(plan, orders, blanks)
        self.assertEqual(knives_after, knives_before, 'a split may not move knives')
        self.assertGreater(after, before, 'a split must bill less, so the score rises')
        report = check(plan, data=ROOT / 'data/semi', weight_mode='strict', round='semi')
        self.assertTrue(report['passed'], report['error_counts'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
