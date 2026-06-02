"""
Core data types for replayd. Everything else builds on these.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    CAPTURED = "captured"
    FAILED = "failed"


class ReplayVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass
class ToolCall:
    """A single tool invocation recorded during an agent run."""

    name: str
    arguments: dict[str, Any]
    result: Any = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ToolCall:
        return cls(name=d["name"], arguments=d["arguments"], result=d.get("result"))


@dataclass
class CapturedRun:
    """
    A complete record of one agent run: what went in, what came out,
    every tool call made, and the model/prompt used.
    """

    id: str
    input: Any
    output: Any
    tool_calls: list[ToolCall]
    model: str | None
    prompt_version: str | None
    timestamp: datetime
    status: RunStatus
    failure_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "input": self.input,
            "output": self.output,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "model": self.model,
            "prompt_version": self.prompt_version,
            "timestamp": self.timestamp.isoformat(),
            "status": self.status.value,
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CapturedRun:
        return cls(
            id=d["id"],
            input=d["input"],
            output=d["output"],
            tool_calls=[ToolCall.from_dict(tc) for tc in d.get("tool_calls", [])],
            model=d.get("model"),
            prompt_version=d.get("prompt_version"),
            timestamp=datetime.fromisoformat(d["timestamp"]),
            status=RunStatus(d["status"]),
            failure_reason=d.get("failure_reason"),
        )


@dataclass
class TestCase:
    """
    A saved regression test derived from a failed run.

    forbidden_actions:    tool call names that must NOT appear in a replay.
    expected_action:      tool call name that MUST appear in a replay.
    expected_action_args: optional dict of argument key/value pairs that must
                          ALL be present on the expected_action call. When
                          None (default), any call to the expected tool name
                          satisfies the check.
    required_sequence:    ordered list of tool names that must appear in that
                          relative order (not necessarily consecutive). E.g.
                          ["check_constraints", "finalize_plan"] means
                          check_constraints must be called before finalize_plan.
    grader_prompt:        LLM-as-judge prompt for semantic failures. When set,
                          the grader uses an LLM to evaluate the replay output
                          instead of (or alongside) structural assertions.
    forbidden_call_args:  optional dict of argument key/value pairs that must
                          ALL be present on a tool call for it to count as a
                          forbidden violation. When None (default), any call to
                          a forbidden tool name fails.

    Example — only fail if 'approve_refund' is called with amount=1200:
        save_test(run_id, forbidden_actions=["approve_refund"],
                  forbidden_call_args={"amount": 1200})

    Example — assert search happens before respond:
        save_test(run_id, required_sequence=["search_web", "respond"])
    """

    id: str
    run_id: str
    failure_reason: str
    forbidden_actions: list[str]
    expected_action: str | None
    grader_prompt: str | None
    created_at: datetime
    forbidden_call_args: dict | None = None
    expected_action_args: dict | None = None
    required_sequence: list[str] | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "failure_reason": self.failure_reason,
            "forbidden_actions": self.forbidden_actions,
            "expected_action": self.expected_action,
            "expected_action_args": self.expected_action_args,
            "required_sequence": self.required_sequence,
            "grader_prompt": self.grader_prompt,
            "created_at": self.created_at.isoformat(),
            "forbidden_call_args": self.forbidden_call_args,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TestCase:
        return cls(
            id=d["id"],
            run_id=d["run_id"],
            failure_reason=d["failure_reason"],
            forbidden_actions=d.get("forbidden_actions", []),
            expected_action=d.get("expected_action"),
            expected_action_args=d.get("expected_action_args"),
            required_sequence=d.get("required_sequence"),
            grader_prompt=d.get("grader_prompt"),
            created_at=datetime.fromisoformat(d["created_at"]),
            forbidden_call_args=d.get("forbidden_call_args"),
        )


@dataclass
class ReplayResult:
    """
    The outcome of replaying a test case against an agent.

    verdict:    PASS means the failure did not return; FAIL means it did.
    reason:     human-readable explanation of why the verdict was reached.
    run:        the fresh CapturedRun produced during replay.
    test:       the TestCase that was replayed.
    """

    verdict: ReplayVerdict
    reason: str
    run: CapturedRun
    test: TestCase

    def __bool__(self) -> bool:
        return self.verdict == ReplayVerdict.PASS


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
