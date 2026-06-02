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

The structural check collects all forbidden violations before returning
so developers see every problem at once. It runs before the LLM call;
if any violation is found, the semantic check is skipped.
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


def grade(
    test: TestCase,
    replay_run: CapturedRun,
    grader_model: str = "claude-haiku-4-5-20251001",
) -> GradeResult:
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
        return _grade_semantic(test, replay_run, grader_model=grader_model)

    return structural


def _grade_structural(test: TestCase, run: CapturedRun) -> GradeResult:
    called_names = {tc.name for tc in run.tool_calls}

    # 1. Forbidden action check — collect all violations so the developer
    #    sees every problem at once rather than fixing one per run.
    violations: list[str] = []
    for forbidden in test.forbidden_actions:
        matching = [tc for tc in run.tool_calls if tc.name == forbidden]
        if not matching:
            continue
        if test.forbidden_call_args is not None:
            # Argument-level filter: only count calls whose arguments contain
            # every key/value pair in forbidden_call_args.
            matching = [
                tc for tc in matching
                if all(tc.arguments.get(k) == v for k, v in test.forbidden_call_args.items())
            ]
        if matching:
            violations.append(forbidden)

    if violations:
        if len(violations) == 1:
            reason = f"Forbidden action '{violations[0]}' was called during replay."
        else:
            joined = ", ".join(f"'{v}'" for v in violations)
            reason = f"Forbidden actions called during replay: {joined}."
        return GradeResult(verdict=ReplayVerdict.FAIL, reason=reason)

    # 2. Expected action check — tool must be called, and if expected_action_args
    #    is set, at least one call must match all those argument key/value pairs.
    if test.expected_action:
        if test.expected_action not in called_names:
            return GradeResult(
                verdict=ReplayVerdict.FAIL,
                reason=(
                    f"Expected action '{test.expected_action}' was not called during replay. "
                    f"Actions called: {sorted(called_names) or 'none'}."
                ),
            )
        if test.expected_action_args is not None:
            arg_matches = [
                tc for tc in run.tool_calls
                if tc.name == test.expected_action
                and all(tc.arguments.get(k) == v for k, v in test.expected_action_args.items())
            ]
            if not arg_matches:
                return GradeResult(
                    verdict=ReplayVerdict.FAIL,
                    reason=(
                        f"Expected action '{test.expected_action}' was called but not with "
                        f"the required arguments {test.expected_action_args}."
                    ),
                )

    # 3. Sequence check — tools must appear in the specified relative order.
    #    Uses first occurrence of each tool name; they need not be consecutive.
    if test.required_sequence:
        first_pos: dict[str, int] = {}
        for i, tc in enumerate(run.tool_calls):
            if tc.name in test.required_sequence and tc.name not in first_pos:
                first_pos[tc.name] = i

        missing = [n for n in test.required_sequence if n not in first_pos]
        if missing:
            joined = ", ".join(f"'{n}'" for n in missing)
            return GradeResult(
                verdict=ReplayVerdict.FAIL,
                reason=f"Required sequence: tool(s) {joined} were not called.",
            )

        for a, b in zip(test.required_sequence, test.required_sequence[1:]):
            if first_pos[a] >= first_pos[b]:
                return GradeResult(
                    verdict=ReplayVerdict.FAIL,
                    reason=(
                        f"Required sequence violated: '{a}' must appear before '{b}'. "
                        f"Call order: {[tc.name for tc in run.tool_calls]}."
                    ),
                )

    return GradeResult(
        verdict=ReplayVerdict.PASS,
        reason="No forbidden actions called; all expected actions present.",
    )


def _grade_semantic(
    test: TestCase,
    run: CapturedRun,
    grader_model: str = "claude-haiku-4-5-20251001",
) -> GradeResult:
    """
    Ask an LLM whether the original failure recurred in this replay.

    Requires the ANTHROPIC_API_KEY environment variable (uses the
    Anthropic Messages API directly to avoid adding a hard dependency
    on a specific SDK version).

    grader_model: Anthropic model slug to use as the judge. Override via
    Replayd(grader_model="...") to avoid editing library source.
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
        model=grader_model,
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
