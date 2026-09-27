"""The requant pass: its bill rule, the two structural rules it must respect, and
the two repairs it needs to stay inside the checker.

`coarsen` pins every round's count at the bed-width cap and nothing downstream
revisits it, so each round's mass lands wherever the piece lattice puts it --
typically ~1.6 t above the blank multiple it is billed at.  `requant_batches`
re-chooses (pieces, count) per round: counts are free of knife cost, so a round
that lands just above a multiple bills one blank less for the same material,
while the pieces it spends to get there are paid for in the delivery ledger.

Two rules make that search harder than a walk over (k, c):
  clause 6  an order's rounds are one contiguous block inside one scheme, so a
            state that scatters an order over islands is worth nothing however
            cheap its bill;
  seam      at every round boundary the shared order must be simultaneously the
            last key on the left and the first on the right, which caps a
            boundary at one shared order -- and rounds sharing the same order on
            both sides must be that order alone.
`evaluate` prices both, `seam_keys` finds the key order that closes a legal
chain, and `split_scheme` cuts a >6-round result at an order-free boundary
(clause 4).  These cases pin the bill arithmetic, both rules' rejection, both
repairs, and then the whole pass against the real checker on real batches.
"""
import importlib.util
import json
import unittest
from decimal import Decimal as D
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'requant_batches', ROOT / 'tools/analysis/requant_batches.py')
rq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rq)

BLANKS = {1: D('5000')}


def order(size, lin, pieces, dia=20.0, steel='GC-3'):
    return dict(size=D(str(size)), dia=D(str(dia)), linear=D(str(lin)),
                linear_int=D(str(lin)), steel=steel, pieces=pieces,
                weight=D(str(pieces)) * D(str(size)) * D(str(lin)))


def batch(orders, schemes, counts, weight='5000.0', blank_type=1):
    lin = D('5.0')
    out = {'orders': list(orders), 'length_scheme': list(schemes),
           'counts': list(counts), 'blank_type': blank_type, 'blank_counts': []}
    for s, c in zip(schemes, counts):
        mass = (sum((D(str(v)) for v in s.values()), D(0)) + 2) * c * lin
        out['blank_counts'].append(
            int((mass / D(weight)).to_integral_value(rounding='ROUND_CEILING')))
    return out


def ctx(sizes, dem, lin='5.0', cap_c=100):
    return ({o: F(str(s)) for o, s in sizes.items()},
            {o: int(d) for o, d in dem.items()}, F(lin), cap_c)


class BillRuleTests(unittest.TestCase):
    """Declared kg is the round's mass rounded *up* to a whole blank, half-open at
    the multiple: material that lands exactly on it bills it, not one more."""

    def test_a_round_bills_whole_blanks_rounded_up(self):
        self.assertEqual(rq.bill(F(10000), F(5000)), F(10000))
        self.assertEqual(rq.bill(F(10001), F(5000)), F(15000))
        self.assertEqual(rq.bill(F(1), F(5000)), F(5000))

    def test_material_exactly_on_a_multiple_bills_that_multiple(self):
        for n in (1, 2, 7):
            self.assertEqual(rq.bill(F(5000) * n, F(5000)), F(5000) * n)
            self.assertEqual(rq.bill(F(5000) * n - F(1, 10 ** 8), F(5000)),
                             F(5000) * n)

    def test_the_bill_never_undercuts_the_material(self):
        w = F(str(D('6162.5')))
        for mass in (F(1), F(6163), F(123249, 20), F(10 ** 7)):
            self.assertGreaterEqual(rq.bill(mass, w), mass)


class SnapCountTests(unittest.TestCase):
    def test_snap_takes_the_lowest_count_that_lands_on_a_multiple(self):
        # 50 m of bed at 7 kg/m is 350 kg per piece; at w=1100 the counts 88..100
        # waste 0/0/...: 88 lands exactly on 30,800, and the cap 100 lands 200 kg
        # over.  The snap must come back with 88, not with the tallest count.
        c = rq.snap_c(F(48), F(1100), F(7), 100)
        self.assertEqual(c, 88)
        self.assertEqual(rq.bill((F(48) + 2) * c * F(7), F(1100)), F(30800))

    def test_snap_stays_inside_the_bed_cap_and_never_exceeds_the_width_cap(self):
        c = rq.snap_c(F(48), F(1100), F(7), 30)
        self.assertLessEqual(c, 30)
        self.assertLessEqual((F(48) + 2) * c * F(7), rq.MASS_CAP)

    def test_snap_gives_up_when_no_count_fits_the_bed(self):
        self.assertIsNone(rq.snap_c(F(100000), F(5000), F(5), 100))


