"""How much over-production does the semi-final PHYSICS force on a solo order?

Read-only diagnostic.  Answers the question that decided the
`max_overproduction_ratio` value: `_solo_scheme` used to demand
`parallel | pieces`, and the 50 m bed floor means `parallel = 1` cannot carry a
heavy order, so an order whose piece count is prime had no legal shape at all.
This measures, per order, the fewest pieces a legal round set can deliver, so the
cap can be set to the smallest value that strands nobody.

For one order the legal segment count of a round is a contiguous integer range
`[k_lo(p), k_hi(p)]` -- the 50/150 m window and the 60 t ceiling are both bounds on
`k` -- so existence and the minimal over-delivery are O(1) per `(n, p)` instead of
a `make_round` sweep.

Writes `diagnostics/semi_overproduction_probe.json`.

Usage:
    python -X utf8 diagnostics/semi_overproduction_probe.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solver import Config, Model, load_blanks, load_orders, _ceil, _floor  # noqa: E402

DATA = ROOT / 'data' / 'semi'


def main():
    raw = json.loads((DATA / 'competition.config.json').read_text(encoding='utf-8'))
    raw.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
               objective='platform_score', baseline_knives=90000)
    cfg = Config(**raw)
    orders = load_orders(str(DATA / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(DATA / 'blanks.normalized.csv'))
    model = Model(orders, cfg, blanks)

    tr = cfg.segment_trim + cfg.round_trim
    usable = max(model.blank_length((0,), b) for b in blanks)
    len_cap = min(cfg.bed_length, usable)

    def k_range(o, p):
        """Contiguous legal segment range for one order at parallel `p`."""
        s, lin = o.size, o.linear_weight
        k_hi = _floor((len_cap - tr) / s)
        if p * lin > 0:
            k_hi = min(k_hi, _floor((cfg.bed_weight / (p * lin) - tr) / s))
        k_lo = max(1, _ceil((cfg.min_bed_length - tr) / s))
        return (k_lo, k_hi) if k_lo <= k_hi else None

    def current_rule_ok(i):
        """The retired rule: `parallel` had to divide the demand exactly."""
        o = model.orders[i]
        for n in range(1, cfg.max_rounds + 1):
            if n > o.pieces:
                continue
            for p in range(1, model.parallel_limit((i,)) + 1):
                t = _ceil(o.pieces / p)
                if t * p != o.pieces:
                    continue
                rng_ = k_range(o, p)
                if rng_ and n * rng_[0] <= t <= n * rng_[1]:
                    return True
        return False

    def best_shape(i, cap_ratio):
        """Minimal over-delivery; cap_ratio <= 0 means physics only (no cap)."""
        o = model.orders[i]
        cap = o.pieces + _floor(o.pieces * cap_ratio) if cap_ratio > 0 else 0
        best = None
        for p in range(1, model.parallel_limit((i,)) + 1):
            rng_ = k_range(o, p)
            if rng_ is None:
                continue
            k_lo, k_hi = rng_
            t_ceil = cap // p if cap_ratio > 0 else 10 ** 9
            for n in range(1, cfg.max_rounds + 1):
                t = max(n * k_lo, _ceil(o.pieces / p))
                if t > n * k_hi or t > t_ceil:
                    continue
                delivered = p * t
                if best is None or delivered < best[0]:
                    best = (delivered, p, t, n)
        return best

    total = len(orders)
    stranded = [i for i in range(total) if not current_rule_ok(i)]

    buckets = {'0': 0, '<=0.5%': 0, '<=2%': 0, '<=5%': 0, '<=10%': 0,
               '<=25%': 0, '>25%': 0, 'physics_infeasible': 0}
    worst = []
    for i in stranded:
        o = model.orders[i]
        b = best_shape(i, 0.0)
        if b is None:
            buckets['physics_infeasible'] += 1
            continue
        delivered, p, t, n = b
        over = delivered - o.pieces
        ratio = over / o.pieces
        if over == 0:
            buckets['0'] += 1
        elif ratio <= 0.005:
            buckets['<=0.5%'] += 1
        elif ratio <= 0.02:
            buckets['<=2%'] += 1
        elif ratio <= 0.05:
            buckets['<=5%'] += 1
        elif ratio <= 0.10:
            buckets['<=10%'] += 1
        elif ratio <= 0.25:
            buckets['<=25%'] += 1
        else:
            buckets['>25%'] += 1
        worst.append((ratio, over, o.pieces, p, t, n, o.oid, o.size))

    cap_scan = {f'{r:.2%}': sum(1 for i in stranded if best_shape(i, r) is None)
                for r in (0.0, 0.005, 0.01, 0.02, 0.05, 0.10, 0.20)}

    worst.sort(reverse=True)
    report = dict(
        orders=total,
        orders_stranded_by_the_old_rule=len(stranded),
        geometry=dict(bed_length=cfg.bed_length, min_bed_length=cfg.min_bed_length,
                      round_trim=cfg.round_trim, segment_trim=cfg.segment_trim,
                      bed_weight=cfg.bed_weight, max_rounds=cfg.max_rounds,
                      usable_length=round(usable, 4), length_cap=round(len_cap, 4)),
        forced_overshoot=buckets,
        stranded_orders_remaining_per_cap=cap_scan,
        worst_overshoot=[dict(oid=w[6], pieces=w[2], size=w[7], over=w[1],
                              ratio=round(w[0], 4), parallel=w[3], segments=w[4], rounds=w[5])
                         for w in worst[:25]],
        sample_stranded=[dict(oid=model.orders[i].oid, pieces=model.orders[i].pieces,
                              size=model.orders[i].size,
                              weight=round(model.orders[i].weight, 2)) for i in stranded[:15]],
    )
    (ROOT / 'diagnostics' / 'semi_overproduction_probe.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('written diagnostics/semi_overproduction_probe.json')
    print(f'  orders stranded by the old rule : {len(stranded)} / {total}')
    print(f'  stranded with a 2% cap          : {cap_scan["2.00%"]}')


if __name__ == '__main__':
    main()
