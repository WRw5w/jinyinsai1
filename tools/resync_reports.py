"""Refresh ZIP hashes and record legacy-model diagnostics without certification.

Current reports always remain unverified and submission_allowed=false.
Original validation_report_legacy_B.json evidence and ZIP bytes are never written.
Exit 1 means the refreshed report is not certified, even when legacy counts are 0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from platform_check import check as platform_check            # noqa: E402
from platform_score import evaluate as platform_evaluate      # noqa: E402
from validation_status import uncertified_report              # noqa: E402


def solution_json_name(names: list[str]) -> str:
    cands = [n for n in names if n.endswith('.json')]
    if len(cands) != 1:
        raise ValueError(f'expected exactly one json in the archive, got {cands}')
    return cands[0]


def resync(pkg: Path, data: Path) -> dict:
    zips = list(pkg.glob('*.zip'))
    if len(zips) != 1:
        raise ValueError(f'{pkg}: expected exactly one zip, found {[z.name for z in zips]}')
    zip_path = zips[0]

    raw = zip_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()

    with zipfile.ZipFile(zip_path) as archive:
        name = solution_json_name([n for n in archive.namelist() if not n.endswith('/')])
        packed = archive.read(name)
        if archive.testzip() is not None:
            raise ValueError(f'{zip_path}: CRC failure')
    plan = json.loads(packed.decode('utf-8'))

    contract = platform_check(plan, data=data, round='semi')
    estimator = platform_evaluate(plan, data=data, round_name='semi')
    violations = estimator.get('violations') or {}

    report = {
        'package': pkg.name,
        'zip': zip_path.name,
        'zip_bytes': len(raw),
        'sha256': digest,
        'inner_json': name,
        'inner_sha256': hashlib.sha256(packed).hexdigest(),
        'schemes': len(plan),
        'rounds': sum(len(b.get('length_scheme') or []) for b in plan),
        'platform_check_passed': bool(contract.get('passed')),
        'platform_check_errors': contract.get('error_counts') or {},
        'violations': violations,
        'violation_count': sum(violations.values()),
    }

    legacy = {key: report.pop(key) for key in
              ('platform_check_passed', 'platform_check_errors', 'violations', 'violation_count')}
    report = uncertified_report(report, legacy)
    original = pkg / 'validation_report_legacy_B.json'
    if original.exists():
        report['legacy_report'] = original.name
        report['legacy_report_sha256'] = hashlib.sha256(original.read_bytes()).hexdigest()

    out = pkg / 'validation_report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                   encoding='utf-8', newline='\n')
    if (pkg / 'validation_report_source.json').exists():
        (pkg / 'validation_report_source.json').write_bytes(out.read_bytes())
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('packages', nargs='*', help='package directories')
    ap.add_argument('--all', action='store_true',
                    help='every semi-final ZIP directory under artifacts/rejected')
    ap.add_argument('--data', default='data/semi')
    args = ap.parse_args()

    pkgs = [ROOT / p for p in args.packages]
    if args.all:
        pkgs = sorted(p for p in (ROOT / 'artifacts/rejected').iterdir()
                      if p.is_dir() and next(p.glob('复赛结果_*.zip'), None))
    if not pkgs:
        ap.error('name at least one package, or pass --all')

    data = ROOT / args.data
    bad = 0
    for pkg in pkgs:
        try:
            rep = resync(pkg, data)
        except Exception as exc:                              # noqa: BLE001
            print(f'{pkg.name:32} ERROR: {exc}')
            bad += 1
            continue
        ok = rep['platform_check_passed'] and rep['violation_count'] == 0
        print(f'{pkg.name:32} {rep["sha256"][:16]}  '
              f'passed={rep["platform_check_passed"]}  '
              f'violations={rep["violation_count"]}  {"OK" if ok else "CHECK"}')
        if not ok:
            bad += 1
    print()
    if bad:
        print(f'{bad} package(s) need attention.')
        return 1
    print('All reports now match their ZIP bytes.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
