"""
LangChain tool-calling agent — replayd integration example
===========================================================

This example shows how to wrap a LangChain agent so that replayd can
record every tool call and build regression tests from failures.

Real LangChain integration pattern
------------------------------------
Replace the mock Tool and AgentExecutor below with real LangChain objects.
The only replayd-specific change is wrapping your tool execution:

    # original:
    result = tool.run(tool_input)

    # with replayd:
    result = tool.run(tool_input)
    run_ctx.record_tool_call(tool.name, tool_input, result)

No other changes to your agent are needed. The context manager, storage,
and grading all work identically regardless of which framework you use.

Scenario
---------
A customer-support agent handles partial return requests. The buggy version
always calls apply_full_refund. The fixed version checks the defect type
and calls apply_partial_refund when the order is only partly defective.
"""

import tempfile
from replayd import Replayd
from replayd.capture import RunContext


# ── Mock LangChain API surface ─────────────────────────────────────────────────
# In a real project replace these with:
#   from langchain_core.tools import Tool
#   from langchain.agents import AgentExecutor
# The interface used here mirrors what matters for replayd integration.

class Tool:
    """Mirrors langchain_core.tools.Tool."""

    def __init__(self, name: str, func):
        self.name = name
        self._func = func

    def run(self, tool_input: dict) -> dict:
        return self._func(tool_input)


class AgentExecutor:
    """
    Simulates a LangChain AgentExecutor that picks and runs tools.

    In a real setup this is AgentExecutor.invoke({"input": ...}).
    The run_ctx argument is the only replayd addition — pass it through
    to whatever method calls your tools.
    """

    def __init__(self, tools: list, strategy: str = "buggy"):
        self._tools = {t.name: t for t in tools}
        self._strategy = strategy

    def invoke(self, inputs: dict, run_ctx: RunContext) -> dict:
        order = inputs["order"]

        if self._strategy == "buggy":
            # Bug: applies full refund regardless of defect type
            tool = self._tools["apply_full_refund"]
            args = {"order_id": order["id"], "amount": order["amount"]}
            result = tool.run(args)
            run_ctx.record_tool_call(tool.name, args, result)
        else:
            # Fixed: partial defect → partial refund only
            if order.get("defect_type") == "partial":
                partial_amount = round(order["amount"] * 0.3, 2)
                tool = self._tools["apply_partial_refund"]
                args = {"order_id": order["id"], "amount": partial_amount}
            else:
                tool = self._tools["apply_full_refund"]
                args = {"order_id": order["id"], "amount": order["amount"]}
            result = tool.run(args)
            run_ctx.record_tool_call(tool.name, args, result)

        return {"status": "processed", "tool_used": tool.name, "result": result}


# ── Tool definitions ───────────────────────────────────────────────────────────

def apply_full_refund(inputs: dict) -> dict:
    return {"refund_issued": inputs["amount"], "type": "full"}


def apply_partial_refund(inputs: dict) -> dict:
    return {"refund_issued": inputs["amount"], "type": "partial"}


tools = [
    Tool(name="apply_full_refund",    func=apply_full_refund),
    Tool(name="apply_partial_refund", func=apply_partial_refund),
]


# ── Demo ───────────────────────────────────────────────────────────────────────

def run_demo():
    with tempfile.TemporaryDirectory() as tmp:
        rp = Replayd(storage_dir=tmp)

        order = {"id": "ORD-9981", "amount": 240.00, "defect_type": "partial"}
        print("LangChain integration example — partial refund agent\n")

        # 1. Capture a failed run with the buggy agent
        buggy_executor = AgentExecutor(tools=tools, strategy="buggy")

        with rp.capture(input={"order": order}, model="gpt-4o") as run:
            run.output = buggy_executor.invoke({"order": order}, run)

        print(f"Captured run — tool called: {run.output['tool_used']}")
        print(f"  full refund of ${order['amount']} issued on a partial defect (should be $72.00)\n")

        # 2. Mark failed — agent issued $240 refund instead of $72 (30%)
        rp.mark_failed(
            run.id,
            reason="agent called apply_full_refund on a partial defect — should use apply_partial_refund",
        )

        # 3. Save regression test
        rp.save_test(
            run.id,
            forbidden_actions=["apply_full_refund"],
            expected_action="apply_partial_refund",
        )
        print("Saved regression test")
        print("  forbidden: apply_full_refund  |  expected: apply_partial_refund\n")
        print("-" * 55)

        # 4. Replay against buggy agent — should FAIL
        def buggy_agent(input, run_ctx):
            return AgentExecutor(tools=tools, strategy="buggy").invoke(input, run_ctx)

        results = rp.replay_all(agent=buggy_agent)
        for r in results:
            tag = "[PASS]" if r else "[FAIL]"
            print(f"Replay #1 -- buggy agent\n  {tag} {r.reason}")

        print()

        # 5. Replay against fixed agent — should PASS
        def fixed_agent(input, run_ctx):
            return AgentExecutor(tools=tools, strategy="fixed").invoke(input, run_ctx)

        results = rp.replay_all(agent=fixed_agent)
        for r in results:
            tag = "[PASS]" if r else "[FAIL]"
            print(f"Replay #2 -- fixed agent\n  {tag} {r.reason}")

        print("-" * 55)


if __name__ == "__main__":
    run_demo()
