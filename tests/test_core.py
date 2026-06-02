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


# --- new behaviour tests (v0.1.2) ------------------------------------------

# Issue 2: grader_model param is accepted without error
def test_grader_model_param_accepted(tmp_path):
    rp = Replayd(storage_dir=tmp_path / ".replayd", grader_model="claude-opus-4-5")
    assert rp._grader_model == "claude-opus-4-5"

    with rp.capture(input={"x": 1}) as run:
        run.output = mock_agent_bad({"x": 1}, run)
    rp.mark_failed(run.id, reason="test")
    rp.save_test(run.id, forbidden_actions=["approve_refund"])

    # Structural grading does not use grader_model, so this completes without error.
    results = rp.replay_all(agent=mock_agent_bad)
    assert results[0].verdict == ReplayVerdict.FAIL


# Issue 3: warning fires when run.output is not assigned
def test_capture_warns_when_output_is_none(tmp_path):
    import warnings as _warnings
    rp = make_rp(tmp_path)
    with _warnings.catch_warnings(record=True) as w:
        _warnings.simplefilter("always")
        with rp.capture(input="test") as run:
            pass  # deliberately do NOT assign run.output
    assert len(w) == 1
    assert "run.output is None" in str(w[0].message)


# Issue 3: no warning when output is assigned
def test_capture_no_warning_when_output_assigned(tmp_path):
    import warnings as _warnings
    rp = make_rp(tmp_path)
    with _warnings.catch_warnings(record=True) as w:
        _warnings.simplefilter("always")
        with rp.capture(input="test") as run:
            run.output = "done"
    assert len(w) == 0


# Issue 4: all forbidden violations reported in a single FAIL reason
def test_grader_reports_all_forbidden_violations(tmp_path):
    rp = make_rp(tmp_path)

    def multi_forbidden_agent(input, run_ctx: RunContext):
        run_ctx.record_tool_call("bad_tool_a", {}, None)
        run_ctx.record_tool_call("bad_tool_b", {}, None)
        return "done"

    with rp.capture(input={}) as run:
        run.output = multi_forbidden_agent({}, run)
    rp.mark_failed(run.id, reason="both bad tools called")
    rp.save_test(run.id, forbidden_actions=["bad_tool_a", "bad_tool_b"])

    results = rp.replay_all(agent=multi_forbidden_agent)
    assert results[0].verdict == ReplayVerdict.FAIL
    assert "bad_tool_a" in results[0].reason
    assert "bad_tool_b" in results[0].reason


# Issue 5: forbidden_call_args — only fails when args match
def test_forbidden_call_args_triggers_on_matching_args(tmp_path):
    rp = make_rp(tmp_path)

    def agent_with_amount(input, run_ctx: RunContext):
        run_ctx.record_tool_call("approve_refund", {"amount": input["amount"]}, None)
        return "done"

    # Capture a run that calls approve_refund with amount=1200
    inp = {"amount": 1200}
    with rp.capture(input=inp) as run:
        run.output = agent_with_amount(inp, run)
    rp.mark_failed(run.id, reason="large refund approved")
    # Only flag approve_refund when amount=1200
    rp.save_test(run.id, forbidden_actions=["approve_refund"],
                 forbidden_call_args={"amount": 1200})

    results = rp.replay_all(agent=agent_with_amount)
    assert results[0].verdict == ReplayVerdict.FAIL


# Issue 5: forbidden_call_args — passes when args do NOT match
def test_forbidden_call_args_passes_on_non_matching_args(tmp_path):
    rp = make_rp(tmp_path)

    def agent_small_refund(input, run_ctx: RunContext):
        run_ctx.record_tool_call("approve_refund", {"amount": 50}, None)
        return "done"

    # Capture a run that calls approve_refund with amount=1200
    inp_bad = {"amount": 1200}
    with rp.capture(input=inp_bad) as run:
        run.output = agent_small_refund(inp_bad, run)
    rp.mark_failed(run.id, reason="large refund approved")
    # Only flag approve_refund when amount=1200; agent_small_refund uses amount=50
    rp.save_test(run.id, forbidden_actions=["approve_refund"],
                 forbidden_call_args={"amount": 1200},
                 expected_action="approve_refund")

    inp_fixed = {"amount": 50}
    def agent_for_replay(input, run_ctx: RunContext):
        run_ctx.record_tool_call("approve_refund", {"amount": 50}, None)
        return "done"

    results = rp.replay_all(agent=agent_for_replay)
    # amount=50 does not match forbidden_call_args={"amount": 1200}, so PASS
    assert results[0].verdict == ReplayVerdict.PASS


# Issue 6: replay_one docstring — verify method works by test ID
def test_replay_one_by_test_id(tmp_path):
    rp = make_rp(tmp_path)
    user_input = {"amount": 999}

    with rp.capture(input=user_input) as run:
        run.output = mock_agent_bad(user_input, run)
    rp.mark_failed(run.id, reason="bad")
    test = rp.save_test(run.id, forbidden_actions=["approve_refund"])

    result = rp.replay_one(test.id, agent=mock_agent_bad)
    assert result.verdict == ReplayVerdict.FAIL

    result2 = rp.replay_one(test.id, agent=mock_agent_good)
    assert result2.verdict == ReplayVerdict.PASS


