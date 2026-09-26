"""Project command entry. Paths are resolved from the project root on every OS."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import sys
import unittest

ROOT = Path(__file__).resolve().parent
COMMANDS = {
    "solve": "src/solve_semi.py",
    "build": "src/build_submission.py",
    "check": "src/platform_check.py",
    "score": "src/platform_score.py",
    "verify": "src/verify_package.py",
    "prepare": "src/prepare_semi.py",
    "supervise": "src/supervise_island.py",
    "watch": "src/task_watcher.py",
    "progress": "src/watch_semi.py",
    "run-report": "src/check_run.py",
    "reshape": "src/reshape_plan.py",
    "repair": "src/repair_semi.py",
    "compare": "src/compare_candidates.py",
    "smoke": "src/smoke_semi.py",
    "merge": "tools/analysis/merge_chunks.py",
    "audit-clause6": "tools/analysis/clause6_rotation_falsified.py",
    "audit-packages": "tools/analysis/verify_clause6_readings.py",
    "resync": "tools/resync_reports.py",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=[*COMMANDS, "test"])
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    if args.command == "test":
        options = args.args or ["discover", "-s", str(ROOT / "tests")]
        result = unittest.main(module=None, argv=["unittest", *options], exit=False)
        return 0 if result.result.wasSuccessful() else 1
    script = ROOT / COMMANDS[args.command]
    sys.argv = [str(script), *args.args]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
