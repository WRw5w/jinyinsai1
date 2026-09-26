"""Keep obsolete local-model results separate from release authorization."""
from __future__ import annotations

# A freshly built package's report carries the model's own verdict under these keys.
LEGACY_MODEL_KEYS = ('independent_platform_check', 'calibrated_prediction')


def uncertified_build_report(report: dict) -> dict:
    """Downgrade the report `build_submission` writes, in place of writing it raw.

    Without this a fresh `aic.py build` emits `independent_platform_check.passed:
    True` -- a machine-readable approval for a package the platform scored zero on
    2026-09-26, because that check still implements the clause-6 reading the
    submission falsified.  The model's numbers move under `legacy_model_result`
    so the diagnostics survive the downgrade.
    """
    legacy = {key: report.pop(key) for key in LEGACY_MODEL_KEYS if key in report}
    return uncertified_report(report, legacy)


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
