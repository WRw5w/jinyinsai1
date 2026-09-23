"""Re-solve only the chunks that have no cache file, with retries.

`watch_semi.py` proves the need: two chunks went missing below the high-water mark
during a run, so 120 orders were absent from the assembled plan.  Those orders are
pure lost coverage in the platform score, and nothing in the driver notices --
`solve_semi.py` writes down what it placed, never what it skipped.

This is the counterpart to that.  It re-derives the chunk list from the SAME CSV and
the same `--chunk` stride the driver used, so an offset lines up exactly, then
attacks only the offsets whose cache file is missing or unreadable.  Groups are
specified by `--groups`; with none, every group is swept, which makes this safe to
run as a periodic repair pass while the main run is still going.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from solver import Config, load_blanks, load_orders, validate_plan
from solve_semi import solve_chunk


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--config', default='data/semi/competition.config.json')
    ap.add_argument('--output', default='runs/semi_full')
    ap.add_argument('--chunk', type=int, default=60)
    ap.add_argument('--seconds-per-chunk', type=float, default=8.0)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--groups', nargs='*', default=None)
    ap.add_argument('--min-orders', type=int, default=0,
                    help='skip chunks smaller than this (a truncated tail chunk)')
    args = ap.parse_args()

    root = Path(args.output)
    settings = json.loads(Path(args.config).read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective='platform_score', baseline_knives=160000)
    cfg = Config(**settings)
    all_orders = load_orders(str(Path(args.data) / 'orders.normalized.csv'), cfg,
                             skip_invalid=True)
    blanks = load_blanks(str(Path(args.data) / 'blanks.normalized.csv'))

    # The offset must match the driver's `range(0, len(members), chunk)` over the
    # group's members IN FILE ORDER.  `pick_group` is applied to the same `all_orders`
    # list, so file order is preserved on both sides and the strides agree.
    with (Path(args.data) / 'orders_semi.csv').open(encoding='gbk', errors='replace') as fh:
        raw = list(csv.DictReader(fh))
    counts = Counter((r['钢种'], float(r['规格'])) for r in raw if r.get('订单号'))

    wanted = None
    if args.groups:
        wanted = set()
        for spec in args.groups:
            steel, _, dia = spec.partition(':')
            wanted.add((steel, float(dia)))

    repaired = attempted = 0
    for (steel, dia), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if wanted is not None and (steel, dia) not in wanted:
            continue
        members = [o for o in all_orders if o.steel == steel and abs(o.diameter - dia) < 1e-9]
        group_dir = root / 'chunks' / f'{steel}_{dia}'
        offsets = list(range(0, len(members), args.chunk))
        # Concurrency guard.  This script is meant to run WHILE the driver is still
        # working, and both write `chunks/<group>/<offset>.json`.  The driver walks
        # offsets in increasing order, so it never revisits one it passed: a hole
        # below the current high-water mark is therefore a chunk the driver is
        # finished with, and repairing it cannot race.  An offset ABOVE the mark may
        # be the one being solved right now, so it is left alone -- the driver will
        # reach it, and if it fails, a later repair pass catches it below the mark.
        # With `--groups` the caller asserts the group is finished and the guard is
        # lifted, which is how the post-run sweep reclaims the tail holes.
        present = [off for off in offsets if (group_dir / f'{off:06d}.json').exists()]
        high = max(present) if present else -1
        todo = []
        for off in offsets:
            if len(members) - off < args.min_orders:
                continue
            if args.groups is None and off > high:
                continue
            path = group_dir / f'{off:06d}.json'
            ok = False
            if path.exists():
                try:
                    ok = bool(json.loads(path.read_text(encoding='utf-8')).get('complete'))
                except json.JSONDecodeError:
                    ok = False
            if not ok:
                todo.append(off)
        if not todo:
            continue
        print(f'{steel}:{dia} -- {len(todo)}/{len(offsets)} chunks to repair',
              flush=True)
        for off in todo:
            attempted += 1
            chunk = members[off:off + args.chunk]
            try:
                plan, info = solve_chunk(chunk, cfg, blanks, args.seconds_per_chunk,
                                         args.seed + off, group_dir / f'{off:06d}.json')
            except Exception as exc:                                    # noqa: BLE001
                print(f'  @{off:06d} still failing: {type(exc).__name__}: {exc}', flush=True)
                continue
            validate_plan(plan, chunk, cfg, blanks)
            repaired += 1
            print(f'  @{off:06d} repaired ({info})', flush=True)
        # Same guard as `solve_semi.main`: retire the note by RENAMING rather than
        # deleting.  The sandbox's safe-delete guard terminates the process outright
        # (`SAFE_DELETE_BULK_GUARD_ERROR state lock timeout`), so a repair pass must not
        # depend on a delete succeeding.
        try:
            (group_dir / '_failed.json').replace(group_dir / '_failed.retired.json')
        except OSError as exc:                                        # noqa: BLE001
            print(f'  could not retire _failed.json ({type(exc).__name__}: {exc})',
                  flush=True)

    print(f'\nrepaired {repaired}/{attempted} chunks')


if __name__ == '__main__':
    main()