class StructuralRuleTests(unittest.TestCase):
    """What `evaluate` must refuse even when the bill looks cheap."""

    def test_an_order_broken_into_islands_is_refused(self):
        sizes, dem, lin, cap_c = ctx({'A': 10.0, 'B': 10.0}, {'A': 30, 'B': 10})
        st = [({'A': 1}, 10), ({'B': 1}, 10), ({'A': 1}, 10)]
        _obj, _b, _k, viol = rq.evaluate(st, F(5000), sizes, dem, lin, cap_c)
        self.assertGreater(viol, 0, 'A spans rounds 0 and 2 with a hole at 1')

    def test_a_boundary_sharing_two_orders_is_refused(self):
        sizes, dem, lin, cap_c = ctx({'A': 10.0, 'B': 10.0}, {'A': 20, 'B': 20})
        st = [({'A': 1, 'B': 1}, 10), ({'A': 1, 'B': 1}, 10)]
        _obj, _b, _k, viol = rq.evaluate(st, F(5000), sizes, dem, lin, cap_c)
        self.assertGreater(viol, 0, 'both orders would have to close the seam')

    def test_a_round_shared_on_both_sides_must_be_that_order_alone(self):
        sizes, dem, lin, cap_c = ctx({'A': 10.0, 'B': 10.0}, {'A': 30, 'B': 10})
        st = [({'A': 1}, 10), ({'A': 1, 'B': 1}, 10), ({'A': 1}, 10)]
        _obj, _b, _k, viol = rq.evaluate(st, F(5000), sizes, dem, lin, cap_c)
        self.assertGreater(viol, 0, 'middle round is entered and left on A')

    def test_a_chain_that_closes_every_seam_has_no_violation(self):
        # Three 48 m rounds of 16 m pieces; each boundary shares exactly one order
        # (C, then E) and no order is split into islands.
        names = list('ABCDEFG')
        sizes, dem, lin, cap_c = ctx({o: 16.0 for o in names},
                                     {'A': 10, 'B': 10, 'C': 20, 'D': 10,
                                      'E': 20, 'F': 10, 'G': 10})
        st = [({'A': 1, 'B': 1, 'C': 1}, 10),
              ({'C': 1, 'D': 1, 'E': 1}, 10),
              ({'E': 1, 'F': 1, 'G': 1}, 10)]
        _obj, _b, _k, viol = rq.evaluate(st, F(9000), sizes, dem, lin, cap_c)
        self.assertEqual(viol, 0)

    def test_a_round_above_the_bed_cap_is_refused(self):
        sizes, dem, lin, cap_c = ctx({'A': 10.0}, {'A': 200})
        _obj, _b, _k, viol = rq.evaluate([({'A': 14}, 50)], F(5000), sizes, dem,
                                         lin, cap_c)   # (140 + 2) * 50 * 5 = 35,500
        self.assertEqual(viol, 0)
        _obj, _b, _k, viol = rq.evaluate([({'A': 14}, 95)], F(5000), sizes, dem,
                                         lin, cap_c)   # (140 + 2) * 95 * 5 = 67,450
        self.assertGreater(viol, 0)

    def test_a_round_outside_the_net_window_is_refused(self):
        sizes, dem, lin, cap_c = ctx({'A': 10.0}, {'A': 100})
        _obj, _b, _k, viol = rq.evaluate([({'A': 4}, 5)], F(5000), sizes, dem,
                                         lin, cap_c)    # net 40 < 48
        self.assertGreater(viol, 0)
        _obj, _b, _k, viol = rq.evaluate([({'A': 15}, 5)], F(5000), sizes, dem,
                                         lin, cap_c)    # net 150 > 148
        self.assertGreater(viol, 0)

    def test_a_short_delivery_is_refused(self):
        sizes, dem, lin, cap_c = ctx({'A': 10.0}, {'A': 100})
        _obj, _b, _k, viol = rq.evaluate([({'A': 4}, 5)], F(5000), sizes, dem,
                                         lin, cap_c)     # 20 pieces of 100 demanded
        self.assertGreater(viol, 0)

    def test_the_count_cap_is_refused(self):
        sizes, dem, lin, cap_c = ctx({'A': 10.0}, {'A': 1000})
        _obj, _b, _k, viol = rq.evaluate([({'A': 4}, 51)], F(5000), sizes, dem,
                                         lin, F(50))
        self.assertGreater(viol, 0)


