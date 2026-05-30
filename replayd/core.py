"""
Main entry point — the Replayd class.

    rp = Replayd()

    with rp.capture(input=user_input) as run:
        run.output = agent.run(user_input)

    rp.mark_failed(run.id, reason="approved refund after policy limit")
    rp.save_test(run.id, forbidden_actions=["approve_refund"], expected_action="escalate")

    results = rp.replay_all(agent=my_agent_fn)
    for r in results:
        print(r.verdict, r.reason)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from replayd.capture import CaptureContext
from replayd.models import CapturedRun, ReplayResult, RunStatus, TestCase, new_id, utcnow
from replayd.replay import replay_all as _replay_all
from replayd.replay import replay_one as _replay_one
from replayd.storage import Storage


class Replayd:
    def __init__(self, storage_dir: str | Path = ".replayd") -> None:
        self._storage = Storage(storage_dir)

    # ------------------------------------------------------------------
    # 1. Capture
    # ------------------------------------------------------------------

    def capture(
        self,
        input: Any = None,
        model: str | None = None,
        prompt_version: str | None = None,
    ) -> CaptureContext:
        """
        Context manager. Assign run.output inside the block.

            with rp.capture(input=user_msg) as run:
                run.output = agent.run(user_msg)
        """
        return CaptureContext(
            input=input,
            model=model,
            prompt_version=prompt_version,
            on_exit=self._storage.save_run,
        )

    # ------------------------------------------------------------------
    # 2. Mark failed
    # ------------------------------------------------------------------

    def mark_failed(self, run_id: str, reason: str) -> CapturedRun:
        """Attach a failure reason to a captured run and persist it."""
        run = self._storage.load_run(run_id)
        run.status = RunStatus.FAILED
        run.failure_reason = reason
        self._storage.save_run(run)
        return run

    # ------------------------------------------------------------------
    # 3. Save as regression test
    # ------------------------------------------------------------------

    def save_test(
        self,
        run_id: str,
        *,
        forbidden_actions: list[str] | None = None,
        expected_action: str | None = None,
        grader_prompt: str | None = None,
    ) -> TestCase:
        """
        Convert a failed run into a replayable regression test.

        At least one of forbidden_actions, expected_action, or grader_prompt
        must be provided so the grader has something to check.
        """
        run = self._storage.load_run(run_id)
        if run.status != RunStatus.FAILED:
            raise ValueError(
                f"Run {run_id} has not been marked as failed. "
                "Call mark_failed() before save_test()."
            )
        if not forbidden_actions and not expected_action and not grader_prompt:
            raise ValueError(
                "Provide at least one of: forbidden_actions, expected_action, grader_prompt."
            )

        test = TestCase(
            id=new_id(),
            run_id=run_id,
            failure_reason=run.failure_reason,
            forbidden_actions=forbidden_actions or [],
            expected_action=expected_action,
            grader_prompt=grader_prompt,
            created_at=utcnow(),
        )
        self._storage.save_test(test)
        return test

    # ------------------------------------------------------------------
    # 4. Replay
    # ------------------------------------------------------------------

    def replay_all(
        self,
        agent: Callable,
        *,
        stop_on_first_failure: bool = False,
    ) -> list[ReplayResult]:
        """
        Replay every saved test. Returns a list of ReplayResult.

        The agent callable receives (input, run_ctx) where run_ctx is a
        RunContext. Call run_ctx.record_tool_call() inside the agent for
        each tool invocation so the grader can inspect them.
        """
        return _replay_all(agent, self._storage, stop_on_first_failure=stop_on_first_failure)

    def replay_one(self, test_id: str, agent: Callable) -> ReplayResult:
        """Replay a single test case by its test ID."""
        test = self._storage.load_test(test_id)
        return _replay_one(test, agent, self._storage)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def list_runs(self) -> list[CapturedRun]:
        return self._storage.list_runs()

    def list_tests(self) -> list[TestCase]:
        return self._storage.list_tests()

    def get_run(self, run_id: str) -> CapturedRun:
        return self._storage.load_run(run_id)

    def get_test(self, test_id: str) -> TestCase:
        return self._storage.load_test(test_id)
