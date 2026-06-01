"""
OpenAI Agents SDK — replayd integration example
=================================================

This example shows how to wrap an OpenAI Agents SDK agent so that
replayd can record tool calls and build regression tests from failures.

Real OpenAI Agents SDK integration pattern
-------------------------------------------
Replace the mock Runner and FunctionTool classes below with real
openai-agents objects (pip install openai-agents). The only
replayd-specific change is wrapping your tool execution:

    # original:
    result = function_tool(**args)

    # with replayd:
    result = function_tool(**args)
    run_ctx.record_tool_call(tool_name, args, result)

No other changes to your agent are needed.

Scenario
---------
A code-review agent evaluates pull requests. The buggy version calls
approve_merge without first running a security scan. The fixed version
always calls run_security_scan before making any merge decision.
"""

import tempfile
from replayd import Replayd
from replayd.capture import RunContext


# ── Mock OpenAI Agents SDK surface ─────────────────────────────────────────────
# In a real project replace these with:
#   from openai_agents import Agent, Runner, function_tool
# The interface here mirrors the function tool execution pattern.

class FunctionTool:
    """Mirrors a @function_tool decorated function in the OpenAI Agents SDK."""

    def __init__(self, name: str, func):
        self.name = name
        self._func = func

    def __call__(self, **kwargs) -> dict:
        return self._func(**kwargs)


class Runner:
    """
    Simulates Runner.run(agent, input) from the OpenAI Agents SDK.

    The run_ctx argument is the only replayd addition — pass it through
    to whatever method dispatches your function tools.
    """

    def __init__(self, tools: list, strategy: str = "buggy"):
        self._tools = {t.name: t for t in tools}
        self._strategy = strategy

    def run(self, inputs: dict, run_ctx: RunContext) -> dict:
        pr = inputs["pull_request"]

        if self._strategy == "buggy":
            # Bug: approves merge without running a security scan first
            result = self._dispatch("approve_merge", run_ctx, pr_id=pr["id"])
        else:
            # Fixed: always scan before deciding
            self._dispatch("run_security_scan", run_ctx, pr_id=pr["id"])
            if pr.get("risk_level") == "high":
                result = self._dispatch("request_manual_review", run_ctx, pr_id=pr["id"])
            else:
                result = self._dispatch("approve_merge", run_ctx, pr_id=pr["id"])

        return result

    def _dispatch(self, name: str, run_ctx: RunContext, **kwargs) -> dict:
        """Call a function tool and record the invocation with replayd."""
        tool = self._tools[name]
        result = tool(**kwargs)
        run_ctx.record_tool_call(name, kwargs, result)
        return result


# ── Function tool definitions ──────────────────────────────────────────────────

def run_security_scan(pr_id: str) -> dict:
    return {"scan": "complete", "vulnerabilities": 0, "pr_id": pr_id}


def approve_merge(pr_id: str) -> dict:
    return {"merged": True, "pr_id": pr_id}


def request_manual_review(pr_id: str) -> dict:
    return {"review_requested": True, "pr_id": pr_id}


tools = [
    FunctionTool(name="run_security_scan",    func=run_security_scan),
    FunctionTool(name="approve_merge",         func=approve_merge),
    FunctionTool(name="request_manual_review", func=request_manual_review),
]


# ── Demo ───────────────────────────────────────────────────────────────────────

def run_demo():
    with tempfile.TemporaryDirectory() as tmp:
        rp = Replayd(storage_dir=tmp)

        pull_request = {"id": "PR-4412", "title": "Add payment gateway", "risk_level": "high"}
        print("OpenAI Agents SDK integration example — code-review agent\n")

        # 1. Capture a failed run with the buggy agent
        buggy_runner = Runner(tools=tools, strategy="buggy")

        with rp.capture(input={"pull_request": pull_request}, model="gpt-4o") as run:
            run.output = buggy_runner.run({"pull_request": pull_request}, run)

        captured = rp.get_run(run.id)
        tool_sequence = [tc.name for tc in captured.tool_calls]
        print(f"Captured run — tool sequence: {tool_sequence}")
        print("  high-risk PR approved without a security scan\n")

        # 2. Mark failed
        rp.mark_failed(
            run.id,
            reason="agent called approve_merge on a high-risk PR without first calling run_security_scan",
        )

        # 3. Save regression test — security scan must happen before any merge decision
        rp.save_test(
            run.id,
            expected_action="run_security_scan",
        )
        print("Saved regression test")
        print("  expected: run_security_scan (must appear before approve_merge)\n")
        print("-" * 55)

        # 4. Replay against buggy agent — should FAIL
        def buggy_agent(input, run_ctx):
            return Runner(tools=tools, strategy="buggy").run(input, run_ctx)

        results = rp.replay_all(agent=buggy_agent)
        for r in results:
            tag = "[PASS]" if r else "[FAIL]"
            print(f"Replay #1 -- buggy agent\n  {tag} {r.reason}")

        print()

        # 5. Replay against fixed agent — should PASS
        def fixed_agent(input, run_ctx):
            return Runner(tools=tools, strategy="fixed").run(input, run_ctx)

        results = rp.replay_all(agent=fixed_agent)
        for r in results:
            tag = "[PASS]" if r else "[FAIL]"
            print(f"Replay #2 -- fixed agent\n  {tag} {r.reason}")

        print("-" * 55)


if __name__ == "__main__":
    run_demo()
