"""Drive the re-cutting passes along a chosen trajectory, each stage to its own fixed point.

The passes in `shift_cuts.py` do not commute.  Which flag goes first decides which
fixed point the search lands on -- on the semi-final plan the six orders of
`--requant-cuts/--recount-rounds/--split-schemes` spread over 0.017 points -- and even
the winning order, applied a second time to its own output, still recovers a canonical
~4.6 t of declared mass the first pass missed (a third pass moves nothing).  This tool
drives that on purpose:

    trajectory.py INPUT --order QSR --passes 2 --output OUT

expands `--order` into cumulative prefix stages ([M,X,o1], [M,X,o1,o2], ... with
M = `--min-rounds`, X = `--mixed-rounds` and Q/S/R the three search flags), iterates
every stage until its own stats report no further drop, then runs the re-boxing cascade
(`repack_batches` -> shift with every flag -> `share_orders` -> shift again) and repeats
the whole thing `--passes` times.  The current best plan is QSR twice: 92.1554 -> 92.1572.

A stage that cannot improve its input exits "keep the input" and is skipped, not fatal.
The final plan is written to `--output`, strict-checked and scored, and the run refuses
to end below the score of its input -- a trajectory is a search, not a licence to lose.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from platform_check import check, read_plan          # noqa: E402
from platform_score import evaluate                  # noqa: E402

SHIFT = ROOT / 'tools/analysis/shift_cuts.py'
REPACK = ROOT / 'tools/analysis/repack_batches.py'
SHARE = ROOT / 'tools/analysis/share_orders.py'

FLAG = {'Q': '--requant-cuts', 'S': '--split-schemes', 'R': '--recount-rounds'}
LEAD_FLAGS = ['--min-rounds', '--mixed-rounds']
ALL_FLAGS = [*LEAD_FLAGS, '--requant-cuts', '--recount-rounds', '--split-schemes']
MAX_REPS = 6                    # a stage that improves every rep for longer than this
                                # is not converging; stop and let the caller look


def score_of(plan, data: Path, round_name: str):
    """Strict-check and score a plan the same way every other tool in this tree does."""
    report = check(plan, data, round=round_name)
    if not report['passed']:
        raise SystemExit(f'trajectory plan fails the strict checker: {report["error_counts"]}')
    result = evaluate(plan, data, round_name=round_name)
    return (result['knives'], result['coverage_percent_rounded'], result['score_capped'])


def run_stage(tool: Path, flags, src: Path, out: Path, stats: Path, data: str, round_name: str,
              max_reps: int, label: str, records: list):
    """One stage, re-run while it still buys something.

    A stage ends on a keep-input exit, on its own reported `cost_delta` of 0.0, or --
    for `repack_batches`/`share_orders`, which report no delta -- on a score that
    stopped rising.  `share_orders` writes its output *before* refusing it, so the
    exit code, not the file, is what says a stage was kept.
    """
    started, reps, total_delta, prev_score = time.time(), 0, 0.0, None
    while reps < max_reps:
        reps += 1
        proc = subprocess.run(
            [sys.executable, '-X', 'utf8', str(tool), str(src), '--output', str(out),
             '--stats', str(stats), '--data', data, '--round', round_name, *flags],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        if proc.returncode != 0 or not out.is_file():
            note = (proc.stdout or proc.stderr).strip().splitlines()
            print(f'{label} rep {reps} {tool.stem:15s} kept its input'
                  f'{": " + note[-1] if note else ""}', flush=True)
            outcome = 'kept its input'
            break
        delta = json.loads(stats.read_text(encoding='utf-8')).get('cost_delta_kg_equivalent') \
            if stats.is_file() else None
        knives, coverage, score = score_of(read_plan(out), Path(data), round_name)
        print(f'{label} rep {reps} {tool.stem:15s} {knives} {coverage} {score:.4f}'
              f'  delta {delta}', flush=True)
        total_delta += delta or 0.0
        src = out
        if delta == 0.0:        # wrote a plan of equal cost: its own fixed point
            outcome = 'fixed point'
            break
        if delta is None and prev_score is not None and score <= prev_score:
            outcome = 'no further gain'
            break
        prev_score = score
        outcome = f'stopped at {max_reps} reps'
    records.append({'label': label, 'tool': tool.stem, 'flags': list(flags), 'reps': reps,
                    'delta_kg_equivalent': total_delta, 'outcome': outcome,
                    'seconds': round(time.time() - started, 1)})
    return src


def stages_of(order: str, cascade: bool, lead: bool):
    """(tool, flags) pairs: cumulative prefixes of the order, then the re-boxing cascade."""
    stages = []
    if lead:
        stages.append((SHIFT, LEAD_FLAGS))
    prefix = list(LEAD_FLAGS)
    for letter in order:
        prefix = [*prefix, FLAG[letter]]
        stages.append((SHIFT, list(prefix)))
    if cascade:
        stages += [(REPACK, []), (SHIFT, ALL_FLAGS), (SHARE, []), (SHIFT, ALL_FLAGS)]
    return stages


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('input', type=Path, help='plan JSON or submission ZIP to search from')
    ap.add_argument('--order', required=True,
                    help='letters from Q (requant), S (split), R (recount), e.g. QSR')
    ap.add_argument('--passes', type=int, default=1, help='times to run the whole trajectory')
    ap.add_argument('--lead', action='store_true', help='start with a min/mixed-only stage')
    ap.add_argument('--no-cascade', action='store_true', help='skip repack/share re-boxing')
    ap.add_argument('--max-reps', type=int, default=MAX_REPS)
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--round', default='semi')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--stats', type=Path)
    args = ap.parse_args()

    order = args.order.strip().upper()
    if not order or set(order) - set(FLAG) or len(set(order)) != len(order):
        raise SystemExit('--order must be a non-repeating string over Q, S, R (e.g. QSR)')
    if args.passes < 1:
        raise SystemExit('--passes must be at least 1')

    started = time.time()
    before = score_of(read_plan(args.input), Path(args.data), args.round)
    print(f'start {args.input.name}: {before[0]} {before[1]} {before[2]:.4f}', flush=True)

    records: list = []
    with tempfile.TemporaryDirectory(prefix='trajectory_') as work:
        work = Path(work)
        src, stage_no = args.input, 0
        for pass_no in range(1, args.passes + 1):
            for tool, flags in stages_of(order, not args.no_cascade, args.lead):
                stage_no += 1
                src = run_stage(tool, flags, src, work / f'stage{stage_no}.json',
                                work / f'stage{stage_no}.stats.json', args.data, args.round,
                                args.max_reps,
                                f'{order} pass {pass_no} stage {stage_no}', records)
        shutil.copyfile(src, args.output)

    knives, coverage, score = score_of(read_plan(args.output), Path(args.data), args.round)
    print(f'final {knives} {coverage} {score:.4f} in {time.time() - started:.0f}s', flush=True)
    if score < before[2]:
        args.output.unlink()
        raise SystemExit(f'{args.order} ended below its input ({score:.4f} < {before[2]:.4f}); '
                         'removed the output -- this is a bug, not a result')
    if args.stats:
        args.stats.write_text(json.dumps({
            'input': {'path': str(args.input), 'knives': before[0], 'score': before[2]},
            'order': order, 'passes': args.passes, 'lead': args.lead,
            'cascade': not args.no_cascade, 'stages': records,
            'final': {'path': str(args.output), 'knives': knives, 'coverage': coverage,
                      'score': score, 'seconds': round(time.time() - started, 1)},
        }, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
