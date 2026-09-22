"""Identify what the long-running solve_semi chunk is actually doing.

The previous probe showed the solve process is NOT spinning (CPU flat, memory
flat 25 MB), so it is blocked rather than looping.  That rules out the
`_enumerate_round_shapes` class of bug that was just fixed and points at either
a genuine wait or -- more likely on this workload -- a very deep recursion /
large finite search that simply costs minutes.

Two cheap, non-invasive checks:

1. Thread states, via the process's own thread wait reasons.  A thread sitting
   in `Wait` on a synchronisation object means blocked; `Running` means CPU work
   happened between samples even if the average is low.
2. The chunk's expected cost.  `XL2:38.0` is 20 orders in ONE chunk, i.e. the
   whole group fits in a single 60-order batch -- the closing search sees the
   largest order set it ever will.  Every other single-chunk group here is
   <=46 orders, so this is the fattest single construction in the run.

`Get-Process` alone cannot see the Python stack, so this script reports what is
observable and prints the sampling command the operator can run in the task
manager if deeper inspection is needed.
"""
from __future__ import annotations

import csv
import collections
import math
import subprocess
import time
from pathlib import Path


def ps(cmd: str) -> str:
    return subprocess.run(['powershell', '-NoProfile', '-Command', cmd],
                          capture_output=True, text=True, errors='replace').stdout


def main() -> int:
    script = (
        "Get-Process python -ErrorAction SilentlyContinue | ForEach-Object { "
        "\"$($_.Id)|$($_.CPU)|$($_.WorkingSet64)|"
        "$(($_.Threads | ForEach-Object { $_.ThreadState }).Length)|"
        "$(($_.Threads | ForEach-Object { $_.WaitReason }) -join ',')\" }"
    )
    rows = [l for l in ps(script).strip().splitlines() if '|' in l]
    print('live python processes:')
    for row in rows:
        pid, cpu, mem, nthr, reasons = row.split('|', 4)
        mem_mb = float(mem) / 1024 / 1024
        print(f'  pid {pid}: cpu={cpu}s mem={mem_mb:.0f}MB threads={nthr}')
        print(f'      wait reasons: {reasons}')

    # How heavy is the outstanding group, really?
    orders = list(csv.DictReader(
        open('data/semi/orders.normalized.csv', encoding='utf-8-sig')))
    groups = collections.Counter((r['steel'], r['diameter']) for r in orders)
    print()
    print('single-chunk groups (the straight-through constructions):')
    for (steel, dia), count in sorted(groups.items(), key=lambda kv: -kv[1]):
        if math.ceil(count / 60) != 1:
            continue
        sizes = {float(r['length']) for r in orders
                 if r['steel'] == steel and r['diameter'] == dia}
        span = max(sizes) - min(sizes)
        print(f'  {steel}:{dia:6s} {count:3d} orders  distinct sizes={len(sizes):2d} '
              f'range={span:.3f}m')
    print()
    print(f'checked at {time.strftime("%H:%M:%S")}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
