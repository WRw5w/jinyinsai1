"""Make the orders that never share a round share one, and price each share.

The platform's coverage numerator counts an order only when it stands on a
round with another order (`platform_score` reads `len(set(scheme)) > 1`), so an
order that is cut alone is worth less than an order cut beside someone else,
however much steel it delivers.  `reshape_plan` already pays `COVER_BONUS_KG`
for a share when it moves a cut point, and `cut_packed`'s group gate pays for it
when a whole group is re-packed -- but neither can act on a single batch: the
sweep leaves lone-order batches alone by construction, and a re-pack that wins
one batch can lose the group.

This pass works batch by batch instead.  For every order that never shares, it
re-cuts that order's own batch (`cut_batch` on the same order list), and pairs
the batch with each other batch of its (steel, dia) group and re-cuts the union.
The rate is `cost_of`'s -- a knife is `KNIFE_KG` of declared mass, a share is
`COVER_BONUS_KG` of it -- and a move is taken only when it comes out strictly
cheaper at that rate.  A move that loses another order its share is priced as
the loss it is, so the pass cannot trade one of our shares for another.

Rounds, counts, pieces and blank types are re-derived by the same laying code
`repack_batches` uses; nothing here invents a cut.  The strict checker and the
scorer still gate the result: `main` writes the plan and then refuses to hand it
over if it fails `check(weight_mode='strict')` or scores no better.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repack_batches as rb                                    # noqa: E402
import shift_cuts as sc                                        # noqa: E402

CAP_ROUNDS = rb.CAP_ROUNDS
PARTNERS = 40           # partner batches a merge search looks at, nearest first


def lone_orders(plan, orders):
    """Order ids that never stand on a round with another order."""
    covered = set()
    for batch in plan:
        covered |= sc.covered_orders(batch['length_scheme'])
    return sorted(set(orders) - covered, key=lambda oid: orders[oid]['pieces'])


def group_key(batch, orders):
    first = batch['orders'][0]
    return (str(orders[first]['steel']), float(orders[first]['dia']))


def chain_len(oids, orders):
    """Metres the batch's chain needs, every order at its own tallest count."""
    return sum(chain for chain, _ in rb.batch_items(list(oids), orders))


def best_move(plan, orders, blanks, oid, max_rounds, knife_kg, partners):
    """The cheapest way to make `oid` share a round, or None.  `cost_of`'s rate."""
    at = next((a for a, b in enumerate(plan) if oid in b['orders']), None)
    if at is None:
        return None
    batch = plan[at]
    if oid in sc.covered_orders(batch['length_scheme']):
        return None                     # already shared, nothing to buy
    old = [batch]
    old_cost = rb.cost_of(old, orders, blanks, knife_kg)
    best = None                         # (gain, new batches, consumed indices, kind, detail)

    again = rb.cut_batch(list(batch['orders']), orders, blanks, max_rounds, knife_kg)
    if again is not None:
        gain = old_cost - rb.cost_of([again], orders, blanks, knife_kg)
        if gain > 0:
            best = (gain, [again], [at], 'recut', '')

    key = group_key(batch, orders)
    mine = chain_len(batch['orders'], orders)
    near = []
    for other in range(len(plan)):
        if other == at or group_key(plan[other], orders) != key:
            continue
        chain = chain_len(plan[other]['orders'], orders)
        if mine + chain <= max_rounds * sc.NET_MAX:      # else no laying can carry both
            near.append((abs(chain - mine), other))
    near.sort()
    for _, other in near[:partners]:
        mate = plan[other]
        seq = list(batch['orders']) + [o for o in mate['orders'] if o not in batch['orders']]
        made = rb.cut_batch(seq, orders, blanks, max_rounds, knife_kg)
        if made is None:
            continue
        gain = old_cost + rb.cost_of([mate], orders, blanks, knife_kg) - \
            rb.cost_of([made], orders, blanks, knife_kg)
        if gain > 0 and (best is None or gain > best[0]):
            best = (gain, [made], [at, other], 'merge', f'with batch {other}')
    return best


def share(plan, orders, blanks, max_rounds=CAP_ROUNDS, knife_kg=sc.KNIFE_KG,
          partners=PARTNERS, verbose=False):
    """Re-cut and merge batches until every order shares a round, or nothing wins."""
    stats = dict(recuts=0, merges=0, orders_newly_shared=0, gain_kg=0.0, sweeps=0)
    while True:
        lone = lone_orders(plan, orders)
        if not lone:
            break
        applied = 0
        for oid in lone:
            move = best_move(plan, orders, blanks, oid, max_rounds, knife_kg, partners)
            if move is None:
                continue
            gain, fresh, consumed, kind, detail = move
            shared_before = sum(len(sc.covered_orders(plan[a]['length_scheme']))
                                for a in consumed)
            for a in sorted(consumed, reverse=True):
                plan.pop(a)
            plan.extend(fresh)
            stats['orders_newly_shared'] += len(sc.covered_orders(fresh[0]['length_scheme'])) \
                - shared_before
            stats['gain_kg'] += gain
            stats['recuts' if kind == 'recut' else 'merges'] += 1
            applied += 1
            if verbose:
                print(f'  {kind:5s} {oid}  gain {gain:+,.0f} kg  {detail}')
        stats['sweeps'] += 1
        if not applied:
            break
    return plan, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', help='plan JSON whose lone orders are to be shared and merged')
    ap.add_argument('--output', required=True)
    ap.add_argument('--stats')
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--round', default='semi')
    ap.add_argument('--partners', type=int, default=PARTNERS)
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    sys.path.insert(0, 'src')
    from platform_check import check, load_orders_and_blanks, read_plan
    from platform_score import evaluate
    orders, blanks, _, _ = load_orders_and_blanks(Path(args.data), args.round)
    plan = read_plan(args.input)
    started = time.time()
    # `share` rewrites the plan in place, so the "before" reading has to come from
    # the file rather than from the object it hands back.
    before = evaluate(read_plan(args.input), Path(args.data), round_name=args.round)
    out, stats = share(plan, orders, blanks, partners=args.partners, verbose=args.verbose)
    stats['seconds'] = round(time.time() - started, 1)
    Path(args.output).write_text(
        json.dumps(out, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    report = check(out, data=Path(args.data), weight_mode='strict', round=args.round)
    stats['strict_passed'] = report['passed']
    stats['strict_errors'] = report.get('error_counts') or {}
    after = evaluate(out, Path(args.data), round_name=args.round)
    stats['knives_before'] = before['knives']
    stats['knives_after'] = after['knives']
    stats['coverage_before'] = before['coverage_percent_rounded']
    stats['coverage_after'] = after['coverage_percent_rounded']
    stats['score_before'] = round(before['score_capped'], 4)
    stats['score_after'] = round(after['score_capped'], 4)
    if args.stats:
        Path(args.stats).write_text(json.dumps(stats, ensure_ascii=False, indent=2) + '\n',
                                    encoding='utf-8')
    print(json.dumps(stats, ensure_ascii=False))
    if not report['passed'] or after['score_capped'] < before['score_capped'] - 1e-9:
        raise SystemExit('the shared plan is not an improvement: keep the input')


if __name__ == '__main__':
    main()