class SeamKeyTests(unittest.TestCase):
    def _closes(self, keys):
        """Every boundary: the shared order is last on the left, first on the right."""
        for left, right in zip(keys, keys[1:]):
            shared = set(left) & set(right)
            self.assertEqual(len(shared), 1, f'boundary {left}|{right}')
            o = next(iter(shared))
            self.assertEqual(left[-1], o, f'{left} must end on {o}')
            self.assertEqual(right[0], o, f'{right} must begin on {o}')

    def test_the_key_order_closes_a_three_round_chain(self):
        keys = rq.seam_keys([['A', 'B'], ['B', 'C'], ['C']])
        self.assertEqual(keys, [['A', 'B'], ['B', 'C'], ['C']])
        self._closes(keys)

    def test_scrambled_keys_are_repaired(self):
        keys = rq.seam_keys([['B', 'A'], ['C', 'B'], ['C']])
        self._closes(keys)

    def test_a_round_shared_on_one_side_only_keeps_its_own_order(self):
        keys = rq.seam_keys([['A', 'B'], ['C', 'D']])
        self.assertEqual(sorted(keys[0]), ['A', 'B'])
        self.assertEqual(sorted(keys[1]), ['C', 'D'])


class SplitSchemeTests(unittest.TestCase):
    """Clause 4 caps a scheme at six rounds; a longer result must be cut at a
    boundary that shares no order, or dropped."""

    def test_a_scheme_within_six_rounds_is_returned_whole(self):
        b = batch('AB', [{'A': 60.0, 'B': 60.0}], [2])
        self.assertEqual(rq.split_scheme(b), [b])

    def test_eight_rounds_split_at_an_order_free_boundary(self):
        schemes = [{'A': 60.0}, {'A': 60.0}, {'A': 60.0}, {'A': 60.0},
                   {'B': 60.0}, {'B': 60.0}, {'C': 60.0}, {'C': 60.0}]
        b = batch('ABC', schemes, [2] * 8)
        parts = rq.split_scheme(b)
        self.assertEqual([len(p['counts']) for p in parts], [6, 2])
        self.assertEqual(parts[0]['orders'], ['A', 'B'])
        self.assertEqual(parts[1]['orders'], ['C'])
        for p in parts:
            self.assertLessEqual(len(p['counts']), 6)
            self.assertEqual(p['blank_type'], b['blank_type'])
            self.assertEqual(len(p['length_scheme']), len(p['blank_counts']))

    def test_no_cut_exists_when_every_boundary_shares_an_order(self):
        b = batch('A', [{'A': 60.0}] * 8, [2] * 8)
        self.assertIsNone(rq.split_scheme(b))

    def test_the_split_conserves_every_round(self):
        schemes = [{'A': 60.0}, {'A': 60.0}, {'A': 60.0}, {'A': 60.0},
                   {'B': 60.0}, {'B': 60.0}, {'C': 60.0}, {'C': 60.0}]
        b = batch('ABC', schemes, [2] * 8)
        parts = rq.split_scheme(b)
        self.assertEqual([len(p['counts']) for p in parts], [6, 2])
        self.assertEqual([c for p in parts for c in p['counts']], b['counts'])
        self.assertEqual([s for p in parts for s in p['length_scheme']],
                         b['length_scheme'])


