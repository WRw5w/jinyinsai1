"""Keep relaunching a chunked island until every chunk is solved.

A multi-hour `solve_semi.py` run cannot be launched once and awaited on this machine: a
sleep cycle terminates the process outright (2026-09-23, all seven islands died at once
with no `[budget]` line in their logs).  Relaunching is free because every chunk is
cached under `chunks/<group>/<offset>.json`, so the only thing missing was something to
do the relaunching.

Completion is judged from the CACHE, not from the driver's exit status, because those
are not the same event.  `solve_semi.main` also exits 0 when it hits its wall-clock
budget, and a sleep makes that happen on the first chunk after the wake -- so exit 0
means "stopped", and only a full set of cached chunks means "done".

    python supervise_island.py --output runs/semi_anchor_v1 --seconds-per-chunk 20
    python supervise_island.py --output runs/semi_refine_s1 --seconds-per-chunk 60 \
        --refine runs/semi_merged/result.json --attempts 30
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import solve_semi as SS  # noqa: E402
from solver import Config  # noqa: E402


def semi_cfg(config_path):
    settings = json.loads(Path(config_path).read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    cap_yield_numerator=True, knife_convention='platform_floor',
                    objective='platform_score', baseline_knives=160000)
    for key in ('offset_mode', 'refine'):
        settings.pop(key, None)
    return Config(**settings)


def expected_chunks(data, config_path, chunk, groups=None):
    """How many `<offset>.json` files a complete run should leave behind."""
    cfg = semi_cfg(config_path)
    orders = SS.load_orders(str(Path(data) / 'orders.normalized.csv'), cfg, skip_invalid=True)
    wanted = None
    if groups:
        wanted = set()
        for spec in groups:
            steel, _, dia = spec.partition(':')
            wanted.add((steel, float(dia)))
    total = 0
    for key in SS.group_keys(orders):
        if wanted is not None and key not in wanted:
            continue
        total += -(-len(SS.pick_group(orders, f'{key[0]}:{key[1]}')) // chunk)
    return total


def cached_chunks(output):
    """Complete chunk caches, ignoring `_failed*.json` and half-written files."""
    total = 0
    chunks_dir = Path(output) / 'chunks'
    if not chunks_dir.is_dir():
        return 0
    for path in chunks_dir.glob('*/[0-9]*.json'):
        try:
            if json.loads(path.read_text(encoding='utf-8')).get('complete'):
                total += 1
        except (json.JSONDecodeError, OSError):
            continue
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--output', required=True)
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--config', default='data/semi/competition.config.json')
    ap.add_argument('--chunk', type=int, default=60)
    ap.add_argument('--seconds-per-chunk', type=float, default=20.0)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--offset-mode', choices=('anchor', 'share', 'none'), default='anchor')
    ap.add_argument('--offset-anchor', default='runs/seed_fixed_all.json')
    ap.add_argument('--refine', default=None)
    ap.add_argument('--groups', nargs='*', default=None)
    # Each launch gets its own allowance; a sleep burns one, so this is a per-attempt
    # guard, not a total.  Deliberately smaller than the total work so a wake always
    # finds the deadline already expired and the attempt exits promptly instead of
    # re-running for hours inside a supervisor cycle.
    ap.add_argument('--budget', type=float, default=3600.0)
    ap.add_argument('--attempts', type=int, default=60)
    ap.add_argument('--pause', type=float, default=10.0, help='seconds between attempts')
    args = ap.parse_args()

    target = expected_chunks(args.data, args.config, args.chunk, args.groups)
    print(f'[supervisor] {args.output}: {target} chunks expected', flush=True)

    for attempt in range(1, args.attempts + 1):
        before = cached_chunks(args.output)
        if before >= target:
            print(f'[supervisor] complete: {before}/{target}', flush=True)
            return 0
        cmd = [sys.executable, '-u', '-X', 'utf8', str(ROOT / 'solve_semi.py'),
               '--output', args.output, '--data', args.data, '--config', args.config,
               '--chunk', str(args.chunk),
               '--seconds-per-chunk', str(args.seconds_per_chunk),
               '--budget', str(args.budget), '--seed', str(args.seed),
               '--offset-mode', args.offset_mode,
               '--offset-anchor', args.offset_anchor,
               '--skip-physical']
        if args.refine:
            cmd += ['--refine', args.refine]
        if args.groups:
            cmd += ['--groups', *args.groups]
        print(f'[supervisor] attempt {attempt}/{args.attempts} '
              f'({before}/{target} chunks cached)', flush=True)
        started = time.perf_counter()
        proc = subprocess.run(cmd, cwd=str(ROOT))
        after = cached_chunks(args.output)
        print(f'[supervisor] attempt {attempt} ended: rc={proc.returncode} '
              f'{before} -> {after}/{target} chunks in {time.perf_counter() - started:.0f}s',
              flush=True)
        if after >= target:
            print(f'[supervisor] complete: {after}/{target}', flush=True)
            return 0
        if after == before:
            # No progress at all: either every remaining chunk is genuinely failing, or
            # the machine is asleep and the launch cannot do work.  Back off so a sleeping
            # host is not hammered with relaunches.
            time.sleep(args.pause)
    print(f'[supervisor] gave up after {args.attempts} attempts '
          f'({cached_chunks(args.output)}/{target})', flush=True)
    return 1


if __name__ == '__main__':
    sys.exit(main())
