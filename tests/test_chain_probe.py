"""chain_probe: route B's acceptance sample, and the `order=` knob it rests on.

The probe's whole claim is "the CHAIN changed, not the budget", so the pieces that
carry that claim are pinned here: `layout` must honour a forced sequence (and
refuse a sequence that is not a permutation), and the perturbation generator must
only ever emit permutations of the input.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CP = _load('chain_probe', 'tools/analysis/chain_probe.py')
CL = _load('chain_layout', 'tools/analysis/chain_layout.py')


def mk(schemes, counts, blank_type=1):
    out = {'orders': [], 'length_scheme': list(schemes), 'counts': list(counts),
           'blank_type': blank_type, 'blank_counts': []}
    for s in schemes:
        for o in s:
            if o not in out['orders']:
                out['orders'].append(o)
    return out


# `chain_layout.layout` divides lengths by `sizes`, so it takes FLOATS; only
# `requant_batches` keeps 定尺 as Decimals.  Pin the float contract here.
SIZES = {o: 10.0 for o in 'ABCD'}


class LayoutOrderTests(unittest.TestCase):
    """`order=` is what makes the chain a variable instead of a constant."""

    def setUp(self):
        # Totals 100/60/60/40 -> 260 m, two rounds of 130.
        self.b = mk([{'D': 100.0, 'A': 30.0}, {'A': 30.0, 'B': 60.0, 'C': 40.0}],
                    [10, 10])

    def test_default_is_the_descending_total_chain(self):
        default = CL.layout(self.b, SIZES)
        self.assertIsNotNone(default)
        forced = CL.layout(self.b, SIZES, order=['D', 'A', 'B', 'C'])
        self.assertEqual(default, forced)

    def test_a_different_chain_cuts_different_rounds(self):
        default = CL.layout(self.b, SIZES)
        other = CL.layout(self.b, SIZES, order=['C', 'B', 'A', 'D'])
        self.assertIsNotNone(other)
        self.assertNotEqual([frozenset(r) for r in default],
                            [frozenset(r) for r in other])

    def test_a_chain_that_is_not_a_permutation_is_refused(self):
        self.assertIsNone(CL.layout(self.b, SIZES, order=['A', 'B', 'C']))       # short
        self.assertIsNone(CL.layout(self.b, SIZES, order=['A', 'B', 'C', 'D', 'D']))


class PerturbationTests(unittest.TestCase):
    """Every perturbation must be a permutation, and the families must be present."""

    def test_only_permutations_are_emitted_and_the_families_show_up(self):
        seq = list('ABCD')
        cands = CP.perturbations(seq)
        self.assertTrue(cands)
        for _label, cand in cands:
            self.assertEqual(sorted(cand), sorted(seq))
            self.assertNotEqual(cand, seq)                # the identity is excluded
        labels = [label for label, _ in cands]
        self.assertTrue(any(x.startswith('swap') for x in labels))
        self.assertTrue(any(x.startswith('move') for x in labels))
        self.assertTrue(any(x.startswith('reorder') for x in labels))
        keys = [tuple(c) for _, c in cands]
        self.assertEqual(len(keys), len(set(keys)))       # deduped

    def test_max_arms_bounds_the_sample(self):
        cands = CP.perturbations(list('ABCDEFGH'), max_arms=7)
        self.assertEqual(len(cands), 7)


class StateFromLayoutTests(unittest.TestCase):
    """The layout's pieces must convert to a state that delivers every order."""

    def test_clearing_count_covers_every_floor(self):
        st = [{'A': 3}, {'A': 2, 'B': 5}]
        # A is held 5 times, needs 11 -> count 3; B held 5, needs 4 -> count 1.
        count = CP.clearing_count(st, {'A': 11, 'B': 4})
        self.assertEqual(count, 3)

    def test_clearing_count_refuses_a_chain_that_dropped_an_order(self):
        self.assertIsNone(CP.clearing_count([{'A': 1}], {'A': 1, 'B': 1}))

    def test_rounds_to_state_refuses_off_grid_lengths(self):
        self.assertIsNone(CP.rounds_to_state([{'A': 55.0}], SIZES))   # not a 10 m multiple
        self.assertEqual(CP.rounds_to_state([{'A': 50.0}], SIZES), [{'A': 5}])