class StateToFieldsTests(unittest.TestCase):
    def test_blank_counts_are_the_declared_blanks_at_that_mass(self):
        orders = {'A': order(12.0, 5.0, 20)}
        sizes = {'A': F(12)}
        st = [({'A': 2}, 10)]                       # net 24 -> (26) * 10 * 5 = 1300
        f = rq.state_to_fields(st, 5000, sizes, orders, F(5))
        self.assertEqual(f['counts'], [10])
        self.assertEqual(f['blank_counts'], [1])
        self.assertEqual(f['length_scheme'], [{'A': 24.0}])

    def test_the_fields_land_on_the_blank_multiple_the_annealer_priced(self):
        orders = {'A': order(12.0, 5.0, 20)}
        sizes = {'A': F(12)}
        st = [({'A': 2}, 10)]
        f = rq.state_to_fields(st, 5000, sizes, orders, F(5))
        mass = (F(24) + 2) * 10 * F(5)
        self.assertEqual(F(f['blank_counts'][0]) * F(5000), rq.bill(mass, F(5000)))


class SplitStartTests(unittest.TestCase):
    """The one-round-bought starts: what they must look like and what they may
    never be, since their whole point is that the annealer starts from them."""

    def test_half_an_order_carries_a_whole_one_to_reach_the_floor(self):
        # 5 m pieces: half of A's 9 is 20 m, short of the 48 m floor, so the next
        # order is carried across whole (45 m) and the new round opens at 65 m.
        # The three orders are symmetric here, so all three splits come back.
        sizes = {o: F(5) for o in 'ABC'}
        st = [({'A': 9, 'B': 9, 'C': 9}, 60)]
        starts = rq.split_starts(st, sizes, F(5000), F(5), 100)
        self.assertEqual(len(starts), 3)
        new = starts[0]                                  # the A split, first by name
        self.assertEqual(len(new), 2)
        self.assertEqual(new[1][0], {'A': 4, 'B': 9})
        self.assertEqual(new[0][0], {'A': 5, 'C': 9})
        self.assertEqual(new[1][1], rq.snap_c(F(65), F(5000), F(5), 100))
        self.assertTrue(rq.structural_ok(new))
        self.assertEqual({o for kd, _ in new for o in kd}, set('ABC'))

    def test_the_input_state_is_not_touched(self):
        sizes = {o: F(5) for o in 'ABC'}
        st = [({'A': 9, 'B': 9, 'C': 9}, 60)]
        rq.split_starts(st, sizes, F(5000), F(5), 100)
        self.assertEqual(st, [({'A': 9, 'B': 9, 'C': 9}, 60)])

    def test_a_round_no_order_can_leave_is_not_split(self):
        # Every order here is half the round; moving any of them leaves the parent
        # under the floor, so there is nothing to split.
        sizes = {o: F(5) for o in 'AB'}
        st = [({'A': 5, 'B': 5}, 60)]
        self.assertEqual(rq.split_starts(st, sizes, F(5000), F(5), 100), [])

    def test_a_start_that_would_strand_an_order_is_dropped(self):
        # A's other round is 0 and the new round lands at 1, so carrying B (whose
        # one other round is 2) across would leave B an island; the candidate goes.
        sizes = {o: F(5) for o in 'ABC'}
        st = [({'A': 9}, 60), ({'A': 9, 'B': 9, 'C': 9}, 60), ({'B': 4}, 60)]
        for cand in rq.split_starts(st, sizes, F(5000), F(5), 100):
            self.assertTrue(rq.structural_ok(cand))


class CoveragePriceTests(unittest.TestCase):
    """One share is worth COVER_KG, and it is a price, not a legality rule.

    The platform counts an order as covered only when some round carries it
    beside another order (`platform_score.py`), so `_cy1.json` -- whose two lone
    orders are the whole reason its coverage is 9,997/9,999 -- would have lost
    0.004 points if the requant descent had stripped two more shares for a bill
    saving of a few hundred kg (measured on bi=2156, plan_joint200 vs _cy1).
    """

    def test_the_share_price_is_the_platforms_marginal_point(self):
        self.assertAlmostEqual(float(rq.COVER_KG), 28838.0, delta=1.0)
        self.assertEqual(rq.COVER_KG,
                         F(2, 10) * 100 / (F(9999) * rq.PTS_PER_KG))

    def test_two_shares_cost_exactly_two_shares(self):
        # Same rounds, same pieces, same bill and same knives -- only the round
        # assignment differs, so the objective gap is the share price alone.
        sizes, dem, lin, cap_c = ctx({'A': 10.0, 'B': 10.0}, {'A': 140, 'B': 70})
        alone = [({'A': 14}, 10), ({'B': 7}, 10)]
        paired = [({'A': 7, 'B': 7}, 10), ({'A': 7}, 10)]
        va = rq.evaluate(alone, F(5000), sizes, dem, lin, cap_c)
        vp = rq.evaluate(paired, F(5000), sizes, dem, lin, cap_c)
        self.assertEqual(va[3], 0)
        self.assertEqual(vp[3], 0)
        self.assertEqual(va[1], vp[1], 'the bills must be identical')
        self.assertEqual(va[2], vp[2], 'the knives must be identical')
        self.assertEqual(rq.shared_orders(paired), {'A', 'B'})
        self.assertEqual(rq.shared_orders(alone), set())
        self.assertEqual(va[0] - vp[0], 2 * rq.COVER_KG)

    def test_a_lone_order_stays_legal_it_is_only_priced(self):
        sizes, dem, lin, cap_c = ctx({'A': 10.0, 'B': 10.0}, {'A': 140, 'B': 70})
        val = rq.evaluate([({'A': 14}, 10), ({'B': 7}, 10)], F(5000), sizes, dem,
                          lin, cap_c)
        self.assertEqual(val[3], 0, 'coverage is a score term, not a violation')


