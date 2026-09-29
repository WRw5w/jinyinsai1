"""Whole-plan metrics: the number experiments rank on, and the one that ships.

Two things have to hold.  First, the score must be the canonical scorer's, not a
parallel arithmetic that agrees today -- `metrics()` asserts that, and these cases
pin the assertion in both directions.  Second, the arithmetic that turns a plan
into K / M / D / H must be exact: the blank bill is a round-ceiling on a
half-open interval, where a 1e-7 slip moves a whole blank, so it is checked
against `Fraction` rather than a float.

The regression case against `plan_r14` is the number the auditor asked to be
reproducible from a fixed commit; it skips on a clean checkout because the plan
artefacts are gitignored, the same way the repo's other real-data tests do.
"""
import json
import unittest
from fractions import Fraction as F
from pathlib import Path

from plan_metrics import _bill, _check_closed_form, default_data, metrics

ROOT = Path(__file__).resolve().parents[1]


class DataDirTests(unittest.TestCase):
    """The CSV directory is round-scoped: `data/semi`, not `data`."""

    def test_default_data_is_round_scoped(self):
        self.assertEqual(default_data('semi'), ROOT / 'data' / 'semi')
        self.assertEqual(default_data('prelim'), ROOT / 'data' / 'prelim')

    def test_metrics_defaults_to_the_round_directory(self):
        # A plan is only readable against one round's tables, so the default has
        # to point at the round's own directory; `data/` has no orders_semi.csv.
        self.assertTrue((default_data('semi') / 'orders_semi.csv').is_file())


class BlankBillTests(unittest.TestCase):
    """`_bill`: declared kg for material m at blank weight w, half-open ceiling."""

    def test_ceils_up_to_a_whole_blank(self):
        self.assertEqual(_bill(F(100), F(3)), F(102))       # 33.33 -> 34 blanks

    def test_exact_multiple_does_not_add_a_blank(self):
        self.assertEqual(_bill(F(99), F(3)), F(99))

    def test_epsilon_makes_an_exact_multiple_a_half_open_interval(self):
        # (m - 1e-7) / w must land just *below* the integer, so a plan whose
        # material exactly fills its blanks is not billed one blank extra.
        self.assertEqual(_bill(F(100) - F(1, 10 ** 7), F(100)), F(100))

    def test_fractional_blank_weights_stay_fractional(self):
        # Three of the five types are not whole kilograms; billing on a truncated
        # weight is what `cost_of` used to do.
        self.assertEqual(_bill(F('9613.5'), F('9613.5')), F('9613.5'))
        self.assertEqual(_bill(F('6957.216'), F('6957.216')), F('6957.216'))


class ClosedFormTests(unittest.TestCase):
    """The printed score must be the scorer's -- a divergence must fail loudly."""

    def _scored(self, **over):
        base = dict(rules=dict(baseline_knives=160000.0), violation_count=0)
        return {**base, **over}

    def test_agrees_with_the_scorer(self):
        s = dict(knives=184415, yield_pct=94.7603433672152, cov_pct=100.0,
                 violation_count=0, score=92.60847267752666)
        _check_closed_form(s, self._scored())          # must not raise

    def test_a_divergent_score_raises(self):
        s = dict(knives=184415, yield_pct=94.7603433672152, cov_pct=100.0,
                 violation_count=0, score=93.0)
        with self.assertRaises(AssertionError):
            _check_closed_form(s, self._scored())

    def test_at_or_below_the_baseline_the_cap_makes_it_unassertable(self):
        # Below 160000 knives `evaluate` caps the knife subscore at 100, so the
        # closed form legitimately differs; the guard must skip, not fire.
        s = dict(knives=87270, yield_pct=96.42, cov_pct=30.0,
                 violation_count=0, score=98.93)
        _check_closed_form(s, self._scored())

    def test_a_violation_makes_it_unassertable(self):
        s = dict(knives=184415, yield_pct=94.7603433672152, cov_pct=100.0,
                 violation_count=3, score=77.6)
        _check_closed_form(s, self._scored())


class PlanR14RegressionTests(unittest.TestCase):
    """The figure the audit asked to be reproducible from a fixed commit."""

    PLAN = ROOT / 'artifacts/runs/requant/plan_r14.json'

    def setUp(self):
        if not self.PLAN.is_file():
            self.skipTest(f'{self.PLAN.name} missing (gitignored tree)')
        self.m = metrics(json.loads(self.PLAN.read_text(encoding='utf-8')))

    def test_components_match_the_published_figures(self):
        m = self.m
        self.assertEqual(m['knives'], 184415)
        self.assertEqual(m['finished_weight'], 506157994.0)
        self.assertEqual(m['shared'], 9999)
        self.assertEqual(m['violation_count'], 0)
        self.assertEqual(m['schemes'], 2303)
        self.assertEqual(m['rounds'], 10233)

    def test_the_material_figure_is_the_exact_one(self):
        # The audit's 534,145,622 kg was inferred from a *displayed* yield; the
        # real figure is ~244 kg lower, and it is the new baseline.
        self.assertAlmostEqual(float(self.m['raw_weight']), 534145377.71, places=2)

    def test_the_score_is_the_canonical_one(self):
        self.assertAlmostEqual(self.m['score'], 92.60847267752666, places=13)

    def test_the_assertion_inside_metrics_ran(self):
        # `metrics` asserts the closed form equals the scorer; on this plan that
        # guard is live, so a silent divergence cannot pass this case.
        self.assertGreater(self.m['knives'], 160000)


if __name__ == '__main__':
    unittest.main()
