"""
End-to-end example of replayd.

Run from the repo root with:
    pip install -e .
    python examples/basic_example.py

Or without installing:
    PYTHONPATH=. python examples/basic_example.py

The example simulates a refund-approval agent that has a bug: it approves
refunds above the $500 policy limit. We capture the failure, save it as a
regression test, then replay it against both the buggy agent (expects FAIL)
and the fixed agent (expects PASS).
"""

from replayd import Replayd
from replayd.capture import RunContext


# ---------------------------------------------------------------------------
# Mock agents
# ---------------------------------------------------------------------------

def buggy_agent(input: dict, run_ctx: RunContext) -> dict:
    amount = input.get("amount", 0)
    run_ctx.record_tool_call(
        name="approve_refund",
        arguments={"amount": amount, "customer_id": input.get("customer_id")},
        result={"approved": True},
    )
    return {"action": "approve_refund", "amount": amount}


def fixed_agent(input: dict, run_ctx: RunContext) -> dict:
    amount = input.get("amount", 0)
    policy_limit = 500

    if amount > policy_limit:
        run_ctx.record_tool_call(
            name="escalate",
            arguments={"reason": "refund exceeds policy limit", "amount": amount},
            result={"ticket_id": "ESC-001"},
        )
        return {"action": "escalate", "amount": amount}

    run_ctx.record_tool_call(
        name="approve_refund",
        arguments={"amount": amount, "customer_id": input.get("customer_id")},
        result={"approved": True},
    )
    return {"action": "approve_refund", "amount": amount}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import shutil
    import os

    if os.path.exists(".replayd"):
        shutil.rmtree(".replayd")

    rp = Replayd()
    user_input = {"customer_id": "cust-42", "amount": 1200, "reason": "defective product"}

    # --- Capture ------------------------------------------------------------
    print("Capturing a refund-approval agent run...")
    with rp.capture(input=user_input, model="mock-v1") as run:
        run.output = buggy_agent(user_input, run)

    for tc in rp.get_run(run.id).tool_calls:
        args = ", ".join(f"{k}={v}" for k, v in tc.arguments.items())
        print(f"  agent called: {tc.name}({args})  [policy limit is $500]")
    print(f"  output: {run.output}")

    # --- Mark failed --------------------------------------------------------
    print("\nMarking run as failed...")
    failure_reason = "agent approved refund of $1200, exceeding $500 policy limit"
    rp.mark_failed(run.id, reason=failure_reason)
    print(f"  reason: {failure_reason}")

    # --- Save as test -------------------------------------------------------
    print("\nSaving as regression test...")
    test = rp.save_test(
        run.id,
        forbidden_actions=["approve_refund"],
        expected_action="escalate",
    )
    print(f"  forbidden: approve_refund  |  expected: escalate")

    print()
    print("-" * 41)

    # --- Replay: buggy agent ------------------------------------------------
    print("Replay #1 -- buggy agent (regression should be caught)")
    results = rp.replay_all(agent=buggy_agent)
    for r in results:
        verdict = "FAIL" if r.verdict.value == "fail" else "PASS"
        print(f"  [{verdict}] {r.reason}")

    print()

    # --- Replay: fixed agent ------------------------------------------------
    print("Replay #2 -- fixed agent (regression should be resolved)")
    results = rp.replay_all(agent=fixed_agent)
    for r in results:
        verdict = "FAIL" if r.verdict.value == "fail" else "PASS"
        print(f"  [{verdict}] {r.reason}")

    print("-" * 41)
    print("1 failure caught. 1 resolved.")


if __name__ == "__main__":
    main()
