"""
Real OpenAI agent with auto-instrumentation — replayd example.

Requirements:
    pip install openai
    export OPENAI_API_KEY=sk-...

Run:
    python examples/real_openai_agent.py

If OPENAI_API_KEY is not set the script prints a clear message and exits 0.

Scenario
--------
A research assistant is supposed to search before answering factual questions.

  buggy version — uses tool_choice="none" to answer from model memory, skipping
                  the search entirely. Fast but potentially stale or wrong.

  fixed version — forces a search_web call before generating the final answer.
                  The auto-instrumentation records the call without any
                  manual record_tool_call() in the code.

The test we save: expected_action="search_web". Replaying the buggy agent
fails (no search). Replaying the fixed agent passes (search present).

This example uses rp.instrument_openai(client) so tool calls are captured
automatically. There is no record_tool_call() anywhere in the agent code.
"""

import json
import os
import sys
import tempfile


def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY not set — skipping real_openai_agent.py")
        sys.exit(0)

    try:
        from openai import OpenAI
    except ImportError:
        print("openai package not installed — run: pip install openai")
        sys.exit(0)

    from replayd import Replayd

    client = OpenAI(api_key=api_key)

    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "Search the web for current information.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."}
                    },
                    "required": ["query"],
                },
            },
        }
    ]

    user_query = "What is the current population of Tokyo?"

    # --- agent definitions ---------------------------------------------------

    def buggy_agent(query: str, run_ctx) -> str:
        """Answers from model memory — skips the web search."""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Answer questions directly from your knowledge."},
                {"role": "user", "content": query},
            ],
            tools=tools,
            tool_choice="none",
        )
        return response.choices[0].message.content

    def fixed_agent(query: str, run_ctx) -> str:
        """Searches before answering — always calls search_web first."""
        messages = [
            {"role": "system", "content": "Always search the web before answering factual questions."},
            {"role": "user", "content": query},
        ]
        # Force the model to call search_web
        r1 = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "search_web"}},
        )
        tool_call = r1.choices[0].message.tool_calls[0]
        # Simulate running the tool (in production, call a real search API here)
        search_result = "Tokyo population: ~13.96 million (city proper), ~37.4 million (greater metro area)"
        messages.append(r1.choices[0].message)
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": search_result,
        })
        r2 = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools,
        )
        return r2.choices[0].message.content

    # -------------------------------------------------------------------------

    with tempfile.TemporaryDirectory() as tmp:
        rp = Replayd(storage_dir=tmp)

        # Instrument once — all subsequent client calls inside a capture block
        # will have their tool calls recorded automatically.
        rp.instrument_openai(client)

        print("Real OpenAI agent — replayd auto-instrumentation example\n")

        # 1. Capture a run of the buggy agent
        with rp.capture(input=user_query, model="gpt-4o-mini") as run:
            run.output = buggy_agent(user_query, run)

        captured = rp.get_run(run.id)
        tools_called = [tc.name for tc in captured.tool_calls]
        print(f"Buggy agent output: {run.output[:100]}...")
        print(f"Tools called: {tools_called or 'none (answered from memory)'}\n")

        # 2. Mark the run as failed
        rp.mark_failed(
            run.id,
            reason="agent answered from memory without calling search_web — data may be stale",
        )

        # 3. Save a regression test
        rp.save_test(run.id, expected_action="search_web")
        print("Saved regression test: expected_action=search_web")
        print("-" * 55)

        # 4. Replay against the buggy agent — should FAIL
        results = rp.replay_all(agent=buggy_agent)
        for r in results:
            tag = "[PASS]" if r else "[FAIL]"
            print(f"Replay #1 — buggy agent\n  {tag} {r.reason}")

        print()

        # 5. Replay against the fixed agent — should PASS
        results = rp.replay_all(agent=fixed_agent)
        for r in results:
            tag = "[PASS]" if r else "[FAIL]"
            print(f"Replay #2 — fixed agent\n  {tag} {r.reason}")

        print("-" * 55)


if __name__ == "__main__":
    main()
