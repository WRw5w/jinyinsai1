"""blank_step: the joint two-round billet rebalance, its domain, and its acceptance rule.

The probe exists to answer one question the existing operators cannot: can two rounds
jointly shed a whole billet?  These cases pin the arithmetic that decides it, the
domain it enumerates (so "no improving config in the domain" is a bounded claim and
not a timeout), and -- most importantly -- a hand-built case where the only legal
improvement is a JOINT move, which a single-round operator provably cannot reach.
"""
import importlib.util
import unittest
from decimal import Decimal as D
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'blank_step', ROOT / 'tools' / 'analysis' / 'blank_step.py')
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)

BLANKS = {1: D('5000')}


def order(size, lin, pieces, dia=20.0):
    return dict(size=D(str(size)), dia=D(str(dia)), linear=D(str(lin)),
                linear_int=D(str(lin)), steel='GC-3', pieces=int(pieces),
                weight=D(str(pieces)) * D(str(size)) * D(str(lin)))


def mk(schemes, counts, weight='5000', blank_type=1, lin='5.0'):
    """A batch from {oid: length} rounds; blank_counts recomputed the platform's way."""
    out = {'orders': [], 'length_scheme': list(schemes), 'counts': list(counts),
           'blank_type': blank_type, 'blank_counts': []}
    for s in schemes:
        for o in s:
            if o not in out['orders']:
                out['orders'].append(o)
    for s, c in zip(schemes, counts):
        mass = (sum((D(str(v)) for v in s.values()), D(0)) + 2) * D(str(c)) * D(lin)
        out['blank_counts'].append(int((mass / D(weight)).to_integral_value(
            rounding='ROUND_CEILING')))
    return out


class DeltaSTests(unittest.TestCase):
    """The acceptance rule is the exact score delta, not a kg/knife rate."""

    def test_no_change_is_exactly_zero(self):
        self.assertEqual(bs.delta_s(0, 0), 0.0)

    def test_one_blank_at_one_extra_knife_still_wins(self):
        # 6,162 kg is the lightest blank; one extra knife costs 6.4e6/(K(K-1)).
        self.assertGreater(bs.delta_s(+1, -6162), 0.0)
        self.assertAlmostEqual(bs.delta_s(+1, -6162), 0.0002463, places=6)

    def test_one_blank_free_knives_matches_the_plan_economics(self):
        # The planning note's "省一根坯 ≈ 0.00043–0.00068 分" with no knife change;
        # the five blank weights are 6,162.5 / 6,957.2 / 8,134.5 / 8,818.8 / 9,613.5 kg.
        self.assertAlmostEqual(bs.delta_s(0, -6162.5), 0.0004338, places=6)
        self.assertAlmostEqual(bs.delta_s(0, -9613.5), 0.0006767, places=6)

    def test_an_extra_knife_without_material_is_a_loss(self):
        self.assertLess(bs.delta_s(+1, 0), 0.0)

    def test_shedding_blanks_is_convex_so_ten_beats_one_ten_times_over(self):
        one = bs.delta_s(0, -10 * 6162.5)
        self.assertGreater(one, 10 * bs.delta_s(0, -6162.5))    # 1/M is convex
        self.assertAlmostEqual(one, 10 * bs.delta_s(0, -6162.5), delta=1e-5)

    def test_the_exact_judge_and_the_retired_linear_rate_disagree_in_a_band(self):
        # Why the plan retired the kg/knife rate as the acceptance test.  The exact
        # delta prices a knife at 6.4e6*M0^2/(40*D*K0*(K0+1)) = 2,670 kg; requant's
        # linear KNIFE_KG fraction (set at REF_BILL/REF_KNIVES) says 2,711 kg.  A move
        # trading one knife for 2,671..2,710 kg of material therefore flips sign.
        exact_kg_per_knife = (6_400_000.0 * bs.M0 ** 2) / (40.0 * bs.DEM * bs.K0 * (bs.K0 + 1))
        self.assertAlmostEqual(exact_kg_per_knife, 2670.0, delta=1.0)
        old = float(bs.RQ.KNIFE_KG)
        self.assertAlmostEqual(old, 2710.6, delta=1.0)
        dM = -2700.0                                    # inside the 2,671..2,710 band
        self.assertGreater(bs.delta_s(+1, dM), 0.0)     # exact judge: accept
        self.assertGreater(dM + old * 1, 0.0)           # linear judge: reject


