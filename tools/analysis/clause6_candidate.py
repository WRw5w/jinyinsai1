"""Reproduce clause-6 anchors with a candidate model, not a submission gate.

v2 has a preserved platform receipt; v4=7342 is a historical report whose raw
receipt is still missing. Matching these counts does not certify the model.
"""
from __future__ import annotations

import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def count_boundaries(plan) -> int:
    """Count a boundary once if any shared order is not tail-to-head aligned."""
    count = 0
    for batch in plan:
        rounds = batch.get("length_scheme") or []
        for left, right in zip(rounds, rounds[1:]):
            shared = set(left) & set(right)
            if any(order != next(reversed(left)) or order != next(iter(right))
                   for order in shared):
                count += 1
    return count


def count_order_gaps(plan) -> int:
    """Count orders with non-consecutive round indices separately."""
    count = 0
    for batch in plan:
        positions = {}
        for index, round_ in enumerate(batch.get("length_scheme") or []):
            for order in round_:
                positions.setdefault(order, []).append(index)
        count += sum(indices[-1] - indices[0] + 1 != len(indices)
                     for indices in positions.values())
    return count


def anchor_results() -> dict:
    v2 = json.loads((ROOT / "evidence/semi/pre_fix_backups/"
                    "merged_v2__复赛结果_鱼不吃猫.json").read_bytes())
    with zipfile.ZipFile(ROOT / "artifacts/rejected/submission_semi_merged_v4/"
                         "复赛结果_鱼不吃猫.zip") as archive:
        names = [name for name in archive.namelist() if name.endswith(".json")]
        if len(names) != 1:
            raise ValueError("Expected one solution JSON")
        v4 = json.loads(archive.read(names[0]))
    # Transcription of the feasible adjacency in evidence/rules/AIC竞赛规则.pdf,
    # pages 18-19. JSON insertion order is intentionally retained.
    example = [{"length_scheme": [
        {"A20260105": 109.25, "A20260104": 25.6}, {"A20260104": 70.4}]}]
    cases = [("original_v2", v2, 7030), ("rotated_v4", v4, 7342),
             ("official_example", example, 0)]
    return {
        "status": "candidate_not_certified",
        "submission_allowed": False,
        "v4_receipt": "historical_report_only_raw_receipt_missing",
        "cases": [{"name": name, "boundary_count": count_boundaries(plan),
                   "order_gap_count": count_order_gaps(plan), "expected": expected}
                  for name, plan, expected in cases],
    }


def main() -> int:
    result = anchor_results()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Success means reproducible anchors, never a release decision.
    return 0 if all(case["boundary_count"] == case["expected"]
                    for case in result["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
