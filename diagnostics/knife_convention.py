"""Which knife convention is real?  Strict floor vs snap-near-integers.

`platform_score.row_metrics` counts a row's pieces as `int(L // size)`.  For a
segment written as an exact multiple of the order's size, that expression is
hostage to binary floating point: `3 * 6.9 / 6.9` can come out as
2.9999999999999996 and floor to 2, one piece short of the 3 the plan actually
cuts.  The plans in hand hit this constantly, so the same plan prices at two
very different knife counts depending on how the leftover is handled:

  floor   int(L // size)                        -- literal reading of the code
  snap    int((L + eps) // size), eps relative -- "the planner meant k"

The preliminary round's calibrated feedback leans to *snap*: the 2026-09-16
92.02 observation shows the platform refilling exactly those collapsed segments
(see the `entry_knives` docstring).  This script prices both plans both ways so
the size of the exposure is explicit, and checks which reading reproduces the
internal ledger (`runs/*/progress.json`, 197,569 knives for our plan).
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import platform_score as P  # noqa: E402

DATA = ROOT / 'data' / 'semi'
EPS = 1e-9


def find_plan(folder: Path) -> Path:
    cands = [p for p in folder.glob('*.json')
             if 'validation' not in p.name and 'score' not in p.name]
    return max(cands, key=lambda p: p.stat().st_size)


def pieces_of(length: float, size: float, snap: bool) -> int:
    if not snap:
        return int(length // size)
    return int((length + abs(length) * EPS) // size)


def price(name: str, path: Path, context, snap: bool):
    plan = json.loads(path.read_text(encoding='utf-8'))
    knives = rows = collapsed = 0
    delivered = defaultdict(float)      # oid -> credited mass (capped at demand)
    produced = defaultdict(float)       # oid -> raw delivered mass
    raw = 0.0
    for batch in plan:
        for scheme, parallel, stock in zip(batch['length_scheme'], batch['counts'],
                                           batch['blank_counts']):
            k = 0
            for oid, length in scheme.items():
                o = context.orders[oid]
                kk = pieces_of(length, o.size_m, snap)
                if snap and int(length // o.size_m) != kk:
                    collapsed += 1
                k += kk
                produced[oid] += length * parallel * o.linear_weight
            knives += k + 1
            rows += 1
            raw += stock * context.blank_weights[batch['blank_type']]
    credited = 0.0
    for oid, o in context.orders.items():
        ceiling = P._demand_numerator_mass(o)
        credited += min(produced.get(oid, 0.0), ceiling)
    return dict(name=name, snap=snap, knives=knives, rows=rows,
                collapsed=collapsed, yield_pct=100 * credited / raw, raw=raw,
                score=0.4 * min(100.0, 100 * 160000 / knives)
                      + 0.3 * (100 * credited / raw) + 0.2 * 100.0 + 0.1 * 100.0)


def main():
    context = P.load_scoring_data(DATA, 'semi')
    paths = {'OURS': find_plan(ROOT / 'submission_semi_nolimit'),
             'THEIRS': find_plan(ROOT / 'runs' / 'theirs_main')}
    print(f'{"plan":<8} {"convention":<10} {"knives":>10} {"collapsed segs":>15} '
          f'{"yield %":>9} {"subscore@160k":>14} {"total@160k":>11}')
    out = {}
    for label, path in paths.items():
        for snap in (False, True):
            r = price(label, path, context, snap)
            out[(label, snap)] = r
            print(f'{label:<8} {"snap" if snap else "floor":<10} {r["knives"]:>10,} '
                  f'{r["collapsed"]:>15,} {r["yield_pct"]:>9.4f} '
                  f'{min(100.0, 100 * 160000 / r["knives"]):>14.4f} {r["score"]:>11.4f}')
    print()
    for snap in (False, True):
        a, b = out[('OURS', snap)], out[('THEIRS', snap)]
        print(f'  {"snap" if snap else "floor":<6} knives: ours {a["knives"]:,} vs theirs '
              f'{b["knives"]:,} -> theirs {(b["knives"] / a["knives"] - 1):+.2%}; '
              f'total: ours {a["score"]:.4f} vs theirs {b["score"]:.4f} '
              f'(theirs {b["score"] - a["score"]:+.4f})')
    print()
    print('  internal ledger for ours (runs/semi_nolimit_v1/progress.json) says 197,569 / 92.09%')
    prog = list(ROOT.glob('runs/*/progress.json'))
    for p in prog:
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
            print(f'    {p.parent.name}: {json.dumps(d, ensure_ascii=False)[:300]}')
        except Exception as exc:                       # noqa: BLE001
            print(f'    {p}: unreadable ({exc})')


if __name__ == '__main__':
    main()