class DomainTests(unittest.TestCase):
    """What the enumeration covers -- the reason a NO is a bounded proof."""

    def test_coupled_windows_are_the_ones_sharing_an_order(self):
        b = mk([{'A': 60.0, 'S': 40.0}, {'B': 60.0, 'S': 16.0}, {'C': 50.0}], [10, 10, 10])
        orders = {'A': order(10.0, 5.0, 60), 'B': order(10.0, 5.0, 60),
                  'S': order(4.0, 5.0, 140), 'C': order(10.0, 5.0, 50)}
        st = bs.RQ.load_state(b, {o: F(str(orders[o]['size'])) for o in orders})
        wins = bs.coupled_windows(st, 2)
        self.assertIn((0, 1), wins)          # share S
        self.assertNotIn((0, 2), wins)       # share nothing: that is the old operator
        self.assertNotIn((1, 2), wins)

    def test_delivery_floor_makes_an_impossible_window_report_no_config(self):
        # 1,000 pieces at count 10 need 100 segments; the 148 m bed caps it near 14.
        b = mk([{'A': 60.0}], [10])
        orders = {'A': order(10.0, 5.0, 1000)}
        res, status, meta = bs.solve_window(b, orders, BLANKS, (0,), 2, 3, 40000, 60)
        self.assertEqual(res, [])
        self.assertEqual(status, 'ok')       # exhausted, not truncated
        self.assertGreater(meta['pairs'], 0)

    def test_mass_cap_bounds_the_net_length(self):
        # lin = 100 kg/m -> the 60 t bed caps net at 60000/(c*lin) - 2, far below the
        # 148 m bed; every returned config must respect its own count's cap.
        b = mk([{'A': 50.0}], [10], lin='100.0')
        orders = {'A': order(10.0, 100.0, 50)}
        res, status, meta = bs.solve_window(b, orders, BLANKS, (0,), 2, 3, 40000, 60)
        self.assertTrue(res)
        for r in res:
            cap = bs.round_cap_net(r['counts'][0], 100.0)
            self.assertLessEqual(max(r['nets']), cap + 1e-9)
            self.assertLessEqual(max(r['nets']), 148.0 + 1e-9)
            self.assertGreaterEqual(min(r['nets']), 48.0 - 1e-9)

    def test_count_and_segment_minimums_hold_in_every_returned_config(self):
        b = mk([{'A': 60.0, 'S': 40.0}, {'B': 60.0, 'S': 16.0}], [10, 10])
        orders = {'A': order(10.0, 5.0, 60), 'B': order(10.0, 5.0, 60),
                  'S': order(4.0, 5.0, 140)}
        res, status, meta = bs.solve_window(b, orders, BLANKS, (0, 1), 4, 3, 40000, 60)
        self.assertTrue(res)
        for r in res:
            for c in r['counts']:
                self.assertGreaterEqual(c, 1)
            for (_, _), k in zip(meta['slots_flat'], r['assign']):
                self.assertGreaterEqual(k, 1)


