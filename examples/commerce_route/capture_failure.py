"""
Capture the broken commerce route behavior as a replay case.

This script keeps the example deterministic and local: it runs the broken
agent, compares the actual route to the expected route, and writes the readable
JSON replay fixture used by run_replay.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from broken_agent import route_request


CASE_ID = "commerce_image_request_001"
USER_INPUT = "Can I see a picture?"
EXPECTED_ROUTE = "send_product_image"
CASE_PATH = (
    Path(__file__).resolve().parent
    / ".replayd"
    / "tests"
    / "case_07_send_product_image.json"
)


def build_case(actual_route: str) -> dict:
    return {
        "case_id": CASE_ID,
        "input": USER_INPUT,
        "expected_route": EXPECTED_ROUTE,
        "failed_actual_route": actual_route,
        "failure_reason": (
            "User asked for a product image, but the agent replied with text "
            "instead of routing to send_product_image."
        ),
        "assertions": {
            "actual_route_must_equal": EXPECTED_ROUTE,
            "regression_route": actual_route,
            "fail_when_actual_route_is": actual_route,
        },
        "created_from_failed_run": True,
        "release_gate": {
            "check_name": "replayd / commerce route",
            "on_fail": "BLOCK",
            "on_pass": "PASS",
        },
    }


def write_case(case: dict) -> None:
    CASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CASE_PATH.write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    result = route_request(USER_INPUT)
    actual_route = result["route"]

    print(f"capturing failed run: {CASE_ID}")
    print(f'input: "{USER_INPUT}"')
    print(f"expected route: {EXPECTED_ROUTE}")
    print(f"actual route: {actual_route}")

    if actual_route == EXPECTED_ROUTE:
        print("no failure captured; the broken agent matched the expected route")
        return 0

    case = build_case(actual_route)
    write_case(case)
    print(f"saved replay case: {CASE_PATH}")
    print("release gate: BLOCK if this route regression returns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
