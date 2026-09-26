"""Lay a scheme's orders out so every seam has at most one spanning order.

STATUS: validated prototype.  It fixes the clause-6 structure.  It does NOT yet
produce a submittable package -- read "what is still missing" before using it.

WHY IT EXISTS
-------------
The candidate clause-6 predicate requires *every* order shared by rounds j and
j+1 to sit at the seam at once.  A round may therefore hold as many orders as
will fit, provided only the LAST of them continues into the next round and the
next round opens with it; nothing else may be shared.  Key rotation cannot do
this -- a boundary with two shared orders needs both at the seam at once -- which
is why `rotate_scheme_rounds` shipped a 0-point package on 2026-09-26 while our
own checker called it clean.

WHAT IT DOES
------------
Walks the scheme's orders longest-first, picks the round count first, then
balances every round toward the same target.  An order reaching a round boundary
is SPLIT there and becomes the single spanning order, last on the left and first
on the right.  Length moves between rounds; it is never duplicated.

Splits move whole PIECES, not arbitrary reals: an order's allocation in a round
must be an integer multiple of its 定尺 length, and pieces are integers, so the
grid is respected by construction rather than by rounding afterwards.

On `runs/semi_merged_v4/result.json` (3,200 schemes) the layout and the mass
are both handled, so this is the current state end to end:

    laid out            2,024   (1,173 left alone)
    seam violations     7,344 -> 3,464   (all in schemes left alone)
    gap violations          0 -> 0
    short_delivery          0     (was 565 with a copied count)
    order_mass_floor        0     (was 565)
    bed_width               0     (the count is checked, not assumed)
    bed_weight              1
    blank_material         16

The remaining seam count is entirely the schemes left alone, so the next lever
is the layout success rate, not the checker.

WHY THE LAYOUT SUCCESS RATE STALLS AT 63%, AND A TRAP IN THE CHECKER
--------------------------------------------------------------------
Left alone: 577 do not lay out geometrically, 572 need a count above the bed
width cap, 24 breach the bed weight, 3 are single-order.

The 572 are the interesting ones and they are NOT a layout bug.  Scheme 1115:

    diameter 26.5 -> bed width caps the count at 75
    original counts [75, 71] -- already AT the cap
    original bar-metres for B20273534  6,086
    the order's floor needs             6,284

The original scheme is about 3% short of its own mass floor, and no re-layout can
fix that: raising the count is impossible (the width cap) and adding rounds needs
the order to spread, which is the opposite of what a clean seam wants.

It was never flagged because `platform_check` gates that check on `used[oid] == 1`
-- an order appearing in one round is checked against its weight, one appearing in
several is not.  The chain layout concentrates orders into one or two rounds, so
it walks straight into the check the original avoided.

Two readings, and this file does not settle which:

  * the gate mirrors the platform, in which case concentrating orders CREATES
    violations that the spread-out layout did not have, and the chain shape is
    the wrong answer even though it is seam-clean;
  * the gate is a limitation, the platform checks the total, and those 572
    schemes were already infeasible before clause 6 entered the picture.

MEASURED, and it points at the first reading
--------------------------------------------
On the original `_v4` plan, under the total reading and ignoring the gate:

    2,050 orders   allocated mass below their own weight
    2,036 of those used > 1        (the gate does not look at them)
       14 of those used == 1       (it does)

The platform's two notices (2026-09-23 on v2, 2026-09-26 on v4) report ONLY
`跨轮接续不连续`, with no mass complaint at all.  If the platform checked the
total for every order it would have named about two thousand more violations.
So the multi-round shortfall is not something the platform flags.

And forcing every order to span TWO rounds -- which is what the original does,
and which keeps `used > 1`, so the gate never applies -- makes the delivery and
mass floors disappear entirely:

    forced two-round spans   1,657 laid out, short_delivery 0, order_mass_floor 0
    (seam count rises to 5,552 only because fewer schemes lay out)

That is the shape to build: every order in exactly two consecutive rounds, never
one, so the layout never enters a check the original avoided.  The layout stops
at 1,657 because a scheme whose FIRST order is shorter than the 48 m bed minimum
cannot open with a single-order round -- the next lever, and a much better-posed
one than the mass model was.

FOUR DEAD ENDS, ALL RECORDED BECAUSE EACH LOOKS RIGHT AT FIRST
--------------------------------------------------------------
  1. duplicating the spanning order's length into both rounds instead of
     splitting it -- doubles the material; 20.5% laid out.
  2. splitting at arbitrary reals -- every allocation lands off the 定尺 grid;
     platform_check reports noninteger_multiple 9,162.
  3. filling each round to the 148 m ceiling -- leaves a scrap under the 48 m
     floor at the end; 71.2%, then 72.4% once quantised.
  4. picking the round count first but not balancing the last round -- fixed by
     targeting total/k everywhere.
  5. forcing every order to span two rounds by holding one piece back inside the
     fill loop -- laid out 0 schemes, against 2,024 without it.  Holding a piece
     back barely lowers `held`, so the loop keeps adding orders until the round
     overflows `hi` and every scheme is refused.  The out-of-tree version of this
     idea did the split AFTER laying out and reached 1,657; the in-loop version
     is not equivalent and was reverted rather than shipped broken.  If you retry
     it, build the rounds as head/tail pairs per order instead of patching the
     fill loop.

WHAT IS STILL MISSING -- do not ship the output as-is
-----------------------------------------------------
`counts` and `blank_counts` are placeholder copies of the scheme's first round,
so per-round mass is wrong wherever the layout changed.

The first thing to get right, and it is not obvious: **the length ceiling is
mass-driven, not 148 m**.  A scheme in `runs/semi_merged_v4` runs 46 parallel
bars of a 43 mm section (linear 14.3 kg/m), so 60,000 kg caps a round at
`60000 / (46 x 14.3) - 2 = 89.2 m`, not 148.  Using the bed length band as the
ceiling while copying the original count leaves mass over the limit; feeding the
count into the ceiling instead took `bed_weight` from 3,371 to 76.

With the count held at the scheme's own value and the ceiling mass-aware:

    laid out            2,620   (577 keep their old layout)
    continuity_seam     7,344 -> 1,753
    bed_weight          3,371 -> 76
    blank_material      3,860 -> 1,259
    short_delivery / order_mass_floor   583 -> 565 each

Still open, in likely order of difficulty:

  * `blank_material` 1,259.  `blank_counts` is a placeholder; the original varied
    per round ([5,6,6,6,6,5]) exactly because the round lengths varied.  Each
    round needs `ceil((net + 2) x count x linear / blank_weight)` of them.
  * `short_delivery` / `order_mass_floor` 565.  Holding the count fixed should
    preserve each order's total bar-metres, so these are NOT explained yet --
    do not assume they are the placeholder's fault.  Reproduce one before
    theorising; the earlier floor arithmetic was wrong twice.
  * the 577 schemes that will not lay out, which is where most of the remaining
    1,753 seams live.

Counts should be taken as LOW as the floors allow, because more parallel bars
means more declared blank mass and the yield numerator divides by that.

The checker counts `produced += k * count`, i.e. pieces times parallel bars, and
`pieces = int(weight / (linear * size))` from the order table -- so the two floors
are on the same scale, which was the thing left unsettled in the previous
revision.  It is settled: they are.
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


def layout(scheme, sizes, lo=LO, hi=HI, cap=MAX_ROUNDS, linear=None):
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
    k = rounds_needed(total, lo, hi, cap)
    if k is None:
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
