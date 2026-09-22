"""How much combination coverage is even reachable? Probe the pairing ceiling.

Read-only diagnostic.  Coverage is 20 % of the semi-final score and needs two
orders in the SAME cold-bed round, so a shared scheme must fit `max_rounds = 6`
for every member.  The hard gate is therefore `_can_share`:
`rounds_min(a) + rounds_min(b) <= 6`.

Finding (2026-09-21, the real drop): `_can_share` passes for 100 % of adjacent
pairs in all 36 groups -- 7,029 orders need one round, 2,341 need two, 628 need
three, one needs four -- so the round budget is NOT the bottleneck.  Pairing fails
in practice because `_construct`'s closing round demands an exact landing, which is
why the over-production cap had to become non-zero there too.

Writes `diagnostics/semi_coverage_ceiling.txt`.

Usage:
    python -X utf8 diagnostics/semi_coverage_ceiling.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solver import (Config, Model, load_blanks, load_orders, _can_share,  # noqa: E402
                    _max_delivery_table, _min_rounds_for)

DATA = ROOT / 'data' / 'semi'


def main():
    raw = json.loads((DATA / 'competition.config.json').read_text(encoding='utf-8'))
    raw.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
               objective='platform_score', baseline_knives=90000)
    cfg = Config(**raw)
    orders = load_orders(str(DATA / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(DATA / 'blanks.normalized.csv'))
    model = Model(orders, cfg, blanks)

    groups: dict = {}
    for i, o in enumerate(orders):
        groups.setdefault((o.steel, o.diameter), []).append(i)

    lines, tot_orders = [], 0
    rounds_hist: Counter = Counter()
    for key, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        pair_pass = pair_tried = 0
        for pos, i in enumerate(members):
            o = model.orders[i]
            table = _max_delivery_table(model, (i,), o)
            rounds_hist[_min_rounds_for(model, (i,), o, o.pieces, cfg.max_rounds, table)] += 1
            if pos + 1 < len(members):
                pair_tried += 1
                if _can_share(model, (i, members[pos + 1])):
                    pair_pass += 1
        tot_orders += len(members)
        lines.append(f'{key[0]}:{key[1]}\torders={len(members)}\tadjacent_pairs={pair_tried}\t'
                     f'_can_share_pass={pair_pass} ({pair_pass / max(1, pair_tried):.1%})')

    report = dict(
        orders=tot_orders,
        rounds_min_hist={str(k): v for k, v in sorted(rounds_hist.items())},
        orders_with_single_round_min=rounds_hist.get(1, 0),
        orders_needing_3plus=sum(v for k, v in rounds_hist.items() if k >= 3),
    )
    (ROOT / 'diagnostics' / 'semi_coverage_ceiling.txt').write_text(
        '\n'.join(lines) + '\n' + json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('written diagnostics/semi_coverage_ceiling.txt')
    print('  rounds_min histogram:', dict(sorted(rounds_hist.items())))


if __name__ == '__main__':
    main()
