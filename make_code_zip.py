"""Build `deliverables_semi/复赛代码与模型.zip` with a fixed, auditable file list.

The semi-final asks for the core code, the model and the environment, and the
reviewers run the result back.  So the archive carries exactly the modules the
reproduction commands in `环境与复现说明.md` touch, plus the data and the
evidence the report cites -- and nothing that is regenerable or machine-specific
(`runs/`, `.exe`, `legacy/`, the preliminary-round archive).

Entries are written in sorted order with a frozen timestamp so the archive is
byte-stable: rebuilding it twice must give the same SHA-256.

Run:
    python -X utf8 make_code_zip.py
"""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'deliverables_semi' / '复赛代码与模型.zip'

CODE = [
    'solver.py', 'round_shaper.py', 'build_semi_plan.py',
    'continuity_shaper.py', 'build_semi_plan2.py',
    'platform_check.py', 'platform_score.py', 'build_submission.py',
    'competition_solver.py', 'prepare_semi.py', 'solve_semi.py',
    'test_solver.py', 'test_platform.py', 'test_platform_score.py',
    'test_semi_solver.py', 'test_semi_check.py', 'test_semi_rules.py',
]

DATA = [
    'orders_semi.csv', 'blank_used_finals.csv', 'constraints.txt',
    'orders.normalized.csv', 'blanks.normalized.csv', 'data_audit.json',
    'competition.config.json', 'cap002.config.json', 'cap005.config.json',
    'cap010.config.json', 'cap025.config.json',
]

ROOT_FILES = ['RULES.md']

FROZEN = (2026, 9, 21, 0, 0, 0)


def gather():
    items = []  # (arcname, source path)
    items.append(('环境与复现说明.md',
                  ROOT / 'deliverables_semi' / '环境与复现说明.md'))
    for name in ROOT_FILES:
        items.append((name, ROOT / name))
    for name in CODE:
        items.append((f'code/{name}', ROOT / name))
    for name in DATA:
        items.append((f'data/semi/{name}', ROOT / 'data' / 'semi' / name))
    for path in sorted((ROOT / 'diagnostics').iterdir()):
        if path.is_file() and path.suffix in {'.py', '.json', '.md', '.txt'}:
            items.append((f'diagnostics/{path.name}', path))
    items.append(('result/复赛结果_棒材优化.json',
                  ROOT / 'deliverables_semi' / '复赛结果_棒材优化.json'))
    items.append(('result/validation_report.json',
                  ROOT / 'deliverables_semi' / 'validation_report.json'))
    return items


def main():
    items = gather()
    missing = [str(src.relative_to(ROOT)) for _, src in items if not src.is_file()]
    if missing:
        raise SystemExit('missing sources:\n  ' + '\n  '.join(missing))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for arcname, src in sorted(items):
            info = zipfile.ZipInfo(arcname, date_time=FROZEN)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, src.read_bytes())

    with zipfile.ZipFile(OUT) as archive:
        if archive.testzip() is not None:
            raise SystemExit('archive failed CRC')
        names = archive.namelist()
        if names != sorted(names):
            raise SystemExit('entries not sorted')
    raw = OUT.read_bytes()
    print(f'wrote {OUT}')
    print(f'entries={len(items)}  bytes={len(raw):,}  '
          f'sha256={hashlib.sha256(raw).hexdigest()}')
    for arcname, src in sorted(items):
        print(f'  {arcname:<48} {src.stat().st_size:>10,}')


if __name__ == '__main__':
    main()
