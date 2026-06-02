"""
Replay engine — runs saved test cases against an agent and grades results.

The agent passed to replay_all / replay_one must be callable:
    output = agent(input, run_ctx)

Call run_ctx.record_tool_call() inside the agent for each tool invocation.
See examples/basic_example.py for a complete working pattern.
"""

from __future__ import annotations

from typing import Any, Callable

from replayd.capture import RunContext, _active_run_ctx
from replayd.grader import grade
from replayd.models import CapturedRun, ReplayResult, ReplayVerdict, RunStatus, TestCase, new_id, utcnow
from replayd.storage import Storage


AgentCallable = Callable[[Any, RunContext], Any]


def _run_agent(agent: AgentCallable, original_run: CapturedRun) -> CapturedRun:
    """Execute the agent against the original input and return a fresh CapturedRun."""
    run_ctx = RunContext(
        input=original_run.input,
        model=original_run.model,
        prompt_version=original_run.prompt_version,
    )
    # Set the active context so instrumented clients record into this run_ctx
    # even during replay (not just during rp.capture() blocks).
    token = _active_run_ctx.set(run_ctx)
    try:
        output = agent(original_run.input, run_ctx)
    finally:
        _active_run_ctx.reset(token)
    run_ctx.output = output
    return CapturedRun(
        id=new_id(),
        input=original_run.input,
        output=output,
        tool_calls=list(run_ctx._tool_calls),
        model=run_ctx._model,
        prompt_version=run_ctx._prompt_version,
        timestamp=utcnow(),
        status=RunStatus.CAPTURED,
    )


def replay_one(
    test: TestCase,
    agent: AgentCallable,
    storage: Storage,
    _run_cache: dict[str, CapturedRun] | None = None,
    grader_model: str = "claude-haiku-4-5-20251001",
) -> ReplayResult:
    """Replay a single test case and return the graded result."""
    if _run_cache is not None and test.run_id in _run_cache:
        original_run = _run_cache[test.run_id]
    else:
        original_run = storage.load_run(test.run_id)
        if _run_cache is not None:
            _run_cache[test.run_id] = original_run

    try:
        fresh_run = _run_agent(agent, original_run)
    except Exception as exc:
        # Agent crashed during replay. Return a FAIL result instead of
        # propagating the exception and aborting the entire replay_all run.
        failed_run = CapturedRun(
            id=new_id(),
            input=original_run.input,
            output=None,
            tool_calls=[],
            model=original_run.model,
            prompt_version=original_run.prompt_version,
            timestamp=utcnow(),
            status=RunStatus.CAPTURED,
        )
        return ReplayResult(
            verdict=ReplayVerdict.FAIL,
            reason=f"Agent raised {type(exc).__name__} during replay: {exc}",
            run=failed_run,
            test=test,
        )

    grade_result = grade(test, fresh_run, grader_model=grader_model)
    return ReplayResult(
        verdict=grade_result.verdict,
        reason=grade_result.reason,
        run=fresh_run,
        test=test,
    )


def replay_all(
    agent: AgentCallable,
    storage: Storage,
    *,
    stop_on_first_failure: bool = False,
    grader_model: str = "claude-haiku-4-5-20251001",
) -> list[ReplayResult]:
    """
    Replay every saved test case.

    Returns a list of ReplayResult in test-creation order.
    If stop_on_first_failure is True, halts after the first FAIL verdict.
    """
    if not callable(agent):
        raise TypeError(
            f"agent must be a callable that accepts (input, run_ctx), "
            f"got {type(agent).__name__} instead."
        )

    tests = storage.list_tests()
    if not tests:
        return []

    # Cache original runs in memory so each run file is read at most once,
    # even when multiple tests reference the same run.
    run_cache: dict[str, CapturedRun] = {}

    results: list[ReplayResult] = []
    for test in tests:
        result = replay_one(test, agent, storage, _run_cache=run_cache, grader_model=grader_model)
        results.append(result)
        if stop_on_first_failure and result.verdict == ReplayVerdict.FAIL:
            break

    return results
