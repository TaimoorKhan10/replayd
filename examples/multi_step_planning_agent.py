"""
Multi-step planning agent example for replayd.

Scenario:
    A project-scheduling agent receives a request to finalize a sprint plan.
    The correct behavior is to check_constraints (team capacity, dependencies)
    before calling finalize_plan.

    The buggy agent skips check_constraints and finalizes immediately, which
    causes downstream problems when overbooked team members or unresolved
    blockers are locked into the plan.

    replayd captures the bad run, saves it as a regression test, and verifies
    that future versions of the agent cannot ship the same mistake.

Run:
    python examples/multi_step_planning_agent.py
"""

import shutil
from replayd import Replayd
from replayd.capture import RunContext


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

SPRINT_REQUEST = {
    "sprint": "2026-Q3-Sprint-1",
    "tasks": [
        {"id": "T-101", "title": "Migrate auth service", "estimate_days": 5},
        {"id": "T-102", "title": "Add rate limiting",    "estimate_days": 3},
        {"id": "T-103", "title": "Deprecate legacy API", "estimate_days": 2, "blocked_by": "T-101"},
    ],
    "team_capacity_days": 7,
}


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

def buggy_planning_agent(input: dict, run_ctx: RunContext) -> dict:
    """
    Skips constraint checking and finalizes the plan immediately.
    Total estimated days (10) exceeds team capacity (7), and T-103 depends on
    T-101 which is not yet done — but the agent never checks any of this.
    """
    tasks = input["tasks"]

    run_ctx.record_tool_call(
        "finalize_plan",
        {"sprint": input["sprint"], "tasks": [t["id"] for t in tasks]},
        {"status": "finalized", "sprint": input["sprint"]},
    )
    return {"action": "finalize_plan", "sprint": input["sprint"], "tasks_locked": len(tasks)}


def fixed_planning_agent(input: dict, run_ctx: RunContext) -> dict:
    """
    Checks constraints first. Finds capacity overrun and an unresolved
    blocker, then refuses to finalize — asks for missing info instead.
    """
    tasks = input["tasks"]
    capacity = input["team_capacity_days"]
    total_days = sum(t["estimate_days"] for t in tasks)
    blockers = [t for t in tasks if "blocked_by" in t]

    issues = []
    if total_days > capacity:
        issues.append(f"total estimate ({total_days}d) exceeds capacity ({capacity}d)")
    for t in blockers:
        issues.append(f"{t['id']} is blocked by {t['blocked_by']}")

    run_ctx.record_tool_call(
        "check_constraints",
        {"sprint": input["sprint"], "total_days": total_days, "capacity": capacity},
        {"violations": issues},
    )

    if issues:
        run_ctx.record_tool_call(
            "ask_for_missing_info",
            {"sprint": input["sprint"], "issues": issues},
            {"status": "pending_resolution"},
        )
        return {
            "action": "ask_for_missing_info",
            "sprint": input["sprint"],
            "issues": issues,
        }

    run_ctx.record_tool_call(
        "finalize_plan",
        {"sprint": input["sprint"], "tasks": [t["id"] for t in tasks]},
        {"status": "finalized", "sprint": input["sprint"]},
    )
    return {"action": "finalize_plan", "sprint": input["sprint"], "tasks_locked": len(tasks)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    shutil.rmtree(".replayd", ignore_errors=True)
    rp = Replayd()

    print("=" * 60)
    print("  Multi-step Planning Agent — replayd demo")
    print("=" * 60)

    # --- Step 1: Capture the buggy run ------------------------------------
    print("\n[1] Capturing buggy agent run...")
    with rp.capture(input=SPRINT_REQUEST, model="mock-planner-v1") as run:
        run.output = buggy_planning_agent(SPRINT_REQUEST, run)

    saved = rp.get_run(run.id)
    for tc in saved.tool_calls:
        args = ", ".join(f"{k}={v}" for k, v in tc.arguments.items())
        print(f"    tool: {tc.name}({args})")
    print(f"    output: {run.output}")

    # --- Step 2: Mark it failed -------------------------------------------
    failure_reason = (
        "Agent finalized sprint plan without checking constraints — "
        "10d of work committed against a 7d capacity, and T-103 blocked by T-101"
    )
    print(f"\n[2] Marking run as failed...")
    rp.mark_failed(run.id, reason=failure_reason)
    print(f"    reason: {failure_reason}")

    # --- Step 3: Save as regression test ----------------------------------
    print("\n[3] Saving regression test...")
    test = rp.save_test(
        run.id,
        forbidden_actions=["finalize_plan"],
        expected_action="check_constraints",
    )
    print(f"    forbidden: finalize_plan (without check_constraints first)")
    print(f"    expected:  check_constraints")

    print()
    print("-" * 60)

    # --- Step 4: Replay against buggy agent (should FAIL) ----------------
    print("Replay #1 — buggy agent  (regression should be caught)")
    results = rp.replay_all(agent=buggy_planning_agent)
    for r in results:
        tag = "FAIL" if r.verdict.value == "fail" else "PASS"
        print(f"  [{tag}] {r.reason}")

    print()

    # --- Step 5: Replay against fixed agent (should PASS) ----------------
    print("Replay #2 — fixed agent  (regression should be resolved)")
    results = rp.replay_all(agent=fixed_planning_agent)
    for r in results:
        tag = "FAIL" if r.verdict.value == "fail" else "PASS"
        print(f"  [{tag}] {r.reason}")

    print("-" * 60)
    print("1 planning regression caught. 1 resolved.")


if __name__ == "__main__":
    main()