class StructuralOkTests(unittest.TestCase):
    def test_a_contiguous_chain_passes(self):
        self.assertTrue(rq.structural_ok([({'A': 1}, 10), ({'A': 1, 'B': 1}, 10)]))

    def test_an_island_fails(self):
        self.assertFalse(rq.structural_ok([({'A': 1}, 10), ({'B': 1}, 10),
                                           ({'A': 1}, 10)]))

    def test_two_shared_orders_on_one_boundary_fail(self):
        self.assertFalse(rq.structural_ok([({'A': 1, 'B': 1}, 10),
                                           ({'A': 1, 'B': 1}, 10)]))

    def test_a_round_entered_and_left_on_one_order_must_be_alone(self):
        self.assertFalse(rq.structural_ok([({'A': 1}, 10), ({'A': 1, 'B': 1}, 10),
                                           ({'A': 1}, 10)]))
        self.assertTrue(rq.structural_ok([({'A': 1}, 10), ({'A': 1}, 10),
                                          ({'A': 1}, 10)]))


class WorkingTreePassTests(unittest.TestCase):
    """The pass itself, on the real plan, under the real checker."""

    def setUp(self):
        self.source = ROOT / 'runs/_cy1.json'
        if not self.source.is_file():
            self.skipTest('runs/_cy1.json missing (gitignored tree)')
        from platform_check import load_orders_and_blanks
        self.orders, self.blanks, _, _ = load_orders_and_blanks(ROOT / 'data/semi',
                                                                'semi')
        self.plan = json.loads(self.source.read_text(encoding='utf-8'))

    def _emit(self, bi, w, state):
        lin, cap_c, sizes, dem = rq.batch_ctx(self.orders, self.plan[bi])
        b = dict(self.plan[bi])
        b.update(rq.state_to_fields(state, self.blanks[int(w)], sizes,
                                    self.orders, lin))
        b['blank_type'] = int(w)
        parts = rq.split_scheme(b)
        self.assertIsNotNone(parts)
        return parts, lin, sizes, dem

    def _patched(self, repl):
        """The whole plan with each replaced batch swapped for its parts."""
        out = []
        for j, b in enumerate(self.plan):
            out.extend(repl[j] if j in repl else [b])
        return out

    def test_real_split_starts_are_legal_and_keep_every_order(self):
        # The construction runs on 2,275 real batches before the deep anneal; a
        # candidate that is illegal or that drops an order would be a plan the
        # merge could never emit, so the invariants are checked on real shapes.
        seen = 0
        for bi, b in enumerate(self.plan):
            if len(b['counts']) > 5:
                continue
            lin, cap_c, sizes, dem = rq.batch_ctx(self.orders, b)
            w = F(str(self.blanks[int(b['blank_type'])]))
            st = rq.load_state(b, sizes)
            for cand in rq.split_starts(st, sizes, w, lin, cap_c):
                self.assertTrue(rq.structural_ok(cand), f'bi={bi} illegal start')
                self.assertEqual(len(cand), len(st) + 1, f'bi={bi} rounds')
                self.assertEqual({o for kd, _ in cand for o in kd},
                                 {o for kd, _ in st for o in kd}, f'bi={bi} orders')
                for kd, c in cand:
                    net = sum(F(k) * sizes[o] for o, k in kd.items())
                    self.assertGreaterEqual(net, rq.NET_LO, f'bi={bi} net {net}')
                    self.assertLessEqual(net, rq.NET_HI, f'bi={bi} net {net}')
                    self.assertLessEqual((net + 2) * c * lin, rq.MASS_CAP,
                                         f'bi={bi} mass')
                    self.assertLessEqual(c, cap_c, f'bi={bi} count')
                seen += 1
            if seen >= 40:
                break
        self.assertGreater(seen, 0, 'no real batch produced a split start')

    def test_the_piece_floor_already_carries_the_mass_floor(self):
        # Clause 8 asks for the kg allocated to an order to reach its weight; the
        # data's weight is never above pieces * size * linear, so delivered >=
        # demanded pieces -- the only floor `evaluate` prices -- already implies it.
        # If a future data drop broke that, the search would aim at states the
        # checker rejects, so the implication is worth pinning next to the pass.
        for oid, o in self.orders.items():
            nominal = (D(str(o['pieces'])) * D(str(o['size'])) * D(str(o['linear'])))
            self.assertLessEqual(D(str(o['weight'])), nominal,
                                 f'{oid}: clause 8 is not implied by the piece floor')

    def test_re_emitting_a_batch_through_the_passes_own_fields_stays_legal(self):
        # The merge step rebuilds every patched batch from (pieces, count) pairs;
        # a batch fed back through that path untouched must come out legal and at
        # the same bill, or the pass would be corrupting plans it never improved.
        # The checker audits whole plans (every order exactly once), so the three
        # rebuilt batches go back into the plan they came from.
        from platform_check import check
        repl, done = {}, 0
        for bi, b in enumerate(self.plan):
            if len(b['counts']) > 6:
                continue
            lin, cap_c, sizes, dem = rq.batch_ctx(self.orders, b)
            w = int(b['blank_type'])
            st = rq.load_state(b, sizes)
            _obj, billed, _k, viol = rq.evaluate(st, F(str(self.blanks[w])), sizes,
                                                 dem, lin, cap_c)
            self.assertEqual(viol, 0, f'bi={bi} the plan itself must be legal')
            parts, _lin, _sizes, _dem = self._emit(bi, w, st)
            re_billed = sum(F(p['blank_counts'][i]) * F(str(self.blanks[w]))
                            for p in parts for i in range(len(p['counts'])))
            self.assertEqual(re_billed, billed, f'bi={bi} bill moved')
            repl[bi] = parts
            done += 1
            if done >= 3:
                break
        self.assertEqual(done, 3, 'the plan has fewer than three short schemes')
        res = check(self._patched(repl), data=ROOT / 'data/semi', round='semi')
        self.assertTrue(res['passed'], res['error_counts'])

    def test_an_improved_batch_stays_legal_and_no_dearer(self):
        from platform_check import check
        for bi in (1776, 633, 89):
            lin, cap_c, sizes, dem = rq.batch_ctx(self.orders, self.plan[bi])
            got = rq.improve_batch(self.orders, self.blanks, self.plan[bi],
                                   6000, 1, 8)
            if got is None:
                continue
            self.assertEqual(got['val'][3], 0, 'a candidate must be violation-free')
            self.assertLess(got['val'][0], got['base'][0][0])
            parts, _lin, _sizes, _dem = self._emit(bi, got['w'], got['state'])
            for p in parts:
                self.assertLessEqual(len(p['counts']), 6)
            res = check(self._patched({bi: parts}), data=ROOT / 'data/semi',
                        round='semi')
            self.assertTrue(res['passed'], f'bi={bi}: {res["error_counts"]}')
            return
        self.skipTest('no improvement found for the sampled batches at this budget')


    def test_the_descent_never_costs_a_share(self):
        # bi=2156 is where the plain descent traded two shares for a bill saving
        # smaller than COVER_KG before the price sat in the objective
        # (plan_joint200 vs _cy1: 9,995 combined orders against 9,997), so its
        # only gain is gone now; bi=22 and bi=11 come from the same 200-batch
        # sample and still improve, keeping a pass that merely refused every move
        # from passing.
        moved = 0
        for bi in (2156, 22, 11):
            lin, cap_c, sizes, dem = rq.batch_ctx(self.orders, self.plan[bi])
            st0 = rq.load_state(self.plan[bi], sizes)
            got = rq.improve_batch(self.orders, self.blanks, self.plan[bi], 0, 1, 8,
                                   w_try=2)
            if got is None:
                continue
            self.assertEqual(got['val'][3], 0, 'a candidate must be violation-free')
            self.assertLess(got['val'][0], got['base'][0][0])
            self.assertGreaterEqual(len(rq.shared_orders(got['state'])),
                                    len(rq.shared_orders(st0)),
                                    f'bi={bi}: the descent stripped a share')
            moved += 1
        self.assertGreaterEqual(moved, 2, 'the descent should still improve the sample')


