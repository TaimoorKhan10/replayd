"""
Context manager for recording a single agent run.

Usage:
    with rp.capture() as run:
        result = agent.run(input)

Tool calls are not recorded automatically by default — they must be appended
via run.record_tool_call(), or by calling rp.instrument_openai(client) /
rp.instrument_anthropic(client) once before entering a capture block.
The context manager records start time, captures the output assigned to
run.output, and persists on exit.
"""

from __future__ import annotations

import warnings
from contextvars import ContextVar
from typing import Any, Callable

from replayd.models import CapturedRun, RunStatus, ToolCall, new_id, utcnow


# Tracks the RunContext that is currently active inside a `with rp.capture()`
# block. Used by the instrumentation layer to record tool calls automatically.
# ContextVar is safe under threading and asyncio — each task/thread has its own
# value without any shared-state risk.
_active_run_ctx: ContextVar[RunContext | None] = ContextVar(
    "replayd_active_run_ctx", default=None
)


class RunContext:
    """
    Mutable handle to an in-progress capture. Passed to the `with` block.

    Assign run.output inside the block; call run.record_tool_call() for
    each tool the agent invokes (or use rp.instrument_openai/anthropic for
    automatic recording).
    """

    def __init__(
        self,
        input: Any,
        model: str | None = None,
        prompt_version: str | None = None,
    ) -> None:
        self._id = new_id()
        self._input = input
        self._model = model
        self._prompt_version = prompt_version
        self._tool_calls: list[ToolCall] = []
        # Pending tool call requests from the model, keyed by provider call ID.
        # The instrumentation layer writes here when it sees a tool_use/tool_calls
        # response block, then pops and records when it sees the matching result
        # in a subsequent request.
        self._pending_tool_calls: dict[str, tuple[str, dict]] = {}
        self._timestamp = utcnow()
        self.output: Any = None

    @property
    def id(self) -> str:
        return self._id

    def record_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        result: Any = None,
    ) -> None:
        """Append one tool invocation to this run's record."""
        self._tool_calls.append(ToolCall(name=name, arguments=arguments, result=result))

    def _to_captured_run(self) -> CapturedRun:
        return CapturedRun(
            id=self._id,
            input=self._input,
            output=self.output,
            tool_calls=list(self._tool_calls),
            model=self._model,
            prompt_version=self._prompt_version,
            timestamp=self._timestamp,
            status=RunStatus.CAPTURED,
        )


class CaptureContext:
    """
    Returned by Replayd.capture(). Wraps RunContext so the storage callback
    can fire on __exit__ and the active context var stays in sync.
    """

    def __init__(
        self,
        input: Any,
        model: str | None,
        prompt_version: str | None,
        on_exit: Callable[[CapturedRun], None],
    ) -> None:
        self._run_ctx = RunContext(input, model, prompt_version)
        self._on_exit = on_exit
        self._ctx_token = None  # set in __enter__, reset in __exit__

    def __enter__(self) -> RunContext:
        self._ctx_token = _active_run_ctx.set(self._run_ctx)
        return self._run_ctx

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        # Clear the active context before any downstream work.
        if self._ctx_token is not None:
            _active_run_ctx.reset(self._ctx_token)
        if self._run_ctx.output is None and exc_type is None:
            warnings.warn(
                "replayd: run.output is None — did you forget to assign it inside the "
                "'with rp.capture(...) as run:' block?\n"
                "  with rp.capture(input=...) as run:\n"
                "      run.output = your_agent(input)  # assign here",
                stacklevel=2,
            )
        run = self._run_ctx._to_captured_run()
        self._on_exit(run)
        # Do not suppress exceptions raised inside the with block.
        return False
