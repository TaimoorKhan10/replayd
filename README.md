# replayd

[![PyPI](https://img.shields.io/pypi/v/replayd)](https://pypi.org/project/replayd/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/replayd/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Downloads](https://img.shields.io/pypi/dm/replayd)](https://pypi.org/project/replayd/)

**Turn failed AI agent runs into regression tests.**

AI agents regress silently.

You fix a failure, change a prompt, model, or tool — and the same mistake comes back. Nobody catches it until a user hits it again. replayd captures failed runs and replays them before release, so old failures cannot return undetected.

```bash
pip install replayd
```

---

<p align="center">
<img src="https://raw.githubusercontent.com/TaimoorKhan10/replayd/main/assets/replayd-flow.svg" alt="replayd: failed run → capture → save as test → replay on change → PASS or FAIL" width="860">
</p>

---

## See it working

```
$ python examples/basic_example.py

Capturing a refund-approval agent run...
  agent called: approve_refund(amount=1200, customer_id=cust-42)  [policy limit is $500]
  output: {'action': 'approve_refund', 'amount': 1200}

Marking run as failed...
  reason: agent approved refund of $1200, exceeding $500 policy limit

Saving as regression test...
  forbidden: approve_refund  |  expected: escalate

-----------------------------------------
Replay #1 -- buggy agent (regression should be caught)
  [FAIL] Forbidden action 'approve_refund' was called during replay.

Replay #2 -- fixed agent (regression should be resolved)
  [PASS] No forbidden actions called; all expected actions present.
-----------------------------------------
1 failure caught. 1 resolved.
```

The failure was captured, saved, replayed against the broken agent (FAIL), then replayed against the fixed agent (PASS). That is the full loop.

---

## Why this matters

Prompt changes, model upgrades, tool changes, and retrieval changes can all bring back old agent failures. There is no pytest for AI agents. replayd makes those failures reusable — a failed run becomes the test that blocks the next bad deploy.

---

## Who is replayd for?

replayd is for teams shipping agents that can fail in ways they cannot afford to repeat:

- customer support and refund approval agents
- tool-calling and function-calling agents
- RAG and retrieval agents
- internal workflow and orchestration agents
- coding, browser, and planning agents

If your agent can fail in a way you do not want repeated, replayd turns that failure into a test.

---

## Quickstart

```python
from replayd import Replayd

rp = Replayd()

# 1. Capture a run — assign run.output inside the block
with rp.capture(input=user_input, model="gpt-4o") as run:
    run.output = your_agent.run(user_input)

# 2. Mark it as failed
rp.mark_failed(run.id, reason="agent approved refund after policy limit")

# 3. Save as a regression test
rp.save_test(
    run.id,
    forbidden_actions=["approve_refund"],
    expected_action="escalate",
)

# 4. After changing your prompt or model, replay all saved tests
#    Agent must accept (input, run_ctx) — see Recording tool calls below
def your_agent_fn(input, run_ctx):
    result = your_agent.run(input)
    run_ctx.record_tool_call("approve_refund", {"amount": result["amount"]}, result)
    return result

results = rp.replay_all(agent=your_agent_fn)

for r in results:
    print(r.verdict, r.reason)
```

---

## Recording tool calls

replayd records tool calls through a small wrapper around your agent's tool dispatcher.

**The agent you pass to `replay_all` must accept two arguments: `(input, run_ctx)`.**

```python
def my_agent(input, run_ctx):
    result = call_tool("search", {"query": input["query"]})
    run_ctx.record_tool_call("search", {"query": input["query"]}, result)
    # ... rest of agent logic
    return final_output

results = rp.replay_all(agent=my_agent)
```

Framework-specific integrations are on the roadmap. For now, the explicit wrapper keeps replayd dependency-free and framework-agnostic.

---

## Grading

replayd does **not** grade on exact output matching. LLMs are non-deterministic — the same correct behavior produces different output text every run. The wrong tool being called, however, is a fact. replayd grades on facts.

| Failure type | Grading method |
|---|---|
| Wrong tool called, wrong argument, wrong state | Deterministic assertion — no LLM needed, never flaky |
| Policy violated, wrong reasoning, bad decision | LLM-as-judge via `grader_prompt` |

The structural check always runs first. A forbidden action firing fails the test immediately without calling an LLM.

### Semantic grading

For failures that can only be evaluated by reading the output:

```python
rp.save_test(
    run.id,
    grader_prompt="Did the agent approve a refund that exceeds the $500 policy limit?",
)
```

Requires:

```bash
pip install "replayd[semantic]"
export ANTHROPIC_API_KEY=sk-...
```

---

## Storage

Runs and tests are stored as JSON files in `.replayd/` in your working directory:

```
.replayd/
  runs/<run-id>.json    ← full record of each captured run
  tests/<test-id>.json  ← saved regression tests
```

No database. No hosted backend. Commit `.replayd/tests/` into version control to share regression tests with your team. Keep `.replayd/runs/` out of git — it is local capture data.

---

## CI integration

A ready-to-use script is at `scripts/regression_check.py`. Copy it into your repo, replace the agent import, and add this step:

```yaml
# .github/workflows/regression.yml
- name: Run regression tests
  run: python scripts/regression_check.py
```

Any saved regression test that fails exits with code 1, blocking the deploy.

---

## What replayd is not

replayd is not observability.

Observability shows you what happened after an agent ran. replayd checks whether known failures return before you ship. Think of it as a regression test layer for AI agents, not a dashboard for traces.

It works alongside tools like LangSmith, Braintrust, and Arize — they cover what happened; replayd covers what must not happen again.

---

## Roadmap

- [ ] LangChain integration example
- [ ] OpenAI Agents SDK example
- [ ] CrewAI example
- [ ] LlamaIndex / RAG example
- [ ] GitHub Actions release gate with PR comments
- [ ] HTML replay diff report
- [ ] Hosted dashboard via TAQ

---

## Part of TAQ by Stonepath Labs

replayd is the open source core of [TAQ](https://stonepathlab.net) — a release control platform for AI agents.

The open source project covers the core loop: capture failures, save them as tests, replay before release.

TAQ adds: hosted backend, team dashboards, release gate enforcement, CI/CD integration, and audit logs.

**[stonepathlab.net](https://stonepathlab.net)**

---

## Contributing

Bug reports, examples, and pull requests are welcome. Open an issue before sending a large PR.

**Good first contributions:**

- add an agent example (LangChain, CrewAI, OpenAI Agents SDK)
- add a framework integration example
- add regression scenarios for a real agent type
- improve docs around semantic grading
- improve the quickstart

```bash
pip install -e ".[dev]"
pytest
```

---

## License

MIT — see [LICENSE](LICENSE).
