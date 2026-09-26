"""Semi-final (复赛) rule arithmetic: knives, coverage, continuity, round cap.

Most assertions come from the semi-final `constraints.txt` and the PDF's own
worked example.  The receipt regression at the bottom is different: it is pinned
against four official 2026-09-26 score rows (the submission night), which settled
the knife reading (nearest integer, not floor), the yield numerator (order-side,
Σ min(physical delivered, required)) and the weight table (0.4/0.4/0.2/0).  The
lower half of the file keeps the written-rule cases so a later contradiction is
cheap to localise.
"""
import math
import unittest
from pathlib import Path

from platform_check import read_plan
from platform_score import (Rules, ScoringData, ScoringOrder, detect_violations,
                            evaluate, row_metrics, score_components)

ROOT = Path(__file__).resolve().parents[1]


def order(size_m, diameter_mm=20.0, linear=None):
    linear = linear if linear is not None else math.pi * (diameter_mm / 1000) ** 2 / 4 * 9860
    return ScoringOrder(size_m, diameter_mm, int(diameter_mm), linear, linear, 0.0)


def batch(names, rounds, parallel=5, blanks=2, blank_type=1):
    """rounds is a list of {oid: length_m} dicts, one per cold-bed round."""
    return {'orders': names, 'length_scheme': rounds, 'counts': [parallel] * len(rounds),
            'blank_type': blank_type, 'blank_counts': [blanks] * len(rounds)}


class SemiRuleTests(unittest.TestCase):
    def test_pdf_example_fifty_metres_three_metre_length_is_seventeen_knives(self):
        """PDF 六(一): 16 pieces from a 50 m bar costs 15 parting cuts + 2 trims.

        The rule is written as 段数 + 1, and 17 = 16 + 1.  Under the preliminary
        per-segment rule the same round costs 17 too, but for a two-order round
        the two rules diverge -- see the next test.
        """
        context = ScoringData({'A': order(3.0)}, {1: 1000.0}, 1, ())
        semi, prelim = Rules.semi(), Rules.prelim()
        self.assertEqual(row_metrics({'A': 48.0}, 1, 1, 1, context, semi)[0], 17)
        self.assertEqual(row_metrics({'A': 48.0}, 1, 1, 1, context, prelim)[0], 17)

    def test_shared_round_charges_one_head_tail_pair_not_one_per_order(self):
        """Semi-final knives = round segments + 1; preliminary = segments + #orders.

        Two orders on one round, 4 and 2 pieces: the semi-final rule pays one
        parting per piece plus a single trim pair (7), while the preliminary rule
        charges a trim pair to each order (4+1 + 2+1 = 8).
        """
        context = ScoringData({'A': order(2.0), 'B': order(2.0)}, {1: 1000.0}, 2, ())
        scheme = {'A': 8.0, 'B': 4.0}
        self.assertEqual(row_metrics(scheme, 5, 1, 1, context, Rules.semi())[0], 7)
        self.assertEqual(row_metrics(scheme, 5, 1, 1, context, Rules.prelim())[0], 8)

    def test_knife_count_is_independent_of_parallel_bars(self):
        """All bars are cut by one pass, so `counts` must not scale the knives."""
        context = ScoringData({'A': order(2.0)}, {1: 1000.0}, 1, ())
        counts = {row_metrics({'A': 8.0}, p, 1, 1, context, Rules.semi())[0] for p in (1, 5, 46, 100)}
        self.assertEqual(counts, {5})

    def test_coverage_requires_sharing_one_round(self):
        """constraints.txt / RULES.md §4: same scheme is not enough in the semi-final.

        A alone in round 1, B alone in round 2, in the same scheme: the orders never
        meet on a cold bed, so neither is a combination order.  A+B then B+C makes
        all three combination orders.
        """
        context = ScoringData({k: order(4.0) for k in 'ABC'}, {1: 1e9}, 3, ())
        never = [batch(['A', 'B'], [{'A': 40.0}, {'B': 40.0}])]
        self.assertEqual(evaluate(never, scoring_data=context, rules=Rules.semi())['coverage'], 0.0)
        self.assertEqual(evaluate(never, scoring_data=context, rules=Rules.prelim())['coverage'],
                         2 / 3)

        shared = [batch(['A', 'B'], [{'A': 20.0, 'B': 20.0}]),
                  batch(['B', 'C'], [{'B': 20.0, 'C': 20.0}])]
        self.assertAlmostEqual(evaluate(shared, scoring_data=context, rules=Rules.semi())['coverage'],
                               1.0)

    def test_lone_order_scheme_never_counts_as_coverage(self):
        context = ScoringData({'A': order(4.0)}, {1: 1e9}, 1, ())
        plan = [batch(['A'], [{'A': 40.0}])]
        self.assertEqual(evaluate(plan, scoring_data=context, rules=Rules.semi())['coverage'], 0.0)

    def test_continuity_violation_is_counted_per_order_and_locates_the_gap(self):
        """RULES.md §5: 连续性按轮次计、跳轮按订单计.

        A is cut in rounds 1 and 3 with B alone in round 2 -- exactly the 09-15
        11:11 example -- so A is flagged once, with both round indices reported.
        """
        plan = [batch(['A', 'B'], [{'A': 20.0}, {'B': 20.0}, {'A': 20.0}])]
        context = ScoringData({'A': order(4.0), 'B': order(4.0)}, {1: 1e9}, 2, ())
        violations, detail = detect_violations(plan, context, Rules.semi())
        self.assertEqual(violations.get('continuity'), 1)
        self.assertEqual(detail[0]['order'], 'A')
        self.assertEqual(detail[0]['rounds'], [0, 2])
        # A block of consecutive rounds is fine.
        ok = [batch(['A', 'B'], [{'A': 20.0}, {'A': 20.0, 'B': 20.0}, {'A': 20.0}])]
        self.assertEqual(detect_violations(ok, context, Rules.semi())[0].get('continuity'), None)
        # The preliminary round does not police continuity at all.
        self.assertEqual(detect_violations(plan, context, Rules.prelim())[0], {})

    def test_round_cap_is_six(self):
        """constraints.txt clause 4: 冷床轮数不得超过 6 轮."""
        names = ['A']
        rounds = [{'A': 4.0}] * 7
        plan = [batch(names, rounds)]
        context = ScoringData({'A': order(4.0)}, {1: 1e9}, 1, ())
        violations, _ = detect_violations(plan, context, Rules.semi())
        self.assertEqual(violations.get('round_cap'), 1)
        self.assertEqual(detect_violations([batch(names, rounds[:6])], context, Rules.semi())[0], {})

    def test_penalty_is_five_points_per_violation(self):
        context = ScoringData({'A': order(4.0), 'B': order(4.0)}, {1: 1e9}, 2, ())
        plan = [batch(['A', 'B'], [{'A': 20.0}] * 4 + [{'B': 20.0}] + [{'A': 20.0}])]
        report = evaluate(plan, scoring_data=context, rules=Rules.semi())
        self.assertEqual(report['violation_count'], 1)
        self.assertEqual(report['penalty_points'], 5.0)
        self.assertAlmostEqual(report['score_capped_after_penalty'],
                               report['score_capped'] - 5.0, places=12)

    def test_weight_table_reproduces_the_pdf_and_the_qa_reading(self):
        """PDF prints 40/30/20/10; RULES.md records the Q&A claim (40/40/20/0).

        Settled 2026-09-26: the Q&A reading is operative.  Of the four official
        semi-final receipts only 0.4*K + 0.4*Y + 0.2*C reproduces every displayed
        total (the PDF table would need a time subscore equal to each plan's own
        yield).  The default now carries the certified weights; the PDF table stays
        available as an explicit override for historical comparisons.
        """
        finished, raw, coverage, knives = 95.0, 100.0, 1.0, 90000
        model_b = score_components(knives, finished, raw, coverage, Rules.semi())
        # 0.4*100 + 0.4*95 + 0.2*100
        self.assertAlmostEqual(model_b['score_capped'], 98.0, places=12)
        pdf = score_components(knives, finished, raw, coverage,
                               Rules.semi(weights=(0.4, 0.3, 0.2, 0.1)))
        # 0.4*100 + 0.3*95 + 0.2*100 + 0.1*100
        self.assertAlmostEqual(pdf['score_capped'], 98.5, places=12)

    def test_semi_knives_are_below_prelim_knives_on_multi_order_rounds(self):
        """Sanity: the semi-final rule is a strict saving once a round is shared."""
        context = ScoringData({k: order(2.0) for k in 'ABC'}, {1: 1e9}, 3, ())
        plan = [batch(['A', 'B', 'C'], [{'A': 10.0, 'B': 6.0, 'C': 4.0}])]
        semi = evaluate(plan, scoring_data=context, rules=Rules.semi())
        prelim = evaluate(plan, scoring_data=context, rules=Rules.prelim())
        self.assertEqual(semi['knives'], 11)      # 5 + 3 + 2 segments, +1
        self.assertEqual(prelim['knives'], 13)    # (5+1) + (3+1) + (2+1)
        self.assertLess(semi['knives'], prelim['knives'])


