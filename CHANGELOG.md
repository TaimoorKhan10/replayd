# Changelog

## v0.1.3 — 2026-06-03

Four new capabilities, all backwards compatible.

**Auto-instrumentation**

- `rp.instrument_openai(client)` — call once before any capture block. The
  OpenAI client's `chat.completions.create` is wrapped so that tool calls
  the model requests, and the results returned in subsequent messages, are
  recorded automatically into the active run context. No `record_tool_call()`
  calls needed in agent code.
- `rp.instrument_anthropic(client)` — same for Anthropic `messages.create`.
  Handles the `tool_use` / `tool_result` message pattern.
- Works during `replay_all` / `replay_one` as well as during initial capture.
- Idempotent — calling twice on the same client is safe.

**CLI**

- `replayd run --agent module.path:agent_fn` — imports the callable, runs
  `replay_all` against all tests in `.replayd/tests/`, prints PASS/FAIL per
  test, exits 0 if all pass, exits 1 if any fail. Ready for CI pipelines.
- `replayd --version` — prints version and exits 0.
- Registered as a console entry point in `pyproject.toml`; available as
  `replayd` after `pip install replayd`.

**Deeper grading**

- `save_test(..., expected_action_args={"key": "val"})` — asserts the expected
  action was called with arguments that are a superset of this dict. Fails if
  the action was called with different arguments. Backwards compatible — default
  is None (existing behaviour unchanged).
- `save_test(..., required_sequence=["tool_a", "tool_b"])` — asserts that tool
  calls appear in this relative order (first occurrence; need not be
  consecutive). Can be used as the sole grading criterion. Fails if any tool is
  missing or appears in the wrong order.

**Example**

- `examples/real_openai_agent.py` — runnable with a real `OPENAI_API_KEY`.
  Shows `instrument_openai`, captures a buggy search-skipping agent, saves a
  test, and replays both buggy and fixed versions. Skips cleanly if no key is
  set.

**Tests**

- `tests/test_instrumentation.py` — 10 tests covering OpenAI and Anthropic
  auto-recording, idempotency, no-op outside capture, replay compatibility,
  and coexistence with manual `record_tool_call()`.
- `tests/test_cli.py` — 7 tests covering exit codes 0, 1, 2, `--version`,
  and output content.
- `tests/test_core.py` — 6 new tests for `expected_action_args` and
  `required_sequence` grading.
- Total: 49 tests passing.

## v0.1.2 — 2026-05-31

Six issues fixed, three real-world example agents added, test suite expanded from 34 to 41 tests.

**Fixes**

- Fix: README Python badge now correctly shows `python-3.10+` (was `3.8+`); the code uses `str | None` syntax which requires 3.10+
- Fix: grading model is no longer hardcoded — `Replayd(grader_model="...")` lets callers override the LLM judge without editing library source; default remains `claude-haiku-4-5-20251001`
- Fix: `CaptureContext.__exit__` now emits a `warnings.warn` when `run.output` is `None` at exit, alerting developers who forget to assign it inside the `with` block; the run is still saved
- Fix: structural grader now collects **all** forbidden violations before returning — a replay that calls two forbidden tools reports both in the `reason` string instead of stopping at the first
- Fix: `save_test()` accepts an optional `forbidden_call_args: dict` parameter; when set, a forbidden action only triggers a FAIL if the call arguments contain every key/value pair in the dict — enables argument-level assertions without reaching for the LLM judge
- Fix: `replay_one()` now has a docstring with a usage example

**New examples**

- `examples/multi_step_planning_agent.py` — planning agent that skips `check_constraints` before `finalize_plan`
- `examples/rag_policy_agent.py` — RAG support agent that approves a refund from a stale/deprecated policy chunk
- `examples/incident_response_agent.py` — SRE agent that calls `rollback_deploy` without paging the on-call engineer first

**Tests**

- `tests/test_examples.py` — 6 tests (buggy/fixed pair for each new example agent)
- `tests/test_core.py` — 8 new tests covering `grader_model`, `output=None` warning, multi-violation reporting, `forbidden_call_args` matching, and `replay_one` by test ID
- Total: 41 tests (was 34)

## v0.1.1 — 2026-05-30

Bug fixes and performance improvement from stress testing.

- Fix: agent raising an exception during replay no longer crashes `replay_all` — returns a `FAIL` result with the exception message instead
- Fix: passing a non-callable as `agent` to `replay_all` now raises a clear `TypeError` explaining what was expected
- Fix: run caching in `replay_all` reduces disk reads — 100-test replay improved from 9.5s to 2.6s (3.58× faster)

## v0.1.0 — 2026-05-30

First release of replayd.

- Capture failed AI agent runs with full context
- Mark runs as failed and save as regression tests
- Replay saved tests against updated agent versions
- Structural grading for tool call and action failures
- Semantic grading via LLM-as-judge for policy violations
- Zero runtime dependencies for core functionality
- CI integration script included
- 13 tests passing
