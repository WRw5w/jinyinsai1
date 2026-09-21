"""Solve the whole semi-final drop, one (steel, diameter) group at a time.

Why this exists rather than a bigger `--limit` on `smoke_semi.py`: the drop has
9,999 valid orders across 36 groups, and the two largest hold 3,532 and 2,205.
`solver.search_10s` seeds one *bucket* at a time, and a bucket is a whole group,
so a group of 3,532 orders would enter `_seed_group` in one call and never
return -- the annealing phase the budget is meant for would never start.

So each group is chunked.  A chunk is solved as an independent sub-problem and
the chunks are concatenated into the group's plan.  Concatenation is sound
because the platform treats each scheme independently: every constraint (round
count per scheme, continuity per order within its scheme, blank accounting) is
scoped to one scheme, and no scheme spans two chunks.  Coverage is the one
metric that is NOT additive -- it needs two different orders in the same ROUND,
which `_pack_group` already arranges inside a chunk, and this driver measures
the assembled plan with the real `platform_score.evaluate` rather than summing
per-chunk numbers.

Checkpointing: each chunk writes `runs/<output>/chunks/<group>/<index>.json` and
the driver skips any chunk whose file already exists.  A run killed by the
sandbox therefore resumes instead of restarting, which matters because a full
pass is measured in hours, not minutes.

Usage:
    python -X utf8 solve_semi.py --seconds-per-chunk 60 --chunk 60
    python -X utf8 solve_semi.py --groups "NP01:43" "C60:26.5" --chunk 40
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

from platform_check import check
from platform_score import evaluate
from solver import Config, load_blanks, load_orders, search_10s, validate_plan


def pick_group(orders, spec):
    steel, _, dia = spec.partition(':')
    dia = float(dia)
    return [o for o in orders if o.steel == steel and abs(o.diameter - dia) < 1e-9]


def group_keys(orders):
    seen = []
    for o in orders:
        key = (o.steel, o.diameter)
        if key not in seen:
            seen.append(key)
    return seen


def solve_chunk(chunk, cfg, blanks, seconds, seed, cache_path):
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding='utf-8'))
            if data.get('complete'):
                return data['plan'], 'cached'
        except (json.JSONDecodeError, KeyError):
            pass
    started = time.perf_counter()
    plan = search_10s(chunk, cfg, seconds=seconds, seed=seed, blanks=blanks)
    elapsed = time.perf_counter() - started
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(dict(complete=True, elapsed=round(elapsed, 3),
                                          plan=plan), ensure_ascii=False,
                                     separators=(',', ':')) + '\n', encoding='utf-8')
    return plan, round(elapsed, 3)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--config', default='data/semi/competition.config.json')
    ap.add_argument('--output', default='runs/semi_full')
    ap.add_argument('--chunk', type=int, default=60,
                    help='orders per solved sub-problem')
    ap.add_argument('--seconds-per-chunk', type=float, default=20.0,
                    help='ANNEALING budget per chunk; seeding is not counted '
                         'against it because it must run to completion')
    ap.add_argument('--budget', type=float, default=3600.0,
                    help='wall-clock budget for the whole run, seconds')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--groups', nargs='*', default=None,
                    help='restrict to these "steel:diameter" specs')
    ap.add_argument('--skip-physical', action='store_true',
                    help='skip the per-chunk physical check (much faster)')
    args = ap.parse_args()

    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    settings = json.loads(Path(args.config).read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective='platform_score', baseline_knives=90000)
    cfg = Config(**settings)
    all_orders = load_orders(str(Path(args.data) / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(Path(args.data) / 'blanks.normalized.csv'))
    (root / 'config.json').write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n',
                                      encoding='utf-8')

    keys = group_keys(all_orders)
    if args.groups:
        wanted = set()
        for spec in args.groups:
            steel, _, dia = spec.partition(':')
            wanted.add((steel, float(dia)))
        keys = [k for k in keys if k in wanted]

    started = time.perf_counter()
    deadline = started + args.budget
    assembled = []
    per_group = []
    for key in keys:
        if time.perf_counter() >= deadline:
            print(f'[budget] stopping before {key[0]}:{key[1]}', flush=True)
            break
        members = pick_group(all_orders, f'{key[0]}:{key[1]}')
        group_dir = root / 'chunks' / f'{key[0]}_{key[1]}'
        group_plan = []
        failures = 0
        for offset in range(0, len(members), args.chunk):
            if time.perf_counter() >= deadline:
                break
            chunk = members[offset:offset + args.chunk]
            cache_path = group_dir / f'{offset:06d}.json'
            try:
                plan, info = solve_chunk(chunk, cfg, blanks, args.seconds_per_chunk,
                                         args.seed + offset, cache_path)
            except Exception as exc:                                  # noqa: BLE001
                # A chunk that fails must not lose the chunks around it, but it also
                # must not be silently dropped: unplaced orders are exactly the
                # coverage the platform scores.  Record it and keep going.
                failures += 1
                print(f'  [{key[0]}:{key[1]}] chunk @{offset} FAILED: '
                      f'{type(exc).__name__}: {exc}', flush=True)
                traceback.print_exc()
                continue
            group_plan.extend(plan)
        per_group.append(dict(group=f'{key[0]}:{key[1]}', orders=len(members),
                              rounds=sum(len(b['length_scheme']) for b in group_plan),
                              plans=len(group_plan), failures=failures))
        assembled.extend(group_plan)
        print(f'[{key[0]}:{key[1]}] {len(members)} orders -> {len(group_plan)} schemes, '
              f'{failures} failed chunks, {time.perf_counter() - started:.0f}s elapsed', flush=True)
        (root / 'result.partial.json').write_text(
            json.dumps(assembled, ensure_ascii=False, separators=(',', ':')) + '\n',
            encoding='utf-8')
        (root / 'progress.json').write_text(
            json.dumps(dict(groups=per_group, orders=len(assembled),
                            elapsed=round(time.perf_counter() - started, 1)),
                       ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    (root / 'result.json').write_text(
        json.dumps(assembled, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')

    # The assembled plan is scored against the WHOLE drop, not the chunks, so a
    # group left unsolved shows up as missing coverage rather than disappearing.
    summary = dict(groups=per_group)
    placed = {oid for batch in assembled for oid in batch['orders']}
    # `check` reads the round's raw CSVs (`orders_semi.csv`, `blank_used_finals.csv`)
    # from the directory it is handed, so it needs `--data` itself.  It also counts
    # every order in those files as required, so a run that solved a subset would
    # report the remainder as `order_coverage` / `short_delivery`.  Materialise a
    # scoped directory holding only the rows this run placed -- the same device
    # `smoke_semi.py` uses -- so the physical verdict describes the plan rather than
    # the scope.  Blanks are copied verbatim: they are a five-row catalogue indexed
    # by blank_type and must stay complete.
    if not args.skip_physical:
        scope = root / 'scoped_data'
        scope.mkdir(parents=True, exist_ok=True)
        src = Path(args.data) / 'orders_semi.csv'
        kept = []
        with src.open(encoding='gbk', errors='replace') as fh:
            kept.append(fh.readline())
            for line in fh:
                if line.split(',', 1)[0].strip() in placed:
                    kept.append(line)
        (scope / 'orders_semi.csv').write_text(''.join(kept), encoding='gbk')
        for name in ('blank_used_finals.csv', 'constraints.txt'):
            source = Path(args.data) / name
            if source.exists():
                (scope / name).write_bytes(source.read_bytes())
        physical = check(assembled, scope, round='semi')
        summary['physical_passed'] = physical['passed']
        summary['physical_errors'] = physical['error_counts']
    # `validate_plan` demands a scheme for every order it is given (clause 12), so it
    # is handed only the orders this run actually placed.  Passing `all_orders` would
    # raise "Missing orders" for every unsolved group, which is a statement about the
    # run's scope rather than a defect in the plan.  Coverage is measured by
    # `platform_score.evaluate` against the full drop and reported separately below.
    scope_orders = [o for o in all_orders if o.oid in placed]
    metrics = validate_plan(assembled, scope_orders, cfg, blanks)
    summary.update(plans=len(assembled), orders_placed=len(placed),
                   orders_total=len(all_orders), rounds=metrics['rounds'], knives=metrics['knives'],
                   yield_rate=round(metrics['yield_rate'], 6),
                   coverage=round(metrics['coverage'], 6))
    try:
        scoring = evaluate(assembled, Path(args.data), round_name='semi', baseline_knives=90000)
        summary.update(semi_coverage=round(scoring['coverage'], 6),
                       semi_violation_count=scoring['violation_count'],
                       semi_score_capped=round(scoring['score_capped'], 4))
    except Exception as exc:                                          # noqa: BLE001
        summary['score_error'] = f'{type(exc).__name__}: {exc}'


    (root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n',
                                       encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
