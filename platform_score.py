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
    `numerator_capped_by_demand`
                         True -> the yield numerator stops at each order's
                         demanded mass, so over-production is produced but not
                         credited ("超产不扣分，但超产部分不计入成材率").
    `penalise_short_delivery`
                         True -> an order delivered below its piece demand, or
                         missing from the plan entirely, is one violation each.

    The two delivery switches are semi-final rules and default to False: the
    preliminary round's over-production penalty was documented as broken and
    every preliminary feedback was reproduced with a numerator that counts all
    produced kilograms, so `prelim` keeps its calibrated arithmetic bit for bit.
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
    numerator_capped_by_demand: bool = False
    penalise_short_delivery: bool = False
    segment_convention: str = 'floor'   # or 'nearest'; see row_segments()

    @staticmethod
    def prelim(baseline_knives=90000.0):
        return Rules('prelim', 'segment', False, None, False,
                     (0.4, 0.3, 0.2, 0.1), baseline_knives)

    @staticmethod
    def semi(baseline_knives=90000.0, weights=(0.4, 0.3, 0.2, 0.1), max_rounds=6):
        # Keywords, not positions: the eighth positional field is `time_subscore`.
        return Rules('semi', 'round', True, max_rounds, True, weights, baseline_knives,
                     numerator_capped_by_demand=True, penalise_short_delivery=True)


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
    pieces: int = 0

    @property
    def demand_kg(self):
        """Mass a plan may credit for this order under the semi-final numerator.

        `pieces` is the delivery demand exactly as `solver.load_orders` derives it
        (`ceil(weight / (size * mu_phys))`).  The credited mass is that piece count
        at the *scoring* (integer-diameter) linear weight, i.e. the same definition
        of over-production `max_overproduction_ratio` uses -- delivering more than
        `pieces` is the overshoot the semi-final numerator refuses to count.

        Fixtures that leave both fields at zero get no cap at all, which is why
        `prelim` and the hand-built rule fixtures are unaffected.
        """
        if self.pieces > 0:
            return self.pieces * self.size_m * self.linear_weight
        return self.required_kg


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
            physical_linear = math.pi * (diameter / 1000) ** 2 / 4 * 9860
            # Same derivation as `solver.load_orders`, including its epsilon, so the
            # delivery check here cannot disagree with the plan the solver produced.
            ratio = weight_kg / (physical_linear * size)
            pieces = max(1, math.ceil(ratio - 1e-10 * max(1, abs(ratio))))
            orders[oid] = ScoringOrder(
                size, diameter, integer_diameter,
                math.pi * (integer_diameter / 1000) ** 2 / 4 * 9860,
                physical_linear,
                weight_kg,
                pieces,
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
def row_segments(length_m, size_m, convention='floor'):
    """Pieces of `size_m` the platform credits for one declared net length.

    Two readings of the same declared number, and they disagree on **39.8%** of
    the semi-final `(k, size)` combinations (`data/semi/orders.normalized.csv`,
    measured 2026-09-21): `solver._export` writes `round(k * size, 9)`, and the
    float nearest that value is not always >= the exact multiple.  With
    `size_m = 3.6, k = 5` the written 18.0 divides to 4.999999999999999, so

    * `'floor'`  -> 4   (what `row_metrics` charges knives for: `int(L // s)`)
    * `'nearest'` -> 5  (what `platform_check` credits pieces for:
                         `abs(L / s - k) <= 1e-8`)

    Neither is settled by the preliminary feedbacks, because those plans were
    built to *exploit* the floor.  `'floor'` stays the default so no calibrated
    number moves silently; the semi-final knife model should be re-priced under
    `'nearest'` before the first semi submission is trusted.
    """
    if convention == 'floor':
        return int(length_m // size_m)
    if convention == 'nearest':
        return int(round(length_m / size_m))
    raise ValueError(f'unknown segment convention: {convention!r}')


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
            segments += row_segments(length, order.size_m, rules.segment_convention)
            finished += length * parallel * order.linear_weight
        knives = segments + 1
    else:
        raise ValueError(f'unknown knife_add_per: {rules.knife_add_per!r}')
    return knives, finished, blank_count * scoring_data.blank_weights[blank_type]


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
        # CALIBRATED 2026-09-22 against the semi-final rejection notice.  The
        # platform reported `跨轮接续不连续(7559条)` with the offending locations
        # printed as `方案a批j` pairs -- i.e. one violation per *round boundary*,
        # not per order.  On the rejected package (round_shaper: every order in
        # every round, so every pair of adjacent rounds has the identical order
        # set) this predicate reproduces 7559 exactly, including the non-
        # forgeable detail that `方案1` skips 批3: 批3/批4 straddle a
        # `merge_compatible` splice, where the two sides are different schemes
        # and therefore have different order sets.
        #
        # The competing readings are excluded by the data:
        #   * "an order's rounds must be adjacent" (per-order gap) counts
        #     `sum(m_s * (R_s - 1))` on that package, an order of magnitude more;
        #   * "adjacent rounds must be disjoint" is impossible -- 28.6% of the
        #     semi orders exceed the 60 t single-round bed limit and must span
        #     rounds, and the official PDF example itself puts A20260104 in two
        #     adjacent rounds.
        # The two surviving sub-rules of clause 6 are both charged here, so a
        # plan is safe under either emphasis.
        for a, batch in enumerate(plan):
            rounds = batch.get('length_scheme') or []
            seen_on = [frozenset(r.keys()) for r in rounds]
            for j in range(len(seen_on) - 1):
                if seen_on[j] == seen_on[j + 1]:
                    bump('continuity', scheme=a, round=j)
            per_order = {}
            for j, s in enumerate(seen_on):
                for oid in s:
                    per_order.setdefault(oid, []).append(j)
            for oid, js in per_order.items():
                if len(js) > 1 and js != list(range(js[0], js[0] + len(js))):
                    bump('continuity_skip', scheme=a, order=oid, rounds=js)
    if rules.penalise_short_delivery:
        # Semi-final delivery floor (constraints.txt clause 8 and the PDF's
        # under-production penalty).  Pieces are recounted the way
        # `platform_check` counts them -- the nearest integer of L / size -- and
        # NOT with the floor `row_metrics` charges knives for: the solver built
        # every row as an exact `k` multiple, so the nearest integer is the
        # declared delivery, and flooring the float would report a shortfall on
        # 39.8% of the (k, size) combinations the semi data can produce.
        produced = {}
        for batch in plan:
            for scheme, parallel in zip(batch.get('length_scheme') or [],
                                        batch.get('counts') or []):
                for oid, length in scheme.items():
                    order = context.orders.get(oid)
                    if order is None:
                        continue
                    produced[oid] = (produced.get(oid, 0)
                                     + row_segments(length, order.size_m, 'nearest') * parallel)
        for oid, order in context.orders.items():
            got = produced.get(oid, 0)
            if got == 0 and oid not in order_rounds:
                bump('missing_order', order=oid)          # clause 12
            elif got < order.pieces:
                bump('under_delivery', order=oid, produced=got, required=order.pieces)
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
        base = baseline_knives if baseline_knives is not None else 90000.0
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
    rows = entries = 0
    included, combined = set(), set()
    finished_by_order = {}      # oid -> produced mass, for the semi-final numerator cap
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
            finished += f
            raw += r
            rows += 1
            entries += len(scheme)
            physical_finished += sum(length * parallel * context.orders[oid].physical_linear_weight
                                     for oid, length in scheme.items())
            for oid, length in scheme.items():
                finished_by_order[oid] = (finished_by_order.get(oid, 0.0)
                                          + length * parallel * context.orders[oid].linear_weight)
    # Semi-final numerator: overshoot is produced but not credited, so each order
    # contributes at most its demanded mass (see `ScoringOrder.demand_kg`).
    credited_finished = finished
    if rules.numerator_capped_by_demand:
        credited_finished = 0.0
        for oid, mass in finished_by_order.items():
            demand = context.orders[oid].demand_kg
            credited_finished += min(mass, demand) if demand > 0 else mass
    violations, violation_detail = detect_violations(plan, context, rules)
    violation_count = sum(violations.values())
    coverage = len(combined) / len(context.orders)
    result = dict(
        round=rules.round,
        rules=dict(knife_add_per=rules.knife_add_per, coverage_shared=rules.coverage_shared,
                   max_rounds=rules.max_rounds, continuity=rules.continuity,
                   weights=list(rules.weights), baseline_knives=rules.baseline_knives,
                   time_subscore=rules.time_subscore,
                   numerator_capped_by_demand=rules.numerator_capped_by_demand,
                   penalise_short_delivery=rules.penalise_short_delivery,
                   segment_convention=rules.segment_convention),
        knives=knives, finished_weight=credited_finished,
        finished_weight_uncapped=finished, overshoot_kg=finished - credited_finished,
        blank_weight=raw, raw_weight=raw,
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
    result.update(score_components(knives, credited_finished, raw, coverage, rules))
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