class JointMoveTests(unittest.TestCase):
    """The capability itself: a move only a JOINT window can make.

    Round 0 carries 100 m, round 1 carries 76 m, and the shared order S must deliver
    exactly 140 pieces.  Shedding the billet needs 8 m moved from round 0 to round 1
    (-> 92 / 84, bills 5,000 + 5,000 instead of 10,000 + 5,000).  Neither round can do
    it alone: dropping a segment from round 0 breaks S's delivery, and adding one to
    round 1 buys nothing.
    """

    def setUp(self):
        self.b = mk([{'A': 60.0, 'S': 40.0}, {'B': 60.0, 'S': 16.0}], [10, 10])
        self.orders = {'A': order(10.0, 5.0, 60), 'B': order(10.0, 5.0, 60),
                       'S': order(4.0, 5.0, 140)}

    def test_the_joint_window_sheds_a_billet(self):
        res, status, meta = bs.solve_window(self.b, self.orders, BLANKS, (0, 1), 4, 3, 40000, 60)
        self.assertTrue(res)
        best = res[0]
        self.assertGreater(best['dS'], 0.0)
        self.assertLessEqual(best['blanks'], -1.0 + 1e-9)     # at least one whole blank
        self.assertAlmostEqual(best['dM'], -5000.0, places=6)  # exactly one blank
        self.assertLessEqual(best['dK'], 0)                    # and it saves knives too
        self.assertGreaterEqual(best['dS'], bs.delta_s(0, -5000) - 1e-12)

    def test_no_single_round_can_shed_the_billet(self):
        # Round 0 alone: S's delivery floor (140 pieces) pins its segments at >= 10, so
        # the 92 m rebalance is out of reach.  Round 1 alone can add a segment but that
        # only buys a knife, never material.  Both single-round windows therefore stay
        # on their treads -- a materialless move -- while the joint window leaves them.
        res0, _, _ = bs.solve_window(self.b, self.orders, BLANKS, (0,), 4, 3, 40000, 60)
        res1, _, _ = bs.solve_window(self.b, self.orders, BLANKS, (1,), 4, 3, 40000, 60)
        res01, _, _ = bs.solve_window(self.b, self.orders, BLANKS, (0, 1), 4, 3, 40000, 60)
        self.assertLessEqual(max([r['blanks'] for r in res0] + [0.0]), 0.0)
        self.assertLessEqual(max([r['blanks'] for r in res1] + [0.0]), 0.0)
        self.assertGreater(bs.delta_s(res01[0]['dK'], res01[0]['dM']),
                           max(r['dS'] for r in res0 + res1))

    def test_the_config_round_trips_into_legal_batch_fields(self):
        res, _, meta = bs.solve_window(self.b, self.orders, BLANKS, (0, 1), 4, 3, 40000, 60)
        st = bs.RQ.load_state(self.b, {o: F(str(self.orders[o]['size']))
                                       for o in self.orders})
        patched = bs.patch_plan([self.b], 0, self.orders, BLANKS, st, (0, 1), res[0], meta)
        out = patched[0]
        self.assertEqual(out['orders'], self.b['orders'])          # membership untouched
        self.assertEqual(out['blank_type'], self.b['blank_type'])
        lin = F('5.0')
        for s, c, bc in zip(out['length_scheme'], out['counts'], out['blank_counts']):
            mass = (sum((F(str(v)) for v in s.values()), F(0)) + 2) * c * lin
            self.assertEqual(bc, bs.RQ.ceil_frac((mass - F(1, 10 ** 7)) / F(5000)))
            for o, length in s.items():
                k = F(str(length)) / F(str(self.orders[o]['size']))
                self.assertEqual(k, int(k))


class RealBatchTests(unittest.TestCase):
    """The first confirmed instance, kept as an anchor (skipped on a bare tree)."""

    SOURCE = ROOT / 'artifacts/runs/requant/plan_deep1.json'

    def test_batch_1374_window_2_3_sheds_exactly_one_light_blank(self):
        if not self.SOURCE.is_file():
            self.skipTest('plan_deep1.json missing (gitignored tree)')
        from platform_check import load_orders_and_blanks, read_plan
        orders, blanks, _, _ = load_orders_and_blanks(ROOT / 'data/semi', 'semi')
        plan = read_plan(self.SOURCE)
        b = plan[1374]
        st = bs.RQ.load_state(b, {o: F(str(orders[o]['size'])) for o in b['orders']})
        res, status, meta = bs.solve_window(b, orders, blanks, (2, 3), 4, 3, 40000, 60)
        self.assertTrue(res)
        best = res[0]
        w = float(blanks[b['blank_type']])
        self.assertGreater(best['dS'], 0.0)
        self.assertEqual(best['dK'], +1)
        self.assertAlmostEqual(best['blanks'], -1.0, places=9)
        self.assertAlmostEqual(best['dM'], -w, places=6)


if __name__ == '__main__':
    unittest.main()
