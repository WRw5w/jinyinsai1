"""Deterministic seeding-only A/B for the round-shape enumeration fix.

`solve_semi.py` cannot express this arm: `search_10s` rejects `seconds <= 0`, and
any positive annealing budget is wall-clock boxed and therefore not reproducible.
But the fix lives entirely in the CONSTRUCTION path -- `_seed_group` -> `_construct`
-> `_closing_rounds` -> `_round_moves` -> `_enumerate_round_shapes` -- and seeding
runs with `deadline=None`, i.e. on the problem's own difficulty rather than a
timer.  So this driver reproduces the driver's chunking (same group split, same
`--chunk`, same per-chunk seed arithmetic `seed + offset`) and calls the seeding
path directly, once per arm.  No clock, no annealing, byte-identical inputs: any
difference is the enumeration and nothing else.

    python diagnostics/seeding_ab.py --arm fixed  --out runs/seed_fixed.json
    python diagnostics/seeding_ab.py --arm legacy --out runs/seed_legacy.json

`--arm legacy` injects `diagnostics/solver_prefix_snapshot.py`, which is
`git show HEAD:solver.py` -- the pre-fix file verbatim, not a retyped copy.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'diagnostics'))

import solver as S  # noqa: E402

DATA = ROOT / 'data' / 'semi'


def install(arm: str):
    if arm == 'legacy':
        import solver_prefix_snapshot as legacy
        S._enumerate_round_shapes = legacy._enumerate_round_shapes
    S._ROUND_SHAPE_CACHE.clear()


def load():
    settings = json.loads((DATA / 'competition.config.json').read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective='platform_score', baseline_knives=160000)
    cfg = S.Config(**settings)
    orders = S.load_orders(str(DATA / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = S.load_blanks(str(DATA / 'blanks.normalized.csv'))
    return cfg, orders, blanks


def seed_chunk(chunk, cfg, blanks, seed):
    """`search_10s` with the annealing phase deleted -- seeding only, no clock."""
    model = S.Model(chunk, cfg, blanks)
    rng = random.Random(seed)
    out = []
    for group in S._groups(chunk):
        out.extend(S._seed_group(model, group, S._construct, rng, None, chunk, cfg))
    return S._export(out, chunk, cfg)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--arm', choices=('fixed', 'legacy'), required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--chunk', type=int, default=60)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--groups', nargs='*', default=None)
    args = ap.parse_args()

    install(args.arm)
    cfg, orders, blanks = load()

    from solve_semi import group_keys, pick_group
    keys = group_keys(orders)
    if args.groups:
        wanted = {(spec.partition(':')[0], float(spec.partition(':')[2])) for spec in args.groups}
        keys = [k for k in keys if k in wanted]

    plan, started, per_group = [], time.perf_counter(), []
    for steel, dia in keys:
        members = pick_group(orders, f'{steel}:{dia}')
        before = len(plan)
        for offset in range(0, len(members), args.chunk):
            plan.extend(seed_chunk(members[offset:offset + args.chunk], cfg, blanks,
                                   args.seed + offset))
        per_group.append((f'{steel}:{dia}', len(members), len(plan) - before))
        print(f'  [{steel}:{dia}] {len(members):>5} orders -> {len(plan) - before:>5} schemes '
              f'({time.perf_counter() - started:,.0f}s)', flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, separators=(',', ':')) + '\n',
                   encoding='utf-8')
    print(f'[arm {args.arm}] wrote {out}  ({len(plan):,} schemes, '
          f'{time.perf_counter() - started:,.0f}s)')


if __name__ == '__main__':
    main()
