"""The knife count's theoretical floor for the semi-final drop.

The first attempt got this wrong and the mistake is worth recording, because the
conclusion flips on it.  `pieces` is the number of 5 m rods demanded, and `knives`
came out *smaller* than `pieces` -- which is impossible if each rod costs a cut.  It
does not, because the saw cuts `parallel` bars in ONE pass: a round that puts
`k_i` segments on each of `p` bars yields `p * sum(k_i)` rods using only
`sum(k_i) + 1` knives (the 09-15 11:11 ruling: "锯切刀数不乘棒材根数", plus one
head/tail pair per round).  So

    knives per round = sum(k) + 1
    rods  per round = p * sum(k)
    =>  knives per rod = (sum(k) + 1) / (p * sum(k))

and the lever is `p`, NOT `sum(k)`.  `p` is capped twice:

    width : p <= floor(bed_width_mm / diameter)      -- 2000 mm of bed
    weight: p <= bed_weight / (round_length * linear) -- 60 t of bars

with round_length = sum(k)*size + trim constrained to [50 m, 150 m].  Those three
constraints are what make the floor finite, and the optimum sits where `sum(k)` is
SMALLEST (short rounds are light, so `p` stays wide) -- the opposite of the instinct
that long rounds amortise better.

Reported per order as `pieces * best_ratio`, i.e. the cost if every round were full.
Real plans pay extra for the last partial round and for schemes shared between
orders, so this is a genuine lower bound.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from solver import Config, load_orders  # noqa: E402

settings = json.loads((ROOT / 'data/semi/competition.config.json').read_text(encoding='utf-8'))
settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                objective='platform_score', baseline_knives=160000)
cfg = Config(**settings)
orders = load_orders(str(ROOT / 'data/semi/orders.normalized.csv'), cfg, skip_invalid=True)

floor_total = 0.0
per_dia = defaultdict(lambda: [0, 0.0, 0.0])   # dia -> [orders, pieces, knives floor]
for o in orders:
    p_width = int(cfg.bed_width_mm // o.diameter)
    best = None
    for sum_k in range(1, int((cfg.bed_length - cfg.round_trim) // o.size) + 1):
        length = sum_k * o.size + cfg.round_trim
        if length < cfg.min_bed_length - 1e-8:
            continue
        p = min(p_width, int(cfg.bed_weight // (length * o.linear_weight)))
        if p < 1:
            continue
        rods = p * sum_k
        knives = sum_k + 1
        ratio = knives / rods
        if best is None or ratio < best[0]:
            best = (ratio, sum_k, p, length, rods, knives)
    if best is None:
        continue
    ratio, sum_k, p, length, rods, knives = best
    floor_total += o.pieces * ratio
    slot = per_dia[o.diameter]
    slot[0] += 1
    slot[1] += o.pieces
    slot[2] += o.pieces * ratio

print(f'orders                {len(orders)}')
print(f'sum(pieces)           {sum(o.pieces for o in orders):,}')
print()
print('best per-round shape by diameter (the floor assumes every round is full):')
print(f'{"dia":>7} {"orders":>7} {"pieces":>12} {"km/rd":>6} {"p":>4} {"len":>6} '
      f'{"rods/rd":>8} {"kn/rd":>6} {"kn/rod":>8}')
for dia in sorted(per_dia):
    o = next(x for x in orders if x.diameter == dia)
    p_width = int(cfg.bed_width_mm // dia)
    best = None
    for sum_k in range(1, int((cfg.bed_length - cfg.round_trim) // o.size) + 1):
        length = sum_k * o.size + cfg.round_trim
        if length < cfg.min_bed_length - 1e-8:
            continue
        p = min(p_width, int(cfg.bed_weight // (length * o.linear_weight)))
        if p < 1:
            continue
        ratio = (sum_k + 1) / (p * sum_k)
        if best is None or ratio < best[0]:
            best = (ratio, sum_k, p, length, p * sum_k, sum_k + 1)
    n, pieces, _ = per_dia[dia]
    print(f'{dia:>7} {n:>7} {pieces:>12,} {best[1]:>6} {best[2]:>4} {best[3]:>6.0f} '
          f'{best[4]:>8} {best[5]:>6} {best[0]:>8.5f}')

print()
print(f'THEORETICAL FLOOR     {floor_total:,.0f} knives')
for base in (90000, 160000):
    print(f'  vs baseline {base:>6,}: {floor_total / base:.2f}x -> subscore '
          f'{min(1.0, base / floor_total) * 40:.1f}/40 if the floor were reached')
print()
print('NOTE: the floor is NOT reachable -- it ignores that shared schemes must serve')
print('several orders at once, that the last round of each order runs part-full, and')
print('that exactly-hitting the demanded piece count needs p | pieces.  It only says')
print('how much room exists above it.')
