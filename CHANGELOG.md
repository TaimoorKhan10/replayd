# Changelog

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
