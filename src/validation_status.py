"""Keep local-model results separate from release authorization."""
from __future__ import annotations

# A freshly built package's report carries the model's own verdict under these keys.
LEGACY_MODEL_KEYS = ('independent_platform_check', 'calibrated_prediction')

UNCERTIFIED_REASON = (
    "No release certification: these numbers come from the local models, not from "
    "the platform scorer. Read them as diagnostics, not as acceptance. The seam "
    "count still rests on a candidate predicate -- four official notices fit it "
    "exactly and the official worked example must score 0, but the platform's "
    "source has not been seen."
)


def uncertified_build_report(report: dict) -> dict:
    """Downgrade the report `build_submission` writes, in place of writing it raw.

    Without this a fresh `aic.py build` emits `independent_platform_check.passed:
    True`, which reads as approval for a package nobody has certified.  The
    model's numbers move under `model_result` so the diagnostics survive the
    downgrade.
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
        "reason": UNCERTIFIED_REASON,
        "model_result": legacy_result,
    }
