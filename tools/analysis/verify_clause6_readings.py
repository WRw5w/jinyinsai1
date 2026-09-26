"""Historical B/C/G diagnostics, not a certification or release gate.

B misses known violations. Use aic.py audit-clause6 for the candidate model.
The copy comparison below only compares B counts, not full JSON equivalence.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (label, path relative to repo root, official ground truth or None)
#
# `merged_v2` MUST point at the preserved violating copy, not at the package
# directory: as of 2026-09-23 the directory's .json was resynced to the repaired
# plan, which erases the only evidence we have for predicate B.  The original
# 7030-violation plan is archived under evidence/semi/pre_fix_backups/.
CASES = [
    ('merged_v2  (ours, REJECTED)',
     'evidence/semi/pre_fix_backups/merged_v2__复赛结果_鱼不吃猫.json', 7030),
    ('safety     (ours)',           'artifacts/rejected/submission_semi_safety',    None),
    ('FIXED      (ours, repaired)', 'artifacts/rejected/submission_semi_FIXED',     None),
    ('nolimit    (ours, repaired)', 'artifacts/rejected/submission_semi_nolimit',   None),
    ('rival 371f209 (other line)',  'evidence/rival_semi_371f209.json', None),
]


SKIP_IN_NAME = ('validation', 'feedback', 'packed_score')

# Which directories are semi-final deliverables?  Selecting them by name prefix
# (`submission_semi_*`) is how this file used to do it, and that silently skipped
# `submission_ours_94` and `submission_ours_seed` -- both are semi-final packages
# listed in MIGRATION.md, both were repaired on 2026-09-23, and both kept a stale
# plan `.json` and a stale validation report because the resync pass used the same
# prefix.  A deliverable is better identified by what it holds: a semi-final ZIP.
def semi_dirs(root: Path):
    for d in sorted((root / 'artifacts/rejected').glob('submission*')):
        if d.is_dir() and next(d.glob('复赛结果_*.zip'), None) is not None:
            yield d


def load(path: Path):
    """Read a plan from a directory of deliverables, or a bare .json file."""
    if path.is_dir():
        cands = [p for p in path.glob('*.json')
                 if not any(s in p.name for s in SKIP_IN_NAME)]
        if not cands:
            raise FileNotFoundError(f'no plan json under {path}')
        return json.loads(cands[0].read_text(encoding='utf-8'))
    if path.suffix == '.zip':
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist()
                        if n.endswith('.json') and 'validation' not in n)
            return json.loads(z.read(name).decode('utf-8'))
    return json.loads(path.read_text(encoding='utf-8'))


def report_hash_mismatches(root: Path) -> list[str]:
    """Each package's validation report must cite the hash of the ZIP on disk.

    When a repair rebuilds the ZIP but not the report, the report describes an
    artifact that no longer exists.  That is worse than having no report: it
    looks like evidence while certifying nothing, and it invites shipping an
    unvalidated package.  It happened on 2026-09-23 -- merged_v2's report still
    said 852c9b84... while the ZIP had become 49862fc7... -- so it is checkable.

    Regenerate with `python -X utf8 tools/resync_reports.py --all`.
    """
    bad: list[str] = []
    for d in semi_dirs(root):
        if not d.is_dir():
            continue
        zips = list(d.glob('*.zip'))
        if not zips:
            continue
        actual = hashlib.sha256(zips[0].read_bytes()).hexdigest()
        report = None
        for cand in ('validation_report.json', 'validation_report_source.json'):
            if (d / cand).exists():
                report = d / cand
                break
        if report is None:
            bad.append(f'{d.name}: has a ZIP but no validation report')
            continue
        cited = re.findall(r'[0-9a-f]{64}', report.read_text(encoding='utf-8'))
        if actual not in cited:
            bad.append(f'{d.name}: report cites {cited[:1] or ["nothing"]} but the '
                       f'ZIP is {actual[:16]}... -- rerun tools/resync_reports.py')
    return bad

    """A deliverable directory holds the SAME plan twice: a `.json` and a `.zip`.

    The `.zip` is what gets uploaded.  If the two drift apart (a repair applied
    to the zip but not the json, say), a future run can pick up the stale .json
    and ship a package that scores zero.  That actually happened on 2026-09-23:
    three of four repaired packages still had violating .json copies.  Report
    every directory where they disagree.
    """
    bad: list[str] = []
    for d in semi_dirs(root):
        if not d.is_dir():
            continue
        zips = list(d.glob('*.zip'))
        jsons = [p for p in d.glob('*.json')
                 if not any(s in p.name for s in SKIP_IN_NAME)]
        if not zips or not jsons:
            continue
        try:
            zplan = load(zips[0])
            jplan = json.loads(jsons[0].read_text(encoding='utf-8'))
            if not isinstance(jplan, list):
                continue
        except Exception:                              # noqa: BLE001
            continue
        if reading_B(zplan) != reading_B(jplan):
            bad.append(f'{d.name}: zip B={reading_B(zplan)} but '
                       f'json B={reading_B(jplan)} -- stale .json, resync it')
    return bad


def rounds_of(plan):
    for batch in plan:
        if isinstance(batch, dict):
            yield batch.get('length_scheme') or []


def zip_json_mismatches(root: Path) -> list[str]:
    """A deliverable directory holds the SAME plan twice: a `.json` and a `.zip`.

    The `.zip` is what gets uploaded.  If the two drift apart (a repair applied
    to the zip but not the json, say), a future run can pick up the stale .json
    and ship a package that scores zero.  That actually happened on 2026-09-23:
    three of four repaired packages still had violating .json copies.  Report
    every directory where they disagree.
    """
    bad: list[str] = []
    for d in semi_dirs(root):
        if not d.is_dir():
            continue
        zips = list(d.glob('*.zip'))
        jsons = [p for p in d.glob('*.json')
                 if not any(s in p.name for s in SKIP_IN_NAME)]
        if not zips or not jsons:
            continue
        try:
            zplan = load(zips[0])
            jplan = json.loads(jsons[0].read_text(encoding='utf-8'))
            if not isinstance(jplan, list):
                continue
        except Exception:                              # noqa: BLE001
            continue
        if reading_B(zplan) != reading_B(jplan):
            bad.append(f'{d.name}: zip B={reading_B(zplan)} but '
                       f'json B={reading_B(jplan)} -- stale .json, resync it')
    return bad


def reading_B(plan) -> int:
    """Legacy B: overlap AND tail != head; matching v2 does not certify it."""
    n = 0
    for rounds in rounds_of(plan):
        for left, right in zip(rounds, rounds[1:]):
            if left and right and set(left) & set(right) \
                    and next(reversed(left)) != next(iter(right)):
                n += 1
    return n


def reading_C(plan) -> int:
    """The other line's predicate: identical order sets."""
    n = 0
    for rounds in rounds_of(plan):
        for left, right in zip(rounds, rounds[1:]):
            if left and right and set(left) == set(right):
                n += 1
    return n


