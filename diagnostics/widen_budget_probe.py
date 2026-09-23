"""Is the residual "wider twin" loss caused by the enumeration work budget?

After the `offer()` guard fix, the 1- and 2-size groups keep the widest bed for
every delivery (`shape_dedup_probe.py` reports zero wider twins).  The 3-size
group still shows a few hundred, which is suspicious: `_SHAPE_WORK_LIMIT` caps the
enumeration at 300,000 `offer` probes, and the `parallel` sweep runs ASCENDING, so
the budget is spent on the NARROW beds and the wide ones are never visited at all.

This probe re-enumerates the same geometry under progressively larger budgets and
prints how many wider twins survive.  If the count collapses as the budget grows,
the residual loss is truncation bias, not the dedup rule.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'diagnostics'))
import solver as S  # noqa: E402
from shape_dedup_probe import build, brute_max_p  # noqa: E402


def find_group(plan, orders, index, want_len, want_dia):
    for batch in plan:
        if len(batch['orders']) != want_len:
            continue
        if orders[index[batch['orders'][0]]].diameter == want_dia:
            return tuple(sorted(index[o] for o in batch['orders']))
    return None


def main():
    cfg, orders, model = build()
    index = {o.oid: i for i, o in enumerate(orders)}
    plan = json.loads((ROOT / 'submission_semi_nolimit' / '复赛结果_棒材优化.json')
                      .read_text(encoding='utf-8'))
    ids = find_group(plan, orders, index, 3, 43.0)
    print(f'ids {ids}  dias {[model.orders[i].diameter for i in ids]}')
    blank = model.catalogue(ids)[0]
    sizes = [model.orders[i].size for i in ids]
    linear = model.orders[ids[0]].linear_weight
    blank_len = model.blank_length(ids, blank)
    plimit = model.parallel_limit(ids)
    best = brute_max_p(model, ids, cfg, blank_len)
    print(f'plimit {plimit}  brute legal deltas {len(best):,}')

    original = S._SHAPE_WORK_LIMIT
    try:
        for limit in (300_000, 1_000_000, 5_000_000, 50_000_000):
            S._SHAPE_WORK_LIMIT = limit
            got = S._enumerate_round_shapes(model, ids, sizes, linear, blank_len, plimit, cfg)
            kept = {m[0]: (m[1], m[2], sum(m[2]) + 1) for m in got}
            wider = [d for d in kept if d in best and best[d][0] > kept[d][0]]
            lost = sum(kept[d][2] - best[d][2] for d in wider)
            print(f'  work_limit {limit:>12,}: moves {len(kept):>7,}  '
                  f'wider-twin deltas {len(wider):>6,}  knives lost {lost:>7,}')
    finally:
        S._SHAPE_WORK_LIMIT = original


if __name__ == '__main__':
    main()
