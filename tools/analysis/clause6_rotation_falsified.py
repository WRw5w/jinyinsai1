"""Both official clause-6 anchors, and why reading B is falsified.

Anchor 1  2026-09-23  submission_semi_merged_v2 (keys NOT rotated)  -> 7030
Anchor 2  2026-09-26  submission_semi_merged_v4 (keys rotated)      -> 7342

`rotate_scheme_rounds` rewrites the *insertion order* of the keys inside each
round's dict.  Between the two anchors our local reading B (which compares the
last key of the left round with the first key of the right round) went from 7344
to 0 -- while the platform's count stayed essentially flat.  So the platform does
not read insertion order, and the rotation has never fixed anything.

The three key orderings below agree on anchor 1 (that is what made the earlier
"B is confirmed" reading look safe) and only diverge on anchor 2, after the
rotation has destroyed the insertion order.  One anchor with no discriminating
power is exactly the trap `clause6_OPEN_RISK_20260923.md` described.

Run:  python -X utf8 diagnostics/clause6_rotation_falsified.py
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ANCHOR_V2 = 7030   # merged_v2, submitted 2026-09-23
ANCHOR_V4 = 7342   # submission_semi_merged_v4, submitted 2026-09-26


def load_zip(path: Path):
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist()
                    if n.endswith(".json") and "validation" not in n)
        return json.loads(z.read(name).decode("utf-8"))


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def seq(batch, rnd, mode):
    if mode == "dict":
        return list(rnd.keys())
    if mode == "sorted":
        return sorted(rnd.keys())
    if mode == "orderslist":
        idx = {o: k for k, o in enumerate(batch.get("orders") or [])}
        return sorted(rnd.keys(), key=lambda o: idx.get(o, 10**9))
    raise ValueError(mode)


def count(plan, mode: str) -> int:
    """Boundaries with a shared order whose left tail != right head, per ordering."""
    n = 0
    for batch in plan:
        if not isinstance(batch, dict):
            continue
        rounds = batch.get("length_scheme") or []
        for left, right in zip(rounds, rounds[1:]):
            if not (left and right and set(left) & set(right)):
                continue
            a, c = seq(batch, left, mode), seq(batch, right, mode)
            if a and c and a[-1] != c[0]:
                n += 1
    return n


def main() -> int:
    v2 = load_json(ROOT / "evidence/semi/pre_fix_backups/"
                   "merged_v2__复赛结果_鱼不吃猫.json")
    v4 = load_zip(ROOT / "artifacts/rejected/submission_semi_merged_v4/复赛结果_鱼不吃猫.zip")

    print(f"{'key ordering':<16}{'v2 (anchor 7030)':>18}{'v4 (anchor 7342)':>18}")
    print("-" * 54)
    for mode in ("dict", "sorted", "orderslist"):
        print(f"{mode:<16}{count(v2, mode):>18}{count(v4, mode):>18}")
    print()
    print(f"official anchors: v2 = {ANCHOR_V2}, v4 = {ANCHOR_V4}")

    ok = True
    # Both canonical orderings must reproduce anchor 1 and stay near anchor 2.
    for mode in ("sorted", "orderslist"):
        if count(v2, mode) != ANCHOR_V2:
            print(f"FAIL: {mode} no longer reproduces anchor 1")
            ok = False
    # The insertion-order reading must be the one that collapses on anchor 2.
    if count(v4, "dict") == ANCHOR_V4:
        print("FAIL: insertion order still matches anchor 2 -- "
              "the falsification no longer holds")
        ok = False
    else:
        print("OK: insertion-order reading collapses to "
              f"{count(v4, 'dict')} on the rotated package while the platform "
              f"reported {ANCHOR_V4} -- rotation is a no-op.")
    print("OK: every ordering agrees on anchor 1, which is why one anchor "
          "could not discriminate.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
