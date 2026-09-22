"""End-to-end check: does `_solo_scheme` place every one of the 9,999 orders?

Read-only diagnostic, run against the CURRENT config.  It replicates
`_seed_group`'s fallback loop (try each catalogue blank, no deadline) and reports
any order that still has no scheme, plus the over-delivery the solver ends up
spending.  This is the harness that proves the over-production fix closed the gap
the old `parallel | pieces` rule left open.

Writes `diagnostics/semi_solo_placement.json`.

Usage:
    python -X utf8 diagnostics/semi_solo_placement.py
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solver import Config, Model, load_blanks, load_orders, _solo_scheme  # noqa: E402

DATA = ROOT / 'data' / 'semi'


def main():
    raw = json.loads((DATA / 'competition.config.json').read_text(encoding='utf-8'))
    raw.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
               objective='platform_score', baseline_knives=90000)
    cfg = Config(**raw)
    orders = load_orders(str(DATA / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(DATA / 'blanks.normalized.csv'))
    model = Model(orders, cfg, blanks)
    rng = random.Random(7)

    failures, total_over, max_ratio = [], 0.0, 0.0
    ratios, round_hist = [], {}
    for i, o in enumerate(orders):
        got = None
        for blank in model.catalogue((i,)):
            got = _solo_scheme(model, (i,), blank, rng, None, randomized=False)
            if got is not None:
                break
        if got is None:
            failures.append(o.oid)
            continue
        delivered = sum(k * r.parallel for r in got.rounds for k in r.ks)
        if delivered > model.caps[i]:
            failures.append(o.oid + '(over cap)')
        over = delivered - o.pieces
        total_over += over
        ratio = over / o.pieces
        ratios.append(ratio)
        max_ratio = max(max_ratio, ratio)
        round_hist[len(got.rounds)] = round_hist.get(len(got.rounds), 0) + 1

    ratios.sort()
    demand = sum(o.pieces for o in orders)
    report = dict(
        orders=len(orders),
        failures=len(failures),
        sample_failures=failures[:20],
        rounds_per_scheme={str(k): v for k, v in sorted(round_hist.items())},
        overshoot=dict(
            total_pieces=round(total_over, 1),
            relative_to_demand=round(total_over / demand, 6),
            max_order_ratio=round(max_ratio, 6),
            p50=round(ratios[len(ratios) // 2], 6),
            p99=round(ratios[int(len(ratios) * 0.99)], 6),
            orders_with_any_overshoot=sum(1 for r in ratios if r > 0),
        ),
    )
    (ROOT / 'diagnostics' / 'semi_solo_placement.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('written diagnostics/semi_solo_placement.json')
    print(f'  order placement failures: {len(failures)} / {len(orders)}')


if __name__ == '__main__':
    main()
