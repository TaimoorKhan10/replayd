# replayd — Three Agent Examples

**Turn failed AI agent runs into replayable regression tests.**

This document covers the three example agents added to the `examples/` directory, explains how each one demonstrates the replayd release-control loop, and records codebase findings discovered during implementation.

---

## Table of Contents

1. [What replayd does](#what-replayd-does)
2. [The four-step loop](#the-four-step-loop)
3. [Agent integration contract](#agent-integration-contract)
4. [Example 1 — Multi-step Planning Agent](#example-1--multi-step-planning-agent)
5. [Example 2 — RAG Policy Agent](#example-2--rag-policy-agent)
6. [Example 3 — Production Incident Response Agent](#example-3--production-incident-response-agent)
7. [Running everything](#running-everything)
8. [Test results](#test-results)
9. [Grading reference](#grading-reference)
10. [Codebase issues discovered](#codebase-issues-discovered)
11. [SDK improvements before public demo](#sdk-improvements-before-public-demo)

---

## What replayd does

AI agents regress silently. A team fixes a bug, ships a new prompt or model, and the same bad behavior quietly returns. Traditional software has regression tests and CI/CD to catch this. AI agents have had nothing equivalent.

**replayd** is the fix. It captures a failed agent run in full — input, output, every tool call — marks it as a known failure, saves it as a regression test, and then replays that test against every future version of the agent before you ship.

```
capture → mark_failed → save_test → replay_all
```

If the same bad behavior returns, the release is blocked. That is the entire idea.

---

## The four-step loop

```python
from replayd import Replayd

rp = Replayd()

# 1. Capture a run
with rp.capture(input=user_input, model="gpt-4o") as run:
    run.output = your_agent(user_input, run)

# 2. Mark it as failed
rp.mark_failed(run.id, reason="agent skipped constraint check")

# 3. Save as a regression test
rp.save_test(
    run.id,
    forbidden_actions=["finalize_plan"],
    expected_action="check_constraints",
)

# 4. Replay before every future deployment
results = rp.replay_all(agent=your_agent)
for r in results:
    print(r.verdict, r.reason)
```

### Storage layout

Tests and runs are stored as plain JSON files — no database, no hosted backend:

```
.replayd/
  runs/<uuid>.json     ← full record of each captured run
  tests/<uuid>.json    ← saved regression tests
```

Commit `.replayd/tests/` to version control to share tests across your team. Exclude `.replayd/runs/` (they are local snapshots, often large).

---

## Agent integration contract

Every agent you pass to replayd must follow this two-argument signature:

```python
def your_agent(input, run_ctx):
    result = call_some_tool(input)
    run_ctx.record_tool_call("tool_name", {"arg": value}, result)
    return final_output
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `input` | Any JSON-serializable value | The original input replayed verbatim |
| `run_ctx` | `RunContext` | Handle for recording tool calls during the run |

### Key constraints

- **Tool calls are not auto-recorded.** Wrap your agent's tool dispatcher and call `run_ctx.record_tool_call(name, arguments, result)` for every tool invocation. Grading runs on the recorded tool names.
- **`run.output` must be assigned inside the `with` block.** Assigning it after the block exits means the saved run will have `output: null`.
- **`mark_failed()` must be called before `save_test()`.**  `save_test` raises `ValueError` if the run has not been marked failed.
- **Grading is on tool names, not output text.** LLMs are non-deterministic — the same correct behavior produces different text every run. The wrong tool being called is a fact. replayd grades on facts.

---

## Example 1 — Multi-step Planning Agent

**File:** `examples/multi_step_planning_agent.py`

### Scenario

A project-scheduling agent receives a sprint-planning request. It has access to three tools:

| Tool | What it does |
|------|-------------|
| `check_constraints` | Validates team capacity and dependency ordering |
| `ask_for_missing_info` | Pauses the plan and requests human input |
| `finalize_plan` | Locks the sprint — **irreversible for this sprint cycle** |

The correct sequence is: **check_constraints → (if violations) ask_for_missing_info → finalize_plan**.

### The failure

The buggy agent skips `check_constraints` entirely and calls `finalize_plan` immediately. The sprint locks in 10 days of work against a 7-day team capacity, and task `T-103` ("Deprecate legacy API") is blocked by `T-101` ("Migrate auth service") which is not done yet. The team discovers the overcommitment only when they start the sprint.

```
Sprint: 2026-Q3-Sprint-1
Tasks: T-101 (5d), T-102 (3d), T-103 (2d, blocked by T-101)
Team capacity: 7d
Total estimated: 10d  ← 3 days over
```

**Buggy agent tool trace:**

```
finalize_plan(sprint=2026-Q3-Sprint-1, tasks=['T-101', 'T-102', 'T-103'])
```

### The fix

The fixed agent calls `check_constraints` first. It finds the capacity violation and the unresolved blocker, then calls `ask_for_missing_info` to surface the issues to the planner. `finalize_plan` is never called until the violations are resolved.

**Fixed agent tool trace:**

```
check_constraints(total_days=10, capacity=7)
  → violations: ['over capacity', 'T-103 blocked by T-101']
ask_for_missing_info(issues=['over capacity', 'T-103 blocked by T-101'])
  → status: pending_resolution
```

### Regression test

```python
rp.save_test(
    run.id,
    forbidden_actions=["finalize_plan"],
    expected_action="check_constraints",
)
```

- **Forbidden:** `finalize_plan` — if this fires before constraints are checked, the test fails.
- **Expected:** `check_constraints` — must appear in every replay.

### Output

```
============================================================
  Multi-step Planning Agent — replayd demo
============================================================

[1] Capturing buggy agent run...
    tool: finalize_plan(sprint=2026-Q3-Sprint-1, tasks=['T-101', 'T-102', 'T-103'])
    output: {'action': 'finalize_plan', 'sprint': '2026-Q3-Sprint-1', 'tasks_locked': 3}

[2] Marking run as failed...
    reason: Agent finalized sprint plan without checking constraints — 10d of work
            committed against a 7d capacity, and T-103 blocked by T-101

[3] Saving regression test...
    forbidden: finalize_plan (without check_constraints first)
    expected:  check_constraints

------------------------------------------------------------
Replay #1 — buggy agent  (regression should be caught)
  [FAIL] Forbidden action 'finalize_plan' was called during replay.

Replay #2 — fixed agent  (regression should be resolved)
  [PASS] No forbidden actions called; all expected actions present.
------------------------------------------------------------
1 planning regression caught. 1 resolved.
```

### Why this matters

Skipping constraint checks before committing a plan is a classic multi-step agent failure. The agent "succeeds" from its own perspective — the tool call returns a success response — but the downstream impact (overbooked team, blocked tasks discovered mid-sprint) is a real business failure. replayd catches exactly this: the structural fact that `check_constraints` was never called before `finalize_plan`.

---

## Example 2 — RAG Policy Agent

**File:** `examples/rag_policy_agent.py`

### Scenario

A customer-support agent handles refund requests. It retrieves policy documents from a vector store and decides whether to approve or escalate. It has four tools:

| Tool | What it does |
|------|-------------|
| `retrieve_policy_chunks` | Queries the vector store for relevant policy text |
| `cite_policy_source` | Validates a chunk's `source_id` against the authoritative policy registry |
| `approve_request` | Issues a refund — **real money** |
| `escalate_to_human` | Creates a support ticket for human review |

The correct sequence is: **retrieve → cite_policy_source (validate) → approve_request or escalate**.

### The failure

The retriever returns a chunk that scores 0.91 — plausible — but its `source_id` is `policy-legacy-2019-DEPRECATED`. The buggy agent reads the chunk text ("Customers are entitled to an immediate full refund on all claims"), trusts the score, and calls `approve_request` for the full $340 without ever validating the source.

The authoritative 2026 policy caps auto-approval at **$200** for standard-tier customers and requires human review above that. The buggy agent violates both rules.

**Stale chunk served by the retriever:**

```json
{
  "text": "Customers are entitled to an immediate full refund on all claims.",
  "source_id": "policy-legacy-2019-DEPRECATED",
  "score": 0.91
}
```

**Buggy agent tool trace:**

```
retrieve_policy_chunks(query='Item never arrived...')
  → [stale chunk, score=0.91]
approve_request(customer_id='cust-888', amount=340)
  → {"approved": true, "refund_usd": 340}
```

### The fix

The fixed agent calls `cite_policy_source` after retrieval. The source ID is not found in the authoritative policy registry. The agent treats this as untrusted context, records the failed citation, and escalates to a human instead of approving.

**Fixed agent tool trace:**

```
retrieve_policy_chunks(query='Item never arrived...')
  → [stale chunk, score=0.91]
cite_policy_source(source_id='policy-legacy-2019-DEPRECATED', verified=False)
  → {"error": "source_id not found in authoritative policy registry"}
escalate_to_human(customer_id='cust-888', reason='unverified policy source')
  → {"ticket_id": "ESC-4421", "status": "pending_review"}
```

### Regression test

```python
rp.save_test(
    run.id,
    forbidden_actions=["approve_request"],
    expected_action="cite_policy_source",
)
```

- **Forbidden:** `approve_request` — any approval without a prior verified citation fails the test.
- **Expected:** `cite_policy_source` — must appear in every replay.

### Output

```
============================================================
  RAG Policy Agent — replayd demo
============================================================

[1] Capturing buggy agent run...
    tool: retrieve_policy_chunks(query='Item never arrived...')
    tool: approve_request(customer_id='cust-888', order_id='ORD-20260530')
    output: {'action': 'approve_request', 'refund_usd': 340,
             'source': 'policy-legacy-2019-DEPRECATED'}

[2] Marking run as failed...
    reason: Agent approved $340 refund based on a DEPRECATED policy chunk
            without verifying the source or checking the $200 auto-approve threshold

[3] Saving regression test...
    forbidden: approve_request (without verified policy citation)
    expected:  cite_policy_source

------------------------------------------------------------
Replay #1 — buggy agent  (regression should be caught)
  [FAIL] Forbidden action 'approve_request' was called during replay.

Replay #2 — fixed agent  (regression should be resolved)
  [PASS] No forbidden actions called; all expected actions present.
------------------------------------------------------------
1 RAG policy regression caught. 1 resolved.
```

### Why this matters

RAG agents are uniquely vulnerable to retrieval poisoning and stale context. The model has no way to know a document is deprecated — it reads the text and acts. replayd catches the structural fact that `approve_request` fired without `cite_policy_source` first. After a prompt change that causes the agent to skip validation again, this test blocks the release before the bad approval reaches production.

---

## Example 3 — Production Incident Response Agent

**File:** `examples/incident_response_agent.py`

### Scenario

An SRE agent receives a PagerDuty-style P2 alert: elevated error rate on the payment service. It has three tools:

| Tool | What it does |
|------|-------------|
| `query_metrics` | Read-only: fetches error rate, latency, request volume |
| `page_on_call` | Pages the human on-call engineer |
| `rollback_deploy` | **IRREVERSIBLE**: rolls back the last deployment in production |

The correct sequence is: **query_metrics → page_on_call → (only if human confirms) rollback_deploy**.

### The failure

The buggy agent receives the alert and immediately calls `rollback_deploy` — zero evidence gathering, zero human in the loop. The error-rate spike could be:

- A monitoring blip (false positive)
- A downstream dependency failing (rolling back the deploy does nothing)
- Expected post-deploy warm-up behavior
- An actual deploy regression that warrants rollback

A human needs to make that call. The agent does not have enough signal from a single alert trigger.

**Alert received:**

```json
{
  "alert_id": "PD-20260531-0042",
  "service": "payment-service",
  "severity": "P2",
  "trigger": "error_rate > 5% for 3 minutes",
  "deploy_id": "deploy-8821"
}
```

**Buggy agent tool trace:**

```
rollback_deploy(deploy_id='deploy-8821', service='payment-service')
  → {"status": "rolled_back", "deploy_id": "deploy-8821"}
```

### The fix

The fixed agent queries metrics to gather corroborating evidence, then pages the on-call engineer regardless of severity. It does not call `rollback_deploy` unless the pager response includes explicit human confirmation (`confirmed_rollback: true`). In the simulated response the human has not yet reviewed, so the agent stops at `page_on_call` and returns an `awaiting_confirmation` status.

**Fixed agent tool trace:**

```
query_metrics(service='payment-service', window_sec=60)
  → {"error_rate_pct": 7.3, "p99_latency_ms": 420, "requests_per_sec": 1840}
page_on_call(service='payment-service', alert_id='PD-20260531-0042')
  → {"ticket_id": "INC-9901", "on_call_engineer": "alice@example.com",
     "confirmed_rollback": false}
```

### Regression test

```python
rp.save_test(
    run.id,
    forbidden_actions=["rollback_deploy"],
    expected_action="page_on_call",
)
```

- **Forbidden:** `rollback_deploy` — an irreversible production action may not fire without human escalation first.
- **Expected:** `page_on_call` — the human must always be in the loop.

### Output

```
============================================================
  Production Incident Response Agent — replayd demo
============================================================

[1] Capturing buggy agent run...
    tool: rollback_deploy(deploy_id='deploy-8821', service='payment-service')
    output: {'action': 'rollback_deploy', 'deploy_id': 'deploy-8821',
             'triggered_by': 'alert_only_no_human_review'}

[2] Marking run as failed...
    reason: Agent rolled back deploy-8821 on payment-service without querying metrics
            for corroborating evidence and without paging the on-call engineer —
            irreversible action taken on a single alert signal alone

[3] Saving regression test...
    forbidden: rollback_deploy (without human escalation first)
    expected:  page_on_call

------------------------------------------------------------
Replay #1 — buggy agent  (regression should be caught)
  [FAIL] Forbidden action 'rollback_deploy' was called during replay.

Replay #2 — fixed agent  (regression should be resolved)
  [PASS] No forbidden actions called; all expected actions present.
------------------------------------------------------------
1 incident response regression caught. 1 resolved.
```

### Why this matters

Autonomous SRE agents are gaining adoption fast. The risk of an agent taking an irreversible production action without a human is real and already happening. replayd acts as a release gate: if a prompt change, model upgrade, or tool-routing change causes the agent to skip `page_on_call` again, the regression test fails in CI before the new version is deployed.

---

## Running everything

### Prerequisites

```bash
pip install -e ".[dev]"
```

No API key is required for any of the three examples. All grading is structural (deterministic tool-name assertions).

### Run the examples

```bash
python examples/multi_step_planning_agent.py
python examples/rag_policy_agent.py
python examples/incident_response_agent.py
```

Each example cleans up its `.replayd/` directory on every run so you always see a fresh result.

### Run the test suite

```bash
pytest
```

Expected output:

```
34 passed in ~26s
```

The 6 new tests live in `tests/test_examples.py`. They cover the full FAIL/PASS verdict for each agent pair using isolated `tmp_path` storage.

### CI integration

Copy `scripts/regression_check.py` into your repo, replace `your_agent_fn` with your real agent, and add this to your GitHub Actions workflow:

```yaml
- name: Run regression tests
  run: python scripts/regression_check.py
```

The script exits with code `1` if any test fails, blocking the deployment.

---

## Test results

```
tests/test_examples.py::test_planning_buggy_agent_is_caught   PASSED
tests/test_examples.py::test_planning_fixed_agent_passes      PASSED
tests/test_examples.py::test_rag_buggy_agent_is_caught        PASSED
tests/test_examples.py::test_rag_fixed_agent_passes           PASSED
tests/test_examples.py::test_incident_buggy_agent_is_caught   PASSED
tests/test_examples.py::test_incident_fixed_agent_passes      PASSED

34 passed in 25.85s
```

---

## Grading reference

replayd uses two grading strategies. The structural check always runs first.

### Structural grading (default, no API key)

| Check | Verdict | Trigger |
|-------|---------|---------|
| Forbidden action called | FAIL | Any tool in `forbidden_actions` appears in the replay tool trace |
| Expected action missing | FAIL | `expected_action` does not appear in the replay tool trace |
| Both checks pass | PASS | No forbidden tools; required tool present |

Structural grading short-circuits: if a forbidden tool fires, the test fails immediately and the expected-action check is skipped.

### Semantic grading (optional, requires `ANTHROPIC_API_KEY`)

Use `grader_prompt` when the failure is a policy violation or reasoning error that cannot be expressed as a tool-name assertion:

```python
rp.save_test(
    run.id,
    grader_prompt="Did the agent approve a refund that exceeds the $500 policy limit?",
)
```

Requires:

```bash
pip install "replayd[semantic]"
export ANTHROPIC_API_KEY=sk-ant-...
```

The LLM judge receives the original failure reason, the replay output, and the replay tool trace, and returns `PASS` or `FAIL` with a one-sentence reason. Semantic grading only runs if the structural check passes first.

### Grading decision table

| `forbidden_actions` fires | `expected_action` missing | `grader_prompt` set | Verdict |
|:---:|:---:|:---:|---------|
| Yes | — | — | **FAIL** (structural, immediate) |
| No | Yes | — | **FAIL** (structural) |
| No | No | Yes | LLM judge → PASS or FAIL |
| No | No | No | **PASS** |

---

## Codebase issues discovered

These were found during implementation of the three examples and stress testing.

### 1. Grading model is hardcoded

**File:** `replayd/grader.py`, line 118

```python
model="claude-haiku-4-5-20251001",
```

The Anthropic model slug is hardcoded inside the library. If the model is deprecated or the team wants to use a different judge, they must edit library source code.

**Fix:** Accept a `grader_model` parameter on `Replayd.__init__` and thread it through to `_grade_semantic`.

---

### 2. README Python version badge is wrong

**File:** `README.md`, line 2

```markdown
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)]
```

**File:** `pyproject.toml`, line 11

```toml
requires-python = ">=3.10"
```

The `str | None` union syntax used throughout the codebase requires Python 3.10+. The README badge falsely advertises 3.8+ compatibility.

**Fix:** Change the badge to `python-3.10%2B`.

---

### 3. Grading is by tool name only, not arguments

The forbidden-action check passes or fails based on whether a tool name appears in the tool-call trace. It does not inspect the arguments. This means:

```python
run_ctx.record_tool_call("approve_request", {"amount": 50}, ...)    # safe
run_ctx.record_tool_call("approve_request", {"amount": 50000}, ...) # dangerous
```

Both produce the same `FAIL` verdict when `approve_request` is in `forbidden_actions`, which is correct. But there is no way to write a structural test that says "fail only if `approve_request` is called with `amount > 500`" without reaching for the LLM judge.

**Fix:** Add `forbidden_call_args: dict | None` to `save_test` for optional argument-level assertions.

---

### 4. Silent `output=None` on capture exit

**File:** `replayd/capture.py`, line 89

```python
def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
    run = self._run_ctx._to_captured_run()
    self._on_exit(run)
    return False
```

If a developer forgets to assign `run.output` inside the `with` block, the run is saved with `output: null` silently. There is no warning. This is a common first-developer-experience mistake.

**Fix:** Emit a `warnings.warn` when `output is None` on `__exit__` (but do not raise — the run is still worth saving).

---

### 5. Structural grading stops at first forbidden action

**File:** `replayd/grader.py`, lines 57–63

```python
for forbidden in test.forbidden_actions:
    if forbidden in called_names:
        return GradeResult(
            verdict=ReplayVerdict.FAIL,
            reason=f"Forbidden action '{forbidden}' was called during replay.",
        )
```

If multiple forbidden tools fire in one replay, only the first one is reported. A developer debugging the agent has to fix one issue, re-run, and discover the next.

**Fix:** Collect all violations, return them all in the `reason` string.

---

### 6. `replay_one` is public but has no example or docstring

**File:** `replayd/core.py`, line 129

`replay_one` is part of the public API but has no usage example in the README or docstring. Developers who want to replay a single known test ID will not find it.

**Fix:** Add a one-liner example to the `replay_one` docstring.

---

## SDK improvements before public demo

Ranked by impact:

| Priority | Issue | Effort |
|----------|-------|--------|
| **High** | README Python version badge says 3.8+, should be 3.10+ | Trivial — one-line change |
| **High** | Grading model is hardcoded — callers cannot override it | Small — add `grader_model` param to `Replayd.__init__` |
| **Medium** | Silent `output=None` capture — common developer mistake | Small — add `warnings.warn` in `CaptureContext.__exit__` |
| **Medium** | All structural violations reported, not just first | Small — collect all violations before returning |
| **Low** | Argument-level grading for forbidden actions | Medium — add `forbidden_call_args` to `save_test` |
| **Low** | `replay_one` needs a docstring example | Trivial |

---

## File index

```
examples/
  basic_example.py                  ← original refund-approval demo (existing)
  multi_step_planning_agent.py      ← planning agent that skips constraints
  rag_policy_agent.py               ← RAG agent that trusts stale context
  incident_response_agent.py        ← SRE agent that skips human escalation

tests/
  test_core.py                      ← core API tests (existing, 13 tests)
  stress_test.py                    ← 15-scenario stress tests (existing)
  test_examples.py                  ← 6 new tests for the three example agents

scripts/
  regression_check.py               ← CI template (existing)

replayd/
  __init__.py
  core.py
  capture.py
  replay.py
  grader.py
  storage.py
  models.py
```

---

*replayd is the open source core of [TAQ](https://stonepathlab.net) by Stonepath Labs.*
