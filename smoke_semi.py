"""Smoke-solve one real semi-final group under the semi-final rule set.

The 复赛 drop has 9,999 valid orders in 36 (steel, diameter) groups, and the two
largest groups hold 3,532 and 2,205 orders -- far beyond anything the preliminary
round exercised.  This script solves a single group end to end so the pipeline can
be measured before committing to a full run, and so the semi-final constraint set
is exercised against real data rather than only tiny fixtures.

Usage:
    python -X utf8 smoke_semi.py --group "NP01:43" --limit 120 --seconds 20
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from platform_check import check
from platform_score import evaluate
from solver import Config, load_blanks, load_orders, search_10s, validate_plan


def pick_group(orders, group):
    steel, _, dia = group.partition(':')
    dia = float(dia)
    return [o for o in orders if o.steel == steel and abs(o.diameter - dia) < 1e-9]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--config', default='data/semi/competition.config.json')
    ap.add_argument('--group', default='NP01:43')
    ap.add_argument('--limit', type=int, default=120)
    ap.add_argument('--seconds', type=float, default=20.0)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--output', default='runs/semi_smoke')
    args = ap.parse_args()

    settings = json.loads(Path(args.config).read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective='platform_score', baseline_knives=90000)
    cfg = Config(**settings)
    orders = load_orders(str(Path(args.data) / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(Path(args.data) / 'blanks.normalized.csv'))
    group = pick_group(orders, args.group)
    print(f"group {args.group}: {len(group)} orders, solving first {min(args.limit, len(group))}")

    subset = group[:args.limit]
    started = time.perf_counter()
    plan = search_10s(subset, cfg, seconds=args.seconds, seed=args.seed, blanks=blanks)
    elapsed = time.perf_counter() - started

    metrics = validate_plan(plan, subset, cfg, blanks)
    # `check` and `evaluate` read the whole semi-final drop, so a partial plan
    # would always report the unsolved remainder as `order_coverage` /
    # `short_delivery` (exactly 9999 - len(subset)).  Materialise a scoped data
    # directory holding only the orders this run actually covers, so the smoke
    # report measures the plan rather than the harness.
    scope = Path(args.output) / 'scoped_data'
    scope.mkdir(parents=True, exist_ok=True)
    covered = {o.oid for o in subset}
    # Orders: keep only the rows this run solved.  Blanks: copy verbatim, they
    # are a 5-row catalogue indexed by blank_type and must stay complete.
    src = Path(args.data) / 'orders_semi.csv'
    kept = []
    with src.open(encoding='gbk', errors='replace') as fh:
        kept.append(fh.readline())
        for line in fh:
            if line.split(',', 1)[0].strip() in covered:
                kept.append(line)
    (scope / 'orders_semi.csv').write_text(''.join(kept), encoding='gbk')
    for name in ('blank_used_finals.csv', 'constraints.txt'):
        source = Path(args.data) / name
        if source.exists():
            (scope / name).write_bytes(source.read_bytes())
    physical = check(plan, scope, round='semi')
    scoring = evaluate(plan, scope, round_name='semi', baseline_knives=90000)

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'result.json').write_text(
        json.dumps(plan, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')

    summary = dict(
        group=args.group, orders=len(subset), seconds=args.seconds, elapsed_seconds=round(elapsed, 3),
        rounds=metrics['rounds'], plans=metrics['plans'], knives=metrics['knives'],
        yield_rate=round(metrics['yield_rate'], 6), coverage=round(metrics['coverage'], 6),
        semi_violations=scoring['violations'], semi_violation_count=scoring['violation_count'],
        semi_score_capped=round(scoring['score_capped'], 4),
        semi_score_after_penalty=round(scoring['score_capped_after_penalty'], 4),
        physical_passed=physical['passed'], physical_errors=physical['error_counts'],
        max_rounds_used=max(len(b['length_scheme']) for b in plan),
        max_round_weight_kg=physical['max_round_weight_kg'],
        min_physical_length_m=physical['min_physical_length_m'],
        max_physical_length_m=physical['max_physical_length_m'],
    )
    (out / 'smoke_report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n',
                                           encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
