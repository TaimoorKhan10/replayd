"""
Auto-instrumentation for OpenAI and Anthropic clients.

Call rp.instrument_openai(client) or rp.instrument_anthropic(client) once,
before entering any capture block. After that, use the client normally.
Tool calls the model requests during an active capture block are recorded
automatically — no manual record_tool_call() calls needed.

How it works
------------
The OpenAI and Anthropic APIs follow a two-step tool-call pattern:

  Step 1 — model response contains tool call *requests* (name + arguments).
  Step 2 — caller executes the tools and sends *results* back in the next request.

The instrumented create() wrapper intercepts both steps:
  - After step 1: saves each pending request keyed by the provider's call ID.
  - Before step 2: matches incoming tool results to the saved requests and
    records the complete ToolCall (name, arguments, result) into the active
    RunContext.

If there is no active capture block (_active_run_ctx is None) the wrapper is
a transparent pass-through — nothing is recorded and nothing crashes.

Limitations
-----------
  - stream=True: the wrapper detects this and emits a warnings.warn() inside
    an active capture block. Tool calls are not recorded from streamed responses.
    Use run_ctx.record_tool_call() manually, or disable streaming for captured runs.
  - Async clients: patch_openai_client detects async create() and emits a warning.
    Use run_ctx.record_tool_call() manually for async agents.
"""

from __future__ import annotations

import inspect
import json
import warnings
from typing import Any

from replayd.capture import _active_run_ctx

# Sentinel attribute names written onto the patched object so instrument_*
# calls are idempotent.
_OPENAI_PATCHED = "_replayd_patched_openai"
_ANTHROPIC_PATCHED = "_replayd_patched_anthropic"

_STREAMING_WARN = (
    "replayd: auto-instrumentation does not record tool calls from streaming responses. "
    "Use run_ctx.record_tool_call() to record them manually, "
    "or disable streaming for captured runs."
)

_ASYNC_OPENAI_WARN = (
    "replayd: auto-instrumentation does not support async OpenAI clients yet. "
    "Use run_ctx.record_tool_call() to record tool calls manually."
)

_ASYNC_ANTHROPIC_WARN = (
    "replayd: auto-instrumentation does not support async Anthropic clients yet. "
    "Use run_ctx.record_tool_call() to record tool calls manually."
)


# ---------------------------------------------------------------------------
# Shared extraction helpers (used by both sync and async wrappers)
# ---------------------------------------------------------------------------

def _openai_extract_results(ctx, kwargs: dict) -> None:
    """Record tool results from incoming role='tool' messages."""
    for msg in kwargs.get("messages", []):
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        call_id = msg.get("tool_call_id", "")
        if call_id in ctx._pending_tool_calls:
            name, tool_args = ctx._pending_tool_calls.pop(call_id)
            ctx.record_tool_call(name, tool_args, msg.get("content"))


def _openai_register_requests(ctx, response) -> None:
    """Store tool call requests from the response for matching on the next call."""
    for choice in getattr(response, "choices", []):
        msg = getattr(choice, "message", None)
        if msg is None:
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments)
            except Exception:
                tool_args = {}
            ctx._pending_tool_calls[tc.id] = (name, tool_args)


def _anthropic_extract_results(ctx, kwargs: dict) -> None:
    """Record tool results from Anthropic-format tool_result content blocks."""
    for msg in kwargs.get("messages", []):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            call_id = block.get("tool_use_id", "")
            if call_id in ctx._pending_tool_calls:
                name, tool_args = ctx._pending_tool_calls.pop(call_id)
                ctx.record_tool_call(name, tool_args, block.get("content"))


def _anthropic_register_requests(ctx, response) -> None:
    """Store tool_use blocks from the Anthropic response for matching on the next call."""
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use":
            ctx._pending_tool_calls[block.id] = (block.name, block.input)


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

def patch_openai_client(client: Any) -> None:
    """
    Wrap client.chat.completions.create to auto-record tool calls.

    Covers the synchronous, non-streaming OpenAI client only.
    Calling on an async client emits a warning and does not patch.
    Calling with stream=True inside a capture block emits a warning.

    Idempotent — calling twice on the same client has no effect.
    Reversible via unpatch_openai_client().
    """
    completions = client.chat.completions
    if getattr(completions, _OPENAI_PATCHED, False):
        return

    original_create = completions.create

    # Async clients are not supported yet — warn and leave the client untouched.
    if inspect.iscoroutinefunction(original_create):
        warnings.warn(_ASYNC_OPENAI_WARN, stacklevel=3)
        return

    def _patched_create(*args, **kwargs):
        ctx = _active_run_ctx.get()

        # Streaming is not supported — warn and pass through unchanged.
        if kwargs.get("stream") and ctx is not None:
            warnings.warn(_STREAMING_WARN, stacklevel=2)
            return original_create(*args, **kwargs)

        if ctx is not None:
            _openai_extract_results(ctx, kwargs)

        response = original_create(*args, **kwargs)

        ctx = _active_run_ctx.get()
        if ctx is not None:
            _openai_register_requests(ctx, response)

        return response

    completions.create = _patched_create
    completions._replayd_original_create = original_create
    setattr(completions, _OPENAI_PATCHED, True)


def unpatch_openai_client(client: Any) -> None:
    """
    Restore client.chat.completions.create to its original method.
    Idempotent — safe to call on an already-unpatched client.
    """
    completions = client.chat.completions
    if not getattr(completions, _OPENAI_PATCHED, False):
        return
    completions.create = completions._replayd_original_create
    try:
        delattr(completions, "_replayd_original_create")
    except AttributeError:
        pass
    setattr(completions, _OPENAI_PATCHED, False)


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

def patch_anthropic_client(client: Any) -> None:
    """
    Wrap client.messages.create to auto-record tool calls.

    Covers the synchronous, non-streaming Anthropic client only.
    Calling on an async client emits a warning and does not patch.
    Calling with stream=True inside a capture block emits a warning.

    Idempotent — calling twice on the same client has no effect.
    Reversible via unpatch_anthropic_client().
    """
    messages_api = client.messages
    if getattr(messages_api, _ANTHROPIC_PATCHED, False):
        return

    original_create = messages_api.create

    # Async clients are not supported yet — warn and leave the client untouched.
    if inspect.iscoroutinefunction(original_create):
        warnings.warn(_ASYNC_ANTHROPIC_WARN, stacklevel=3)
        return

    def _patched_create(*args, **kwargs):
        ctx = _active_run_ctx.get()

        # Streaming is not supported — warn and pass through unchanged.
        if kwargs.get("stream") and ctx is not None:
            warnings.warn(_STREAMING_WARN, stacklevel=2)
            return original_create(*args, **kwargs)

        if ctx is not None:
            _anthropic_extract_results(ctx, kwargs)

        response = original_create(*args, **kwargs)

        ctx = _active_run_ctx.get()
        if ctx is not None:
            _anthropic_register_requests(ctx, response)

        return response

    messages_api.create = _patched_create
    messages_api._replayd_original_create = original_create
    setattr(messages_api, _ANTHROPIC_PATCHED, True)


def unpatch_anthropic_client(client: Any) -> None:
    """
    Restore client.messages.create to its original method.
    Idempotent — safe to call on an already-unpatched client.
    """
    messages_api = client.messages
    if not getattr(messages_api, _ANTHROPIC_PATCHED, False):
        return
    messages_api.create = messages_api._replayd_original_create
    try:
        delattr(messages_api, "_replayd_original_create")
    except AttributeError:
        pass
    setattr(messages_api, _ANTHROPIC_PATCHED, False)
