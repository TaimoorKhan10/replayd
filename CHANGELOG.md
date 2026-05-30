# Changelog

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
