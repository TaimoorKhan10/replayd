"""
Replay the commerce image-request regression case against a selected agent.

Run from the repo root:
    python examples/commerce_route/run_replay.py --agent broken
    python examples/commerce_route/run_replay.py --agent fixed
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path


CASE_PATH = (
    Path(__file__).resolve().parent
    / ".replayd"
    / "tests"
    / "case_07_send_product_image.json"
)


def load_case() -> dict:
    with CASE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_agent(name: str):
    module = importlib.import_module(f"{name}_agent")
    return module.route_request


def print_report(case: dict, actual_route: str, exit_code: int) -> None:
    decision = "PASS" if exit_code == 0 else case["release_gate"]["on_fail"]

    print(f"replayd run {case['case_id']}")
    print()
    print("input:")
    print(f'"{case["input"]}"')
    print()
    print("expected route:")
    print(case["expected_route"])
    print()
    print("actual route:")
    print(actual_route)
    print()
    print("decision:")
    print(decision)
    print()
    print("exit code:")
    print(exit_code)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the commerce route replay case.")
    parser.add_argument(
        "--agent",
        choices=["broken", "fixed"],
        required=True,
        help="Agent implementation to replay against.",
    )
    args = parser.parse_args(argv)

    case = load_case()
    route_request = load_agent(args.agent)
    result = route_request(case["input"])
    actual_route = result["route"]

    exit_code = 0 if actual_route == case["expected_route"] else 1
    print_report(case, actual_route, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
