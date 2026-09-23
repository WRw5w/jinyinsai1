"""Is the annealing phase losing its own best-so-far?

`search_10s` keeps `best_metrics` / `best` and returns `_export(best.values())`, so
its output must never be worse -- by the solver's OWN `_key` -- than the seeded
incumbent it starts from.  `_key` is what the loop compares with, so a regression
in `_key` is a bookkeeping failure, not a debatable objective.

Method, all inside the exact production code path:
  * `seconds` tiny  -> `deadline` is already in the past, the annealing loop runs
    zero iterations, and `_export(best)` hands back the seeded incumbent.  This is
    the seeding baseline INCLUDING any objective-convention quirk.
  * `seconds` real  -> the annealing phase runs.
`S._export` is monkeypatched so the in-memory `_key(_totals(batches))` of whatever
`best` holds at export time is recorded -- i.e. the same numbers the loop compared.

    python diagnostics/anneal_regress.py --seconds 20 --chunk 60 --seed 1
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import solver as S  # noqa: E402

DATA = ROOT / 'data' / 'semi'
CAPTURED: list = []


def load():
    settings = json.loads((DATA / 'competition.config.json').read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective='platform_score', baseline_knives=160000)
    cfg = S.Config(**settings)
    orders = S.load_orders(str(DATA / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = S.load_blanks(str(DATA / 'blanks.normalized.csv'))
    return cfg, orders, blanks


def instrument():
    real_export = S._export

    def export(batches, orders, cfg):
        batches = list(batches)
        key = S._key(S._totals(batches), cfg, len(orders))
        CAPTURED.append((key, len(batches), S._totals(batches)))
        return real_export(batches, orders, cfg)
    S._export = export


def run(chunk, cfg, blanks, seconds, seed, stats=None):
    CAPTURED.clear()
    t0 = time.perf_counter()
    plan = S.search_10s(chunk, cfg, seconds=seconds, seed=seed, blanks=blanks, stats=stats)
    return plan, CAPTURED[0], time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--seconds', type=float, default=20.0)
    ap.add_argument('--chunk', type=int, default=60)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--top', type=int, default=60)
    args = ap.parse_args()

    instrument()
    cfg, orders, blanks = load()
    from solve_semi import group_keys
    keys = group_keys(orders)
    steel, size = keys[0]
    pool = [o for o in orders if (o.steel, o.diameter) == (steel, size)]
    chunk = pool[:args.chunk]
    print(f"group {steel}:{size}   chunk of {len(chunk)} orders   seed {args.seed}")

    tiny, (k_seed, n_seed, m_seed), _ = run(chunk, cfg, blanks, 1e-9, args.seed)
    big, (k_best, n_best, m_best), elapsed = run(chunk, cfg, blanks, args.seconds, args.seed)

    print(f"\n  seeded incumbent (seconds=1e-9)   key={k_seed}  batches={n_seed}")
    print(f"  after annealing (seconds={args.seconds})   key={k_best}  batches={n_best}"
          f"   ({elapsed:.1f}s wall)")
    print(f"\n  DELTA  key[0] {-1 * (k_best[0] - k_seed[0]):+0.6f} points"
          f"   knives {k_best[1] - k_seed[1]:+d}"
          f"   batches {n_best - n_seed:+d}")
    print(f"  metrics seed {tuple(round(x, 3) if isinstance(x, float) else x for x in m_seed)}")
    print(f"  metrics best {tuple(round(x, 3) if isinstance(x, float) else x for x in m_best)}")
    if k_best > k_seed:
        print("\n  *** REGRESSION: the returned plan is worse than the seeded incumbent "
              "by the solver's own `_key`. ***")
    else:
        print("\n  best-so-far held: annealing did not regress its own objective.")


if __name__ == '__main__':
    main()
