"""
Grading logic — the most important part of replayd.

Two grading strategies:

1. Structural (deterministic)
   Checks whether forbidden tool calls appeared or required tool calls
   were missing. No LLM needed. Always runs first.

2. Semantic (LLM-as-judge)
   Used when the test has a grader_prompt. Sends the original failure
   reason, the replay output, and the replay tool calls to an LLM and
   asks whether the same failure recurred. Returns PASS or FAIL with
   a one-sentence reason.

The structural check short-circuits: if a forbidden action fires, the
test fails immediately without calling the LLM.
"""

from __future__ import annotations

import json
import os
from typing import Any

from replayd.models import CapturedRun, ReplayVerdict, TestCase


class GradeResult:
    def __init__(self, verdict: ReplayVerdict, reason: str) -> None:
        self.verdict = verdict
        self.reason = reason

    def __bool__(self) -> bool:
        return self.verdict == ReplayVerdict.PASS


def grade(test: TestCase, replay_run: CapturedRun) -> GradeResult:
    """
    Grade a replay run against its test case.
    Returns a GradeResult with verdict and human-readable reason.
    """
    # --- structural check first (cheap, deterministic) ---
    structural = _grade_structural(test, replay_run)
    if structural.verdict == ReplayVerdict.FAIL:
        return structural

    # --- semantic check if grader_prompt is set ---
    if test.grader_prompt:
        return _grade_semantic(test, replay_run)

    return structural


def _grade_structural(test: TestCase, run: CapturedRun) -> GradeResult:
    called_names = {tc.name for tc in run.tool_calls}

    for forbidden in test.forbidden_actions:
        if forbidden in called_names:
            return GradeResult(
                verdict=ReplayVerdict.FAIL,
                reason=f"Forbidden action '{forbidden}' was called during replay.",
            )

    if test.expected_action and test.expected_action not in called_names:
        return GradeResult(
            verdict=ReplayVerdict.FAIL,
            reason=(
                f"Expected action '{test.expected_action}' was not called during replay. "
                f"Actions called: {sorted(called_names) or 'none'}."
            ),
        )

    return GradeResult(
        verdict=ReplayVerdict.PASS,
        reason="No forbidden actions called; all expected actions present.",
    )


def _grade_semantic(test: TestCase, run: CapturedRun) -> GradeResult:
    """
    Ask an LLM whether the original failure recurred in this replay.

    Requires the ANTHROPIC_API_KEY environment variable (uses the
    Anthropic Messages API directly to avoid adding a hard dependency
    on a specific SDK version).
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "Semantic grading requires the 'anthropic' package. "
            "Install it with: pip install anthropic"
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Semantic grading requires an Anthropic API key."
        )

    tool_call_summary = _summarise_tool_calls(run.tool_calls)

    user_message = (
        f"You are grading an AI agent replay test.\n\n"
        f"Original failure reason: {test.failure_reason}\n\n"
        f"Grading criteria: {test.grader_prompt}\n\n"
        f"Replay output:\n{json.dumps(run.output, indent=2, default=str)}\n\n"
        f"Tool calls made during replay:\n{tool_call_summary}\n\n"
        f"Did the same failure recur? Reply with exactly one of:\n"
        f"PASS: <one sentence explaining why the failure did not recur>\n"
        f"FAIL: <one sentence explaining why the failure recurred>"
    )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{"role": "user", "content": user_message}],
    )

    response_text = message.content[0].text.strip()

    if response_text.startswith("PASS"):
        reason = response_text[len("PASS:"):].strip() if ":" in response_text else response_text
        return GradeResult(verdict=ReplayVerdict.PASS, reason=reason)
    elif response_text.startswith("FAIL"):
        reason = response_text[len("FAIL:"):].strip() if ":" in response_text else response_text
        return GradeResult(verdict=ReplayVerdict.FAIL, reason=reason)
    else:
        # Unexpected format — treat as fail and surface the raw response.
        return GradeResult(
            verdict=ReplayVerdict.FAIL,
            reason=f"Grader returned unexpected format: {response_text}",
        )


def _summarise_tool_calls(tool_calls: list[Any]) -> str:
    if not tool_calls:
        return "(no tool calls)"
    lines = []
    for i, tc in enumerate(tool_calls, 1):
        lines.append(
            f"{i}. {tc.name}({json.dumps(tc.arguments, default=str)}) → {json.dumps(tc.result, default=str)}"
        )
    return "\n".join(lines)
