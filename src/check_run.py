"""One-shot acceptance report for a `solve_semi.py` run directory.

Reads `result.partial.json` (written after every group, so it is valid at any
moment) and prints the numbers the semi-final score actually depends on:

    knives / knives-per-bar / baseline ratio   -> the 40-point term
    yield_rate (capped numerator)              -> the 30-point term
    coverage                                   -> the 20-point term
    physical floor, violations, tri-metric agreement

Kept separate from `platform_score.py` so a finished run can be judged without
re-solving anything.  Safe to run mid-flight: it only reads.

    python check_run.py runs/semi_nolimit_v1
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import platform_score as ps  # noqa: E402


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else 'runs/semi_nolimit_v1')
    partial = root / 'result.partial.json'
    if not partial.exists():
        print(f'no {partial} yet -- the run has not finished its first group')
        return 1
    plan = json.loads(partial.read_text(encoding='utf-8'))
    if not plan:
        print('result.partial.json is empty')
        return 1

    rules = ps.Rules.semi(baseline_knives=160000.0)
    # `round_name` selects which order file `load_scoring_data` reads
    # (`orders_<round_name>.csv`), so it must be passed explicitly -- the
    # default 'prelim' looks for `orders_prelim.csv` and dies on a semi run.
    report = ps.evaluate(plan, Path('data/semi'), rules=rules, round_name='semi')

    print(f'run dir     : {root}')
    print(f'schemes     : {len(plan)}')
    items = report if isinstance(report, dict) else vars(report)

    # The four weighted terms plus their capped totals.  `evaluate` returns a flat
    # dict of ~30 keys; these are the ones the score actually rides on.
    order = ('knives', 'knives_per_bar', 'knives_subscore',
             'yield_rate', 'yield_subscore', 'coverage', 'coverage_subscore',
             'time_subscore', 'violation_count',
             'score_capped_display', 'score_capped',
             'score_from_rounded_subscores_capped_display',
             'score_from_rounded_subscores_capped')
    seen = set()
    for key in order:
        if key in items:
            value = items[key]
            if isinstance(value, float):
                print(f'{key:44s}: {value:,.6f}')
            else:
                print(f'{key:44s}: {value}')
            seen.add(key)
    print('--- remaining scalar fields ---')
    for key in sorted(items):
        if key in seen:
            continue
        value = items[key]
        if value is None or isinstance(value, (int, float, str, bool)):
            if isinstance(value, float):
                print(f'{key:44s}: {value:,.6f}')
            else:
                print(f'{key:44s}: {value}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