class OfficialReceiptTests(unittest.TestCase):
    """The four 2026-09-26 receipts, reproduced by the scorer exactly.

    Each candidate ZIP was submitted and the platform returned a row with
    刀数/成材率/覆盖率/总分.  The scorer must reproduce all four numbers for every
    package -- that is the whole calibration of the semi rule set.  The ZIPs live
    under the gitignored `artifacts/candidates/`, so the cases skip when the
    working tree has been cleaned.
    """

    RECEIPTS = {
        'cand_rounded': (185229, 91.39, 97.43, 90.59),
        'cand_covprobe25': (185233, 91.40, 96.46, 90.40),
        'cand_covprobe': (185252, 91.41, 93.81, 89.87),
        'cand_yieldprobe': (185229, 87.21, 97.43, 88.92),
    }

    def test_official_receipts_are_reproduced(self):
        for name, (knives, yield_pct, coverage_pct, total) in self.RECEIPTS.items():
            folder = ROOT / 'artifacts/candidates' / name
            zips = sorted(folder.glob('*.zip')) if folder.is_dir() else []
            if not zips:
                self.skipTest(f'candidate artifact missing: {name} (gitignored tree)')
            report = evaluate(read_plan(zips[0]), ROOT / 'data/semi', round_name='semi')
            with self.subTest(candidate=name):
                self.assertEqual(report['knives'], knives)
                self.assertEqual(report['yield_percent_rounded'], yield_pct)
                self.assertEqual(report['coverage_percent_rounded'], coverage_pct)
                self.assertEqual(report['score_capped_display'], total)
                self.assertEqual(report['violation_count'], 0)
                self.assertEqual(report['included_order_count'], 9999)


if __name__ == '__main__':
    unittest.main(verbosity=2)
