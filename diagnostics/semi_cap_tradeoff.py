"""Sweep the over-production cap for the solo fallback and quantify the trade-off.

Read-only diagnostic.  For each cap ratio, pick the shape `_solo_scheme` would
choose (minimise `total_k + n`, then over-delivery) for every one of the 9,999
orders and report

  * total knives   -- `sum(ks) + 1` per round, the 40 % term of the score
  * total over-delivery in pieces and as a fraction of demand -- the yield term

so the cap can be chosen against the real objective instead of by taste.

Reading the result: raising the cap buys fewer knives (the 40 % term is `1/K`, so
it is knife-hungry) and costs uncredited material (the 30 % yield term).  The
sweep shows the curve flattens by ~10 % because the binding limit stops being the
cap and becomes the 60 t / 150 m geometry.  2 % is the smallest value that strands
no order, which is why the shipped config uses it.

Writes `diagnostics/semi_cap_tradeoff.json`.

Usage:
    python -X utf8 diagnostics/semi_cap_tradeoff.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solver import Config, Model, load_blanks, load_orders, _ceil, _floor  # noqa: E402

DATA = ROOT / 'data' / 'semi'


def main():
    raw = json.loads((DATA / 'competition.config.json').read_text(encoding='utf-8'))
    raw.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
               objective='platform_score', baseline_knives=90000,
               max_overproduction_ratio=0.02)
    cfg = Config(**raw)
    orders = load_orders(str(DATA / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(DATA / 'blanks.normalized.csv'))
    model = Model(orders, cfg, blanks)

    tr = cfg.segment_trim + cfg.round_trim
    len_hi = min(cfg.bed_length, max(model.blank_length((0,), b) for b in blanks))
    demand_pieces = sum(o.pieces for o in orders)

    def pick(o, i, cap):
        size = o.size
        k_lo = max(1, _ceil((cfg.min_bed_length - tr) / size))
        best = None
        for n in range(1, min(cfg.max_rounds, o.pieces) + 1):
            for p in range(1, model.parallel_limit((i,)) + 1):
                k_hi = min(_floor((len_hi - tr) / size),
                           _floor((cfg.bed_weight / (p * o.linear_weight) - tr) / size))
                if k_hi < k_lo:
                    continue
                t = max(n * k_lo, _ceil(o.pieces / p))
                if t > n * k_hi or p * t > cap:
                    continue
                key = (t + n, p * t, p)
                if best is None or key < best:
                    best = key
            if best is not None:
                return best        # n is swept ascending; first n with a shape wins
        return None

    rows = []
    for ratio in (0.0, 0.005, 0.02, 0.05, 0.10, 0.20, 0.50):
        knives = over = infeasible = 0
        for i, o in enumerate(orders):
            got = pick(o, i, o.pieces + _floor(o.pieces * ratio))
            if got is None:
                infeasible += 1
                continue
            t_plus_n, delivered, _p = got
            knives += t_plus_n
            over += delivered - o.pieces
        rows.append(dict(ratio=f'{ratio:.1%}', infeasible_orders=infeasible,
                         total_knives=knives, over_delivery_pieces=over,
                         over_delivery_of_demand=round(over / demand_pieces, 6)))

    report = dict(demand_pieces=demand_pieces,
                  note='solo fallback only; pairs are built by _construct',
                  rows=rows)
    (ROOT / 'diagnostics' / 'semi_cap_tradeoff.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('written diagnostics/semi_cap_tradeoff.json')
    for row in rows:
        print(f"  cap {row['ratio']:>6}  infeasible={row['infeasible_orders']:>5}  "
              f"knives={row['total_knives']:>8}  waste={row['over_delivery_of_demand']:.4%}")


if __name__ == '__main__':
    main()
