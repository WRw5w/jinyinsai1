"""Lay a scheme's orders out so every seam has at most one spanning order.

STATUS: validated prototype, NOT yet a packer.  It fixes the clause-6 structure
and nothing else -- see "what is still missing" below.

WHY IT EXISTS
-------------
The candidate clause-6 predicate requires *every* order shared by rounds j and
j+1 to sit at the seam at once.  A round may therefore hold as many orders as
will fit, provided only the LAST of them continues into the next round and the
next round opens with it; nothing else may be shared.  Key rotation cannot do
this (a boundary with two shared orders needs both at the seam at once), which is
why `rotate_scheme_rounds` scored 0 on 2026-09-26 while our checker said clean.

WHAT IT DOES
------------
Walks the scheme's orders longest-first, picks the round count first and then
balances every round to the same target.  An order that reaches the round
boundary is SPLIT there: it becomes the single spanning order, first in the next
round and last in this one.  Each order's total length is moved between rounds,
never duplicated.

On `runs/semi_merged_v4/result.json`: 3,197 of 3,200 schemes lay out, the round
count DROPS by 2,352 (fewer rounds is fewer knives), and the seam count goes
148 -> 0 (from 7,344).  `tests/test_chain_layout.py` pins that.

WHAT IS STILL MISSING -- do not ship the output as-is
-----------------------------------------------------
Running the laid-out plan through `platform_check` shows, besides the seam:

    noninteger_multiple  9162   split points must land on multiples of the
                                order's 定尺 length; the balance target is an
                                arbitrary real, so every split currently misses
    short_delivery       4607   \\  all four follow from the placeholder below:
    order_mass_floor     4607   /  counts and blank_counts are copied from the
    blank_material       4558   \\  scheme's first round, so per-round mass is
    bed_weight           3937   /  wrong everywhere once the layout changes

So the remaining work is: quantise the split points to the order's 定尺 grid, and
recompute `counts` / `blank_counts` per round from the new per-round mass.
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


def chain_rounds(scheme, lo=LO, hi=HI, cap=MAX_ROUNDS):
    """Return a new list of {order: length} rounds, or None if it will not fit."""
    totals = {}
    for rnd in scheme['length_scheme']:
        for oid, length in rnd.items():
            totals[oid] = totals.get(oid, 0.0) + length
    if len(totals) < 2:
        return None

    total = sum(totals.values())
    k = rounds_needed(total, lo, hi, cap)
    if k is None:
        return None
    target = total / k

    seq = sorted(totals, key=lambda o: -totals[o])
    remaining = {o: totals[o] for o in seq}
    rounds, i = [], 0
    for j in range(k):
        need = (total - target * j) if j == k - 1 else target
        current, held = {}, 0.0
        while i < len(seq) and held < need - 1e-9:
            oid = seq[i]
            take = min(remaining[oid], need - held)
            current[oid] = take
            held += take
            remaining[oid] -= take
            if remaining[oid] <= 1e-9:
                i += 1
        rounds.append(dict(current))
    if i != len(seq) or any(sum(r.values()) < lo - 1e-9
                            or sum(r.values()) > hi + 1e-9 for r in rounds):
        return None
    return rounds


def relayout(plan, cap=MAX_ROUNDS):
    """Apply `chain_rounds` to every multi-order scheme.  Returns (plan, stats)."""
    laid = skipped = 0
    for scheme in plan:
        if len(scheme.get('orders') or []) < 2:
            skipped += 1
            continue
        rounds = chain_rounds(scheme, cap=cap)
        if rounds is None:
            skipped += 1
            continue
        scheme['length_scheme'] = rounds
        # Placeholder only: per-round mass is wrong until these are recomputed.
        n = len(rounds)
        scheme['counts'] = [scheme['counts'][0]] * n
        scheme['blank_counts'] = [scheme['blank_counts'][0]] * n
        laid += 1
    return plan, {'laid_out': laid, 'untouched': skipped}


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else 'runs/semi_merged_v4/result.json')
    plan = json.loads(src.read_text(encoding='utf-8'))
    _, stats = relayout(plan)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    main()
