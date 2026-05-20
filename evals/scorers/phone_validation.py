from __future__ import annotations

from evals.types import RunOutput, Score, Trace
from store_assistant.validation.exceptions import PhoneValidationError
from store_assistant.validation.phone import validate_and_normalize_phone


def score(trace: Trace, run_output: RunOutput) -> Score:
    del run_output  # synthetic scorer: phones come from trace.expected, not the run
    expected = trace["expected"]
    accepted = expected.get("expected_phones_accepted", [])
    rejected = expected.get("expected_phones_rejected", [])
    if not accepted and not rejected:
        return Score(
            name="phone_validation",
            passed=True,
            reason="trace does not assert on phone validation",
            details={"applicable": False},
        )

    failures: list[str] = []

    for raw in accepted:
        try:
            validate_and_normalize_phone(raw)
        except PhoneValidationError as exc:
            failures.append(f"expected accept but rejected ({exc.reason}): {raw!r}")

    for raw in rejected:
        try:
            validate_and_normalize_phone(raw)
        except PhoneValidationError:
            continue
        failures.append(f"expected reject but accepted: {raw!r}")

    passed = not failures
    return Score(
        name="phone_validation",
        passed=passed,
        reason=(
            "all phones classified correctly"
            if passed
            else f"{len(failures)} misclassification(s)"
        ),
        details={
            "expected_accepted": accepted,
            "expected_rejected": rejected,
            "failures": failures,
        },
    )
