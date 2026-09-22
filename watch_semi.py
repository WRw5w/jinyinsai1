"""Report how much of the semi-final drop is actually solved, and where the holes are.

Why a separate watcher rather than reading `progress.json`: the driver only writes
it at a GROUP boundary, and the two big groups (`NP01:43` at 59 chunks, `C60:26.5`
at 37) are hours long -- so `progress.json` says nothing for most of a run.  The
per-chunk cache files under `chunks/<group>/<offset>.json` are the real ledger.

The gap column is the point of the whole script.  A chunk with no cache file is
either (a) not reached yet or (b) was reached and failed -- and those two demand
opposite responses, so the watcher decides which by looking at how far the run has
advanced through the group.  A hole BELOW the high-water mark that has no
`_failed.json` entry is the dangerous case: a transient failure the old driver
swallowed before it grew retries.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data' / 'semi'
RUN = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'runs' / 'semi_full'
CHUNK = 60


def main():
    with (DATA / 'orders_semi.csv').open(encoding='gbk', errors='replace') as fh:
        rows = list(csv.DictReader(fh))
    counts = Counter((r['钢种'], float(r['规格'])) for r in rows if r.get('订单号'))

    total_chunks = sum((n + CHUNK - 1) // CHUNK for n in counts.values())
    done_chunks = 0
    solved_orders = 0
    failed_orders = 0
    holes = []

    for (steel, dia), n in counts.items():
        group_dir = RUN / 'chunks' / f'{steel}_{dia}'
        offsets = list(range(0, n, CHUNK))
        present = set()
        for off in offsets:
            path = group_dir / f'{off:06d}.json'
            if not path.exists():
                continue
            present.add(off)
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except json.JSONDecodeError:
                continue
            if data.get('complete'):
                done_chunks += 1
                solved_orders += min(CHUNK, n - off)
        # A hole below the high-water mark was reached and did not produce a cache
        # file: that is a lost chunk, not merely unstarted work.
        if present:
            high = max(present)
            for off in offsets:
                if off <= high and off not in present:
                    holes.append((steel, dia, off, min(CHUNK, n - off)))
        failure_note = group_dir / '_failed.json'
        if failure_note.exists():
            try:
                note = json.loads(failure_note.read_text(encoding='utf-8'))
                failed_orders += len(note.get('orders') or [])
            except json.JSONDecodeError:
                pass

    print(f'chunks  {done_chunks}/{total_chunks}'
          f'  ({100.0 * done_chunks / total_chunks:.1f} %)')
    print(f'orders  {solved_orders}/{len(rows)}'
          f'  ({100.0 * solved_orders / len(rows):.1f} %)')
    if failed_orders:
        print(f'failed  {failed_orders} orders recorded in _failed.json')
    if holes:
        print(f'\nholes below the high-water mark ({len(holes)} chunks, '
              f'{sum(h[3] for h in holes)} orders):')
        for steel, dia, off, size in sorted(holes, key=lambda h: -h[3])[:25]:
            print(f'  {steel}:{dia:<6} @{off:06d}  {size:>3} orders')
    else:
        print('\nno holes below the high-water mark')

    started = RUN / 'summary.json'
    if started.exists():
        print(f'\nsummary.json exists -- run finished')
        print(started.read_text(encoding='utf-8')[:1200])


if __name__ == '__main__':
    main()
