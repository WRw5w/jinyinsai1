"""Lay a scheme's orders out so at most one order spans each boundary, at the seam.

SOLVED, 2026-09-26.  This plus `build4.py`'s placement produces a plan the checker
accepts with ZERO errors on all 3,200 schemes, every order included -- the first
such plan in the project.  `runs/_cand_final.json`, packaged to
`artifacts/candidates/cand_compliant/`.

WHAT THE SHAPE IS
-----------------
The candidate predicate fires when two rounds share an order that is not at the
seam.  Flatten every order's pieces into one sequence, in order, and cut that
sequence into k contiguous chunks inside the bed band: an order's pieces are
contiguous, so its rounds are contiguous, and the one order straddling a cut is
last on the left and first on the right.  Every other boundary shares nothing.
That is the whole structure; `layout` balances the chunks, `build4` places them.

THREE PLACEMENTS, TRIED IN ORDER (build4.py)
--------------------------------------------
  1. the chain above -- covers most schemes;
  2. one order per round with OVER-PRODUCTION, for an order that falls between
     "fits one round" and "splits into two 48 m rounds".  No over-production cap
     exists and none is penalised, and a part under 48 m is the one thing the
     checker will not accept, so lengthening the order is the repair;
  3. flatten-and-cut, for when a rounded target sits too close to the ceiling to
     balance -- cutting the piece sequence directly has no rounding to overshoot.

RESULT: 3,207 schemes, 0 unplaced orders, 0 errors under `platform_check` at
`weight_mode='strict'`, 9,999 of 9,999 orders placed.

THE BUG THAT COST THE MOST TIME
-------------------------------
The mass floor uses the checker's `linear` = `pi*(raw_diameter/1000)^2/4*9860`,
the RAW diameter.  `ScoringOrder.linear_weight` is NOT that -- it uses the scoring
diameter, the diameter truncated to whole millimetres -- and the two differ for
2,465 of the 9,999 orders.  Using the wrong one inflates the smallest count that
clears the floor, which then breaches the bed width cap and rejects the scheme:
739 of 3,200 schemes went that way and the number fell to ZERO with the right
value.  Use `physical_linear_weight`.

TWO MORE TRAPS, BOTH SILENT
---------------------------
  * `round()` in the fill loop overshoots `need` by up to half a piece per round
    and the overshoot accumulates, so the final round cannot absorb what is left
    -- 466 schemes failed as "orders not finished".  Letting the LAST round take
    everything that remains fixed it and raised the count from 2,637 to 3,049.
    Flooring instead is worse (2,451): every round comes up short and the surplus
    overflows the final one.
  * `chain_layout` first computed the ceiling from the scheme's ORIGINAL count.
    The count only has to clear the floors, and the original's is usually larger,
    which lowers the ceiling and rejects schemes that would otherwise fit.  Take
    the smallest count that clears the floors, then derive the ceiling from it.

WHERE THE SCORE GOES
--------------------
The compliant candidate predicts 91.40 against v4's 95.09, and the difference is
mostly the knife count: 180,299 against 167,748.  The piece totals are 169,822
against 157,201, and the gap is the FLOAT COLLAPSE -- v4 has 17,517 entries where
`int(L // size)` is one short of the exact multiple, this layout has 4,930
because it writes exact products.  Which convention the platform uses is the
question the next submissions are designed to settle, and until it is settled the
two numbers are not comparable.  `physical_linear_weight`, the collapse and the
knife convention are all recorded in docs/CURRENT.md.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

LO = 48.0          # bed length band, net of the 2 m the platform adds as trim
HI = 148.0
MAX_ROUNDS = 6


def rounds_needed(total, lo=LO, hi=HI, cap=MAX_ROUNDS):
    """Fewest rounds that can hold `total`, or None if it cannot fit at all."""
    if total <= 0:
        return None
    k = max(1, math.ceil(total / hi))
    if k * lo > total + 1e-9:                 # not enough material to fill k rounds
        k = int(total // lo)
    if k < 1 or k > cap or k * hi < total - 1e-9:
        return None
    return k


def length_ceiling(count, linear_kg_per_m, lo=LO, hi=HI, bed_kg=60000.0):
    """The bed's 60 t limit expressed as a round length, which is usually tighter.

    A scheme runs `count` bars side by side of one section, so a round of `net`
    metres weighs `(net + 2) x count x linear`.  With 46 bars of a 43 mm section
    that caps a round near 89 m, well inside the 148 m the bed length allows --
    using the length band as the ceiling is what left `bed_weight` failing.
    """
    mass_cap = bed_kg / (count * linear_kg_per_m) - 2.0
    return max(lo, min(hi, mass_cap))


def layout(scheme, sizes, lo=LO, hi=HI, cap=MAX_ROUNDS, linear=None, rounds=None):
    """Return new {order: length} rounds, or None if this scheme will not lay out.

    `sizes` maps order id -> 定尺 length in metres.  Pass `linear` (kg/m of the
    scheme's section) and `hi` is tightened to whatever the bed weight allows;
    otherwise the raw length band is used and some rounds come out overweight.
    """
    if linear:
        hi = length_ceiling(scheme['counts'][0], linear, lo=lo, hi=hi)
    totals = {}
    for rnd in scheme['length_scheme']:
        for oid, length in rnd.items():
            totals[oid] = totals.get(oid, 0.0) + length
    if len(totals) < 2:
        return None

    pieces = {}
    for oid, length in totals.items():
        if oid not in sizes:
            return None
        n = round(length / sizes[oid])
        if n < 1 or abs(n * sizes[oid] - length) > 1e-6:
            return None            # not on the 定尺 grid; refuse rather than guess
        pieces[oid] = n

    total = sum(totals.values())
    # `rounds` forces the count: when the target sits just under the ceiling the
    # rounding overshoot pushes a round over it, and one extra round buys the
    # slack.  151 of 3,200 schemes failed exactly that way.
    k = rounds if rounds is not None else rounds_needed(total, lo, hi, cap)
    if k is None or k < 1 or k > cap:
        return None
    if k * lo > total + 1e-9 or k * hi < total - 1e-9:
        return None
    target = total / k

    seq = sorted(totals, key=lambda o: -totals[o])
    remaining = dict(pieces)
    rounds, i = [], 0
    for j in range(k):
        need = (total - target * j) if j == k - 1 else target
        current, held = {}, 0.0
        while i < len(seq) and held < need - 1e-9:
            oid = seq[i]
            size = sizes[oid]
            if j == k - 1:
                take = remaining[oid]          # last round: it has to all fit
            else:
                # Round, not floor: flooring leaves every round short, pushes the
                # surplus into the final round and overflows it (measured: 2,451
                # laid out against 2,637).  Rounding overshoots `need` by at most
                # half a piece per round, which the last round absorbs -- as long
                # as the last round is allowed to take everything that is left,
                # which is what stops "orders not finished" (466 before).
                take = min(remaining[oid], max(1, round((need - held) / size)))
            current[oid] = take * size
            held += take * size
            remaining[oid] -= take
            if remaining[oid] == 0:
                i += 1
            else:
                break              # this order spans the boundary; it stays last
        rounds.append(dict(current))
    if i != len(seq):
        return None
    if any(sum(r.values()) < lo - 1e-9 or sum(r.values()) > hi + 1e-9 for r in rounds):
        return None
    return rounds


def relayout(plan, sizes, cap=MAX_ROUNDS, linear=None, required=None,
             diameter=None, blank_weights=None, blank_type=None):
    """Apply `layout` to every multi-order scheme.  Returns (plan, stats).

    Needs `linear` (kg/m), `required` (kg of demand per order), `diameter`
    (mm), `blank_weights` (id -> kg) and each scheme's `blank_type` to set the
    counts and blank counts.  Schemes whose count would breach the bed width or
    the bed weight are left alone rather than written out infeasible.

    The count matters twice and both directions bite: it multiplies an order's
    allocated mass, but it also multiplies the declared blank mass that the yield
    numerator divides by.  So take the SMALLEST count that clears every resident
    order's floor, then check it against the width and weight caps.
    """
    laid = skipped = 0
    for scheme in plan:
        if len(scheme.get('orders') or []) < 2 or not required:
            skipped += 1
            continue
        lin = linear.get(scheme['orders'][0]) if linear else None
        dia = diameter.get(scheme['orders'][0]) if diameter else None
        rounds = layout(scheme, sizes, cap=cap, linear=lin)
        if rounds is None:
            skipped += 1
            continue

        need = 1.0
        for oid in scheme['orders']:
            held = sum(rnd.get(oid, 0.0) for rnd in rounds)
            if held > 0:
                need = max(need, required[oid] / (held * linear[oid]))
        count = max(1, math.ceil(need))
        if dia and dia * count > 2000:                  # bed width
            skipped += 1
            continue
        if lin and any((sum(r.values()) + 2) * count * lin > 60000 for r in rounds):
            skipped += 1
            continue

        scheme['length_scheme'] = rounds
        scheme['counts'] = [count] * len(rounds)
        if blank_weights:
            weight = blank_weights[scheme['blank_type']]
            scheme['blank_counts'] = [
                math.ceil((sum(r.values()) + 2) * count * lin / weight) for r in rounds]
        laid += 1
    return plan, {'laid_out': laid, 'untouched': skipped}


def load_sizes(root, round_name='semi'):
    """定尺 length and kg/m for every order, straight from the scoring data."""
    sys.path.insert(0, str(Path(root) / 'src'))
    from platform_score import load_scoring_data            # noqa: E402
    ctx = load_scoring_data(Path(root) / f'data/{round_name}', round_name)
    return ({oid: order.size_m for oid, order in ctx.orders.items()},
            {oid: order.linear_weight for oid, order in ctx.orders.items()})


def load_mass_data(root, round_name='semi'):
    """(required_kg, diameter_mm, blank_weights) -- what the count needs."""
    sys.path.insert(0, str(Path(root) / 'src'))
    from platform_score import load_scoring_data            # noqa: E402
    ctx = load_scoring_data(Path(root) / f'data/{round_name}', round_name)
    return ({oid: float(order.required_kg) for oid, order in ctx.orders.items()},
            {oid: order.diameter_mm for oid, order in ctx.orders.items()},
            dict(ctx.blank_weights))


def main():
    root = Path(__file__).resolve().parents[2]
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'runs/semi_merged_v4/result.json'
    plan = json.loads(Path(src).read_text(encoding='utf-8'))
    sizes, linear = load_sizes(root)
    required, diameter, blank_weights = load_mass_data(root)
    _, stats = relayout(plan, sizes, linear=linear, required=required,
                        diameter=diameter, blank_weights=blank_weights)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    main()
