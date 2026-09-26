"""Persistent local process watcher: native blocking wait, no model polling.

The current session does not expose the task_watcher MCP tool. This local runner
owns the child process, captures its actual exit code, and writes a terminal
receipt. It does not promise to wake the Codex conversation or send messages.
"""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from competition_solver import atomic_json


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--timeout', type=float, default=2100)
    ap.add_argument('--expected-metrics')
    ap.add_argument('command', nargs=argparse.REMAINDER)
    args = ap.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        ap.error('worker command required after --')
    root = Path(args.run_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    hashes = {}
    for source in ['src/solver.py', 'src/competition_solver.py', 'src/task_watcher.py',
                   'src/solve_semi.py', 'src/platform_check.py', 'src/platform_score.py',
                   'data/semi/orders.normalized.csv', 'data/semi/blanks.normalized.csv',
                   'data/semi/competition.config.json', 'data/semi/data_audit.json']:
        path = Path(source)
        if path.is_file():
            hashes[source] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = dict(state='starting', watcher_pid=os.getpid(), started_at=started.isoformat(),
                   command=command, cwd=str(Path.cwd()), python=sys.version, platform=platform.platform(),
                   source_sha256=hashes, monitoring='native process wait; no periodic polling',
                   timeout_seconds=args.timeout)
    atomic_json(root / 'task_status.json', receipt)
    code = 1
    try:
        with (root / 'stdout.log').open('w', encoding='utf-8') as stdout, (root / 'stderr.log').open('w', encoding='utf-8') as stderr:
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            child = subprocess.Popen(command, stdout=stdout, stderr=stderr, creationflags=flags)
            receipt.update(state='running', worker_pid=child.pid,
                           watchdog_deadline=(started + timedelta(seconds=args.timeout)).isoformat())
            atomic_json(root / 'task_status.json', receipt)
            try:
                code = child.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                code = 124
                receipt['error'] = 'Owned worker exceeded watchdog timeout and was stopped'
        if code == 0 and args.expected_metrics:
            metrics = json.loads(Path(args.expected_metrics).read_text(encoding='utf-8'))
            if metrics.get('state') != 'completed':
                raise RuntimeError('Worker exited successfully without completed result metrics')
            receipt['result_metrics'] = metrics
    except Exception as exc:
        code = 1
        receipt['error'] = f'{type(exc).__name__}: {exc}'
    receipt.update(state='completed' if code == 0 else 'failed', exit_code=code,
                   completed_at=datetime.now(timezone.utc).isoformat())
    atomic_json(root / 'task_status.json', receipt)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
