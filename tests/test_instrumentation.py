"""
Tests for auto-instrumentation (instrument_openai / instrument_anthropic).

Uses minimal mock clients that mirror only the parts of the OpenAI and
Anthropic response shapes that the instrumentation layer inspects. No real
API calls are made.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from replayd import Replayd
from replayd.capture import RunContext
from replayd.models import ReplayVerdict


# ---------------------------------------------------------------------------
# Mock client builders
# ---------------------------------------------------------------------------

def _openai_response(tool_calls: list[dict] | None = None, content: str = "done"):
    """Build a minimal OpenAI-shaped response object."""
    if tool_calls:
        tc_objs = [
            SimpleNamespace(
                id=f"call_{i}",
                function=SimpleNamespace(
                    name=tc["name"],
                    arguments=json.dumps(tc["arguments"]),
                ),
            )
            for i, tc in enumerate(tool_calls)
        ]
        msg = SimpleNamespace(tool_calls=tc_objs, content=None)
    else:
        msg = SimpleNamespace(tool_calls=None, content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _make_openai_client(create_fn):
    """Wrap a create function in the minimal client structure instrument_openai expects."""
    completions = SimpleNamespace(create=create_fn)
    chat = SimpleNamespace(completions=completions)
    return SimpleNamespace(chat=chat)


def _anthropic_response(tool_uses: list[dict] | None = None, text: str = "done"):
    """Build a minimal Anthropic-shaped response object."""
    if tool_uses:
        blocks = [
            SimpleNamespace(
                type="tool_use",
                id=f"toolu_{i}",
                name=tu["name"],
                input=tu["input"],
            )
            for i, tu in enumerate(tool_uses)
        ]
    else:
        blocks = [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=blocks)


def _make_anthropic_client(create_fn):
    """Wrap a create function in the minimal client structure instrument_anthropic expects."""
    messages_api = SimpleNamespace(create=create_fn)
    return SimpleNamespace(messages=messages_api)


# ---------------------------------------------------------------------------
# OpenAI instrumentation
# ---------------------------------------------------------------------------

def test_openai_tool_calls_auto_recorded(tmp_path):
    """Full two-step loop: request → execute → result → final answer."""
    rp = Replayd(storage_dir=tmp_path / ".replayd")

    step = [0]

    def mock_create(**kwargs):
        step[0] += 1
        if step[0] == 1:
            return _openai_response(tool_calls=[{"name": "get_weather", "arguments": {"city": "London"}}])
        return _openai_response(content="It is sunny.")

    client = _make_openai_client(mock_create)
    rp.instrument_openai(client)

    with rp.capture(input="weather?") as run:
        messages = [{"role": "user", "content": "weather?"}]
        r1 = client.chat.completions.create(messages=messages)
        # Simulate executing the tool and feeding result back
        messages.append({"role": "tool", "tool_call_id": "call_0", "content": "Sunny, 22C"})
        r2 = client.chat.completions.create(messages=messages)
        run.output = r2.choices[0].message.content

    saved = rp.get_run(run.id)
    assert len(saved.tool_calls) == 1
    tc = saved.tool_calls[0]
    assert tc.name == "get_weather"
    assert tc.arguments == {"city": "London"}
    assert tc.result == "Sunny, 22C"


def test_openai_nothing_recorded_outside_capture(tmp_path):
    """Calls outside a capture block must not crash and must not record anything."""
    rp = Replayd(storage_dir=tmp_path / ".replayd")

    def mock_create(**kwargs):
        return _openai_response(tool_calls=[{"name": "some_tool", "arguments": {}}])

    client = _make_openai_client(mock_create)
    rp.instrument_openai(client)

    # No active capture block — should be a silent pass-through
    response = client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    assert response is not None  # no crash


def test_openai_instrument_is_idempotent(tmp_path):
    """Calling instrument_openai twice must not double-wrap."""
    rp = Replayd(storage_dir=tmp_path / ".replayd")
    call_count = [0]

    def mock_create(**kwargs):
        call_count[0] += 1
        return _openai_response()

    client = _make_openai_client(mock_create)
    rp.instrument_openai(client)
    rp.instrument_openai(client)  # second call should be a no-op

    with rp.capture(input="x") as run:
        client.chat.completions.create(messages=[])
        run.output = "ok"

    assert call_count[0] == 1  # underlying create called exactly once


def test_openai_multiple_tool_calls_in_one_response(tmp_path):
    """Two tool calls in one response → two records."""
    rp = Replayd(storage_dir=tmp_path / ".replayd")
    step = [0]

    def mock_create(**kwargs):
        step[0] += 1
        if step[0] == 1:
            return _openai_response(tool_calls=[
                {"name": "search", "arguments": {"q": "foo"}},
                {"name": "lookup", "arguments": {"id": 42}},
            ])
        return _openai_response()

    client = _make_openai_client(mock_create)
    rp.instrument_openai(client)

    with rp.capture(input="x") as run:
        messages = [{"role": "user", "content": "x"}]
        r1 = client.chat.completions.create(messages=messages)
        messages.append({"role": "tool", "tool_call_id": "call_0", "content": "res_a"})
        messages.append({"role": "tool", "tool_call_id": "call_1", "content": "res_b"})
        r2 = client.chat.completions.create(messages=messages)
        run.output = "ok"

    saved = rp.get_run(run.id)
    assert len(saved.tool_calls) == 2
    names = {tc.name for tc in saved.tool_calls}
    assert names == {"search", "lookup"}


def test_openai_auto_records_during_replay(tmp_path):
    """Auto-instrumentation must work inside replay_all, not just capture."""
    rp = Replayd(storage_dir=tmp_path / ".replayd")
    step = [0]

    def mock_create(**kwargs):
        step[0] += 1
        if step[0] % 2 == 1:
            return _openai_response(tool_calls=[{"name": "ping", "arguments": {}}])
        return _openai_response()

    client = _make_openai_client(mock_create)
    rp.instrument_openai(client)

    # Initial capture
    step[0] = 0
    with rp.capture(input="test") as run:
        msgs = [{"role": "user", "content": "test"}]
        client.chat.completions.create(messages=msgs)
        msgs.append({"role": "tool", "tool_call_id": "call_0", "content": "pong"})
        client.chat.completions.create(messages=msgs)
        run.output = "done"

    rp.mark_failed(run.id, reason="test")
    rp.save_test(run.id, expected_action="ping")

    # Reset step counter so replay gets the same response sequence
    def agent(inp, run_ctx):
        step[0] = 0
        msgs = [{"role": "user", "content": inp}]
        client.chat.completions.create(messages=msgs)
        msgs.append({"role": "tool", "tool_call_id": "call_0", "content": "pong"})
        client.chat.completions.create(messages=msgs)
        return "done"

    results = rp.replay_all(agent=agent)
    assert results[0].verdict == ReplayVerdict.PASS


# ---------------------------------------------------------------------------
# Anthropic instrumentation
# ---------------------------------------------------------------------------

def test_anthropic_tool_calls_auto_recorded(tmp_path):
    """Full two-step loop for Anthropic format."""
    rp = Replayd(storage_dir=tmp_path / ".replayd")
    step = [0]

    def mock_create(**kwargs):
        step[0] += 1
        if step[0] == 1:
            return _anthropic_response(tool_uses=[{"name": "fetch_price", "input": {"item": "apple"}}])
        return _anthropic_response(text="$1.20")

    client = _make_anthropic_client(mock_create)
    rp.instrument_anthropic(client)

    with rp.capture(input="price?") as run:
        messages = [{"role": "user", "content": "price?"}]
        r1 = client.messages.create(messages=messages)
        # Feed result back in Anthropic format
        messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_0", "content": "$1.20"}],
        })
        r2 = client.messages.create(messages=messages)
        run.output = r2.content[0].text

    saved = rp.get_run(run.id)
    assert len(saved.tool_calls) == 1
    tc = saved.tool_calls[0]
    assert tc.name == "fetch_price"
    assert tc.arguments == {"item": "apple"}
    assert tc.result == "$1.20"


def test_anthropic_nothing_recorded_outside_capture(tmp_path):
    rp = Replayd(storage_dir=tmp_path / ".replayd")

    def mock_create(**kwargs):
        return _anthropic_response(tool_uses=[{"name": "some_tool", "input": {}}])

    client = _make_anthropic_client(mock_create)
    rp.instrument_anthropic(client)

    response = client.messages.create(messages=[{"role": "user", "content": "hi"}])
    assert response is not None


def test_anthropic_instrument_is_idempotent(tmp_path):
    rp = Replayd(storage_dir=tmp_path / ".replayd")
    call_count = [0]

    def mock_create(**kwargs):
        call_count[0] += 1
        return _anthropic_response()

    client = _make_anthropic_client(mock_create)
    rp.instrument_anthropic(client)
    rp.instrument_anthropic(client)

    with rp.capture(input="x") as run:
        client.messages.create(messages=[])
        run.output = "ok"

    assert call_count[0] == 1


# ---------------------------------------------------------------------------
# Manual record_tool_call still works alongside instrumentation
# ---------------------------------------------------------------------------

def test_manual_record_still_works(tmp_path):
    rp = Replayd(storage_dir=tmp_path / ".replayd")

    with rp.capture(input="test") as run:
        run.record_tool_call("manual_tool", {"x": 1}, "result")
        run.output = "done"

    saved = rp.get_run(run.id)
    assert len(saved.tool_calls) == 1
    assert saved.tool_calls[0].name == "manual_tool"


def test_manual_and_auto_can_coexist(tmp_path):
    """Manual calls and instrumented calls both appear in the record."""
    rp = Replayd(storage_dir=tmp_path / ".replayd")
    step = [0]

    def mock_create(**kwargs):
        step[0] += 1
        if step[0] == 1:
            return _openai_response(tool_calls=[{"name": "auto_tool", "arguments": {}}])
        return _openai_response()

    client = _make_openai_client(mock_create)
    rp.instrument_openai(client)

    with rp.capture(input="x") as run:
        msgs = [{"role": "user", "content": "x"}]
        client.chat.completions.create(messages=msgs)
        msgs.append({"role": "tool", "tool_call_id": "call_0", "content": "auto_result"})
        client.chat.completions.create(messages=msgs)
        run.record_tool_call("manual_tool", {}, "manual_result")
        run.output = "done"

    saved = rp.get_run(run.id)
    names = [tc.name for tc in saved.tool_calls]
    assert "auto_tool" in names
    assert "manual_tool" in names
