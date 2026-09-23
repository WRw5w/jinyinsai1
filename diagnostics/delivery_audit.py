"""Delivery audit: does each semi-final plan actually meet every order's demand?

`platform_score.evaluate` prices a plan but does not check clause 8 of
constraints.txt ("allocated weight must not fall below the order weight"), so a
plan can look cheap simply by under-delivering.  This script applies that check
independently, using the same piece convention as the solver's `validate_plan`
(a row hands order `o` exactly `int(L // size) * parallel` pieces).

Both the capped mass (yield numerator) and the uncapped physical mass are
reported, because the semi-final credits only the capped part.
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


def find_plan(folder: Path) -> Path:
    cands = [p for p in folder.glob('*.json')
             if 'validation' not in p.name and 'score' not in p.name]
    return max(cands, key=lambda p: p.stat().st_size)


def audit(name: str, path: Path, context):
    plan = json.loads(path.read_text(encoding='utf-8'))
    pieces = defaultdict(int)          # oid -> pieces handed over (floor convention)
    mass = defaultdict(float)          # oid -> scoring mass handed over
    for batch in plan:
        for scheme, parallel in zip(batch['length_scheme'], batch['counts']):
            for oid, length in scheme.items():
                o = context.orders[oid]
                k = int(length // o.size_m)
                pieces[oid] += k * parallel
                mass[oid] += length * parallel * o.linear_weight

    demand_pieces = 0
    produced_pieces = 0
    demand_mass = 0.0
    produced_mass = 0.0
    short = []
    for oid, o in context.orders.items():
        need = max(1, math.ceil(o.required_kg / (o.size_m * o.physical_linear_weight)))
        got = pieces.get(oid, 0)
        demand_pieces += need
        produced_pieces += got
        demand_mass += need * o.size_m * o.physical_linear_weight
        produced_mass += mass.get(oid, 0.0)
        if got < need:
            short.append((oid, need, got, (got - need) / need, o.size_m, o.diameter_mm))
    short.sort(key=lambda t: t[3])
    print(f'=== {name} ({path.name}) ===')
    print(f'  orders in plan            {len(pieces):>10,} / {len(context.orders):,}')
    print(f'  demand   pieces           {demand_pieces:>10,}   mass {demand_mass / 1000:>14,.1f} t')
    print(f'  produced pieces           {produced_pieces:>10,}   '
          f'({produced_pieces / demand_pieces - 1:+.2%})   mass {produced_mass / 1000:>14,.1f} t '
          f'({produced_mass / demand_mass - 1:+.2%})')
    print(f'  orders short of demand    {len(short):>10,}')
    for row in short[:6]:
        print(f'      {row[0]}  need {row[1]:>6,} got {row[2]:>6,}  {row[3]:+.2%}  '
              f'size {row[4]:.2f} m dia {row[5]}')
    print()
    return dict(short=len(short), produced=produced_pieces, demand=demand_pieces)


def main():
    context = P.load_scoring_data(DATA, 'semi')
    a = audit('OURS (submission_semi_nolimit)', find_plan(ROOT / 'submission_semi_nolimit'), context)
    b = audit('THEIRS (machine 2, main)', find_plan(ROOT / 'runs' / 'theirs_main'), context)


if __name__ == '__main__':
    main()
