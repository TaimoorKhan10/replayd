# Contributing to replayd

Thank you for your interest. replayd is a focused tool — zero runtime dependencies, deterministic by default, framework-agnostic. Contributions that stay true to those principles are most welcome.

## Philosophy

- **Zero runtime dependencies.** The core SDK installs nothing. No LLM, no database, no cloud SDK. The optional `[semantic]` extra (Anthropic API for LLM-as-judge grading) is the only exception.
- **Structural grading first.** A forbidden tool being called is a fact, not an opinion. Assertions about tool calls should never be flaky. Exact output matching is intentionally not supported — LLMs are non-deterministic.
- **Framework-agnostic.** replayd does not care how your agent is built. It sees input, output, and tool calls — nothing else. LangChain, OpenAI Agents SDK, CrewAI, raw API calls — all integrate the same way.

## Quick start

```bash
git clone https://github.com/TaimoorKhan10/replayd.git
cd replayd
pip install -e ".[dev]"
pytest
```

Three commands. All tests should pass before you make any changes.

## Architecture

The codebase is intentionally small — five source files, no framework, no magic.

| File | What it does |
|---|---|
| `replayd/models.py` | Core data types: `CapturedRun`, `TestCase`, `ReplayResult`, `ToolCall`. All serialize to/from plain JSON dicts via `to_dict()` / `from_dict()`. |
| `replayd/capture.py` | The `with rp.capture() as run:` context manager and `RunContext` — the mutable handle passed into agent calls so tool invocations can be recorded mid-run. |
| `replayd/storage.py` | All file I/O. Reads and writes UTF-8 JSON to `.replayd/runs/` and `.replayd/tests/`. No database, no ORM, no cloud. |
| `replayd/grader.py` | Structural grader runs first (forbidden actions, expected action, forbidden call args). Semantic grader (LLM-as-judge via Anthropic API) runs only when `grader_prompt` is set. |
| `replayd/replay.py` + `replayd/core.py` | Orchestration layer. `core.py` is the public API class. `replay.py` handles the replay loop, run caching (avoids re-reading the same JSON file for every test), and exception isolation so a crashing agent returns FAIL instead of aborting the whole suite. |

## Running tests

```bash
# Core suite — run before every PR
pytest tests/test_core.py tests/test_examples.py -v

# Full stress test suite — local only, not required for CI
pytest tests/stress_test.py -v
```

The CI workflow (`.github/workflows/tests.yml`) runs `test_core.py` and `test_examples.py` on Python 3.10, 3.11, and 3.12. Your PR must pass all three.

## Writing integration examples

Integration examples live in `examples/`. Each new example should:

1. **Work with no API key** — use mock tools and agents, not real LLM calls.
2. **Show a realistic failure scenario** — wrong tool called, wrong argument, wrong sequence.
3. **Print `[FAIL]` then `[PASS]`** — buggy agent first, fixed agent second. The output format makes the value obvious at a glance.
4. **Include an integration pattern comment block at the top** — show the two-line diff between the original agent code and the replayd-wrapped version.

Use `examples/langchain_tool_agent.py` or `examples/openai_agents_sdk_example.py` as a template. Both use `tempfile.TemporaryDirectory()` for storage so they leave no files behind when run.

## PR process

- **Open an issue first** for anything that changes the public API, adds a new file to the core package, or adds a dependency.
- **Tests required.** All PRs must pass `pytest tests/test_core.py tests/test_examples.py`. New features should come with new tests in `test_core.py`.
- **No new runtime dependencies.** `dependencies = []` in `pyproject.toml` stays empty. If your change genuinely requires a new dependency, open an issue to discuss it first.
- **Keep commits focused.** One logical change per PR is easier to review, easier to revert, and easier to attribute in the changelog.

## Good first contributions

| Contribution | Notes |
|---|---|
| Add a CrewAI integration example | Follow `examples/langchain_tool_agent.py` — mock the crew, show FAIL then PASS |
| Add an AutoGen integration example | Same pattern — no real API key, realistic failure scenario |
| Add a smolagents integration example | Same |
| Add a raw OpenAI function-calling example | `openai` library, not the Agents SDK — different integration surface |
| Improve type annotations in `grader.py` | Stricter `TypedDict` types for grader arguments |
| Add `--test-id` flag to `scripts/regression_check.py` | Run a single named test from the CLI instead of the full suite |
| Add a `replayd ls` CLI command | List saved runs and tests from the terminal |

## License

MIT. By contributing, you agree that your code will be released under the same license.