def reading_G(plan) -> int:
    """Per-order gap: an order's round numbers must be consecutive (clause 6's
    other surviving sub-rule, charged separately)."""
    n = 0
    for batch in plan:
        if not isinstance(batch, dict):
            continue
        per: dict[str, list[int]] = {}
        for j, rnd in enumerate(batch.get('length_scheme') or []):
            for oid in rnd:
                per.setdefault(oid, []).append(j)
        for js in per.values():
            if len(js) > 1 and js != list(range(js[0], js[0] + len(js))):
                n += 1
    return n


def main() -> int:
    print(f"{'package':<30}{'B':>8}{'C':>8}{'G':>8}{'official':>10}  verdict")
    print('-' * 78)
    anchor_ok = True
    for label, rel, truth in CASES:
        path = ROOT / rel
        try:
            plan = load(path)
        except Exception as exc:                      # noqa: BLE001
            print(f'{label:<30}  load error: {exc}')
            continue
        b, c, g = reading_B(plan), reading_C(plan), reading_G(plan)
        if truth is not None:
            hit = 'B HITS' if b == truth else 'B MISSES'
            if b != truth:
                anchor_ok = False
            if c == truth:
                hit += ' / C also hits'
            verdict = f'{hit}  (C off by {c - truth:+d})'
        else:
            verdict = 'legacy B=0 (NOT certified)' if b == 0 else f'B flags {b}'
        print(f'{label:<30}{b:>8}{c:>8}{g:>8}'
              f'{(str(truth) if truth is not None else "-"):>10}  {verdict}')
    print()
    if not anchor_ok:
        print('FAIL: reading B no longer reproduces the official 7030 anchor.')
        return 1
    print('OK: reading B reproduces the official 7030; reading C does not.')
    print('Historical counts only; B=0 does not imply safety or submission authorization.')

    # Deliverable directories carry the plan twice; the .zip is what ships.
    # Drift between the two is a silent 0-point trap -- see the docstring.
    print()
    drift = zip_json_mismatches(ROOT)
    if drift:
        print('FAIL: .zip and .json disagree inside these package directories:')
        for line in drift:
            print(f'  - {line}')
        return 1
    print('OK: ZIP/JSON legacy B counts agree; full content equivalence is NOT checked.')

    # A report must certify the bytes that will actually be uploaded.
    print()
    stale = report_hash_mismatches(ROOT)
    if stale:
        print('FAIL: validation reports do not match the ZIP on disk:')
        for line in stale:
            print(f'  - {line}')
        return 1
    print('OK: every validation report cites the sha256 of its ZIP.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
