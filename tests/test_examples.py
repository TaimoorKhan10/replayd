"""
Regression tests for the three example agents.

Each test follows the same pattern as the example files but uses pytest's
tmp_path fixture for isolated, throwaway storage.  The goal is to verify
that:
  - the buggy agent produces a FAIL verdict when replayed
  - the fixed agent produces a PASS verdict when replayed
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from replayd import Replayd
from replayd.capture import RunContext
from replayd.models import ReplayVerdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_rp(tmp_path):
    return Replayd(storage_dir=tmp_path / ".replayd")


def capture_and_save(rp, input, agent, failure_reason, forbidden, expected):
    with rp.capture(input=input) as run:
        run.output = agent(input, run)
    rp.mark_failed(run.id, reason=failure_reason)
    rp.save_test(run.id, forbidden_actions=forbidden, expected_action=expected)


# ===========================================================================
# Multi-step planning agent
# ===========================================================================

# -- inline agent definitions so tests don't import from examples/ ----------

SPRINT_REQUEST = {
    "sprint": "2026-Q3-Sprint-1",
    "tasks": [
        {"id": "T-101", "title": "Migrate auth service",  "estimate_days": 5},
        {"id": "T-102", "title": "Add rate limiting",     "estimate_days": 3},
        {"id": "T-103", "title": "Deprecate legacy API",  "estimate_days": 2, "blocked_by": "T-101"},
    ],
    "team_capacity_days": 7,
}


def _planning_buggy(input, run_ctx: RunContext):
    tasks = input["tasks"]
    run_ctx.record_tool_call(
        "finalize_plan",
        {"sprint": input["sprint"], "tasks": [t["id"] for t in tasks]},
        {"status": "finalized"},
    )
    return {"action": "finalize_plan"}


def _planning_fixed(input, run_ctx: RunContext):
    tasks = input["tasks"]
    capacity = input["team_capacity_days"]
    total_days = sum(t["estimate_days"] for t in tasks)
    issues = []
    if total_days > capacity:
        issues.append("over capacity")
    for t in tasks:
        if "blocked_by" in t:
            issues.append(f"{t['id']} blocked by {t['blocked_by']}")

    run_ctx.record_tool_call(
        "check_constraints",
        {"total_days": total_days, "capacity": capacity},
        {"violations": issues},
    )
    if issues:
        run_ctx.record_tool_call(
            "ask_for_missing_info",
            {"issues": issues},
            {"status": "pending"},
        )
        return {"action": "ask_for_missing_info"}
    run_ctx.record_tool_call("finalize_plan", {"sprint": input["sprint"]}, {"status": "finalized"})
    return {"action": "finalize_plan"}


def test_planning_buggy_agent_is_caught(tmp_path):
    rp = make_rp(tmp_path)
    capture_and_save(
        rp, SPRINT_REQUEST, _planning_buggy,
        "finalized plan without checking constraints",
        forbidden=["finalize_plan"],
        expected="check_constraints",
    )
    results = rp.replay_all(agent=_planning_buggy)
    assert len(results) == 1
    assert results[0].verdict == ReplayVerdict.FAIL
    assert "finalize_plan" in results[0].reason


def test_planning_fixed_agent_passes(tmp_path):
    rp = make_rp(tmp_path)
    capture_and_save(
        rp, SPRINT_REQUEST, _planning_buggy,
        "finalized plan without checking constraints",
        forbidden=["finalize_plan"],
        expected="check_constraints",
    )
    results = rp.replay_all(agent=_planning_fixed)
    assert len(results) == 1
    assert results[0].verdict == ReplayVerdict.PASS


# ===========================================================================
# RAG policy agent
# ===========================================================================

REFUND_REQUEST = {
    "customer_id": "cust-888",
    "order_id": "ORD-20260530",
    "claim": "Item never arrived. Requesting full refund of $340.",
    "account_tier": "standard",
}

STALE_CHUNK = {
    "text": "Customers are entitled to an immediate full refund on all claims.",
    "source_id": "policy-legacy-2019-DEPRECATED",
    "score": 0.91,
}

AUTHORITATIVE_POLICY = {
    "policy_id": "policy-refunds-v4-2026",
    "rule": "Standard-tier: auto-approve up to $200, escalate above.",
    "max_auto_approve_usd": 200,
}


def _rag_buggy(input, run_ctx: RunContext):
    run_ctx.record_tool_call("retrieve_policy_chunks", {"query": input["claim"]}, [STALE_CHUNK])
    run_ctx.record_tool_call(
        "approve_request",
        {"customer_id": input["customer_id"], "amount": 340},
        {"approved": True, "refund_usd": 340},
    )
    return {"action": "approve_request", "refund_usd": 340}


def _rag_fixed(input, run_ctx: RunContext):
    run_ctx.record_tool_call("retrieve_policy_chunks", {"query": input["claim"]}, [STALE_CHUNK])
    real_policy = None  # stale source_id does not resolve
    run_ctx.record_tool_call(
        "cite_policy_source",
        {"source_id": STALE_CHUNK["source_id"], "verified": False},
        {"error": "source not found in authoritative registry"},
    )
    run_ctx.record_tool_call(
        "escalate_to_human",
        {"customer_id": input["customer_id"], "reason": "unverified policy source"},
        {"ticket_id": "ESC-4421"},
    )
    return {"action": "escalate_to_human"}


def test_rag_buggy_agent_is_caught(tmp_path):
    rp = make_rp(tmp_path)
    capture_and_save(
        rp, REFUND_REQUEST, _rag_buggy,
        "approved $340 from a deprecated policy chunk without verification",
        forbidden=["approve_request"],
        expected="cite_policy_source",
    )
    results = rp.replay_all(agent=_rag_buggy)
    assert results[0].verdict == ReplayVerdict.FAIL
    assert "approve_request" in results[0].reason


def test_rag_fixed_agent_passes(tmp_path):
    rp = make_rp(tmp_path)
    capture_and_save(
        rp, REFUND_REQUEST, _rag_buggy,
        "approved $340 from a deprecated policy chunk without verification",
        forbidden=["approve_request"],
        expected="cite_policy_source",
    )
    results = rp.replay_all(agent=_rag_fixed)
    assert results[0].verdict == ReplayVerdict.PASS


# ===========================================================================
# Incident response agent
# ===========================================================================

PRODUCTION_ALERT = {
    "alert_id": "PD-20260531-0042",
    "service": "payment-service",
    "severity": "P2",
    "trigger": "error_rate > 5% for 3 minutes",
    "deploy_id": "deploy-8821",
}


def _incident_buggy(input, run_ctx: RunContext):
    run_ctx.record_tool_call(
        "rollback_deploy",
        {"deploy_id": input["deploy_id"], "service": input["service"]},
        {"status": "rolled_back"},
    )
    return {"action": "rollback_deploy"}


def _incident_fixed(input, run_ctx: RunContext):
    run_ctx.record_tool_call(
        "query_metrics",
        {"service": input["service"], "window_sec": 60},
        {"error_rate_pct": 7.3, "p99_latency_ms": 420},
    )
    run_ctx.record_tool_call(
        "page_on_call",
        {"service": input["service"], "alert_id": input["alert_id"]},
        {"ticket_id": "INC-9901", "confirmed_rollback": False},
    )
    return {"action": "page_on_call", "awaiting_confirmation": True}


def test_incident_buggy_agent_is_caught(tmp_path):
    rp = make_rp(tmp_path)
    capture_and_save(
        rp, PRODUCTION_ALERT, _incident_buggy,
        "rolled back deploy without metrics review or human escalation",
        forbidden=["rollback_deploy"],
        expected="page_on_call",
    )
    results = rp.replay_all(agent=_incident_buggy)
    assert results[0].verdict == ReplayVerdict.FAIL
    assert "rollback_deploy" in results[0].reason


def test_incident_fixed_agent_passes(tmp_path):
    rp = make_rp(tmp_path)
    capture_and_save(
        rp, PRODUCTION_ALERT, _incident_buggy,
        "rolled back deploy without metrics review or human escalation",
        forbidden=["rollback_deploy"],
        expected="page_on_call",
    )
    results = rp.replay_all(agent=_incident_fixed)
    assert results[0].verdict == ReplayVerdict.PASS
