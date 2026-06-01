<p align="center">
  <img src="assets/banner.png" alt="replayd — The same AI failure should not happen twice" width="100%">
</p>

<p align="center">
  <a href="https://pypi.org/project/replayd/"><img src="https://img.shields.io/pypi/v/replayd?color=C08A3E&label=pypi" alt="PyPI"></a>
  <a href="https://pypi.org/project/replayd/"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="MIT License"></a>
  <a href="https://github.com/TaimoorKhan10/replayd/graphs/contributors"><img src="https://img.shields.io/github/contributors/TaimoorKhan10/replayd?color=3D7A5C" alt="Contributors"></a>
  <a href="https://github.com/TaimoorKhan10/replayd/stargazers"><img src="https://img.shields.io/github/stars/TaimoorKhan10/replayd?style=social" alt="Stars"></a>
</p>

<p align="center">
  <strong>You fixed that agent bug last week. It came back today.</strong><br>
  replayd makes sure that never happens again.
</p>
<p align="center">
  <code>pip install replayd</code>
</p>

## Table of contents
- [The problem](#the-problem)
- [How it works](#quickstart)
- [See it working](#see-it-working)
- [Why replayd](#why-replayd)
- [How replayd compares](#how-replayd-compares)
- [Example agents](#example-agents)
- [Recording tool calls](#recording-tool-calls)
- [Grading](#grading)
- [Storage](#storage)
- [CI integration](#ci-integration)
- [What replayd is not](#what-replayd-is-not)
- [What builders say](#what-builders-say)
- [Star goals](#star-goals)
- [Part of TAQ by Stonepath Labs](#part-of-taq-by-stonepath-labs)
- [Contributing](#contributing)
- [Star history](#star-history)

## The problem
| | Without replayd | With replayd |
|---|---|---|
| Agent fails in production | Fixed manually, forgotten | Saved as a replayable regression test |
| You change a prompt or model | Hope the old failure does not return | Replay proves it cannot return |
| Same bug comes back | Users catch it | Release is blocked before deploy |

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

## Why replayd
AI agents do not only fail once. They regress. You change a prompt, a model, a tool schema, or a retrieval setup, and something that used to work quietly breaks again. Traditional software has regression tests and CI/CD to catch this. AI agents have had nothing equivalent.

replayd is the open source fix. Failed runs become replayable tests. Old failures cannot return undetected.

## How replayd compares
| | replayd | LangSmith | Braintrust | Langfuse |
|---|---|---|---|---|
| Turns failed runs into regression tests | ✅ | Partial | Partial | ❌ |
| Replays known failures before deploy | ✅ | ❌ | ❌ | ❌ |
| Active release gate | ✅ | ❌ | Partial | ❌ |
| Zero runtime dependencies | ✅ | ❌ | ❌ | ❌ |
| Open source core | ✅ | ❌ | ❌ | ✅ |
| Framework agnostic | ✅ | ✅ | ✅ | ✅ |

replayd is not an alternative to observability tools. It works alongside them. LangSmith and Langfuse tell you what happened. replayd makes sure the worst things cannot happen again.

## Example agents
Three production-grade example agents are included. Run any of them with no API key required — all grading is structural.

| Agent | What it catches |
|---|---|
| `examples/multi_step_planning_agent.py` | Finalizing a plan without first calling `check_constraints` (budget, deadline, dependencies) |
| `examples/rag_policy_agent.py` | Approving a refund based on a deprecated policy chunk it should have ignored |
| `examples/incident_response_agent.py` | Running `rollback_deploy` without first paging a human via `escalate_to_human` |

Run them:

```bash
python examples/multi_step_planning_agent.py
python examples/rag_policy_agent.py
python examples/incident_response_agent.py
```

Each example shows FAIL on the buggy agent and PASS on the fixed agent.

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

## Storage

Runs and tests are stored as JSON files in `.replayd/` in your working directory:

```
.replayd/
  runs/<run-id>.json    <- full record of each captured run
  tests/<test-id>.json  <- saved regression tests
```

No database. No hosted backend. Check `.replayd/tests/` into version control to share tests with your team. The `.gitignore` included in this repo excludes `.replayd/` by default — commit only the `tests/` subfolder, not captured runs.

## CI integration

A ready-to-use script is included at `scripts/regression_check.py`. Copy it into your repo, replace the agent import, and add this to your workflow:

```yaml
# .github/workflows/regression.yml
- name: Run regression tests
  run: python scripts/regression_check.py
```

## What replayd is not
replayd is not an observability tool. LangSmith, Braintrust, and Arize tell you what happened after the fact. replayd is an **active release gate** — it replays known failures before you ship. Passive vs active. That is the distinction.

## What builders say
> "If something solved this it would definitely be worth paying for." — r/ycombinator

> "Replaying old failures against new prompts and models should be standard at this point. Otherwise the same bugs just keep coming back quietly." — r/LLMDevs

> "The capture step has too much friction. There's your next action item." — r/LLMDevs

## Star goals

[![GitHub Stars](https://img.shields.io/github/stars/TaimoorKhan10/replayd?style=social)](https://github.com/TaimoorKhan10/replayd/stargazers)

| Milestone | Stars |
|---|---|
| 🌱 Seedling | 50 |
| 🌿 Growing | 100 |
| 🚀 Momentum | 250 |
| 💫 Community | 500 |
| 🏆 Established | 1,000 |

Every star helps more builders find replayd. If it has saved you from a regression, star it.

## Part of TAQ by Stonepath Labs

replayd is the open source core of [TAQ](https://stonepathlab.net) — the full AI release control platform.

TAQ adds: a dashboard, hosted backend, team access controls, release gate enforcement, and audit logs. replayd gets your team started with the concept. TAQ is what you run it on in production.

**[stonepathlab.net](https://stonepathlab.net)**

## Contributing

Bug reports and pull requests are welcome. Open an issue on GitHub to discuss anything before sending a large PR.

The build has no dependencies — `pip install -e ".[dev]"` gives you everything needed to run tests:

```
pip install -e ".[dev]"
pytest
```

**Good first contributions:**
- Add a LangChain integration example
- Add a CrewAI example
- Add an OpenAI Agents SDK example
- Add regression scenarios for a real agent type
- Improve the getting started documentation

## Star history
[![Star History Chart](https://api.star-history.com/svg?repos=TaimoorKhan10/replayd&type=Date)](https://star-history.com/#TaimoorKhan10/replayd&Date)

## License

MIT — see [LICENSE](LICENSE).
