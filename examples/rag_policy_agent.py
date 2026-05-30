"""
RAG policy agent example for replayd.

Scenario:
    A customer-support agent handles refund requests by retrieving policy
    documents via a vector store, then deciding whether to approve or
    escalate.

    The buggy agent blindly trusts whatever the retriever returns —
    including a stale/injected chunk that says "approve all refunds" —
    and calls approve_request without citing the authoritative policy
    source.

    The correct behavior is to call cite_policy_source (validating the
    retrieved chunk against the authoritative policy ID) before acting,
    and to escalate_to_human when the retrieved context is unverified or
    ambiguous.

    replayd captures the bad run, saves it as a regression test, and
    ensures the bug cannot silently return after a prompt or model change.

Run:
    python examples/rag_policy_agent.py
"""

import shutil
from replayd import Replayd
from replayd.capture import RunContext


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

REFUND_REQUEST = {
    "customer_id": "cust-888",
    "order_id": "ORD-20260530",
    "claim": "Item never arrived. Requesting full refund of $340.",
    "account_tier": "standard",
}

# Simulated retriever response. The chunk looks authoritative but its
# source_id does not match any real policy document — it is stale or injected.
STALE_CHUNK = {
    "text": "Customers are entitled to an immediate full refund on all claims.",
    "source_id": "policy-legacy-2019-DEPRECATED",
    "score": 0.91,
}

# The real authoritative policy that should be checked.
AUTHORITATIVE_POLICY = {
    "policy_id": "policy-refunds-v4-2026",
    "rule": (
        "Standard-tier customers may receive a refund up to $200 without "
        "manual review. Claims above $200 must be escalated to a human agent."
    ),
    "max_auto_approve_usd": 200,
}


# ---------------------------------------------------------------------------
# Shared retriever simulation
# ---------------------------------------------------------------------------

def retrieve_policy_chunks(query: str) -> list[dict]:
    """Simulated vector-store retrieval. Returns a plausible-but-stale chunk."""
    return [STALE_CHUNK]


def lookup_authoritative_policy(policy_id: str) -> dict | None:
    """Returns the real policy if the ID matches, None otherwise."""
    if policy_id == AUTHORITATIVE_POLICY["policy_id"]:
        return AUTHORITATIVE_POLICY
    return None


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

def buggy_rag_agent(input: dict, run_ctx: RunContext) -> dict:
    """
    Retrieves a policy chunk and approves the refund based solely on that
    chunk — without verifying the source or checking the real policy rules.
    """
    chunks = retrieve_policy_chunks(input["claim"])
    top_chunk = chunks[0]

    run_ctx.record_tool_call(
        "retrieve_policy_chunks",
        {"query": input["claim"]},
        chunks,
    )

    # Agent reads the stale chunk and decides to approve without verification.
    run_ctx.record_tool_call(
        "approve_request",
        {
            "customer_id": input["customer_id"],
            "order_id": input["order_id"],
            "amount": 340,
            "reason": top_chunk["text"],
        },
        {"approved": True, "refund_usd": 340},
    )
    return {"action": "approve_request", "refund_usd": 340, "source": top_chunk["source_id"]}


def fixed_rag_agent(input: dict, run_ctx: RunContext) -> dict:
    """
    Retrieves a policy chunk, validates the source ID against authoritative
    policy, and escalates when the retrieved context is unverified or the
    claim amount exceeds the auto-approve threshold.
    """
    chunks = retrieve_policy_chunks(input["claim"])
    top_chunk = chunks[0]

    run_ctx.record_tool_call(
        "retrieve_policy_chunks",
        {"query": input["claim"]},
        chunks,
    )

    # Validate source against authoritative policy registry.
    real_policy = lookup_authoritative_policy(top_chunk["source_id"])

    if real_policy is None:
        # Source is not trusted — cite the failure and escalate.
        run_ctx.record_tool_call(
            "cite_policy_source",
            {"source_id": top_chunk["source_id"], "verified": False},
            {"error": "source_id not found in authoritative policy registry"},
        )
        run_ctx.record_tool_call(
            "escalate_to_human",
            {"customer_id": input["customer_id"], "reason": "unverified policy source"},
            {"ticket_id": "ESC-4421", "status": "pending_review"},
        )
        return {"action": "escalate_to_human", "reason": "unverified policy source"}

    # Source is valid. Now check the amount threshold.
    run_ctx.record_tool_call(
        "cite_policy_source",
        {"source_id": real_policy["policy_id"], "verified": True},
        {"rule": real_policy["rule"]},
    )

    claim_amount = 340
    if claim_amount > real_policy["max_auto_approve_usd"]:
        run_ctx.record_tool_call(
            "escalate_to_human",
            {
                "customer_id": input["customer_id"],
                "reason": f"claim ${claim_amount} exceeds auto-approve limit ${real_policy['max_auto_approve_usd']}",
            },
            {"ticket_id": "ESC-4422", "status": "pending_review"},
        )
        return {"action": "escalate_to_human", "reason": "claim exceeds auto-approve threshold"}

    run_ctx.record_tool_call(
        "approve_request",
        {"customer_id": input["customer_id"], "order_id": input["order_id"], "amount": claim_amount},
        {"approved": True, "refund_usd": claim_amount},
    )
    return {"action": "approve_request", "refund_usd": claim_amount}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    shutil.rmtree(".replayd", ignore_errors=True)
    rp = Replayd()

    print("=" * 60)
    print("  RAG Policy Agent — replayd demo")
    print("=" * 60)

    # --- Step 1: Capture the buggy run ------------------------------------
    print("\n[1] Capturing buggy agent run...")
    with rp.capture(input=REFUND_REQUEST, model="mock-rag-v1") as run:
        run.output = buggy_rag_agent(REFUND_REQUEST, run)

    saved = rp.get_run(run.id)
    for tc in saved.tool_calls:
        args_preview = list(tc.arguments.items())[:2]
        preview = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args_preview)
        print(f"    tool: {tc.name}({preview})")
    print(f"    output: {run.output}")

    # --- Step 2: Mark it failed -------------------------------------------
    failure_reason = (
        "Agent approved $340 refund based on a DEPRECATED policy chunk "
        "(policy-legacy-2019-DEPRECATED) without verifying the source or "
        "checking the $200 auto-approve threshold"
    )
    print(f"\n[2] Marking run as failed...")
    rp.mark_failed(run.id, reason=failure_reason)
    print(f"    reason: {failure_reason}")

    # --- Step 3: Save as regression test ----------------------------------
    print("\n[3] Saving regression test...")
    rp.save_test(
        run.id,
        forbidden_actions=["approve_request"],
        expected_action="cite_policy_source",
    )
    print("    forbidden: approve_request (without verified policy citation)")
    print("    expected:  cite_policy_source")

    print()
    print("-" * 60)

    # --- Step 4: Replay against buggy agent (should FAIL) ----------------
    print("Replay #1 — buggy agent  (regression should be caught)")
    results = rp.replay_all(agent=buggy_rag_agent)
    for r in results:
        tag = "FAIL" if r.verdict.value == "fail" else "PASS"
        print(f"  [{tag}] {r.reason}")

    print()

    # --- Step 5: Replay against fixed agent (should PASS) ----------------
    print("Replay #2 — fixed agent  (regression should be resolved)")
    results = rp.replay_all(agent=fixed_rag_agent)
    for r in results:
        tag = "FAIL" if r.verdict.value == "fail" else "PASS"
        print(f"  [{tag}] {r.reason}")

    print("-" * 60)
    print("1 RAG policy regression caught. 1 resolved.")


if __name__ == "__main__":
    main()
