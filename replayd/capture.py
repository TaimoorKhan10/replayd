"""
Context manager for recording a single agent run.

Usage:
    with rp.capture() as run:
        result = agent.run(input)

Tool calls are not recorded automatically — they must be appended manually
via run.record_tool_call(), or by using a wrapper around the agent's tool
dispatcher. The context manager records start time, captures the output
assigned to run.output, and persists on exit.
"""

from __future__ import annotations

import warnings
from typing import Any, Callable

from replayd.models import CapturedRun, RunStatus, ToolCall, new_id, utcnow


class RunContext:
    """
    Mutable handle to an in-progress capture. Passed to the `with` block.

    Assign run.output inside the block; call run.record_tool_call() for
    each tool the agent invokes.
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
    can fire on __exit__.
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

    def __enter__(self) -> RunContext:
        return self._run_ctx

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
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
