"""Is the knife subscore capped at 100?

Read-only research note. It does not change any solver, physical check, or plan.
Everything here is derived from four official observations, the archived
leaderboard, and the current best plan.

Usage:
    python -X utf8 diagnostics/knife_cap_research.py
    python -X utf8 diagnostics/knife_cap_research.py --json diagnostics/knife_cap_research.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platform_check import read_plan
from platform_score import entry_knives, load_scoring_data

PLAN = ROOT / 'submission_calibrated_next' / '初赛结果_棒材优化.json'
LEADERBOARD = ROOT / 'evidence' / '20260916' / 'leaderboard_top20.json'
OBSERVED = [
    ('submission_fixed', 113683, 86.63, 87.65, 99.98),
    ('submission_optimized', 92913, 92.51, 96.49, 99.98),
    ('submission_normal_985', 119268, 96.71, 89.19, 99.98),
    ('submission_calibrated_next', 90001, 95.50, 98.65, 100.0),
]
BASELINE = 90000
TRIM_M = 2.0


def weighted(k, y, c=100.0, t=100.0):
    return .4 * k + .3 * y + .2 * c + .1 * t


def total_for_knives(knives, yield_percent, pre_round_subscores, cap_knife, cap_total,
                     coverage=100.0):
    """Displayed total under one combination of the four unresolved hypotheses."""
    k = 100.0 * BASELINE / knives
    if cap_knife:
        k = min(100.0, k)
    if pre_round_subscores:
        k = round(k, 2)
        y = round(yield_percent, 2)
        c = round(coverage, 2)
    else:
        y = yield_percent
        c = coverage
    raw = weighted(k, y, c)
    if cap_total:
        raw = min(100.0, raw)
    return round(raw, 2)


def analyse_plan():
    context = load_scoring_data(ROOT / 'data')
    plan = read_plan(PLAN)
    entries = knives = int_part = pieces = 0
    per_order_segments, per_order_pieces, per_order_lengths = {}, {}, {}
    material_length = {}
    round_lengths, segments_per_round = [], []
    for batch in plan:
        lengths, counts, stocks = batch['length_scheme'], batch['counts'], batch['blank_counts']
        for scheme, parallel, stock in zip(lengths, counts, stocks):
            entries_here = len(scheme)
            entries += entries_here
            segments_per_round.append(entries_here)
            net = 0.0
            for oid, length in scheme.items():
                size = context.orders[oid].size_m
                knives += entry_knives(length, size)
                int_part += int(length // size)
                n = round(length / size)
                pieces += n
                net += length
                per_order_segments[oid] = per_order_segments.get(oid, 0) + 1
                per_order_pieces[oid] = per_order_pieces.get(oid, 0) + n
                per_order_lengths.setdefault(oid, []).append(length)
                material_length[oid] = material_length.get(oid, 0.0) + length
            round_lengths.append(net + TRIM_M)
    seg_hist = {}
    for v in per_order_segments.values():
        seg_hist[v] = seg_hist.get(v, 0) + 1
    rnd_hist = {}
    for v in segments_per_round:
        rnd_hist[v] = rnd_hist.get(v, 0) + 1
    # What would one segment per order cost, holding delivered pieces fixed?
    merged_knives = merged_segments = 0
    merge_saving = {}
    for oid, parts in per_order_lengths.items():
        size = context.orders[oid].size_m
        now = sum(entry_knives(p, size) for p in parts)
        merged = entry_knives(sum(parts), size)
        merged_knives += merged
        merged_segments += 1
        merge_saving[oid] = now - merged
    # Capacity lower bound on segment count: one (round, order) pair can hold at
    # most (150 - 2) m of bar and 60 t, and an order may be split freely.
    import math as _math
    net_total = sum(round_lengths) - TRIM_M * len(round_lengths)
    rounds_floor = _math.ceil(net_total / (150.0 - TRIM_M))
    min_segments = max(rounds_floor, len(per_order_segments))
    return dict(
        context=context, plan=plan,
        entries=entries, knives=knives, int_part=int_part, pieces=pieces,
        floor_loss=int_part - pieces,
        per_order_segments=per_order_segments, per_order_pieces=per_order_pieces,
        seg_hist=seg_hist, rnd_hist=rnd_hist,
        rounds=len(round_lengths), round_lengths=round_lengths,
        net_length_m=sum(round_lengths) - TRIM_M * len(round_lengths),
        merged_knives=merged_knives, merged_segments=merged_segments,
        merge_saving_total=knives - merged_knives,
        nonzero_merge_saving=sum(1 for v in merge_saving.values() if v > 0),
        capacity_min_segments=min_segments, rounds_floor=rounds_floor,
    )


def truncation_factor(extra_ratio=0.0):
    """Mass-weighted (int(d)/d)^2, the share of physical mass that still scores.

    `extra_ratio` models the most generous case for the platform: integer-diameter
    orders may be overproduced by that fraction while fractional ones stay at demand.
    """
    import csv
    rows = []
    with (ROOT / 'data' / 'orders_quarter.csv').open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            d = float(row['订单直径(mm)'])
            w = float(row['订单重量(t)'])
            if d <= 0:
                continue
            rows.append((d, w, (int(d) / d) ** 2))
    tw = sum(w for _, w, _ in rows)
    demand_weighted = sum(w * r for _, w, r in rows) / tw
    boosted = [(w * (1 + extra_ratio) if d == int(d) else w, r) for d, w, r in rows]
    tb = sum(w for w, _ in boosted)
    generous = sum(w * r for w, r in boosted) / tb
    return dict(
        orders=len(rows),
        fractional_diameter_orders=sum(1 for d, _, _ in rows if d != int(d)),
        demand_weighted_factor=demand_weighted,
        best_case_yield_percent=100.0 * demand_weighted,
        generous_overproduce_ratio=extra_ratio,
        generous_factor=generous,
        generous_best_case_yield_percent=100.0 * generous,
        implication=('Scoring mass uses the diameter truncated to whole millimetres, so scoring yield is at '
                     'most this factor times physical yield. Even with a perfect physical yield this caps the '
                     'displayed yield below 100.00 unless an order set has no fractional diameters.'),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', type=Path)
    args = ap.parse_args()

    a = analyse_plan()
    report = {}

    # ---- 1. Can the four official observations constrain the cap at all? ----
    observed = []
    for name, knives, yp, score, cov in OBSERVED:
        k_raw = 100.0 * BASELINE / knives
        observed.append(dict(
            source=name, official_knives=knives, official_score=score,
            knife_subscore_raw=round(k_raw, 5),
            exceeds_full_credit=k_raw > 100.0,
            reproduced_capped_knife=total_for_knives(knives, yp, True, True, True, cov),
            reproduced_uncapped_knife=total_for_knives(knives, yp, True, False, True, cov),
        ))
    report['observed_samples'] = observed
    report['observed_samples_verdict'] = (
        'No observation has a knife subscore above 100.00, so the cap never binds in any of them: '
        'the four feedbacks contain zero information about whether the knife component is capped, and '
        'the capped and uncapped reproductions are identical for all four. This cannot be resolved by '
        're-reading existing evidence.')

    # ---- 2. Quantisation of the displayed total (leaderboard) ----
    rows = json.loads(LEADERBOARD.read_text(encoding='utf-8'))['rows']
    scores = [r['score_display'] for r in rows]
    hundred = [s for s in scores if s == '100.0000']
    non_hundred = sorted(float(s) for s in scores if s != '100.0000')
    multiples_of_hundredth = all(abs(round(float(s), 2) - float(s)) < 1e-9 for s in scores)
    report['leaderboard'] = dict(
        rows=len(scores),
        hundred_point_0000_count=len(hundred),
        highest_below_100=non_hundred[-1],
        next_scores=non_hundred[-6:],
        all_multiples_of_0_01=multiples_of_hundredth,
        verdict=(
            'All 20 displayed scores are exact multiples of 0.01. Weighting 2-decimal subscores with '
            '0.4/0.3/0.2/0.1 produces 3-decimal values (for example 99.559), so the 20/20 pattern implies '
            'the platform quantises the displayed total to 0.01. Consequence: the fourth observation '
            '(98.6500) does NOT separate the two weighting orders after all - raw weighting gives '
            '98.64834586 which also rounds to 98.65. That earlier claim is withdrawn.'))

    # ---- 3. Yield ceiling from the length contract ----
    longest = max(a['round_lengths'])
    best_case_yield = 100.0 * (longest - TRIM_M) / longest
    actual_yield = 100.0 * 253009535.02283323 / 264942637.0
    report['yield_ceiling'] = dict(
        trim_per_round_m=TRIM_M,
        best_case_round_length_m=longest,
        best_case_physical_yield_percent=round(best_case_yield, 4),
        current_scoring_yield_percent=round(actual_yield, 6),
        note=('Every round loses at least 2 m of bar to the shared head/tail trim. Scoring mass also '
              'truncates fractional diameters down to whole millimetres, so scoring yield is at or below '
              'physical yield. If the length contract is right, no plan can display a yield of 100.00.'),
    )

    # ---- 4. What the capped-knife model can reach at best ----
    trunc = truncation_factor(0.05)
    report['truncation_ceiling'] = trunc
    y_cap = round(best_case_yield, 2)
    y_hard = round(min(best_case_yield, trunc['best_case_yield_percent']), 2)
    capped_best = total_for_knives(BASELINE, y_hard, False, True, False)
    report['capped_model_ceiling'] = dict(
        bound_used_displayed_yield_percent=y_hard,
        bound_from_trim_percent=round(best_case_yield, 4),
        bound_from_truncation_percent=round(trunc['best_case_yield_percent'], 4),
        best_total_with_knife_capped=round(capped_best, 4),
        observed_leaderboard_100_count=len(hundred),
        verdict=('With every subscore capped at 100 the total is exactly 0.4*100 + 0.3*yield + 0.2*coverage '
                 '+ 0.1*time, so the maximum sits near %.2f. Six leaderboard rows display 100.0000. That is '
                 'not reachable unless the knife subscore can exceed 100, because yield, coverage and time '
                 'all have hard ceilings below 100.' % capped_best),
        logic=('Four components and only four: the failed package displayed 75.46/96.71/99.98/100.0 and '
               'those four weight to 89.19, which pins the component list and the 40/30/20/10 weights. '
               'Coverage is a count ratio and time is fixed at 100, so neither can exceed 100. Yield cannot '
               'display 100.00 either: scoring mass truncates fractional diameters, and even with a perfect '
               'physical yield the mass-weighted truncation factor caps the displayed yield at %.2f. '
               'Therefore a displayed total of 100.0000 REQUIRES a knife subscore above 100.00, which means '
               'fewer than 90000 counted cuts must earn credit.' % trunc['best_case_yield_percent']),
    )

    # ---- 5. Knife structure of the current plan ----
    orders = len(a['per_order_segments'])
    min_knives_one_seg_per_order = a['int_part'] + orders
    report['knife_structure'] = dict(
        knives=a['knives'],
        segments=a['entries'],
        rounds=a['rounds'],
        pieces=a['pieces'],
        int_floor_part=a['int_part'],
        segment_overhead=a['entries'],
        float_floor_gap=a['floor_loss'],
        identity='knives = sum(int(L // size)) + one cut per (round, order) segment',
        pieces_identity='sum(int(L // size)) equals pieces delivered minus a float rounding gap',
        orders=orders,
        segments_per_order_histogram=a['seg_hist'],
        segments_per_round_histogram=a['rnd_hist'],
        segments_if_one_per_order=orders,
        knives_if_one_segment_per_order=min_knives_one_seg_per_order,
        conclusion=('Almost all knife cost is the piece count, which demand fixes. The only lever is the '
                    'segment count: each (round, order) pair costs one extra cut. The current plan runs '
                    '%.2f segments per round, which is what a search with no knife gradient produces.'
                    % (a['entries'] / a['rounds'])),
    )

    # ---- 5b. How much of the segment overhead is actually recoverable? ----
    report['segment_lever'] = dict(
        current_knives=a['knives'],
        current_segments=a['entries'],
        orders=a['orders'] if 'orders' in a else len(a['per_order_segments']),
        merged_one_segment_per_order_knives=a['merged_knives'],
        merged_one_segment_per_order_segments=a['merged_segments'],
        achievable_saving=a['merge_saving_total'],
        orders_that_gain_from_merging=a['nonzero_merge_saving'],
        float_floor_discount=a['pieces'] - a['int_part'],
        float_floor_discount_note=('The counted cuts sit BELOW the delivered piece count because of the '
                                   'observed floating // behaviour: sum(int(L // size)) is smaller than the '
                                   'number of pieces. Merging segments gives back the one-cut-per-segment '
                                   'overhead but at the same time gives back part of this floor discount, so '
                                   'the net saving is far smaller than the segment count.'),
        capacity_min_segments=a['capacity_min_segments'],
        rounds_floor=a['rounds_floor'],
        capacity_note=('The bed-length contract alone needs at least %d rounds, and every order needs at '
                       'least one segment, so no plan can go below %d segments. The one-segment-per-order '
                       'merge above is therefore unreachable and %d cuts is an optimistic ceiling on this '
                       'lever, not a target.' % (a['rounds_floor'], a['capacity_min_segments'],
                                                 a['merge_saving_total'])),
        verdict=('Merging every order into a single segment is the best case the structure allows and it is '
                 'worth about %d counted cuts, taking the total from %d to %d. Compare that with the %d cuts '
                 'needed for 99.00 and the %d needed for 100.00 at the current yield.'
                 % (a['merge_saving_total'], a['knives'], a['merged_knives'],
                    a['knives'] - 89227, a['knives'] - 87069)),
    )

    # ---- 6. Score versus knife count, both hypotheses ----
    scenario = []
    for knives in (90001, 90008, 90000, 89500, 89219, 89000, 88000, 87062, 85000, 80000):
        scenario.append(dict(
            knives=knives,
            capped_knife_pre_round=total_for_knives(knives, actual_yield, True, True, True),
            capped_knife_plain=total_for_knives(knives, actual_yield, False, True, True),
            uncapped_knife_plain=total_for_knives(knives, actual_yield, False, False, True),
        ))
    report['score_vs_knives'] = scenario

    thresholds = {}
    for label, target, pre_round, cap_knife in (
            ('99.00_capped', 99.00, False, True),
            ('99.00_uncapped', 99.00, False, False),
            ('100.00_uncapped', 100.00, False, False)):
        lo, hi = 40000, 200000
        if total_for_knives(lo, actual_yield, pre_round, cap_knife, True) < target:
            thresholds[label] = None
            continue
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if total_for_knives(mid, actual_yield, pre_round, cap_knife, True) >= target:
                lo = mid
            else:
                hi = mid
        thresholds[label] = lo
    report['knife_thresholds_at_current_yield'] = dict(
        yield_percent=round(actual_yield, 6),
        current_knives=a['knives'],
        needed_knives=thresholds,
        segments_to_remove={
            k: (a['knives'] - v) for k, v in thresholds.items() if v is not None},
        note=('Under the capped hypothesis no knife reduction helps at all: full credit is already '
              'rounded in. Under the uncapped hypothesis each removed segment is worth about 0.44 '
              'points at the current knife level, which is a far steeper lever than yield.'),
    )

    # ---- 7. The decisive experiment ----
    probe = 89000
    report['decisive_experiment'] = dict(
        design=('Submit a plan that keeps full coverage and the same delivered material but consolidates '
                'rounds so the segment count drops. Holding yield near 95.50 and cutting knives from '
                '%d to about %d separates the hypotheses cleanly.' % (a['knives'], probe)),
        prediction_if_knife_capped=total_for_knives(probe, actual_yield, True, True, True),
        prediction_if_knife_uncapped=total_for_knives(probe, actual_yield, False, False, True),
        separation_points=round(total_for_knives(probe, actual_yield, False, False, True)
                                - total_for_knives(probe, actual_yield, True, True, True), 4),
        regression_risk=('Low: holding material and yield fixed, the capped hypothesis predicts the same '
                         'score as the current 98.6500 submission, so the worst case is no change.'),
        segments_needed=a['knives'] - probe,
        verdict=('A knife reduction of %d is enough for a decisive reading. If the official score stays '
                 'at the capped prediction the cap is real and yield remains the only lever; if it rises '
                 'towards the uncapped prediction, knife count becomes the primary objective and the '
                 '99 and 100 targets move from "raise yield by 1.17 points" to "consolidate segments".'
                 % (a['knives'] - probe)),
    )

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
