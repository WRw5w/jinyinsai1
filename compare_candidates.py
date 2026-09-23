"""Head-to-head scoring of the two semi-final candidates on identical terms.

Both plans are scored with this repository's `platform_check` and
`platform_score`, so the comparison does not depend on either side's own
arithmetic.  The knife baseline is swept because the two sides disagree about it
-- one carries the preliminary 90000 forward, the other uses 160000 -- and the
whole conclusion turns on which is right, so every candidate number is reported
rather than a single pick.

    python compare_candidates.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import platform_check as pc   # noqa: E402
import platform_score as ps   # noqa: E402

DATA = Path('data/semi')
CANDIDATES = [
    ('ours  (solve_semi, chunks)', Path('runs/semi_nolimit_v1/result.json')),
    ('theirs(build_semi_plan)',    Path('runs/theirs_main/复赛结果_棒材优化.json')),
]
BASELINES = [90000.0, 160000.0, 180000.0]


def main() -> int:
    rows = []
    for label, path in CANDIDATES:
        if not path.exists():
            print(f'MISSING {label}: {path}')
            continue
        raw = path.read_bytes()
        plan = json.loads(raw)
        check = pc.check(plan, data=DATA, weight_mode='strict', round='semi')
        entry = dict(label=label, path=str(path), sha256=hashlib.sha256(raw).hexdigest()[:16],
                     schemes=len(plan), passed=check.get('passed'),
                     errors=check.get('error_counts'), scores={})
        for baseline in BASELINES:
            rules = ps.Rules.semi(baseline_knives=baseline)
            rep = ps.evaluate(plan, DATA, rules=rules, round_name='semi')
            entry['scores'][baseline] = rep
            if baseline == BASELINES[0]:
                entry.update(knives=rep['knives'], yield_rate=rep['yield_rate'],
                             coverage=rep['coverage'], violations=rep.get('violation_count'),
                             rounds=rep.get('rounds'))
        rows.append(entry)

    for e in rows:
        print('=' * 74)
        print(f"{e['label']}  [{e['sha256']}]")
        print(f"  file            : {e['path']}")
        print(f"  schemes         : {e['schemes']:,}")
        print(f"  rounds          : {e['rounds']:,}")
        print(f"  physical check  : passed={e['passed']} errors={e['errors']}")
        print(f"  knives          : {e['knives']:,}")
        print(f"  yield_rate      : {e['yield_rate']:.6%}")
        print(f"  coverage        : {e['coverage']:.6%}")
        print(f"  violations      : {e['violations']}")
        print('  score vs baseline:')
        for baseline, rep in e['scores'].items():
            knife_sub = min(100.0, 40.0 * baseline / rep['knives'])
            print(f"    B={int(baseline):>7,}: score_capped={rep['score_capped']:.6f}  "
                  f"score_uncapped={rep['score_uncapped']:.6f}  (knife_sub={knife_sub:.4f})")

    if len(rows) == 2:
        print('=' * 74)
        print('DELTAS  (ours - theirs)')
        a, b = rows
        print(f"  knives     : {a['knives'] - b['knives']:+,}")
        print(f"  yield      : {(a['yield_rate'] - b['yield_rate']) * 100:+.4f} pp")
        print(f"  coverage   : {(a['coverage'] - b['coverage']) * 100:+.4f} pp")
        for baseline in BASELINES:
            d = a['scores'][baseline]['score_capped'] - b['scores'][baseline]['score_capped']
            print(f"  score B={int(baseline):>7,}: {d:+.6f}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
