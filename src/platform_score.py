"""Competition scoring reconstruction, preliminary and semi-final rounds.

Two rule sets live here, selected by `Rules.round`:

* ``'prelim'`` -- calibrated against seven complete official feedbacks.  Knives
  add one cut *per order segment*; coverage counts any order that appeared in a
  multi-order scheme; there is no continuity or round-cap rule.  Every historical
  regression in test_platform_score.py pins this mode, and it must not drift.
* ``'semi'``  -- the 复赛 rule set from ``data/semi/constraints.txt`` and the PDF
  六/九 sections.  Knives add one cut *per round* (PDF: 50 m of bar, 3 m定尺,
  48 m usable -> 16 pieces -> 15 parting cuts + 2 trim cuts = 17); coverage
  requires two orders to share one cold-bed round; a scheme is capped at 6 rounds
  and an order's rounds must be consecutive.

The two modes share the mass model, because the mass model did not change:
the yield numerator uses the diameter truncated to whole millimetres (pinned by
the 96.29% / 96.42% feedbacks) while physical checks keep full precision.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


# ===========================================================================
# Rule sets
# ===========================================================================
@dataclass(frozen=True)
class Rules:
    """All round-dependent scoring switches in one place.

    `knife_add_per`      'segment' (prelim) or 'round' (semi)
    `coverage_shared`    True  -> two orders must share a round
                         False -> any order in a multi-order scheme counts
    `max_rounds`         per-scheme cold-bed round cap; None disables the check
    `continuity`         True -> an order's rounds must be consecutive
    `weights`            (knives, yield, coverage, time) -- PDF 九 gives
                         40/30/20/10; RULES.md records a Q&A claim that the time
                         term is folded into yield, exposed as the alternative
                         (40, 40, 20, 0) so the two can be compared directly.
    """

    round: str = 'prelim'
    knife_add_per: str = 'segment'
    coverage_shared: bool = False
    max_rounds: int | None = None
    continuity: bool = False
    weights: tuple = (0.4, 0.3, 0.2, 0.1)
    baseline_knives: float = 90000.0
    time_subscore: float = 100.0
    penalty_per_violation: float = 5.0

    @staticmethod
    def prelim(baseline_knives=90000.0):
        return Rules('prelim', 'segment', False, None, False,
                     (0.4, 0.3, 0.2, 0.1), baseline_knives)

    @staticmethod
    def semi(baseline_knives=160000.0, weights=(0.4, 0.3, 0.2, 0.1), max_rounds=6):
        return Rules('semi', 'round', True, max_rounds, True, weights, baseline_knives)


# ===========================================================================
# Data model
# ===========================================================================
@dataclass(frozen=True)
class ScoringOrder:
    size_m: float
    diameter_mm: float
    scoring_diameter_mm: int
    linear_weight: float
    physical_linear_weight: float
    required_kg: float = 0.0


@dataclass(frozen=True)
class ScoringData:
    orders: Mapping[str, ScoringOrder]
    blank_weights: Mapping[int, float]
    source_order_count: int
    excluded_order_ids: tuple


ORDER_FILES = {
    'prelim': ('orders_quarter.csv', 'utf-8-sig',
               {'oid': ('订单号',), 'weight': ('订单重量(t)',),
                'steel': ('坯料钢种',), 'diameter': ('订单直径(mm)',),
                'size': ('订单定尺(mm)',)}),
    'semi': ('orders_semi.csv', 'gbk',
             {'oid': ('订单号',), 'weight': ('订单重量',),
              'steel': ('钢种',), 'diameter': ('规格',),
              'size': ('定尺长度',)}),
}
BLANK_FILES = {
    'prelim': ('blank_used.csv', 'utf-8-sig',
               {'bid': ('坯料',), 'a': ('宽度mm',), 'b': ('厚度mm',), 'length': ('长度mm',)}),
    'semi': ('blank_used_finals.csv', 'gbk',
             {'bid': ('编号',), 'a': ('钢坯长度',), 'b': ('钢坯宽度',), 'length': ('钢坯定尺',)}),
}


def _first(row, names):
    for name in names:
        value = row.get(name)
        if value not in (None, ''):
            return value.strip()
    raise KeyError(f'none of {names} present in {sorted(row)[:8]}')


def load_scoring_data(data=Path('data'), round='prelim'):
    """Read the original CSV files, independently of normalized solver input.

    The two rounds disagree on filename, encoding, header text and the unit of
    weight (tonnes vs tonnes-as-written), so the table is explicit rather than
    guessed from the directory contents.
    """
    data = Path(data)
    order_file, order_enc, order_cols = ORDER_FILES[round]
    blank_file, blank_enc, blank_cols = BLANK_FILES[round]
    orders, excluded, source_count = {}, [], 0
    with (data / order_file).open(encoding=order_enc, newline='') as f:
        for row in csv.DictReader(f):
            source_count += 1
            oid = _first(row, order_cols['oid'])
            size = float(_first(row, order_cols['size'])) / 1000
            diameter = float(_first(row, order_cols['diameter']))
            weight_kg = float(_first(row, order_cols['weight'])) * 1000
            # Generalized anomaly predicate: the preliminary round has a
            # negative length, the semi-final round a negative weight.
            if size <= 0 or diameter <= 0 or weight_kg <= 0:
                excluded.append(oid)
                continue
            if oid in orders:
                raise ValueError(f'Duplicate order: {oid}')
            integer_diameter = int(diameter)
            orders[oid] = ScoringOrder(
                size, diameter, integer_diameter,
                math.pi * (integer_diameter / 1000) ** 2 / 4 * 9860,
                math.pi * (diameter / 1000) ** 2 / 4 * 9860,
                weight_kg,
            )
    blanks = {}
    with (data / blank_file).open(encoding=blank_enc, newline='') as f:
        for row in csv.DictReader(f):
            blanks[int(float(_first(row, blank_cols['bid'])))] = (
                float(_first(row, blank_cols['a'])) * float(_first(row, blank_cols['b']))
                * float(_first(row, blank_cols['length'])) * 9860 / 1e9
            )
    return ScoringData(orders, blanks, source_count, tuple(excluded))


# ===========================================================================
# Knife accounting
# ===========================================================================
def entry_knives(length_m, size_m):
    """Preliminary-round knives for one (round, order) segment.

    `max(2, int(L // size) + 1)`, i.e. one parting cut plus a full head/tail
    pair charged to *every* order segment.  Use the observed floating `//`
    semantics; never replace with int(L / s).

    The `max(2, ...)` floor is pinned by the 2026-09-16 92.02 feedback
    (submission_island_partition, official 108671 knives over 35718 segments).
    That plan deliberately wrote every length 1e-12 relatively below its integer
    multiple, which drives `L // size` to `k - 1` and the raw expression to `k`.
    The platform returned 108671 = (sum of k over the 13798 segments with k >= 2)
    + 2 * (the 21920 segments with k == 1), i.e. it refilled the segments whose
    floor had collapsed to zero instead of honouring a 1-knife segment. Without
    the floor the model predicts 86751 and the other four feedbacks still pass,
    so this is a real, discriminating observation rather than a refit.
    """
    return max(2, int(length_m // size_m) + 1)


def row_metrics(length_scheme, parallel, blank_type, blank_count, scoring_data, rules):
    """Return (knife_count, scoring_finished_kg, raw_kg) for a single row."""
    finished = 0.0
    if rules.knife_add_per == 'segment':
        knives = 0
        for oid, length in length_scheme.items():
            order = scoring_data.orders[oid]
            knives += entry_knives(length, order.size_m)
            finished += length * parallel * order.linear_weight
    elif rules.knife_add_per == 'round':
        segments = 0
        for oid, length in length_scheme.items():
            order = scoring_data.orders[oid]
            segments += int(length // order.size_m)
            finished += length * parallel * order.linear_weight
        knives = segments + 1
    else:
        raise ValueError(f'unknown knife_add_per: {rules.knife_add_per!r}')
    return knives, finished, blank_count * scoring_data.blank_weights[blank_type]


def _demand_numerator_mass(order):
    """Yield-numerator ceiling for one order: the mass of its DEMAND, not delivery.

    The semi-final counts over-production as free but yield-less, so an order's
    contribution to the yield numerator is capped at the mass of the pieces it
    actually demanded.  The demand piece count mirrors the solver's
    `ceil(weight / (size * physical_linear_weight))`; the credited mass then uses
    the scoring (integer-diameter) linear weight, exactly as `row_metrics` does.
    """
    pieces = math.ceil(order.required_kg / (order.size_m * order.physical_linear_weight))
    return pieces * order.size_m * order.linear_weight


# ===========================================================================
# Violations
# ===========================================================================
def detect_violations(plan, context, rules):
    """Count rule violations that are *not* already structural schema errors.

    Returns (violations, detail) where violations maps a rule name to a count
    and detail lists the offending locations.  Counting口径 follows the Q&A:
    continuity is counted per offending round, an order split across a gap is
    counted once per order.
    """
    violations, detail = {}, []

    def bump(name, **extra):
        violations[name] = violations.get(name, 0) + 1
        if len(detail) < 200:
            detail.append(dict(rule=name, **extra))

    order_rounds = {}       # oid -> [(scheme index, round index), ...]
    for a, batch in enumerate(plan):
        names = batch.get('orders') or []
        rounds = batch.get('length_scheme') or []
        if rules.max_rounds is not None and len(rounds) > rules.max_rounds:
            bump('round_cap', scheme=a, rounds=len(rounds), limit=rules.max_rounds)
        for j, scheme in enumerate(rounds):
            for oid in scheme:
                order_rounds.setdefault(oid, []).append((a, j))
            if rules.coverage_shared and len(scheme) > 1:
                pass  # coverage is computed in evaluate(); sharing is the rule
    if rules.continuity:
        for oid, places in order_rounds.items():
            by_scheme = {}
            for a, j in places:
                by_scheme.setdefault(a, []).append(j)
            for a, js in by_scheme.items():
                js = sorted(js)
                if len(js) > 1 and js != list(range(js[0], js[0] + len(js))):
                    bump('continuity', scheme=a, order=oid, rounds=js)
        # `跨轮接续不连续` -- the seam rule.  Deliberately a separate copy of the
        # one in platform_check.py rather than a shared import: the checker is
        # meant to re-derive everything from the raw CSVs so a bug in one cannot
        # hide behind the other.  tests/test_clause6_anchors.py pins both against
        # the official notices, which is what actually catches drift -- keeping
        # them "in lockstep" by hand did not, because both were written to the
        # same wrong predicate and agreed with each other while disagreeing with
        # the platform.
        #
        # PREDICATE (candidate, 2026-09-26): every order shared by rounds j and
        # j+1 must be simultaneously the last key of the left round and the first
        # key of the right one.  Two or more shared orders make that impossible,
        # so such a boundary always counts -- once per BOUNDARY, matching how the
        # official notices count (7030 / 7342 / 7559 are seams, not orders).
        #
        # Rationale, the four anchors and the falsified predecessor are documented
        # at length in platform_check.py; the reference implementation is
        # tools/analysis/clause6_candidate.py.
        for a, batch in enumerate(plan):
            rounds = batch.get('length_scheme') or []
            for j, (left, right) in enumerate(zip(rounds, rounds[1:])):
                if not (left and right):
                    continue
                last, first = next(reversed(left)), next(iter(right))
                offenders = sorted(oid for oid in set(left) & set(right)
                                   if oid != last or oid != first)
                if offenders:
                    bump('continuity_seam', scheme=a, round=j, orders=offenders,
                         left_last=last, right_first=first)
    return violations, detail


# ===========================================================================
# Scoring
# ===========================================================================
def score_components(knives, finished_weight, raw_weight, coverage, rules):
    """Return the subscore families for one rule set.

    All historical observations are *below* the knife cap, so the cap can only be
    confirmed by the 98.93 feedback: that plan earned a raw knife subscore of
    103.1282 (87270 cuts) yet the platform reported 子分刀=100.0 and a total of
    98.9300, which only the capped arithmetic reproduces.
    """
    if knives <= 0 or raw_weight <= 0:
        raise ValueError('Positive knife count and raw mass required')
    knife_subscore = 100 * rules.baseline_knives / knives
    yield_rate = finished_weight / raw_weight
    subs_uncapped = dict(knives=knife_subscore, yield_rate=100 * yield_rate,
                         coverage=100 * coverage, time=float(rules.time_subscore))
    subs_capped = dict(subs_uncapped, knives=min(100.0, knife_subscore))

    wk, wy, wc, wt = rules.weights

    def weighted(parts):
        return (wk * parts['knives'] + wy * parts['yield_rate']
                + wc * parts['coverage'] + wt * parts['time'])

    rounded_capped = {k: round(v, 2) for k, v in subs_capped.items()}
    rounded_uncapped = {k: round(v, 2) for k, v in subs_uncapped.items()}
    return dict(
        official_yield_estimate=yield_rate,
        yield_rate=yield_rate,
        yield_percent=100 * yield_rate,
        yield_percent_rounded=round(100 * yield_rate, 2),
        score_capped=weighted(subs_capped),
        score_uncapped=weighted(subs_uncapped),
        score_total_cap_only=min(100.0, weighted(subs_uncapped)),
        score_capped_display=round(weighted(subs_capped), 2),
        score_uncapped_display=round(weighted(subs_uncapped), 2),
        score_from_rounded_subscores_capped=weighted(rounded_capped),
        score_from_rounded_subscores_uncapped=weighted(rounded_uncapped),
        score_from_rounded_subscores_capped_display=round(weighted(rounded_capped), 2),
        score_from_rounded_subscores_uncapped_display=round(weighted(rounded_uncapped), 2),
        subscores=subs_capped,
        subscores_uncapped=subs_uncapped,
        rounded_subscores=rounded_capped,
        rounded_subscores_uncapped=rounded_uncapped,
    )


def evaluate(plan, data=Path('data'), *, scoring_data=None, rules=None,
             round_name='prelim', baseline_knives=None, time_subscore=100.0,
             weights=None, max_rounds=None):
    """Score an already validated plan; this function is not a feasibility check.

    `round_name` selects the rule set ('prelim' or 'semi'); the remaining keywords
    override individual switches so a single run can price the same plan under
    both competing readings of the weight table.

    The parameter is called `round_name` and not `round` on purpose: the dict
    literal below also has a `round` key, and shadowing the builtin would break
    every `round(x, 2)` in this function.
    """
    if rules is None:
        # The fallback baseline is round-specific: the preliminary round used
        # 90000, the semi-final raised it to 160000 (09-21 rule update).  Callers
        # should pass `baseline_knives` explicitly; this default only fires when
        # they do not, and it must not silently price a semi plan against the
        # preliminary baseline.
        if baseline_knives is not None:
            base = baseline_knives
        elif round_name == 'semi':
            base = 160000.0
        else:
            base = 90000.0
        if round_name == 'semi':
            rules = Rules.semi(base)
        elif round_name == 'prelim':
            rules = Rules.prelim(base)
        else:
            raise ValueError(f'unknown round_name: {round_name!r}')
        if weights is not None:
            rules = Rules(**{**rules.__dict__, 'weights': tuple(weights)})
        if max_rounds is not None:
            rules = Rules(**{**rules.__dict__, 'max_rounds': max_rounds})
        if time_subscore != rules.time_subscore:
            rules = Rules(**{**rules.__dict__, 'time_subscore': time_subscore})

    context = scoring_data if scoring_data is not None else load_scoring_data(data, round_name)
    knives, finished, physical_finished, raw = 0, 0.0, 0.0, 0.0
    delivered_mass = {}      # semi only: oid -> scoring-diameter mass already credited
    rows = entries = 0
    included, combined = set(), set()
    for batch in plan:
        names = batch['orders']
        if any(oid not in context.orders for oid in names):
            raise ValueError('Unknown or invalid order in scheme')
        included.update(names)
        lengths, counts, stocks = batch['length_scheme'], batch['counts'], batch['blank_counts']
        if not (len(lengths) == len(counts) == len(stocks)):
            raise ValueError('Mismatched row-array lengths')
        for scheme, parallel, stock in zip(lengths, counts, stocks):
            if rules.coverage_shared:
                # Semi-final: only orders sharing this very round are combined.
                if len(set(scheme)) > 1:
                    combined.update(scheme)
            elif len(set(names)) > 1:
                combined.update(names)
            k, f, r = row_metrics(scheme, parallel, batch['blank_type'], stock, context, rules)
            knives += k
            if rules.round == 'semi':
                # Cap the yield numerator at each order's demand: over-production is
                # free but earns no yield in the semi-final.  Credit only the part of
                # this row that does not push the order past its demand mass.  (The
                # preliminary round keeps its calibrated uncapped numerator.)
                for oid, length in scheme.items():
                    order = context.orders[oid]
                    row_mass = length * parallel * order.linear_weight
                    ceiling = _demand_numerator_mass(order)
                    before = delivered_mass.get(oid, 0.0)
                    finished += max(0.0, min(before + row_mass, ceiling) - min(before, ceiling))
                    delivered_mass[oid] = before + row_mass
            else:
                finished += f
            raw += r
            rows += 1
            entries += len(scheme)
            physical_finished += sum(length * parallel * context.orders[oid].physical_linear_weight
                                     for oid, length in scheme.items())
    violations, violation_detail = detect_violations(plan, context, rules)
    violation_count = sum(violations.values())
    coverage = len(combined) / len(context.orders)
    result = dict(
        round=rules.round,
        rules=dict(knife_add_per=rules.knife_add_per, coverage_shared=rules.coverage_shared,
                   max_rounds=rules.max_rounds, continuity=rules.continuity,
                   weights=list(rules.weights), baseline_knives=rules.baseline_knives,
                   time_subscore=rules.time_subscore),
        knives=knives, finished_weight=finished, blank_weight=raw, raw_weight=raw,
        physical_finished_weight=physical_finished,
        coverage=coverage, coverage_over_source=len(combined) / context.source_order_count,
        coverage_percent_rounded=round(100 * coverage, 2),
        source_order_count=context.source_order_count, valid_order_count=len(context.orders),
        included_order_count=len(included), combination_order_count=len(combined),
        excluded_invalid_orders=list(context.excluded_order_ids),
        plans=len(plan), rounds=rows, entries=entries,
        violations=violations, violation_count=violation_count,
        violation_detail=violation_detail,
        penalty_points=rules.penalty_per_violation * violation_count,
        validation='Scoring only; run platform_check separately with full raw diameters.',
        evidence=['diagnostics/knife_calibration.json', 'diagnostics/yield_semantics.json'],
        calibrated_observations=(
            'Preliminary knife counts are exact and displayed yields match all seven official '
            'feedbacks. Semi-final knives and coverage follow constraints.txt and PDF 六/九 and '
            'are unverified against any official feedback, because none exists yet.'
        ),
        assumptions=dict(
            baseline_knives=rules.baseline_knives, time_subscore=rules.time_subscore,
            weights=list(rules.weights),
            # SETTLED 2026-09-16: submission_knife_hunt_fixed returned knife=87270
            # (raw subscore 103.1282) but the platform reported 子分刀=100.0 and a
            # total of 98.9300 = 0.4*100 + 0.3*96.421004 + 30. Only the capped
            # arithmetic reproduces that total, so the knife subscore is capped at
            # 100 in the computation, not merely in the display.
            knife_component_cap_confirmed=True,
            # The total cap is still unobserved, but it is now unreachable: with the
            # knife subscore capped the maximum total is
            # 0.4*100 + 0.3*100 + 0.2*100 + 0.1*100 = 100.0 exactly.
            final_total_cap_confirmed=False,
            rounding_order_confirmed=False,
            note='Knife cap confirmed by the 98.93 feedback; total cap moot (ceiling is exactly 100). '
                 'Displayed totals are quantised to 0.01.'),
    )
    result.update(score_components(knives, finished, raw, coverage, rules))
    # Apply the explicit -5 per violation term the PDF prints alongside the weights.
    penalty = rules.penalty_per_violation * violation_count
    for key in ('score_capped', 'score_uncapped', 'score_from_rounded_subscores_capped',
                'score_from_rounded_subscores_uncapped'):
        result[f'{key}_after_penalty'] = result[key] - penalty
    for key in ('score_capped_display', 'score_uncapped_display',
                'score_from_rounded_subscores_capped_display',
                'score_from_rounded_subscores_uncapped_display'):
        result[f'{key}_after_penalty'] = round(result[key] - penalty, 2)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--round', choices=['prelim', 'semi'], default='prelim')
    parser.add_argument('--baseline-knives', type=float, default=None)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    from platform_check import read_plan
    report = evaluate(read_plan(args.file), args.data, round_name=args.round,
                      baseline_knives=args.baseline_knives)
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.report:
        args.report.write_text(text, encoding='utf-8')
    print(text, end='')
