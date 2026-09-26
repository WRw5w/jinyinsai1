"""Squeeze over-production out of a certified plan without touching its structure.

The complaint this answers: every order's pieces are delivered `count` bars at a
time, so the granularity of a delivery is `count` pieces, and the plan carries
220,141 delivered pieces (13.5 M kg) beyond demand.  Removing those is pure gain
on both scored terms -- each pattern piece dropped is one knife, and the material
it consumed shrinks the declared blank mass the yield divides by.

WHAT IT MAY CHANGE: piece counts per (round, order) entry, and optionally the
declared `blank_counts` / `blank_type`.  WHAT IT MUST NOT: the set of orders in a
round, their key order, the number of rounds, or `counts`.  Criterion B reads
only which orders share a round and where they sit inside it, so a length-only
edit cannot break the structure the platform certified on 2026-09-26.  The
assertions at the end re-derive that from the raw CSVs rather than trusting this
paragraph.

THE ALLOCATION RULE.  A dropped piece costs `size` metres of its round's bed
length and buys exactly one knife, wherever it is dropped; the material saved is
`size * count * lin_kg` and the bed floor (net >= 48 m) makes metres scarce, so
fill each round's slack with the smallest-`size` drops first.  Order slack
(`produced - required >= count`) and a one-piece floor per entry bound each
entry's drops.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from platform_check import check, load_orders_and_blanks, read_plan   # noqa: E402

NET_MIN = D('48')        # the checker allows physical (net + 2) in [50, 150]
NET_MAX = D('148')


def entry_k(length, size):
    """Pieces in one written length; refuses anything off the 定尺 grid."""
    ratio = D(str(length)) / size
    k = int(ratio.to_integral_value())
    if k <= 0 or abs(ratio - k) > D('1e-8'):
        raise ValueError(f'length {length!r} is not a multiple of {size}')
    return k


def measure(plan, orders, blanks, requant='keep'):
    """Platform-equivalent (knives, mat, declared, numerator, coverage) of a plan.

    `knives` follows the platform reading of the semi rule: one parting cut per
    written segment PLUS one trim pair per round, so `measure()['knives']` is what
    the receipt's 锯切刀数 reports.  `segments` keeps the cut count alone.
    """
    segments = rounds = 0
    mat = D(0)
    declared = D(0)
    delivered = defaultdict(D)
    combined, included = set(), set()
    for batch in plan:
        names = batch['orders']
        included.update(names)
        if len({(orders[n]['steel'], orders[n]['dia']) for n in names}) != 1:
            raise ValueError(f'scheme not homogeneous: {names[:3]}')
        linear = orders[names[0]]['linear']
        for scheme, count, stock in zip(batch['length_scheme'], batch['counts'],
                                        batch['blank_counts']):
            rounds += 1
            if len(set(scheme)) > 1:
                combined.update(scheme)
            net = D(0)
            for oid, length in scheme.items():
                k = entry_k(length, orders[oid]['size'])
                segments += k
                net += D(str(length))
                delivered[oid] += k * count * orders[oid]['size'] * orders[oid]['linear']
            weight = blanks[batch['blank_type']]
            row_mat = (net + 2) * count * linear
            mat += row_mat
            if requant == 'keep':
                declared += stock * weight
            else:
                declared += count_stock(row_mat, weight) * weight
    numerator = sum(min(mass, orders[oid]['weight']) for oid, mass in delivered.items())
    return dict(knives=segments + rounds, segments=segments, rounds=rounds,
                entries=sum(len(b['length_scheme']) for b in plan),
                mat=mat, declared=declared, numerator=numerator,
                coverage=len(combined), included=len(included), orders=len(orders),
                delivered=delivered)


def count_stock(row_mat, weight):
    """`blank_counts` the checker accepts: declared + 1e-7 >= (net + 2) * count * lin."""
    return int(((row_mat - D('1e-7')) / weight).to_integral_value(rounding='ROUND_CEILING'))


def summarize(m, blank_weights=None):
    y = 100 * float(m['numerator']) / float(m['declared'])
    c = 100 * m['coverage'] / m['orders']
    k = 100 * 160000 / m['knives']
    total = 0.4 * k + 0.4 * y + 0.2 * c
    return (f"knives={m['knives']:,} (rounds={m['rounds']:,})  declared={float(m['declared']):,.0f} kg  "
            f"Y={y:.4f}%  C={c:.4f}%  K={k:.3f}  total={total:.4f} -> {round(total, 2)}")


def trim(plan, orders, blanks, requant='keep'):
    """Apply the allocation rule in place; return stats."""
    produced = defaultdict(int)
    for batch in plan:
        for scheme, count in zip(batch['length_scheme'], batch['counts']):
            for oid, length in scheme.items():
                produced[oid] += entry_k(length, orders[oid]['size']) * count

    required = {oid: orders[oid]['pieces'] for oid in orders}
    # (size ascending, then the round's own order) -- see the module docstring.
    entries = []
    for a, batch in enumerate(plan):
        for j, (scheme, count) in enumerate(zip(batch['length_scheme'], batch['counts'])):
            for oid, length in scheme.items():
                entries.append((orders[oid]['size'], a, j, oid))
    entries.sort(key=lambda e: (e[0], e[1], e[2], e[3]))

    round_slack = {}
    for a, batch in enumerate(plan):
        for j, scheme in enumerate(batch['length_scheme']):
            net = sum((D(str(v)) for v in scheme.values()), D(0))
            round_slack[a, j] = net - NET_MIN

    dropped = defaultdict(int)
    dropped_mass = D(0)
    lim_slack = lim_entry = 0
    for size, a, j, oid in entries:
        batch = plan[a]
        scheme = batch['length_scheme'][j]
        count = batch['counts'][j]
        length = scheme[oid]
        k = entry_k(length, size)
        order_slack = produced[oid] - required[oid]
        drop = min(k - 1, order_slack // count, int(round_slack[a, j] / size))
        if drop <= 0:
            if order_slack < count and k > 1:
                lim_slack += 1
            elif k <= 1:
                lim_entry += 1
            continue
        new_k = k - drop
        scheme[oid] = float(D(new_k) * orders[oid]['size'])
        produced[oid] -= drop * count
        round_slack[a, j] -= drop * size
        dropped[oid] += drop
        linear = orders[batch['orders'][0]]['linear']
        dropped_mass += drop * size * count * linear

    if requant in ('recount', 'retype'):
        for batch in plan:
            linear = orders[batch['orders'][0]]['linear']
            mats = [(sum((D(str(v)) for v in scheme.values()), D(0)) + 2) * count * linear
                    for scheme, count in zip(batch['length_scheme'], batch['counts'])]
            if requant == 'retype':
                best = min(blanks, key=lambda t: sum(count_stock(m, blanks[t]) * blanks[t]
                                                     for m in mats))
                batch['blank_type'] = best
            weight = blanks[batch['blank_type']]
            batch['blank_counts'] = [count_stock(m, weight) for m in mats]
    return dict(dropped_pieces=sum(dropped.values()), dropped_mass=dropped_mass,
                orders_touched=len(dropped), blocked_by_round_slack=lim_slack,
                blocked_by_single_piece=lim_entry)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', type=Path, help='plan JSON or submission ZIP to trim')
    ap.add_argument('--data', type=Path, default=ROOT / 'data/semi')
    ap.add_argument('--round', default='semi')
    ap.add_argument('--requant', choices=['keep', 'recount', 'retype'], default='retype')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--stats', type=Path)
    args = ap.parse_args()

    orders, blanks, excluded, spec = load_orders_and_blanks(args.data, args.round)
    plan = read_plan(args.input)
    before = measure(plan, orders, blanks, requant=args.requant)
    stats = trim(plan, orders, blanks, requant=args.requant)

    report = check(plan, data=args.data, weight_mode='strict', round=args.round)
    if not report['passed']:
        raise SystemExit(f"trimmed plan fails the strict checker: {report['error_counts']}")
    after = measure(plan, orders, blanks, requant=args.requant)
    for oid, mass in after['delivered'].items():
        if mass < orders[oid]['weight'] - D('1e-7'):
            raise SystemExit(f'order {oid} fell below its registered weight after trimming')
    if after['coverage'] != before['coverage'] or after['included'] != before['included']:
        raise SystemExit('trim changed which orders share rounds -- that is not allowed')
    for batch in plan:
        for scheme in batch['length_scheme']:
            net = sum((D(str(v)) for v in scheme.values()), D(0))
            if not NET_MIN <= net <= NET_MAX:
                raise SystemExit(f'round net {net} outside [{NET_MIN}, {NET_MAX}]')

    args.output.write_text(json.dumps(plan, ensure_ascii=False, separators=(',', ':')) + '\n',
                           encoding='utf-8')
    out = dict(input=str(args.input), output=str(args.output), requant=args.requant,
               excluded=len(excluded), **{k: (float(v) if isinstance(v, D) else v)
                                          for k, v in stats.items()},
               before=summarize(before), after=summarize(after),
               before_knives=before['knives'], after_knives=after['knives'],
               before_declared=float(before['declared']), after_declared=float(after['declared']))
    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    if args.stats:
        args.stats.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
