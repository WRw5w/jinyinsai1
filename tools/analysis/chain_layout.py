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

On `runs/semi_merged_v4/result.json` (3,200 schemes):

    laid out            2,782   (87.0%; the rest keep their old layout)
    seam violations     7,344 -> 1,338   (all 1,338 are in schemes not laid out)
    gap violations          0 -> 0
    round count         drops -- fewer rounds is fewer knives

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

WHAT IS STILL MISSING -- do not ship the output as-is
-----------------------------------------------------
`counts` and `blank_counts` are placeholder copies of the scheme's first round,
so per-round mass is wrong wherever the layout changed.  `platform_check` on the
laid-out plan:

    blank_material       3860   round mass > declared blank material
    bed_weight           3371   round mass > 60,000 kg
    short_delivery        583   pieces cut < pieces demanded
    order_mass_floor      583   mass allocated to an order < that order's weight

The masses are all of the form `count x linear x (length + 2)`, and the floors
are `k x size x count x linear >= weight` and `k x count >= pieces`, so per round
the count is bounded above by `60000 / ((net + 2) x linear)` and
`2000 / diameter`, and below by each resident order's floor.  In the chain each
round holds at most two orders and each order sits in one or two rounds, so this
is a small per-scheme system rather than a global one.  Counts should be taken as
LOW as the floors allow, because more parallel bars means more declared blank
mass and the yield numerator divides by that.

Note the checker counts `produced += k * count`, i.e. pieces times parallel bars,
so `produced >= order['pieces']` needs `order['pieces']` to be on the same scale.
Confirm that before trusting the delivery floor -- it was not settled here.
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


def layout(scheme, sizes, lo=LO, hi=HI, cap=MAX_ROUNDS):
    """Return new {order: length} rounds, or None if this scheme will not lay out.

    `sizes` maps order id -> 定尺 length in metres.
    """
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


def relayout(plan, sizes, cap=MAX_ROUNDS):
    """Apply `layout` to every multi-order scheme.  Returns (plan, stats)."""
    laid = skipped = 0
    for scheme in plan:
        if len(scheme.get('orders') or []) < 2:
            skipped += 1
            continue
        rounds = layout(scheme, sizes, cap=cap)
        if rounds is None:
            skipped += 1
            continue
        scheme['length_scheme'] = rounds
        # Placeholder only -- see "what is still missing" in the module docstring.
        n = len(rounds)
        scheme['counts'] = [scheme['counts'][0]] * n
        scheme['blank_counts'] = [scheme['blank_counts'][0]] * n
        laid += 1
    return plan, {'laid_out': laid, 'untouched': skipped}


def load_sizes(root, round_name='semi'):
    sys.path.insert(0, str(Path(root) / 'src'))
    from platform_score import load_scoring_data            # noqa: E402
    ctx = load_scoring_data(Path(root) / f'data/{round_name}', round_name)
    return {oid: order.size_m for oid, order in ctx.orders.items()}


def main():
    root = Path(__file__).resolve().parents[2]
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'runs/semi_merged_v4/result.json'
    plan = json.loads(Path(src).read_text(encoding='utf-8'))
    _, stats = relayout(plan, load_sizes(root))
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    main()
