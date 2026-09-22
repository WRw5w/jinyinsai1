"""One-screen acceptance verdict for a semi-final plan.

Read-only.  Runs the two independent checkers over a plan file and prints the
four numbers the score is made of plus every violation class:

  * `platform_check.check`   -- physical/structural verdict on the raw CSVs
  * `platform_score.evaluate` -- the score model (40/30/20/10 + 5 per violation)

Scope.  `platform_check` counts every order in `orders_semi.csv` as required, so a
plan that solves only part of the drop would be reported as thousands of missing
orders -- a statement about the plan's scope, not a defect.  This materialises a
scoped data directory holding only the rows the plan placed (the same device
`solve_semi.py` and `smoke_semi.py` use), and reports coverage against the full
drop separately.

Usage:
    python -X utf8 diagnostics/acceptance_report.py runs/semi_v2/result.json
    python -X utf8 diagnostics/acceptance_report.py runs/semi_v2/result.json --data data/semi
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platform_check import check            # noqa: E402
from platform_score import evaluate         # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plan', type=Path, help='plan JSON, or a solve_semi chunk')
    ap.add_argument('--data', type=Path, default=ROOT / 'data' / 'semi')
    ap.add_argument('--baseline-knives', type=float, default=90000.0)
    args = ap.parse_args()

    raw = json.loads(args.plan.read_text(encoding='utf-8'))
    plan = raw['plan'] if isinstance(raw, dict) else raw
    placed = {oid for batch in plan for oid in batch['orders']}

    # ---- full-drop order count, for the coverage denominator --------------------
    src_orders = args.data / 'orders_semi.csv'
    total_orders = 0
    kept = []
    with src_orders.open(encoding='gbk', errors='replace') as fh:
        kept.append(fh.readline())
        for line in fh:
            if not line.strip():
                continue
            total_orders += 1
            if line.split(',', 1)[0].strip() in placed:
                kept.append(line)

    tmpdir = Path(tempfile.mkdtemp(prefix='accept_'))
    try:
        (tmpdir / 'orders_semi.csv').write_text(''.join(kept), encoding='gbk')
        for name in ('blank_used_finals.csv', 'constraints.txt'):
            source = args.data / name
            if source.exists():
                shutil.copyfile(source, tmpdir / name)

        physical = check(plan, tmpdir, round='semi')
        scoring = evaluate(plan, args.data, round_name='semi',
                           baseline_knives=args.baseline_knives)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    placed_count = sum(len(b['orders']) for b in plan)
    multi = sum(len(b['orders']) for b in plan if len(b['orders']) > 1)
    print(f'plan                : {args.plan}')
    print(f'schemes / orders    : {len(plan)} / {placed_count}'
          f'  (of {total_orders} in the drop)')
    print(f'orders sharing a round: {multi} ({multi / max(1, placed_count):.2%})')
    print(f'rounds              : {sum(len(b["length_scheme"]) for b in plan)}')
    print('--- platform_check ---')
    print(f'physical passed     : {physical["passed"]}')
    print(f'error counts        : {physical["error_counts"]}')
    print('--- platform_score ---')
    # Field names verified against platform_score.evaluate(): there is no top-level
    # `yield` key -- the realised ratio is `yield_rate` (fraction) / `yield_percent`
    # (0-100).  The score that is actually comparable to a platform total is
    # `score_capped_after_penalty`; `score_capped` excludes the -5-per-violation term.
    for key in ('knives', 'finished_weight', 'finished_weight_uncapped', 'overshoot_kg',
                'raw_weight', 'yield_rate', 'yield_percent_rounded', 'coverage',
                'coverage_percent_rounded', 'included_order_count',
                'combination_order_count', 'rounds', 'plans',
                'violation_count', 'penalty_points',
                'score_capped_after_penalty', 'score_uncapped_after_penalty'):
        if key in scoring:
            value = scoring[key]
            print(f'{key:<26}: {round(value, 6) if isinstance(value, float) else value}')
    subs = scoring.get('subscores')
    if subs:
        print(f'subscores (capped)        : '
              f'knife={subs["knives"]:.4f} yield={subs["yield_rate"]:.4f} '
              f'coverage={subs["coverage"]:.4f} time={subs["time"]:.4f}')
        print(f'weights                   : {scoring["rules"]["weights"]}')
    if scoring.get('violations'):
        print(f'violations by kind        : {scoring["violations"]}')
    if physical['error_counts']:
        print('--- first error per kind ---')
        for kind, rows in physical['errors'].items():
            print(f'{kind:<26}: {rows[0]}  (x{len(rows)})')


if __name__ == '__main__':
    main()
