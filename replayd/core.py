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


_DEFAULT_GRADER_MODEL = "claude-haiku-4-5-20251001"


class Replayd:
    def __init__(
        self,
        storage_dir: str | Path = ".replayd",
        grader_model: str = _DEFAULT_GRADER_MODEL,
    ) -> None:
        """
        Create a Replayd instance.

        storage_dir:  directory where runs and tests are stored as JSON files.
        grader_model: Anthropic model slug used for semantic (LLM-as-judge)
                      grading. Only relevant when save_test() is called with
                      a grader_prompt. Defaults to claude-haiku-4-5-20251001.
                      Override to use a different judge without editing library
                      source code.

        Example:
            rp = Replayd(grader_model="claude-opus-4-5")
        """
        self._storage = Storage(storage_dir)
        self._grader_model = grader_model

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
        expected_action_args: dict | None = None,
        required_sequence: list[str] | None = None,
        grader_prompt: str | None = None,
        forbidden_call_args: dict | None = None,
    ) -> TestCase:
        """
        Convert a failed run into a replayable regression test.

        At least one grading criterion must be provided.

        forbidden_actions:
            Tool names that must NOT appear in a replay.

        expected_action:
            Tool name that MUST appear in a replay.

        expected_action_args:
            Optional argument constraints for expected_action. At least one
            call to expected_action must have arguments that are a superset of
            this dict. Ignored when expected_action is None.

        required_sequence:
            Ordered list of tool names that must appear in that relative order
            (first occurrence, not necessarily consecutive). E.g.
            ["validate", "submit"] enforces validate before submit.

        forbidden_call_args:
            Optional argument-level filter for forbidden_actions. A forbidden
            tool only triggers FAIL when its arguments contain every key/value
            pair in this dict.

        grader_prompt:
            LLM-as-judge prompt for policy or reasoning failures. Requires
            pip install replayd[semantic] and ANTHROPIC_API_KEY.
        """
        run = self._storage.load_run(run_id)
        if run.status != RunStatus.FAILED:
            raise ValueError(
                f"Run {run_id} has not been marked as failed. "
                "Call mark_failed() before save_test()."
            )
        if not forbidden_actions and not expected_action and not grader_prompt and not required_sequence:
            raise ValueError(
                "Provide at least one of: forbidden_actions, expected_action, "
                "required_sequence, grader_prompt."
            )

        test = TestCase(
            id=new_id(),
            run_id=run_id,
            failure_reason=run.failure_reason,
            forbidden_actions=forbidden_actions or [],
            expected_action=expected_action,
            expected_action_args=expected_action_args,
            required_sequence=required_sequence,
            grader_prompt=grader_prompt,
            created_at=utcnow(),
            forbidden_call_args=forbidden_call_args,
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
        return _replay_all(
            agent,
            self._storage,
            stop_on_first_failure=stop_on_first_failure,
            grader_model=self._grader_model,
        )

    def replay_one(self, test_id: str, agent: Callable) -> ReplayResult:
        """
        Replay a single test case by its test ID.

        Use this when you want to re-run one specific regression test without
        running the full suite — useful during debugging or after a targeted fix.

        Example:
            tests = rp.list_tests()
            result = rp.replay_one(tests[0].id, agent=my_agent)
            print(result.verdict, result.reason)
        """
        test = self._storage.load_test(test_id)
        return _replay_one(test, agent, self._storage, grader_model=self._grader_model)

    # ------------------------------------------------------------------
    # 5. Instrumentation
    # ------------------------------------------------------------------

    def instrument_openai(self, client) -> None:
        """
        Wrap an OpenAI client so tool calls within a capture block are
        recorded automatically. Works with both OpenAI and AsyncOpenAI.
        Call once per client, before any capture blocks.

            rp.instrument_openai(client)
            with rp.capture(input=...) as run:
                run.output = run_my_agent(client, ...)
            # tool calls are recorded — no record_tool_call() needed

        Streaming (stream=True) is not supported — a warning is emitted.
        Idempotent — safe to call multiple times on the same client.
        """
        from replayd.instrumentation import patch_openai_client
        patch_openai_client(client)

    def instrument_anthropic(self, client) -> None:
        """
        Wrap an Anthropic client so tool calls within a capture block are
        recorded automatically. Works with both Anthropic and AsyncAnthropic.
        Call once per client, before any capture blocks.

            rp.instrument_anthropic(client)
            with rp.capture(input=...) as run:
                run.output = run_my_agent(client, ...)
            # tool calls are recorded — no record_tool_call() needed

        Streaming (stream=True) is not supported — a warning is emitted.
        Idempotent — safe to call multiple times on the same client.
        """
        from replayd.instrumentation import patch_anthropic_client
        patch_anthropic_client(client)

    def uninstrument_openai(self, client) -> None:
        """
        Restore client.chat.completions.create to its original method,
        removing the replayd wrapper. Idempotent.
        """
        from replayd.instrumentation import unpatch_openai_client
        unpatch_openai_client(client)

    def uninstrument_anthropic(self, client) -> None:
        """
        Restore client.messages.create to its original method,
        removing the replayd wrapper. Idempotent.
        """
        from replayd.instrumentation import unpatch_anthropic_client
        unpatch_anthropic_client(client)

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
