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
"""

from __future__ import annotations

import json
from typing import Any

from replayd.capture import _active_run_ctx

# Sentinel attribute names written onto the patched object so instrument_*
# calls are idempotent.
_OPENAI_PATCHED = "_replayd_patched_openai"
_ANTHROPIC_PATCHED = "_replayd_patched_anthropic"


def patch_openai_client(client: Any) -> None:
    """
    Wrap client.chat.completions.create to auto-record tool calls.

    Covers the synchronous, non-streaming OpenAI client only.
    Limitations:
      - stream=True is not supported — pass-through, nothing recorded.
      - AsyncOpenAI is not patched — use record_tool_call() for async agents.
    See README "Auto-instrumentation limitations" for the full picture.

    Idempotent — calling twice on the same client has no effect.
    Reversible via unpatch_openai_client().
    """
    completions = client.chat.completions
    if getattr(completions, _OPENAI_PATCHED, False):
        return

    original_create = completions.create

    def _patched_create(*args, **kwargs):
        ctx = _active_run_ctx.get()

        # Before the API call: extract tool results from incoming messages.
        # These arrive as role="tool" entries whose tool_call_id matches a
        # request we saw in a previous response.
        if ctx is not None:
            for msg in kwargs.get("messages", []):
                if not isinstance(msg, dict) or msg.get("role") != "tool":
                    continue
                call_id = msg.get("tool_call_id", "")
                if call_id in ctx._pending_tool_calls:
                    name, tool_args = ctx._pending_tool_calls.pop(call_id)
                    ctx.record_tool_call(name, tool_args, msg.get("content"))

        response = original_create(*args, **kwargs)

        # After the API call: register tool call requests from the response
        # so we can match them to results when the next message comes in.
        ctx = _active_run_ctx.get()
        if ctx is not None:
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

        return response

    completions.create = _patched_create
    # Store original for reversibility.
    completions._replayd_original_create = original_create
    setattr(completions, _OPENAI_PATCHED, True)


def patch_anthropic_client(client: Any) -> None:
    """
    Wrap client.messages.create to auto-record tool calls.

    Covers the synchronous, non-streaming Anthropic client only.
    Limitations:
      - stream=True / with_streaming_response is not supported.
      - AsyncAnthropic is not patched — use record_tool_call() for async agents.
    See README "Auto-instrumentation limitations" for the full picture.

    Idempotent — calling twice on the same client has no effect.
    Reversible via unpatch_anthropic_client().
    """
    messages_api = client.messages
    if getattr(messages_api, _ANTHROPIC_PATCHED, False):
        return

    original_create = messages_api.create

    def _patched_create(*args, **kwargs):
        ctx = _active_run_ctx.get()

        # Before the API call: extract tool results from incoming messages.
        # Anthropic uses role="user" messages with content blocks of
        # type="tool_result" containing a tool_use_id.
        if ctx is not None:
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

        response = original_create(*args, **kwargs)

        # After the API call: register tool_use blocks from the response.
        ctx = _active_run_ctx.get()
        if ctx is not None:
            for block in getattr(response, "content", []):
                if getattr(block, "type", None) == "tool_use":
                    ctx._pending_tool_calls[block.id] = (block.name, block.input)

        return response

    messages_api.create = _patched_create
    messages_api._replayd_original_create = original_create
    setattr(messages_api, _ANTHROPIC_PATCHED, True)


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
