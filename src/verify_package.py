"""Byte-level acceptance of a built submission ZIP.

The skill's step 5: the published JSON inside the archive is authoritative,
because `length_scheme` representation drives the knife count.  So this reopens
the actual ZIP bytes, re-runs the independent physical check and the platform
score on *those* bytes, and asserts they reproduce the numbers the build reported
-- plus that the sidecar JSON next to the ZIP is byte-identical to the archived
one.

    python verify_package.py submission_semi_nolimit/复赛结果_棒材优化.zip --round semi --data data/semi
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import platform_check as pc   # noqa: E402
import platform_score as ps   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('zip_file', type=Path)
    ap.add_argument('--data', type=Path, default=Path('data/semi'))
    ap.add_argument('--round', default='semi', choices=['prelim', 'semi'])
    ap.add_argument('--weight-mode', default='strict')
    ap.add_argument('--baseline-knives', type=float, default=160000.0)
    ap.add_argument('--expect-knives', type=int, default=None)
    ap.add_argument('--report', type=Path, default=None)
    args = ap.parse_args()

    zip_path = args.zip_file
    raw_zip = zip_path.read_bytes()
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        print(f'archive members : {names}')
        if len(names) != 1:
            print(f'FAIL: expected exactly one member, got {len(names)}')
            return 1
        if archive.testzip() is not None:
            print('FAIL: CRC error')
            return 1
        packed = archive.read(names[0])
    print(f'single JSON     : {names[0]}')
    print(f'zip sha256      : {hashlib.sha256(raw_zip).hexdigest()}')
    print(f'json sha256     : {hashlib.sha256(packed).hexdigest()}')

    sidecar = zip_path.with_suffix('.json')
    if sidecar.exists():
        ok = sidecar.read_bytes() == packed
        print(f'sidecar identical: {ok}')
        if not ok:
            print('FAIL: sidecar JSON differs from the archived bytes')
            return 1
    else:
        print(f'sidecar         : (absent at {sidecar})')

    plan = json.loads(packed)
    print(f'schemes         : {len(plan)}')

    check = pc.check(plan, data=args.data, weight_mode=args.weight_mode, round=args.round)
    passed = check.get('passed')
    errors = check.get('error_counts')
    print(f'physical check  : passed={passed} errors={errors}')
    if not passed:
        print('FAIL: physical check')
        return 1

    rules = ps.Rules.semi(baseline_knives=args.baseline_knives) if args.round == 'semi' \
        else ps.Rules.prelim(baseline_knives=args.baseline_knives)
    report = ps.evaluate(plan, args.data, rules=rules, round_name=args.round)
    for key in ('knives', 'yield_rate', 'coverage', 'violation_count',
                'score_capped', 'score_uncapped'):
        value = report.get(key)
        if isinstance(value, float):
            print(f'{key:16s}: {value:,.6f}')
        else:
            print(f'{key:16s}: {value}')

    if args.expect_knives is not None and report['knives'] != args.expect_knives:
        print(f"FAIL: knives {report['knives']} != expected {args.expect_knives}")
        return 1
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                               encoding='utf-8')
        print(f'report written  : {args.report}')

    violations = report.get('violation_count', 0)
    if violations:
        print(f'FAIL: {violations} rule violations')
        return 1
    print('\nOK: archive layout, CRC, sidecar, physical check and score all verified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
