"""Build a clause-6-clean candidate from a solved plan, without re-solving.

Three placements, tried in order, because each covers what the one before cannot:

  1. the chain (`_place`) -- balances rounds and splits an order across at
     most a couple of them; covers most schemes in one shot;
  2. one order per round with OVER-PRODUCTION -- the rules cap no over-production
     and do not penalise it, so an order that falls between "fits one round" and
     "splits into two that are both at least 48 m" can be lengthened until it
     does fit.  A part below the 48 m bed floor is the one thing the checker will
     not accept, and that is exactly what this repairs;
  3. flatten the orders' pieces into one sequence and cut it into k contiguous
     chunks, each inside the bed band.  This is the chain restated -- an order's
     pieces are contiguous, so its rounds are contiguous and the order straddling
     a cut is last on one side and first on the other -- but it balances on the
     piece grid directly instead of by rounding a target, which is what fails
     when the target sits close to the ceiling.

Anything still unplaced is reported rather than dropped: an order missing from
the plan is `order_coverage`, which the platform will not accept.
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

from chain_place import _place


def _over_produce(group, tot, blank_type, ctx, sizes, blank_weights):
    """One order per round, lengthening it when a round would fall under 48 m."""
    pieces = []
    for oid in group:
        size = sizes[oid]
        lin = ctx['linear'][oid]
        count = max(1, math.ceil(ctx['required'][oid] / (tot[oid] * lin)))
        if ctx['diameter'][oid] * count > 2000:
            return None
        hi = min(148.0, 60000 / (count * lin) - 2)
        if hi < 48:
            return None
        base_pieces = round(tot[oid] / size)
        found = None
        for extra in range(0, 2000):
            total_pieces = base_pieces + extra
            length = total_pieces * size
            for n in range(1, 7):
                if n * 48 > length + 1e-9 or n * hi < length - 1e-9 or total_pieces < n:
                    continue
                split = [total_pieces // n] * n
                for k in range(total_pieces - sum(split)):
                    split[k] += 1
                parts = [x * size for x in split]
                if all(48 - 1e-9 <= x <= hi + 1e-9 for x in parts):
                    found = parts
                    break
            if found:
                break
        if not found:
            return None
        for length in found:
            pieces.append(({oid: length}, count,
                           int(math.ceil((length + 2) * count * lin / blank_weights[blank_type]))))
    if not pieces or len(pieces) > 6:
        return None
    return pieces


def _flatten_cut(group, tot, blank_type, ctx, sizes, blank_weights):
    """Cut the flattened piece sequence into k balanced chunks inside the band."""
    seq = []
    for oid in group:
        size = sizes[oid]
        n = round(tot[oid] / size)
        if n < 1:
            return None
        seq.extend([(oid, size)] * n)
    lin = ctx['linear'][group[0]]
    count = max(1, math.ceil(max(ctx['required'][o] / (tot[o] * ctx['linear'][o])
                                for o in group)))
    if ctx['diameter'][group[0]] * count > 2000:
        return None
    hi = min(148.0, 60000 / (count * lin) - 2)
    if hi < 48:
        return None
    for k in range(1, 7):
        if sum(s for _, s in seq) / k > hi:
            continue
        rounds, cur, held, ok = [], {}, 0.0, True
        for oid, size in seq:
            if held + size > hi + 1e-9:
                if held < 48 - 1e-9:
                    ok = False
                    break
                rounds.append(cur)
                cur, held = {}, 0.0
            cur[oid] = cur.get(oid, 0.0) + size
            held += size
        if not ok:
            continue
        if cur:
            if held < 48 - 1e-9:
                continue
            rounds.append(cur)
        if len(rounds) != k:
            continue
        weight = blank_weights[blank_type]
        return [({o: v for o, v in r.items()}, count,
                 int(math.ceil((sum(r.values()) + 2) * count * lin / weight)))
                for r in rounds]
    return None


def build(plan, ctx, sizes, blank_weights):
    ctx = dict(ctx, sizes=sizes, blank_weights=blank_weights)
    out, unplaced = [], []
    for scheme in plan:
        tot = {}
        for rnd in scheme['length_scheme']:
            for oid, length in rnd.items():
                tot[oid] = tot.get(oid, 0.0) + length
        group = [o for o in scheme.get('orders', []) if o in tot]
        if len(group) < 2:
            out.append(scheme)
            continue
        placed = _place(group, tot, scheme['blank_type'], ctx, sizes)
        if placed is None:
            pieces = _over_produce(group, tot, scheme['blank_type'], ctx, sizes, blank_weights)
            if pieces is None:
                pieces = _flatten_cut(group, tot, scheme['blank_type'], ctx, sizes, blank_weights)
            placed = None
            if pieces:
                placed = [{'orders': list(group),
                           'length_scheme': [p[0] for p in pieces],
                           'counts': [int(p[1]) for p in pieces],
                           'blank_type': scheme['blank_type'],
                           'blank_counts': [int(p[2]) for p in pieces]}]
        if placed is None:
            unplaced.extend(group)
            continue
        out.extend(placed)
    return out, unplaced


def main():
    root = Path(r'D:/02_Projects/ML/jinyinsai1_nolimit')
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'runs/semi_merged_v4/result.json'
    plan = json.loads(Path(src).read_text(encoding='utf-8'))
    from platform_score import load_scoring_data
    data = load_scoring_data(root / 'data/semi', 'semi')
    sizes = {o: x.size_m for o, x in data.orders.items()}
    ctx = {'linear': {o: x.physical_linear_weight for o, x in data.orders.items()},
           'diameter': {o: x.diameter_mm for o, x in data.orders.items()},
           'required': {o: float(x.required_kg) for o, x in data.orders.items()}}
    out, unplaced = build(plan, ctx, sizes, dict(data.blank_weights))
    json.dump(out, open(root / 'runs/_cand_final.json', 'w', encoding='utf-8'),
              ensure_ascii=False)
    print(json.dumps({'schemes': len(out), 'unplaced_orders': unplaced}, ensure_ascii=False))


if __name__ == '__main__':
    main()
