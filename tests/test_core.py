"""
Tests for core replayd behaviour.

Uses pytest's tmp_path fixture for isolated storage per test.
"""

import pytest

from replayd import Replayd
from replayd.capture import RunContext
from replayd.models import ReplayVerdict, RunStatus


# --- shared agents ---------------------------------------------------------

def mock_agent_bad(input, run_ctx: RunContext):
    run_ctx.record_tool_call("approve_refund", {"amount": 1000}, {"ok": True})
    return {"action": "approve_refund"}


def mock_agent_good(input, run_ctx: RunContext):
    run_ctx.record_tool_call("escalate", {"reason": "over limit"}, {"ticket": "T1"})
    return {"action": "escalate"}


def make_rp(tmp_path):
    return Replayd(storage_dir=tmp_path / ".replayd")


# --- capture ---------------------------------------------------------------

def test_capture_saves_run(tmp_path):
    rp = make_rp(tmp_path)
    user_input = {"msg": "hello"}

    with rp.capture(input=user_input) as run:
        run.output = "hi back"

    saved = rp.get_run(run.id)
    assert saved.input == user_input
    assert saved.output == "hi back"
    assert saved.status == RunStatus.CAPTURED


def test_capture_records_tool_calls(tmp_path):
    rp = make_rp(tmp_path)

    with rp.capture(input="test") as run:
        run.record_tool_call("search", {"q": "foo"}, ["result1"])
        run.output = "done"

    saved = rp.get_run(run.id)
    assert len(saved.tool_calls) == 1
    assert saved.tool_calls[0].name == "search"


# --- mark_failed -----------------------------------------------------------

def test_mark_failed_sets_status_and_reason(tmp_path):
    rp = make_rp(tmp_path)

    with rp.capture(input="x") as run:
        run.output = "y"

    rp.mark_failed(run.id, reason="bad output")
    saved = rp.get_run(run.id)
    assert saved.status == RunStatus.FAILED
    assert saved.failure_reason == "bad output"


def test_mark_failed_raises_for_unknown_run(tmp_path):
    rp = make_rp(tmp_path)
    with pytest.raises(KeyError):
        rp.mark_failed("no-such-id", reason="x")


# --- save_test -------------------------------------------------------------

def test_save_test_rejects_run_not_yet_marked_failed(tmp_path):
    rp = make_rp(tmp_path)

    with rp.capture(input="x") as run:
        run.output = "y"

    with pytest.raises(ValueError, match="not been marked as failed"):
        rp.save_test(run.id, forbidden_actions=["bad_tool"])


def test_save_test_requires_at_least_one_grading_criterion(tmp_path):
    rp = make_rp(tmp_path)

    with rp.capture(input="x") as run:
        run.output = "y"
    rp.mark_failed(run.id, reason="bad")

    with pytest.raises(ValueError, match="at least one"):
        rp.save_test(run.id)


def test_save_test_persists_forbidden_and_expected_actions(tmp_path):
    rp = make_rp(tmp_path)

    with rp.capture(input="x") as run:
        run.output = "y"
    rp.mark_failed(run.id, reason="bad")
    test = rp.save_test(run.id, forbidden_actions=["bad_tool"], expected_action="good_tool")

    saved = rp.get_test(test.id)
    assert saved.run_id == run.id
    assert saved.forbidden_actions == ["bad_tool"]
    assert saved.expected_action == "good_tool"


# --- grader (structural) ---------------------------------------------------

def test_grader_fails_when_forbidden_action_is_called(tmp_path):
    rp = make_rp(tmp_path)
    user_input = {"amount": 999}

    with rp.capture(input=user_input) as run:
        run.output = mock_agent_bad(user_input, run)

    rp.mark_failed(run.id, reason="approved too large refund")
    rp.save_test(run.id, forbidden_actions=["approve_refund"], expected_action="escalate")

    results = rp.replay_all(agent=mock_agent_bad)
    assert len(results) == 1
    assert results[0].verdict == ReplayVerdict.FAIL
    assert "approve_refund" in results[0].reason


def test_grader_passes_when_fix_removes_forbidden_action(tmp_path):
    rp = make_rp(tmp_path)
    user_input = {"amount": 999}

    with rp.capture(input=user_input) as run:
        run.output = mock_agent_bad(user_input, run)

    rp.mark_failed(run.id, reason="approved too large refund")
    rp.save_test(run.id, forbidden_actions=["approve_refund"], expected_action="escalate")

    results = rp.replay_all(agent=mock_agent_good)
    assert len(results) == 1
    assert results[0].verdict == ReplayVerdict.PASS


def test_grader_fails_when_expected_action_is_missing(tmp_path):
    rp = make_rp(tmp_path)

    with rp.capture(input={"x": 1}) as run:
        run.output = "noop"

    rp.mark_failed(run.id, reason="missing escalation")
    rp.save_test(run.id, expected_action="escalate")

    def noop_agent(input, run_ctx):
        return "noop"

    results = rp.replay_all(agent=noop_agent)
    assert results[0].verdict == ReplayVerdict.FAIL
    assert "escalate" in results[0].reason


# --- replay_all ------------------------------------------------------------

def test_replay_all_runs_every_saved_test(tmp_path):
    rp = make_rp(tmp_path)
    user_input = {"amount": 999}

    for i in range(3):
        with rp.capture(input=user_input) as run:
            run.output = mock_agent_bad(user_input, run)
        rp.mark_failed(run.id, reason=f"failure {i}")
        rp.save_test(run.id, forbidden_actions=["approve_refund"])

    results = rp.replay_all(agent=mock_agent_bad)
    assert len(results) == 3
    assert all(r.verdict == ReplayVerdict.FAIL for r in results)


def test_replay_all_stops_after_first_failure_when_requested(tmp_path):
    rp = make_rp(tmp_path)
    user_input = {"amount": 999}

    for i in range(3):
        with rp.capture(input=user_input) as run:
            run.output = mock_agent_bad(user_input, run)
        rp.mark_failed(run.id, reason=f"failure {i}")
        rp.save_test(run.id, forbidden_actions=["approve_refund"])

    results = rp.replay_all(agent=mock_agent_bad, stop_on_first_failure=True)
    assert len(results) == 1


def test_replay_all_returns_empty_when_no_tests_saved(tmp_path):
    rp = make_rp(tmp_path)
    results = rp.replay_all(agent=mock_agent_good)
    assert results == []
