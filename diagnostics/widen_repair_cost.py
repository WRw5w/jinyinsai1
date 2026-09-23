"""Cost/benefit of the two ways to stop truncation from eating the wide beds.

`_enumerate_round_shapes` walks `parallel` ASCENDING and abandons the sweep once
`_SHAPE_WORK_LIMIT` `offer` probes are spent, so the budget dies on the narrow beds
and the wide (cheap) ones are never visited.  `widen_budget_probe.py` showed the
3-size group losing 237 wider twins / 2,411 knives at the shipped 300,000 budget,
and recovering all of them at 1,000,000.

Two candidate repairs:

  A. raise the budget (pay for the probes)
  B. sweep `parallel` DESCENDING (spend the same probes on the wide beds first)

This probe times both and re-measures the wider-twin loss, so the repair can be
chosen on measured cost rather than taste.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'diagnostics'))
import solver as S  # noqa: E402
from shape_dedup_probe import build, brute_max_p  # noqa: E402


def reverse_sweep(model, ids, sizes, linear, blank_len, plimit, cfg):
    """`_enumerate_round_shapes` with the parallel sweep reversed, for timing."""
    import itertools
    max_seg = [int((cfg.bed_length - cfg.round_trim) // s) + 1 if s else 0 for s in sizes]
    best = {}
    probes = 0
    truncated = False

    def offer(parallel, ks):
        nonlocal probes
        probes += 1
        lengths = [k * s for k, s in zip(ks, sizes) if k]
        total = sum(lengths) + cfg.round_trim
        if not cfg.min_bed_length - 1e-8 <= total <= cfg.bed_length + 1e-8:
            return False
        if total * parallel * linear > cfg.bed_weight + 1e-8:
            return False
        if max(lengths) + cfg.round_trim > blank_len + 1e-8:
            return False
        delta = tuple(k * parallel for k in ks)
        old = best.get(delta)
        if old is not None and parallel <= old[0]:
            return False
        best[delta] = (parallel, ks)
        return True

    for parallel in range(plimit, 0, -1):
        highs = [max(0, max_seg[j]) for j in range(len(ids))]
        if not any(highs):
            continue
        for j in range(len(ids)):
            if highs[j] <= 0:
                continue
            ks = [0] * len(ids)
            ks[j] = highs[j]
            offer(parallel, ks)
        counter = 0
        for ks in itertools.product(*[range(hi, -1, -1) for hi in highs]):
            counter += 1
            if counter % S._SHAPE_WORK_STRIDE == 0 and probes > S._SHAPE_WORK_LIMIT:
                truncated = True
                break
            if not any(ks):
                continue
            offer(parallel, ks)
        if truncated:
            break
    return best, probes, truncated


def measure(label, fn, best_table):
    t0 = time.perf_counter()
    got = fn()
    dt = time.perf_counter() - t0
    # `_enumerate_round_shapes` hands back a list of `(delta, parallel, ks)`;
    # `reverse_sweep` hands back the raw `{delta: (parallel, ks)}` table.
    if isinstance(got, dict):
        tbl = {d: (p, k, sum(k) + 1) for d, (p, k) in got.items()}
    else:
        tbl = {m[0]: (m[1], m[2], sum(m[2]) + 1) for m in got}
    wider = [d for d in tbl if d in best_table and best_table[d][0] > tbl[d][0]]
    lost = sum(tbl[d][2] - best_table[d][2] for d in wider)
    print(f'  {label:<34} {dt * 1000:>9.1f} ms   deltas {len(tbl):>7,}  '
          f'wider {len(wider):>5,}  lost {lost:>6,}')
    return dt


def main():
    cfg, orders, model = build()
    index = {o.oid: i for i, o in enumerate(orders)}
    plan = json.loads((ROOT / 'submission_semi_nolimit' / '复赛结果_棒材优化.json')
                      .read_text(encoding='utf-8'))
    ids = None
    for batch in plan:
        if len(batch['orders']) == 3 and orders[index[batch['orders'][0]]].diameter == 43.0:
            ids = tuple(sorted(index[o] for o in batch['orders']))
            break
    blank = model.catalogue(ids)[0]
    sizes = [model.orders[i].size for i in ids]
    linear = model.orders[ids[0]].linear_weight
    blank_len = model.blank_length(ids, blank)
    plimit = model.parallel_limit(ids)
    best_table = brute_max_p(model, ids, cfg, blank_len)
    print(f'3-size group ids={ids} plimit={plimit} '
          f'brute legal deltas {len(best_table):,}')

    original = S._SHAPE_WORK_LIMIT
    try:
        print('A. raise the budget (current ascending sweep):')
        for limit in (300_000, 600_000, 1_000_000, 2_000_000):
            S._SHAPE_WORK_LIMIT = limit
            measure(f'work_limit {limit:,}', lambda: S._enumerate_round_shapes(
                model, ids, sizes, linear, blank_len, plimit, cfg), best_table)
        print('B. descending sweep at the shipped budget:')
        for limit in (300_000, 600_000):
            S._SHAPE_WORK_LIMIT = limit
            measure(f'desc work_limit {limit:,}',
                    lambda: reverse_sweep(model, ids, sizes, linear, blank_len, plimit, cfg)[0],
                    best_table)
    finally:
        S._SHAPE_WORK_LIMIT = original


if __name__ == '__main__':
    main()
