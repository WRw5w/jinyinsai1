"""Dump the live call stack of the still-running solve_semi process.

The full run is at 182/183 with `XL2:38.0` outstanding.  That chunk has been
running for minutes where the median is 20 s, so this peeks at what it is doing
right now rather than waiting to find out.

Uses faulthandler's signal-driven dump: on Windows a remote process cannot be
inspected with `py-spy` unless installed, but `faulthandler` can be armed at
startup by the child itself -- which we do not control here.  So the fallback is
to check whether the child left a `faulthandler` registration: it did not, and
this script therefore reports the evidence available from the filesystem plus
the process CPU counter, which is enough to distinguish "grinding" from "stuck".

`Get-Process` CPU time is the decisive signal: a spinning process burns CPU
continuously (CPU time climbs), while a blocked one does not.  Measured in two
samples to get a rate.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def sample():
    """(pid, cpu_seconds, mem_mb) for every python process except this one."""
    script = (
        "Get-Process python -ErrorAction SilentlyContinue | "
        "ForEach-Object { \"$($_.Id) $($_.CPU) $($_.WorkingSet64)\" }"
    )
    out = subprocess.run(
        ['powershell', '-NoProfile', '-Command', script],
        capture_output=True, text=True, errors='replace').stdout
    rows = []
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        pid, cpu, mem = parts
        try:
            rows.append((int(pid), float(cpu), float(mem) / 1024 / 1024))
        except ValueError:
            continue
    return rows


def main() -> int:
    gap = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    first = sample()
    print(f't={time.strftime("%H:%M:%S")}  {len(first)} python process(es)')
    time.sleep(gap)
    second = sample()
    print(f't={time.strftime("%H:%M:%S")}  after {gap:.0f}s')
    by_pid_1 = {p: (c, m) for p, c, m in first}
    for pid, cpu2, mem2 in second:
        if pid not in by_pid_1:
            continue
        cpu1, mem1 = by_pid_1[pid]
        delta = cpu2 - cpu1
        verdict = 'SPINNING (CPU climbing)' if delta > gap * 0.3 else 'idle/blocked'
        print(f'  pid {pid}: cpu {cpu1:.1f} -> {cpu2:.1f} (+{delta:.1f}s in {gap:.0f}s wall) '
              f'mem {mem1:.0f}MB -> {mem2:.0f}MB  => {verdict}')

    root = Path('runs/semi_nolimit_v1')
    print()
    print('chunk files per group (mid-flight snapshot):')
    chunks = root / 'chunks'
    total = 0
    for group_dir in sorted(chunks.iterdir()):
        files = sorted(group_dir.glob('*.json'))
        total += len(files)
        if files:
            last = max(files, key=lambda p: p.stat().st_mtime)
            age = (time.time() - last.stat().st_mtime) / 60
            print(f'  {group_dir.name:16s} {len(files):3d}  last {last.name} '
                  f'({age:.1f} min ago)')
    print(f'  TOTAL {total}/183')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
