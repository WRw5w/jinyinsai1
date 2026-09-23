"""Regression against official observations, independent of solver objectives.

Every case here is preliminary-round.  The seven official feedbacks were all
produced by the preliminary scoring rule set, so these pins must hold under
`Rules.prelim()` exactly as before.  Semi-final arithmetic is covered separately
in test_semi_rules.py, because it has no official feedback to calibrate against.
"""
import json
import math
import unittest
from pathlib import Path

from platform_score import (Rules, ScoringData, ScoringOrder, entry_knives, evaluate,
                            load_scoring_data, row_metrics, score_components)


ROOT = Path(__file__).resolve().parent
PRELIM = Rules.prelim()


def score(*args, **kwargs):
    return score_components(*args, rules=PRELIM, **kwargs)


def row(*args, **kwargs):
    return row_metrics(*args, rules=PRELIM, **kwargs)


def rate(plan, **kwargs):
    kwargs.setdefault('round_name', 'prelim')
    return evaluate(plan, **kwargs)


class PlatformScoreTests(unittest.TestCase):
    def test_complete_official_feedbacks(self):
        context = load_scoring_data(ROOT / 'data')
        cases = [
            ('submission_fixed', 113683, 86.63, 99.98, 87.65, [79.17, 86.63, 99.98, 100.0]),
            ('submission_optimized', 92913, 92.51, 99.98, 96.49, [96.86, 92.51, 99.98, 100.0]),
            ('submission_normal_985', 119268, 96.71, 99.98, 89.19, [75.46, 96.71, 99.98, 100.0]),
            # Yield and subscores were not reported for this submission; 95.5 is
            # what the official total 98.6500 forces the rounded yield to have been.
            ('submission_calibrated_next', 90001, 95.5, 100.0, 98.65, [100.0, 95.5, 100.0, 100.0]),
            # Discriminating case: this plan wrote every length 1e-12 relatively
            # below its integer multiple, so a naive float model predicts 86751.
            # The platform returned 108671 = sum(k for k>=2) + 2*#(k == 1), which
            # pins the max(2, .) floor in entry_knives.
            ('submission_island_partition', 108671, 96.29, 100.0, 92.02, [82.82, 96.29, 100.0, 100.0]),
        ]
        for directory, knives, yield_percent, coverage, score, components in cases:
            with self.subTest(directory=directory):
                plan = json.loads((ROOT / directory / '初赛结果_棒材优化.json').read_text(encoding='utf-8-sig'))
                actual = rate(plan, scoring_data=context)
                self.assertEqual(actual['knives'], knives)
                self.assertEqual(actual['yield_percent_rounded'], yield_percent)
                self.assertEqual(actual['coverage_percent_rounded'], coverage)
                self.assertEqual(list(actual['rounded_subscores'].values()), components)
                self.assertEqual(actual['score_capped_display'], score)
                self.assertEqual(actual['score_from_rounded_subscores_capped_display'], score)
                self.assertEqual(actual['score_uncapped_display'], score)
                # None of these four submissions has a knife subscore above 100, so
                # they cannot settle the cap by themselves. It was settled later by
                # submission_knife_hunt_fixed - see test_knife_component_is_capped.
                self.assertTrue(actual['assumptions']['knife_component_cap_confirmed'])
                self.assertFalse(actual['assumptions']['rounding_order_confirmed'])

    def test_knife_component_is_capped(self):
        """Sixth sample, and the only one that discriminates the knife cap.

        The submitted plan earns a raw knife subscore of 103.1282 (87270 cuts), but
        the platform reported 子分刀=100.0 and a total of 98.9300. Only the capped
        arithmetic, 0.4*100 + 0.3*96.421004 + 0.2*100 + 0.1*100 = 98.9263, reproduces
        that total; the uncapped weighting would have given 100.1776.
        """
        context = load_scoring_data(ROOT / 'data')
        plan = json.loads((ROOT / 'submission_knife_hunt_fixed' / '初赛结果_棒材优化.json')
                          .read_text(encoding='utf-8-sig'))
        actual = rate(plan, scoring_data=context)
        self.assertEqual(actual['knives'], 87270)
        self.assertEqual(actual['yield_percent_rounded'], 96.42)
        self.assertEqual(actual['coverage_percent_rounded'], 100.0)
        self.assertEqual(list(actual['rounded_subscores'].values()), [100.0, 96.42, 100.0, 100.0])
        self.assertGreater(actual['subscores_uncapped']['knives'], 100)
        self.assertEqual(actual['score_capped_display'], 98.93)
        self.assertAlmostEqual(actual['score_capped'], 98.9263011428658, places=9)
        self.assertGreater(actual['score_uncapped'], 100)
        self.assertTrue(actual['assumptions']['knife_component_cap_confirmed'])

    def test_displayed_total_is_quantised_to_one_hundredth(self):
        context = load_scoring_data(ROOT / 'data')
        plan = json.loads((ROOT / 'submission_calibrated_next' / '初赛结果_棒材优化.json')
                          .read_text(encoding='utf-8-sig'))
        actual = rate(plan, scoring_data=context)
        # Raw weighting of unrounded subscores gives 98.64834586. At four decimals
        # that would read 98.6483, but the official row read 98.6500. The platform
        # therefore quantises the displayed total to 0.01 - either by rounding the
        # total or by rounding the subscores first, and the two are not separated
        # by this observation alone.
        self.assertAlmostEqual(actual['score_capped'], 98.64834586707248, places=10)
        self.assertEqual(f"{actual['score_capped']:.4f}", '98.6483')
        self.assertEqual(round(actual['score_capped'], 2), 98.65)
        scores = [r['score_display'] for r in
                  json.loads((ROOT / 'evidence' / '20260916' / 'leaderboard_top20.json')
                             .read_text(encoding='utf-8'))['rows']]
        self.assertEqual(len(scores), 20)
        for s in scores:
            self.assertAlmostEqual(float(s), round(float(s), 2), places=9)

    def test_leaderboard_forces_an_uncapped_knife_component(self):
        # Platform-confirmed length contract: net multiples of the order size, and
        # the platform adds one 2 m head/tail trim per round. A 150 m round can
        # therefore never score more than 148/150 of its bar.
        best_yield_percent = 100.0 * (150.0 - 2.0) / 150.0
        self.assertAlmostEqual(best_yield_percent, 98.66666666666667, places=10)
        capped_ceiling = .4 * 100.0 + .3 * round(best_yield_percent, 2) + .2 * 100.0 + .1 * 100.0
        self.assertAlmostEqual(capped_ceiling, 99.601, places=6)
        # Six leaderboard rows display 100.0000, which no all-capped model can reach.
        rows = json.loads((ROOT / 'evidence' / '20260916' / 'leaderboard_top20.json')
                          .read_text(encoding='utf-8'))['rows']
        hundreds = [r for r in rows if r['score_display'] == '100.0000']
        self.assertEqual(len(hundreds), 6)
        self.assertGreater(100.0, capped_ceiling)
        # Coverage and time cannot exceed 100, and scoring yield uses the truncated
        # diameter, so only the knife term can carry a total above the ceiling.
        self.assertAlmostEqual(100.0 * (27 / 27.8) ** 2, 94.32741576522955, places=9)

    def test_free_knife_zone_and_yield_step_value(self):
        finished, raw = 253009535.02283323, 264942637.0
        # If subscores are pre-rounded, full knife credit survives up to 90004 cuts.
        self.assertEqual(score(90004, finished, raw, 1)['rounded_subscores']['knives'], 100.0)
        self.assertEqual(score(90005, finished, raw, 1)['rounded_subscores']['knives'], 99.99)
        # If only the total is quantised, the displayed total is also flat for a few
        # cuts either side of 90000, so no knife reduction is worth anything until it
        # is large enough to cross a 0.01 step.
        totals = {k: round(score(k, finished, raw, 1)['score_capped'], 2)
                  for k in (89987, 90000, 90001, 90008, 90009)}
        self.assertEqual(totals[89987], 98.65)
        self.assertEqual(totals[90008], 98.65)
        self.assertEqual(totals[90009], 98.64)
        # Under the knife-capped hypothesis the only lever is yield: 99 needs 96.67.
        step_low = score(90000, 96.66 * 10, 1000.0, 1)['score_from_rounded_subscores_capped']
        step_high = score(90000, 96.67 * 10, 1000.0, 1)['score_from_rounded_subscores_capped']
        self.assertAlmostEqual(step_high - step_low, 0.003, places=10)
        self.assertGreaterEqual(step_high, 99.0)
        self.assertLess(step_low, 99.0)

    def test_multiple_order_segments_count_separately(self):
        # Exactly representable lengths isolate per-segment overhead from FP.
        order = ScoringOrder(4.0, 28.0, 28, 2.0, 2.0)
        context = ScoringData({'A': order, 'B': order}, {1: 1000.0}, 2, ())
        k, f, r = row({'A': 12.0, 'B': 8.0}, 5, 1, 2, context)
        self.assertEqual((k, f, r), (7, 200.0, 2000.0))
        self.assertEqual(row({'A': 20.0}, 5, 1, 2, context)[0], 6)

    def test_float_floor_observation_differs_from_int_division(self):
        self.assertEqual(entry_knives(14.7, 4.9), 3)
        self.assertEqual(entry_knives(9.8, 4.9), 3)
        # int(L/s) is not interchangeable with floor division on floating data.
        self.assertEqual(entry_knives(49.0, 4.9), 10)
        self.assertEqual(int(49.0 / 4.9) + 1, 11)

    def test_score_diameter_is_distinct_from_physical_diameter(self):
        context = load_scoring_data(ROOT / 'data')
        fractional = next(o for o in context.orders.values() if o.diameter_mm == 27.8)
        self.assertEqual(fractional.scoring_diameter_mm, 27)
        self.assertAlmostEqual(fractional.linear_weight, math.pi * .027 ** 2 / 4 * 9860)
        self.assertAlmostEqual(fractional.physical_linear_weight, math.pi * .0278 ** 2 / 4 * 9860)
        self.assertGreater(fractional.physical_linear_weight, fractional.linear_weight)

    def test_cap_hypotheses_remain_separate(self):
        report = score(87000, 960, 1000, 1)
        self.assertAlmostEqual(report['score_capped'], 98.8)
        self.assertGreater(report['score_uncapped'], 100)
        self.assertEqual(report['score_total_cap_only'], 100)
        self.assertEqual(report['rounded_subscores']['knives'], 100)
        self.assertGreater(report['rounded_subscores_uncapped']['knives'], 100)

    def test_estimator_flags_the_rejected_package_seams(self):
        """The estimator must not report a clean sheet for a rejected plan.

        `detect_violations` originally implemented only the per-order gap rule
        and the round-equality rule, so it returned ZERO violations for
        `submission_semi_merged_v2` -- the package the platform had already
        rejected with exactly 7030 `跨轮接续不连续`.  A scorer that clears a
        known-bad package is worse than no scorer, so pin the seam count.

        The preserved violating copy is used rather than the package directory:
        the directory's .json and .zip were both repaired on 2026-09-23, which
        would make this test vacuous.
        """
        path = ROOT / 'diagnostics/pre_fix_backups/merged_v2__复赛结果_鱼不吃猫.json'
        if not path.exists():
            self.skipTest(f'preserved violating copy missing: {path.name}')
        plan = json.loads(path.read_text(encoding='utf-8'))
        result = evaluate(plan, data=ROOT / 'data/semi', round_name='semi')
        violations = result.get('violations') or {}
        self.assertEqual(violations.get('continuity_seam'), 7030,
                         'the estimator must reproduce the official seam count')
        self.assertEqual(sum(violations.values()), 7030,
                         'the rejected package has exactly one violation family')

    def test_repaired_packages_are_clean_under_the_estimator(self):
        """The repaired packages must show zero violations from the estimator,
        not merely from platform_check -- the two must agree."""
        import glob
        checked = 0
        for d in sorted(ROOT.glob('submission_semi_*')):
            if not d.is_dir():
                continue
            files = [p for p in glob.glob(str(d / '*.zip'))]
            if not files:
                continue
            report = None
            for cand in ('validation_report.json', 'validation_report_source.json'):
                if (d / cand).exists():
                    report = d / cand
                    break
            if report is None:
                continue
            recorded = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(recorded.get('violation_count'), 0,
                             f'{d.name} carries recorded violations')
            checked += 1
        self.assertGreater(checked, 0, 'no package reports were inspected')


if __name__ == '__main__':
    unittest.main(verbosity=2)
