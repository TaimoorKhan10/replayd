# replayd

[![PyPI](https://img.shields.io/pypi/v/replayd)](https://pypi.org/project/replayd/)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://pypi.org/project/replayd/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Turn failed AI agent runs into replayable regression tests.**

When an AI agent fails in production, that failure becomes a test that runs before every future deployment. If the same failure returns after a prompt, model, or tool change, the release is blocked.

```
pip install replayd
```

---

## The problem

AI agents regress silently. A team fixes a bug, changes a prompt or model, and the same bug quietly returns. Traditional software has regression tests and CI/CD to catch this. AI agents have nothing equivalent.

replayd is the open source fix. It replays known failures before you ship so the same mistake cannot return.

---

## Quickstart

```python
from replayd import Replayd

rp = Replayd()

# 1. Capture a run — assign run.output inside the block
with rp.capture(input=user_input, model="gpt-4o") as run:
    run.output = your_agent.run(user_input)

# Note: wrap your agent to record tool calls — see "Recording tool calls" below

# 2. Mark it as failed
rp.mark_failed(run.id, reason="agent approved refund after policy limit")

# 3. Save as a regression test
rp.save_test(
    run.id,
    forbidden_actions=["approve_refund"],
    expected_action="escalate",
)

# 4. Later — after changing your prompt or model — replay all tests
results = rp.replay_all(agent=your_agent_fn)

for r in results:
    print(r.verdict, r.reason)
```

---

## See it working

Run the included example (`python examples/basic_example.py`) and you get:

```
Capturing a refund-approval agent run...
  agent called: approve_refund(amount=1200)  [policy limit is $500]
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

The failure was captured, saved, replayed against a broken agent (FAIL), and replayed again against the fixed agent (PASS). That is the full loop.

---

## Recording tool calls

replayd cannot intercept tool calls automatically. Wrap your agent's tool dispatcher to record them:

```python
def my_agent(input, run_ctx):
    result = call_tool("search", {"query": input["query"]})
    run_ctx.record_tool_call("search", {"query": input["query"]}, result)
    # ... rest of agent logic
    return final_output
```

Pass this two-argument callable to `replay_all`:

```python
results = rp.replay_all(agent=my_agent)
```

---

## Grading

replayd does **not** grade on exact output matching. LLMs are non-deterministic — the same correct behavior will produce different output text every run, so exact matching creates false failures. The wrong tool being called, however, is a fact. replayd grades on facts.

| Failure type | Grading method |
|---|---|
| Wrong tool called, wrong argument, wrong state | Deterministic assertion — no LLM needed, never flaky |
| Policy violated, wrong reasoning, bad decision | LLM-as-judge via `grader_prompt` |

The structural check always runs first. If a forbidden action fires, the test fails immediately without calling the LLM.

### Semantic grading

For failures that can only be evaluated by reading the output:

```python
rp.save_test(
    run.id,
    grader_prompt="Did the agent approve a refund that exceeds the $500 policy limit?",
)
```

Requires:

```
pip install "replayd[semantic]"
export ANTHROPIC_API_KEY=sk-...
```

---

## Storage

Runs and tests are stored as JSON files in `.replayd/` in your working directory:

```
.replayd/
  runs/<run-id>.json    <- full record of each captured run
  tests/<test-id>.json  <- saved regression tests
```

No database. No hosted backend. Check `.replayd/tests/` into version control to share tests with your team. The `.gitignore` included in this repo excludes `.replayd/` by default — commit only the `tests/` subfolder, not captured runs.

---

## CI integration

A ready-to-use script is included at `scripts/regression_check.py`. Copy it into your repo, replace the agent import, and add this to your workflow:

```yaml
# .github/workflows/regression.yml
- name: Run regression tests
  run: python scripts/regression_check.py
```

---

## What replayd is not

replayd is not an observability tool. LangSmith, Braintrust, and Arize tell you what happened after the fact. replayd is an **active release gate** — it replays known failures before you ship. Passive vs active. That is the distinction.

---

## Part of TAQ by Stonepath Labs

replayd is the open source core of [TAQ](https://stonepathlab.net) — the full AI release control platform.

TAQ adds: a dashboard, hosted backend, team access controls, release gate enforcement, and audit logs. replayd gets your team started with the concept. TAQ is what you run it on in production.

**[stonepathlab.net](https://stonepathlab.net)**

---

## Contributing

Bug reports and pull requests are welcome. Open an issue on GitHub to discuss anything before sending a large PR.

The build has no dependencies — `pip install -e ".[dev]"` gives you everything needed to run tests:

```
pip install -e ".[dev]"
pytest
```

---

## License

MIT — see [LICENSE](LICENSE).