# --- deeper grading (v0.1.3) -----------------------------------------------

def test_expected_action_wrong_args_fails(tmp_path):
    """expected_action called with wrong argument value → FAIL."""
    rp = make_rp(tmp_path)

    def agent_wrong_channel(input, run_ctx: RunContext):
        run_ctx.record_tool_call("notify", {"channel": "email"}, None)
        return "done"

    with rp.capture(input={}) as run:
        run.output = agent_wrong_channel({}, run)
    rp.mark_failed(run.id, reason="wrong channel")
    rp.save_test(
        run.id,
        expected_action="notify",
        expected_action_args={"channel": "sms"},
    )

    results = rp.replay_all(agent=agent_wrong_channel)
    assert results[0].verdict == ReplayVerdict.FAIL
    assert "notify" in results[0].reason
    assert "required arguments" in results[0].reason


def test_expected_action_correct_args_passes(tmp_path):
    """expected_action called with matching args → PASS."""
    rp = make_rp(tmp_path)

    def agent_bad_channel(input, run_ctx: RunContext):
        run_ctx.record_tool_call("notify", {"channel": "email"}, None)
        return "done"

    def agent_good_channel(input, run_ctx: RunContext):
        run_ctx.record_tool_call("notify", {"channel": "sms"}, None)
        return "done"

    with rp.capture(input={}) as run:
        run.output = agent_bad_channel({}, run)
    rp.mark_failed(run.id, reason="wrong channel")
    rp.save_test(
        run.id,
        expected_action="notify",
        expected_action_args={"channel": "sms"},
    )

    results = rp.replay_all(agent=agent_good_channel)
    assert results[0].verdict == ReplayVerdict.PASS


def test_required_sequence_correct_order_passes(tmp_path):
    """Tools called in the required order → PASS."""
    rp = make_rp(tmp_path)

    def agent_bad(input, run_ctx: RunContext):
        run_ctx.record_tool_call("finalize", {}, None)
        return "done"

    def agent_good(input, run_ctx: RunContext):
        run_ctx.record_tool_call("check", {}, None)
        run_ctx.record_tool_call("finalize", {}, None)
        return "done"

    with rp.capture(input={}) as run:
        run.output = agent_bad({}, run)
    rp.mark_failed(run.id, reason="skipped check")
    rp.save_test(run.id, required_sequence=["check", "finalize"])

    results = rp.replay_all(agent=agent_good)
    assert results[0].verdict == ReplayVerdict.PASS


def test_required_sequence_wrong_order_fails(tmp_path):
    """Tools called in the wrong order → FAIL with 'sequence' in reason."""
    rp = make_rp(tmp_path)

    def agent_bad(input, run_ctx: RunContext):
        run_ctx.record_tool_call("finalize", {}, None)
        return "done"

    def agent_wrong_order(input, run_ctx: RunContext):
        run_ctx.record_tool_call("finalize", {}, None)
        run_ctx.record_tool_call("check", {}, None)
        return "done"

    with rp.capture(input={}) as run:
        run.output = agent_bad({}, run)
    rp.mark_failed(run.id, reason="skipped check")
    rp.save_test(run.id, required_sequence=["check", "finalize"])

    results = rp.replay_all(agent=agent_wrong_order)
    assert results[0].verdict == ReplayVerdict.FAIL
    assert "sequence" in results[0].reason.lower()


def test_required_sequence_missing_tool_fails(tmp_path):
    """Tool in required_sequence not called at all → FAIL."""
    rp = make_rp(tmp_path)

    def agent_bad(input, run_ctx: RunContext):
        run_ctx.record_tool_call("finalize", {}, None)
        return "done"

    def agent_skips_check(input, run_ctx: RunContext):
        run_ctx.record_tool_call("finalize", {}, None)
        return "done"

    with rp.capture(input={}) as run:
        run.output = agent_bad({}, run)
    rp.mark_failed(run.id, reason="skipped check")
    rp.save_test(run.id, required_sequence=["check", "finalize"])

    results = rp.replay_all(agent=agent_skips_check)
    assert results[0].verdict == ReplayVerdict.FAIL
    assert "check" in results[0].reason


def test_required_sequence_standalone_criterion(tmp_path):
    """required_sequence alone (without expected_action) is a valid criterion."""
    rp = make_rp(tmp_path)

    def agent_bad(input, run_ctx: RunContext):
        run_ctx.record_tool_call("only_tool", {}, None)
        return "done"

    with rp.capture(input={}) as run:
        run.output = agent_bad({}, run)
    rp.mark_failed(run.id, reason="bad order")
    # No forbidden_actions or expected_action — just a sequence requirement
    test = rp.save_test(run.id, required_sequence=["auth", "only_tool"])

    def agent_correct(input, run_ctx: RunContext):
        run_ctx.record_tool_call("auth", {}, None)
        run_ctx.record_tool_call("only_tool", {}, None)
        return "done"

    results = rp.replay_all(agent=agent_correct)
    assert results[0].verdict == ReplayVerdict.PASS
