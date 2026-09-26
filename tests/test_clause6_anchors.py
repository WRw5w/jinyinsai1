"""Pin both clause-6 seam implementations against every official notice we hold.

`platform_check` and `platform_score` deliberately carry separate copies of the
seam predicate: the checker is meant to re-derive everything from the raw CSVs so
that a bug in one cannot hide behind the other.  That independence only pays off
if something notices when one of them drifts -- and the failure it was supposed to
prevent was worse than drift.  Both copies were written to the SAME wrong
predicate, so they agreed with each other and disagreed with the platform for
three days, through two 0-point submissions.

These assertions are the thing that reads the notices instead.  Every anchor is
the exact byte sequence the platform scored, and the expected number is what the
platform returned.
"""
import json
import unittest
import zipfile
from pathlib import Path

from platform_check import check as platform_check
from platform_score import evaluate as platform_score

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/semi'
BACKUPS = ROOT / 'evidence/semi/pre_fix_backups'

# (label, package, official seam count).  Package is a .json or a .zip.
ANCHORS = [
    ('merged_v2  (2026-09-23 notice: 0 points, 35150 penalty)',
     BACKUPS / 'merged_v2__复赛结果_鱼不吃猫.json', 7030),
    ('safety     (recorded notice)',
     BACKUPS / 'safety__复赛结果_棒材优化.json', 7559),
    ('nolimit    (recorded notice)',
     BACKUPS / 'nolimit__复赛结果_棒材优化.json', 7033),
    ('merged_v4  (2026-09-26 notice: 0 points, 36710 penalty)',
     ROOT / 'artifacts/rejected/submission_semi_merged_v4/复赛结果_鱼不吃猫.zip', 7342),
]


def load(path: Path):
    if path.suffix == '.zip':
        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist()
                        if n.endswith('.json') and 'validation' not in n)
            return json.loads(archive.read(name).decode('utf-8'))
    return json.loads(path.read_text(encoding='utf-8'))


def seam_from_check(plan) -> int:
    result = platform_check(plan, data=DATA, round='semi')
    return (result.get('error_counts') or {}).get('continuity_seam', 0)


def seam_from_score(plan) -> int:
    result = platform_score(plan, DATA, round_name='semi')
    return (result.get('violations') or {}).get('continuity_seam', 0)


class Clause6AnchorTests(unittest.TestCase):
    def test_every_anchor_reproduces_under_both_implementations(self):
        for label, path, official in ANCHORS:
            if not path.exists():
                self.fail(f'anchor package is missing from the tree: {path}')
            plan = load(path)
            with self.subTest(anchor=label, implementation='platform_check'):
                self.assertEqual(seam_from_check(plan), official)
            with self.subTest(anchor=label, implementation='platform_score'):
                self.assertEqual(seam_from_score(plan), official)


if __name__ == '__main__':
    unittest.main()
