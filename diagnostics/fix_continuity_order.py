"""Rotate each round's key order so round N ends where round N+1 begins.

The semi-final continuity clause is STRICTER than "the same order appears in
adjacent rounds": the platform reads a scheme's rounds as one continuous billet
stream, so the last order cut in round N must be the first order cut in round N+1
-- otherwise another order is interleaved at the seam and it reports
`跨轮接续不连续`.

Our solver emits every round of a scheme with the SAME key order (it is the same
shared cold-bed set), which satisfies "same orders" but violates the seam.  The
2026-09-23 official 0-point feedback on submission_semi_merged_v2 pinned this
exactly: 7030 seam violations out of 7034 seams that share orders.  A rotation is
therefore sufficient and costs NOTHING -- the multiset of (order -> length) in
every round is untouched, so knives / yield / coverage are bit-identical.

    python -X utf8 diagnostics/fix_continuity_order.py <in.zip|in.json> [--out ...]

Verified against the official 7030 count before the fix and 0 after.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def seam_violations(plan):
    """(violations, seams) -- the platform's `跨轮接续不连续` count."""
    bad = seams = 0
    for batch in plan:
        ls = batch['length_scheme']
        for left, right in zip(ls, ls[1:]):
            if set(left) & set(right):
                seams += 1
                if next(reversed(left)) != next(iter(right)):
                    bad += 1
    return bad, seams


def rotate_batch(rounds):
    """Rotate round keys so each round ends on the next round's first order.

    Walked back to front: the last round is free, and every earlier round is
    rotated to close onto its successor.  A pair with no shared order is left
    alone (the platform does not constrain it).
    """
    seq = [list(r) for r in rounds]
    for i in range(len(seq) - 2, -1, -1):
        want = seq[i + 1][0]
        cur = seq[i]
        if want in cur:
            k = cur.index(want)
            seq[i] = cur[k + 1:] + cur[:k + 1]
    return [{oid: rounds[i][oid] for oid in seq[i]} for i in range(len(seq))]


def fix(plan):
    for batch in plan:
        batch['length_scheme'] = rotate_batch(batch['length_scheme'])
    return plan


def read_plan(path):
    path = Path(path)
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if len(names) != 1:
                raise ValueError(f'expected one JSON in the ZIP, found {len(names)}')
            return json.loads(z.read(names[0]))
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_plan(plan, path, inner_name):
    path = Path(path)
    raw = json.dumps(plan, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr(inner_name, raw)
    else:
        path.write_bytes(raw)
    return raw


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source')
    ap.add_argument('--out')
    args = ap.parse_args()

    src = Path(args.source)
    plan = read_plan(src)
    before = seam_violations(plan)
    fix(plan)
    after = seam_violations(plan)
    print(f'{src.name}: seam violations {before[0]} -> {after[0]} (of {before[1]} seams)')

    out = Path(args.out) if args.out else src
    if src.suffix.lower() == '.zip':
        with zipfile.ZipFile(src) as z:
            inner = z.namelist()[0]
    else:
        inner = src.name
    write_plan(plan, out, inner)
    print(f'wrote {out} ({out.stat().st_size} bytes)')
    return 0 if after[0] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