class BoundarySigTests(unittest.TestCase):
    """The shape signature has to tell a moved boundary from a moved number."""

    def test_reordered_rounds_and_a_new_seam_both_change_the_signature(self):
        a = [({'A': 1, 'B': 1}, 10), ({'B': 1, 'C': 1}, 10)]
        same_numbers_other_rounds = [({'A': 1, 'C': 1}, 10), ({'B': 2}, 10)]
        self.assertNotEqual(CP.boundary_sig(a), CP.boundary_sig(same_numbers_other_rounds))
        self.assertEqual(CP.boundary_sig(a), CP.boundary_sig([({'A': 1, 'B': 1}, 99),
                                                              ({'B': 1, 'C': 1}, 99)]))


MERGE2 = ROOT / 'artifacts/runs/requant/plan_merge2.json'


class PickBatchTests(unittest.TestCase):
    """Sample batches must be ones the experiment can actually run on.

    Ranking by order count alone picks batch 1478 -- 13 orders, the most of any
    batch, and the one batch whose own chain `chain_layout` refuses.  Arm A1 would
    not exist there and a treatment win could not be told from the plain re-lay.
    """

    def test_every_picked_batch_lays_its_own_chain_out_worst_loss_first(self):
        if not MERGE2.is_file():
            self.skipTest('plan_merge2.json missing (gitignored tree)')
        data = ROOT / 'data' / 'semi'
        if not data.is_dir():
            self.skipTest('data/semi missing')
        from platform_check import load_orders_and_blanks, read_plan
        orders, blanks, _, _ = load_orders_and_blanks(data, 'semi')
        plan = read_plan(MERGE2)

        ranked = CP.pick_batch(plan, orders, blanks)
        self.assertTrue(ranked)
        losses = [r[0] for r in ranked]
        self.assertEqual(losses, sorted(losses, reverse=True))
        for loss, bi, seq, n in ranked:
            self.assertGreaterEqual(n, 3)
            self.assertEqual(seq, CP.chain_of(plan[bi]))
            sizes_f = {o: float(orders[o]['size']) for o in plan[bi]['orders']}
            self.assertIsNotNone(
                CL.layout(dict(plan[bi]), sizes_f, order=seq),
                f'batch {bi} was picked but its own chain will not lay out')


class ScreenTests(unittest.TestCase):
    """The census denominator has to be measured, because it is not always non-zero.

    Batch 89 pins count at 44 (its own demand) with a bed-width cap of 44, so the
    ceiling is 84.957 m against a 507.1 m chain and every one of its 55 perturbations
    overflows.  Reading that as "route B does not pay" would be wrong -- the
    neighbourhood is empty, not evaluated.
    """

    def test_screen_reports_a_denominator_per_batch(self):
        if not MERGE2.is_file():
            self.skipTest('plan_merge2.json missing (gitignored tree)')
        data = ROOT / 'data' / 'semi'
        if not data.is_dir():
            self.skipTest('data/semi missing')
        from platform_check import load_orders_and_blanks, read_plan
        orders, blanks, _, _ = load_orders_and_blanks(data, 'semi')
        plan = read_plan(MERGE2)

        ranked = CP.pick_batch(plan, orders, blanks, top=3)
        rows = CP.screen(plan, orders, blanks, top=3)
        self.assertEqual([r[0] for r in rows], [r[1] for r in ranked])
        for (loss, _bi, _seq, n), (bi, n2, loss2, legal, screened, a1) in zip(ranked, rows):
            self.assertEqual((bi, n2, loss2), (_bi, n, loss))
            self.assertLessEqual(0, legal)
            self.assertLessEqual(legal, screened)
            self.assertIsInstance(a1, bool)


if __name__ == '__main__':
    unittest.main()
