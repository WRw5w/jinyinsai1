"""How full each round is, and what actually stops it from being fuller.

The plant's tempting target is "pack the rounds fuller and there are 1,178 fewer
of them": treat every order at its own tallest count, pack each (steel, diameter)
group into six-round batches, and ceil the chain against the 148 m bed.  `runs/
_repack_sim.py` and `_group_floor.py` print exactly that, and the real plan does
not reach it -- re-choosing the whole batch composition buys 8 rounds, not 1,178.
This script says why, in three measurements over a plan:

1. **Fill against the round's own cap.**  A round can carry `cap(c) = min(148,
   60000 / (c * lin) - 2)` metres of net bar -- the length limit, or the 60 t bed
   less the 2 m trim.  A round that looks half-empty against 148 m may be full
   against its own cap, which the count alone decided.
2. **Which constraint sets that cap.**  If the bed binds for most rounds, then
   "fill to 148" is not on the table at all.
3. **The bill step.**  A round's declared mass is `ceil((net + 2) * c * lin / w)
   * w` -- a staircase in `net`, one whole billet per tread.  A round parked a few
   metres below the next tread cannot take the extra pieces without paying a
   whole billet for them, which is how a round ends up "short" of its cap with
   nothing left to win there.  So `min(shortfall, room before the next tread)`,
   not the shortfall, is what a fuller round could actually use.

Knives are `sum(ceil(pieces / c))` segments plus one per round, and the score
prices them against declared mass (see docs/SCORE_MODEL.md), which is why the
count cannot simply be raised: raising it lowers the segment count and lowers
`cap(c)` with it.
"""
import argparse
import math
import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from platform_check import load_orders_and_blanks, read_plan   # noqa: E402


def rows_of(plan, orders, blanks):
    """(net, cap, metres until the bill pays one more billet, pieces) per round."""
    for batch in plan:
        lin = float(orders[batch['orders'][0]]['linear'])
        weight = float(blanks[batch['blank_type']])
        for scheme, count in zip(batch['length_scheme'], batch['counts']):
            net = float(sum((D(str(v)) for v in scheme.values()), D(0)))
            cap = min(148.0, 60000.0 / (count * lin) - 2.0)
            used = (net + 2.0) * count * lin
            tread = math.ceil(used / weight - 1e-9) * weight
            pieces = sum(int(D(str(v)) / orders[oid]['size']) for oid, v in scheme.items())
            dia = max(float(orders[oid]['dia']) for oid in scheme)
            yield dict(net=net, cap=cap, room=(tread - used) / (count * lin),
                       count=count, pieces=pieces, roller=int(2000.0 / dia),
                       lin=lin)


def report(path, orders, blanks):
    rows = list(rows_of(read_plan(path), orders, blanks))
    n = len(rows)
    full = [r for r in rows if r['net'] >= r['cap'] - 1e-9]
    bed = [r for r in rows if r['cap'] < 148.0]
    short = sorted(r['cap'] - r['net'] for r in rows)
    room = sorted(r['room'] for r in rows)
    ceil_used = [r for r in rows if r['cap'] - r['net'] >= r['room'] - 1e-9]
    print(f'{path}: {n:,} rounds')
    print(f'  mean net {sum(r["net"] for r in rows)/n:.1f} m   '
          f'mean net/cap {sum(r["net"]/r["cap"] for r in rows)/n*100:.1f}%   '
          f'at cap {len(full):,}')
    print(f'  bed-bound (cap < 148) {len(bed):,}   length-bound {n - len(bed):,}')
    print(f'  mean count / roller cap {sum(r["count"]/r["roller"] for r in rows)/n*100:.1f}%')
    print(f'  shortfall vs cap: mean {sum(short)/n:.1f} m, median {short[n//2]:.1f} m')
    print(f'  room before +1 billet: mean {sum(room)/n:.1f} m, median {room[n//2]:.1f} m')
    print(f'  tread-bound (shortfall >= that room): {len(ceil_used):,} / {n:,} '
          f'({len(ceil_used)/n*100:.1f}%)   cap-bound: {n - len(ceil_used):,}')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plans', nargs='+', help='plan JSONs to measure')
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--round', default='semi')
    args = ap.parse_args()
    orders, blanks, _, _ = load_orders_and_blanks(Path(args.data), args.round)
    for plan in args.plans:
        report(plan, orders, blanks)


if __name__ == '__main__':
    main()
