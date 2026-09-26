"""Keep obsolete local-model results separate from release authorization."""
from __future__ import annotations


def uncertified_report(metadata: dict, legacy_result: dict) -> dict:
    return {
        **metadata,
        "status": "unverified",
        "passed": False,
        "platform_check_passed": False,
        "submission_allowed": False,
        "violations": None,
        "violation_count": None,
        "reason": "Legacy B model misses known clause-6 violations; no release certification.",
        "legacy_model_result": legacy_result,
    }
