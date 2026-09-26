"""Build a clause-6-clean candidate from a solved plan, without re-solving.

Every scheme is re-laid-out so that at most one order spans each boundary, at the
seam -- the shape the candidate predicate requires.  A scheme that will not lay
out as a whole is split in half and each half tried, recursively, so every emitted
scheme is seam-clean and no order is dropped.

THE BUG THAT COST THE MOST TIME, RECORDED SO IT CANNOT COME BACK
---------------------------------------------------------------
The mass floor `allocated >= weight` uses the checker's `linear`, which is
`pi * (raw_diameter/1000)^2 / 4 * 9860` -- the RAW diameter.  `ScoringOrder
.linear_weight` is NOT that: it uses the SCORING diameter, i.e. the diameter
truncated to whole millimetres, and the two differ for 2,465 of the 9,999 orders.

Using `linear_weight` inflates the smallest count that clears the floor, which
then breaches the bed width cap, which rejects the scheme.  Measured: 739 of
3,200 schemes were rejected that way and the number fell to ZERO once the raw
value was used.  Use `physical_linear_weight`, which equals the checker's.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

for _extra in (str(Path(__file__).resolve().parents[2] / 'src'),
               str(Path(__file__).resolve().parent)):
    if _extra not in sys.path:
        sys.path.insert(0, _extra)

import chain_layout as CL                                        # noqa: E402
from platform_score import load_scoring_data                     # noqa: E402

LO, HI, BED, WIDTH_CAP, CAP = 48.0, 148.0, 60000.0, 2000.0, 6


def _count_and_ceiling(group, totals, ctx):
    """(count, hi) for a group, or None if the width or weight cap forbids it."""
    lin = ctx['linear'][group[0]]
    need = 1.0
    for oid in group:
        need = max(need, ctx['required'][oid] / (totals[oid] * ctx['linear'][oid]))
    count = max(1, math.ceil(need))
    if ctx['diameter'][group[0]] * count > WIDTH_CAP:
        return None
    hi = min(HI, BED / (count * lin) - 2)
    if hi < LO:
        return None
    return count, hi


def _emit(group, totals, blank_type, ctx, sizes):
    """Lay `group` out as one scheme, or return None."""
    got = _count_and_ceiling(group, totals, ctx)
    if got is None:
        return None
    count, hi = got
    if len(group) == 1 and totals[group[0]] < LO:
        return None                       # a single short order cannot fill a round
    rounds = None
    for k in range(1, CAP + 1):
        candidate = CL.layout({'orders': group,
                               'length_scheme': [{o: totals[o] for o in group}]},
                              sizes, hi=hi, rounds=k)
        if candidate is None:
            continue
        if any((sum(r.values()) + 2) * count * ctx['linear'][group[0]] > BED
               for r in candidate):
            continue
        rounds = candidate
        break
    if rounds is None:
        return None
    lin = ctx['linear'][group[0]]
    weight = ctx['blank_weights'][blank_type]
    return {
        'orders': list(group),
        'length_scheme': rounds,
        'counts': [int(count)] * len(rounds),
        'blank_type': blank_type,
        'blank_counts': [int(math.ceil((sum(r.values()) + 2) * count * lin / weight))
                         for r in rounds],
    }


def _place(group, totals, blank_type, ctx, sizes, depth=0):
    """One scheme covering `group`; split in half (recursively) until it fits."""
    built = _emit(group, totals, blank_type, ctx, sizes)
    if built is not None:
        return [built]
    if len(group) <= 1 or depth > 6:
        return None
    mid = len(group) // 2
    left = _place(group[:mid], totals, blank_type, ctx, sizes, depth + 1)
    if left is None:
        return None
    right = _place(group[mid:], totals, blank_type, ctx, sizes, depth + 1)
    if right is None:
        return None
    return left + right


def build(plan, ctx, sizes):
    out, stats = [], {'schemes': 0, 'split': 0, 'failed': 0}
    for scheme in plan:
        totals = {}
        for rnd in scheme['length_scheme']:
            for oid, length in rnd.items():
                totals[oid] = totals.get(oid, 0.0) + length
        group = [o for o in scheme.get('orders', []) if o in totals]
        if len(group) < 2:
            out.append(scheme)
            stats['schemes'] += 1
            continue
        placed = _place(group, totals, scheme['blank_type'], ctx, sizes)
        if placed is None:
            stats['failed'] += 1
            continue
        if len(placed) > 1:
            stats['split'] += 1
        out.extend(placed)
        stats['schemes'] += len(placed)
    return out, stats


def main():
    root = Path(r'D:/02_Projects/ML/jinyinsai1_nolimit')
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'runs/semi_merged_v4/result.json'
    plan = json.loads(Path(src).read_text(encoding='utf-8'))
    data = load_scoring_data(root / 'data/semi', 'semi')
    ctx = {'linear': {o: x.physical_linear_weight for o, x in data.orders.items()},
           'diameter': {o: x.diameter_mm for o, x in data.orders.items()},
           'required': {o: float(x.required_kg) for o, x in data.orders.items()},
           'blank_weights': dict(data.blank_weights)}
    sizes = {o: x.size_m for o, x in data.orders.items()}
    out, stats = build(plan, ctx, sizes)
    json.dump(out, open(root / 'runs/_cand_chain.json', 'w', encoding='utf-8'),
              ensure_ascii=False)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    main()