class MergeKickTests(unittest.TestCase):
    """The round-merging kick: the one move the descent's neighbourhood lacks.

    `polish` deletes a round by scaling the survivors' piece counts, which leaves
    their *schemes* fixed -- so a round can only be absorbed where a proportional
    count of it already fits.  Two rounds of different orders can never be merged
    that way, and the union scheme is what buys back a round's knife and its ceil
    boundary at once.  `merge_kick` builds that union outright and lets the
    descent repair it; these cases pin the union itself and then the whole pass on
    the batch the search actually wins on.
    """

    def test_the_kick_drops_rounds_and_unions_the_schemes(self):
        import random
        st = [({'A': 2}, 5), ({'B': 3}, 4), ({'C': 1}, 7), ({'D': 4}, 2)]
        got = rq.merge_kick(st, random.Random(0), depth=2)
        self.assertEqual(len(got), 2, 'two rounds must be gone')
        before = {(o): sum(kd.get(o, 0) for kd, _ in st) for kd, _ in st for o in kd}
        after = {o: sum(kd.get(o, 0) for kd, _ in got) for o in before}
        self.assertEqual(before, after, 'every piece count must land somewhere')
        self.assertTrue(all(c in {5, 4, 7, 2} for _, c in got),
                        'survivors keep their own counts; the merged one is gone')

    def test_the_kick_declines_to_empty_a_small_batch(self):
        import random
        st = [({'A': 2}, 5), ({'B': 3}, 4)]
        got = rq.merge_kick(st, random.Random(0), depth=2)
        self.assertEqual(len(got), 2, 'a two-round batch has nothing to merge into')

    def test_a_merge_beats_the_descent_on_the_batch_it_was_found_on(self):
        # bi=1800 of plan_deep1: the descent is a fixpoint there, and the merge
        # buys three rounds for the same bill (65 -> 62 knives, viol 0).  The
        # whole plan is then re-checked, since the round count of a batch is a
        # structural property the checker also has opinions about.
        source = ROOT / 'artifacts/runs/requant/plan_deep1.json'
        if not source.is_file():
            self.skipTest('plan_deep1.json missing (gitignored tree)')
        from platform_check import load_orders_and_blanks, check
        orders, blanks, _, _ = load_orders_and_blanks(ROOT / 'data/semi', 'semi')
        plan = json.loads(source.read_text(encoding='utf-8'))
        bi = 1800
        got = rq.improve_batch(orders, blanks, plan[bi], 0, 0, 8, w_try=2,
                               merges=50, merge_depth=1, merge_seed=11)
        self.assertIsNotNone(got, 'the merge should still improve bi=1800')
        self.assertEqual(got['val'][3], 0, 'a candidate must be violation-free')
        self.assertLess(got['val'][2], got['base'][0][2], 'the merge bought a round')
        lin, cap_c, sizes, dem = rq.batch_ctx(orders, plan[bi])
        b = dict(plan[bi])
        b.update(rq.state_to_fields(got['state'], blanks[int(got['w'])], sizes,
                                    orders, lin))
        b['blank_type'] = int(got['w'])
        parts = rq.split_scheme(b)
        self.assertIsNotNone(parts)
        for p in parts:
            self.assertLessEqual(len(p['counts']), 6, 'clause 4: at most 6 rounds')
        patched = [p for j, x in enumerate(plan) for p in (parts if j == bi else [x])]
        res = check(patched, data=ROOT / 'data/semi', round='semi')
        self.assertTrue(res['passed'], f'patched plan must check: {res["error_counts"]}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
