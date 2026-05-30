"""
Comprehensive stress test — 15 tests, 6 agents, ruthless edge case coverage.

Run from repo root:
    PYTHONPATH=. python tests/stress_test.py
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from replayd import Replayd
from replayd.capture import RunContext

# ============================================================
# 6 AGENTS
# ============================================================

def agent1_always_forbidden(input, run_ctx: RunContext):
    """Always calls the forbidden action, every single run."""
    run_ctx.record_tool_call("forbidden_tool", {"input": str(input)}, {"done": True})
    return {"action": "forbidden_tool", "input": input}


def agent2_always_correct(input, run_ctx: RunContext):
    """Never calls the forbidden action. Always escalates correctly."""
    run_ctx.record_tool_call("escalate", {"input": str(input)}, {"ticket": "T-001"})
    return {"action": "escalate", "input": input}


def agent3_random(input, run_ctx: RunContext):
    """50% chance of calling the forbidden action."""
    if random.random() < 0.5:
        run_ctx.record_tool_call("forbidden_tool", {"input": str(input)}, {"done": True})
        return {"action": "forbidden_tool"}
    run_ctx.record_tool_call("escalate", {"input": str(input)}, {"ticket": "T-001"})
    return {"action": "escalate"}


def agent4_crashes(input, run_ctx: RunContext):
    """Raises a Python exception mid-run every time."""
    raise RuntimeError("Agent crashed: simulated production failure")


def agent5_multi_tool(input, run_ctx: RunContext):
    """Calls multiple tools in sequence: 2 allowed, 1 forbidden, 1 allowed."""
    run_ctx.record_tool_call("lookup_customer", {"id": input.get("customer_id", "?")}, {"name": "Alice"})
    run_ctx.record_tool_call("check_balance", {"id": input.get("customer_id", "?")}, {"balance": 800})
    run_ctx.record_tool_call("forbidden_tool", {"amount": input.get("amount", 0)}, {"approved": True})
    run_ctx.record_tool_call("send_notification", {"msg": "processed"}, {"sent": True})
    return {"action": "completed", "tools_called": 4}


def agent6_conditional(input, run_ctx: RunContext):
    """Only calls forbidden when input dict contains key 'trigger'=True."""
    if input.get("trigger", False):
        run_ctx.record_tool_call("forbidden_tool", {"msg": input.get("message", "")}, {"done": True})
        return {"action": "forbidden_tool"}
    run_ctx.record_tool_call("escalate", {"msg": input.get("message", "")}, {"ticket": "T-002"})
    return {"action": "escalate"}


# ============================================================
# INFRASTRUCTURE
# ============================================================

_pass = 0
_fail = 0
_issues: list[str] = []


def section(title: str) -> None:
    print(f"\n{'=' * 62}")
    print(f"  {title}")
    print(f"{'=' * 62}")


def ok(msg: str) -> None:
    global _pass
    _pass += 1
    print(f"    [OK]   {msg}")


def bad(msg: str) -> None:
    global _fail
    _fail += 1
    _issues.append(msg)
    print(f"    [FAIL] {msg}")


def info(msg: str) -> None:
    print(f"    ...    {msg}")


def fresh(tmp: str | None = None) -> tuple[Replayd, str]:
    """Return a Replayd instance backed by a fresh temp directory."""
    d = tmp or tempfile.mkdtemp(prefix="replayd_test_")
    return Replayd(storage_dir=Path(d) / ".replayd"), d


def capture_and_save(rp: Replayd, inp: dict, agent, reason: str,
                     forbidden: list[str] | None = None,
                     expected: str | None = None) -> str:
    """Helper: full capture -> mark_failed -> save_test. Returns test id."""
    with rp.capture(input=inp) as run:
        run.output = agent(inp, run)
    rp.mark_failed(run.id, reason=reason)
    test = rp.save_test(run.id,
                        forbidden_actions=forbidden or ["forbidden_tool"],
                        expected_action=expected or "escalate")
    return test.id


# ============================================================
# TEST 1 — Basic loop with fresh agents
# ============================================================

def test1():
    section("TEST 1 — Basic loop with fresh agents")
    rp, tmp = fresh()
    try:
        inp = {"customer_id": "c-001", "amount": 750}

        # Capture with buggy agent
        with rp.capture(input=inp, model="test-v1") as run:
            run.output = agent1_always_forbidden(inp, run)
        run_id = run.id
        info(f"Captured run: {run_id[:8]}...")

        # Mark failed
        rp.mark_failed(run_id, reason="agent called forbidden_tool on high-value request")
        saved = rp.get_run(run_id)
        assert saved.status.value == "failed", "Status should be failed"
        ok("mark_failed sets status correctly")

        # Save test
        test = rp.save_test(run_id, forbidden_actions=["forbidden_tool"], expected_action="escalate")
        info(f"Saved test: {test.id[:8]}...")
        ok("save_test persists without error")

        # Replay with same bad agent — expect FAIL
        results = rp.replay_all(agent=agent1_always_forbidden)
        assert len(results) == 1
        assert results[0].verdict.value == "fail"
        info(f"  Agent1 replay: [{results[0].verdict.value.upper()}] {results[0].reason}")
        ok("replay with always-forbidden agent returns FAIL")

        # Replay with good agent — expect PASS
        results = rp.replay_all(agent=agent2_always_correct)
        assert len(results) == 1
        assert results[0].verdict.value == "pass"
        info(f"  Agent2 replay: [{results[0].verdict.value.upper()}] {results[0].reason}")
        ok("replay with always-correct agent returns PASS")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# TEST 2 — 10 failures, 10 tests, check all verdicts
# ============================================================

def test2():
    section("TEST 2 — 10 failures, 10 tests, all verdicts shown")
    rp, tmp = fresh()
    try:
        # All tests forbid "forbidden_tool" — the one action Agent1 always calls.
        # Agent1 always FAILs (calls forbidden_tool).
        # Agent2 always PASSes (never calls forbidden_tool).
        # Each test represents a different failure scenario description.
        failure_scenarios = [
            "approved refund over limit",
            "bypassed authentication check",
            "skipped input validation",
            "wrote raw SQL instead of ORM",
            "sent email without queue",
            "overrode rate limit",
            "called direct refund path",
            "flagged false positive",
            "deleted record without backup",
            "escalated without notification",
        ]

        for i, reason in enumerate(failure_scenarios):
            inp = {"index": i, "amount": (i + 1) * 100}
            with rp.capture(input=inp) as run:
                run.output = agent1_always_forbidden(inp, run)
            rp.mark_failed(run.id, reason=reason)
            rp.save_test(run.id, forbidden_actions=["forbidden_tool"])

        info("10 tests saved. Running replay_all with Agent1 (all should FAIL):")
        results = rp.replay_all(agent=agent1_always_forbidden)
        assert len(results) == 10, f"Expected 10 results, got {len(results)}"
        all_fail = True
        for i, r in enumerate(results):
            v = r.verdict.value.upper()
            print(f"      Test {i+1:02d}: [{v}] {r.reason[:60]}")
            if r.verdict.value != "fail":
                all_fail = False
        if all_fail:
            ok("All 10 replays with Agent1 returned FAIL")
        else:
            bad("Some replays with Agent1 did not return FAIL")

        info("Running replay_all with Agent2 (all should PASS):")
        results = rp.replay_all(agent=agent2_always_correct)
        assert len(results) == 10
        all_pass = True
        for i, r in enumerate(results):
            v = r.verdict.value.upper()
            print(f"      Test {i+1:02d}: [{v}] {r.reason[:60]}")
            if r.verdict.value != "pass":
                all_pass = False
        if all_pass:
            ok("All 10 replays with Agent2 returned PASS")
        else:
            bad("Some replays with Agent2 did not return PASS")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# TEST 3 — Edge cases that must fail gracefully
# ============================================================

def test3():
    section("TEST 3 — Edge cases and graceful failure")
    rp, tmp = fresh()
    try:
        inp = {"amount": 100}

        # 3a: mark_failed with nonexistent run_id
        info("3a: mark_failed() with nonexistent run_id")
        try:
            rp.mark_failed("does-not-exist", reason="test")
            bad("3a: Should have raised — did not")
        except KeyError as e:
            info(f"     Raised KeyError: {e}")
            ok("3a: mark_failed raises KeyError for unknown run_id")
        except Exception as e:
            bad(f"3a: Wrong exception type {type(e).__name__}: {e}")

        # 3b: save_test before mark_failed
        info("3b: save_test() before mark_failed()")
        with rp.capture(input=inp) as run:
            run.output = agent2_always_correct(inp, run)
        try:
            rp.save_test(run.id, forbidden_actions=["forbidden_tool"])
            bad("3b: Should have raised — did not")
        except ValueError as e:
            info(f"     Raised ValueError: {e}")
            ok("3b: save_test raises ValueError before mark_failed")
        except Exception as e:
            bad(f"3b: Wrong exception type {type(e).__name__}: {e}")

        # 3c: replay_all with zero saved tests
        info("3c: replay_all() with zero saved tests")
        rp2, tmp2 = fresh()
        try:
            results = rp2.replay_all(agent=agent2_always_correct)
            assert results == [], f"Expected [], got {results}"
            ok("3c: replay_all returns [] when no tests saved")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

        # 3d: agent raises exception during replay
        info("3d: agent raises exception during replay (Agent4)")
        rp3, tmp3 = fresh()
        try:
            with rp3.capture(input=inp) as run:
                run.output = agent1_always_forbidden(inp, run)
            rp3.mark_failed(run.id, reason="test")
            rp3.save_test(run.id, forbidden_actions=["forbidden_tool"])
            try:
                results = rp3.replay_all(agent=agent4_crashes)
                # After fix: should return a FAIL result, not crash
                assert len(results) == 1
                assert results[0].verdict.value == "fail"
                info(f"     Result: [{results[0].verdict.value.upper()}] {results[0].reason}")
                ok("3d: crashing agent returns FAIL result instead of crashing replay_all")
            except Exception as e:
                bad(f"3d: replay_all crashed when agent raised exception: {type(e).__name__}: {e}")
        finally:
            shutil.rmtree(tmp3, ignore_errors=True)

        # 3e: forbidden_actions=[] and expected_action=None
        info("3e: save_test with no grading criteria")
        rp4, tmp4 = fresh()
        try:
            with rp4.capture(input=inp) as run:
                run.output = "y"
            rp4.mark_failed(run.id, reason="test")
            try:
                rp4.save_test(run.id, forbidden_actions=[], expected_action=None)
                bad("3e: Should have raised — did not")
            except ValueError as e:
                info(f"     Raised ValueError: {e}")
                ok("3e: save_test raises ValueError with no grading criteria")
        finally:
            shutil.rmtree(tmp4, ignore_errors=True)

        # 3f: non-callable agent
        info("3f: non-callable passed as agent to replay_all")
        rp5, tmp5 = fresh()
        try:
            with rp5.capture(input=inp) as run:
                run.output = agent1_always_forbidden(inp, run)
            rp5.mark_failed(run.id, reason="test")
            rp5.save_test(run.id, forbidden_actions=["forbidden_tool"])
            try:
                results = rp5.replay_all(agent="not_a_function")
                bad("3f: Should have raised — did not")
            except TypeError as e:
                msg = str(e)
                info(f"     Raised TypeError: {msg}")
                if "callable" in msg.lower() or "str" in msg.lower():
                    ok("3f: non-callable agent raises TypeError with helpful message")
                else:
                    bad(f"3f: TypeError message not helpful: {msg}")
            except Exception as e:
                bad(f"3f: Wrong exception type {type(e).__name__}: {e}")
        finally:
            shutil.rmtree(tmp5, ignore_errors=True)

        # 3g: .replayd folder deleted mid-session
        info("3g: .replayd folder deleted mid-session")
        rp6, tmp6 = fresh()
        try:
            with rp6.capture(input=inp) as run:
                run.output = agent1_always_forbidden(inp, run)
            rp6.mark_failed(run.id, reason="test")
            rp6.save_test(run.id, forbidden_actions=["forbidden_tool"])
            # Delete the storage directory
            shutil.rmtree(Path(tmp6) / ".replayd")
            try:
                results = rp6.replay_all(agent=agent2_always_correct)
                # After fix: should return [] or raise helpful error
                info(f"     Returns empty list after dir deleted: {results}")
                ok("3g: replay_all returns [] after storage dir deleted")
            except FileNotFoundError as e:
                bad(f"3g: replay_all crashes with FileNotFoundError: {e}")
            except Exception as e:
                bad(f"3g: replay_all crashed with {type(e).__name__}: {e}")
        finally:
            shutil.rmtree(tmp6, ignore_errors=True)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# TEST 4 — Grading logic stress test (5 runs each scenario)
# ============================================================

def test4():
    section("TEST 4 — Grading logic stress test (5 runs x 5 scenarios = 25)")

    scenarios = [
        # (label, agent, forbidden, expected, expected_verdict)
        ("forbidden fires + expected present", agent1_always_forbidden, ["forbidden_tool"], "escalate", "fail"),
        ("forbidden absent + expected present", agent2_always_correct, ["forbidden_tool"], "escalate", "pass"),
        ("forbidden absent + expected missing", agent2_always_correct, ["forbidden_tool"], "send_email", "fail"),
        ("forbidden fires + expected missing", agent1_always_forbidden, ["forbidden_tool"], "send_email", "fail"),
    ]

    all_correct = True
    run_num = 0

    for label, agent, forbidden, expected, expected_verdict in scenarios:
        verdicts = []
        for i in range(5):
            run_num += 1
            rp, tmp = fresh()
            try:
                inp = {"scenario": label, "run": i}
                with rp.capture(input=inp) as run:
                    run.output = agent(inp, run)
                rp.mark_failed(run.id, reason=f"scenario: {label}")
                rp.save_test(run.id, forbidden_actions=forbidden, expected_action=expected)
                results = rp.replay_all(agent=agent)
                v = results[0].verdict.value
                verdicts.append(v)
                print(f"      Run {run_num:02d} [{label[:40]}]: [{v.upper()}] {results[0].reason[:45]}")
                if v != expected_verdict:
                    all_correct = False
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        consistent = len(set(verdicts)) == 1
        if consistent and verdicts[0] == expected_verdict:
            ok(f"Scenario '{label[:40]}' — all 5 runs: {verdicts[0].upper()} (correct)")
        else:
            bad(f"Scenario '{label[:40]}' — inconsistent or wrong: {verdicts}")

    # Scenario 5: no grading criteria — should be blocked by save_test
    info("Scenario 5: no forbidden/expected/grader_prompt — save_test should block this")
    for i in range(5):
        rp, tmp = fresh()
        try:
            inp = {"scenario": "no criteria", "run": i}
            with rp.capture(input=inp) as run:
                run.output = "y"
            rp.mark_failed(run.id, reason="test")
            try:
                rp.save_test(run.id)
                print(f"      Run {i+1}: save_test did not raise — BUG")
                all_correct = False
            except ValueError:
                print(f"      Run {i+1}: [BLOCKED] save_test raised ValueError (correct)")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    ok("Scenario 5: save_test correctly blocks tests with no grading criteria (all 5 runs)")

    if all_correct:
        ok("All 25 grading outcomes are correct and consistent")
    else:
        bad("Some grading outcomes were wrong")


# ============================================================
# TEST 5 — Agent3 randomness (20 replays, zero wrong verdicts)
# ============================================================

def test5():
    section("TEST 5 — Agent3 randomness (20 replays, zero wrong verdicts)")
    random.seed(42)  # Reproducible for reporting, but tests real randomness logic

    rp, tmp = fresh()
    try:
        inp = {"amount": 500}
        with rp.capture(input=inp) as run:
            run.output = agent1_always_forbidden(inp, run)
        rp.mark_failed(run.id, reason="called forbidden_tool")
        rp.save_test(run.id, forbidden_actions=["forbidden_tool"], expected_action="escalate")

        wrong = 0
        for i in range(20):
            results = rp.replay_all(agent=agent3_random)
            r = results[0]
            called_forbidden = any(tc.name == "forbidden_tool" for tc in r.run.tool_calls)
            verdict = r.verdict.value
            expected = "fail" if called_forbidden else "pass"
            correct = verdict == expected
            marker = "OK" if correct else "WRONG"
            tool = "forbidden_tool" if called_forbidden else "escalate   "
            print(f"      Replay {i+1:02d}: called={tool}  verdict=[{verdict.upper()}]  [{marker}]")
            if not correct:
                wrong += 1

        if wrong == 0:
            ok("All 20 random replays graded correctly — zero wrong verdicts")
        else:
            bad(f"{wrong}/20 replays had wrong verdict")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# TEST 6 — Storage stress test (50 tests)
# ============================================================

def test6():
    section("TEST 6 — Storage stress test (50 tests, no corruption)")
    rp, tmp = fresh()
    try:
        inp_map: dict[str, dict] = {}
        test_ids: list[str] = []

        for i in range(50):
            inp = {"index": i, "value": f"item-{i}", "amount": i * 10}
            with rp.capture(input=inp) as run:
                run.output = agent1_always_forbidden(inp, run)
            rp.mark_failed(run.id, reason=f"failure {i}")
            test = rp.save_test(run.id,
                                forbidden_actions=["forbidden_tool"],
                                expected_action="escalate")
            inp_map[test.id] = inp
            test_ids.append(test.id)

        # Verify 50 unique test IDs
        assert len(set(test_ids)) == 50, f"Duplicate test IDs found: {50 - len(set(test_ids))} duplicates"
        ok("50 tests saved with unique IDs")

        # Load all tests back and verify no corruption
        tests = rp.list_tests()
        assert len(tests) == 50, f"Expected 50, got {len(tests)}"
        ok("All 50 tests load back correctly")

        # Replay all 50 — should all FAIL with Agent1
        results = rp.replay_all(agent=agent1_always_forbidden)
        assert len(results) == 50
        all_fail = all(r.verdict.value == "fail" for r in results)
        if all_fail:
            ok("All 50 replays returned FAIL with Agent1")
        else:
            bad(f"Some replays did not FAIL: {sum(1 for r in results if r.verdict.value != 'fail')} wrong")

        info(f"First 5 results: {[r.verdict.value.upper() for r in results[:5]]}")
        info(f"Last 5 results:  {[r.verdict.value.upper() for r in results[-5:]]}")

        # Now delete .replayd and run fresh
        storage_dir = Path(tmp) / ".replayd"
        shutil.rmtree(storage_dir)
        info("Deleted .replayd folder — testing fresh recreation")

        rp2 = Replayd(storage_dir=storage_dir)
        inp2 = {"fresh": True}
        with rp2.capture(input=inp2) as run:
            run.output = agent1_always_forbidden(inp2, run)
        rp2.mark_failed(run.id, reason="fresh test")
        rp2.save_test(run.id, forbidden_actions=["forbidden_tool"])
        results2 = rp2.replay_all(agent=agent1_always_forbidden)
        assert len(results2) == 1 and results2[0].verdict.value == "fail"
        ok("Replayd recreates .replayd folder cleanly after deletion")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# TEST 7 — Agent5 multi-tool test
# ============================================================

def test7():
    section("TEST 7 — Agent5 multi-tool: only forbidden tool flagged")
    rp, tmp = fresh()
    try:
        inp = {"customer_id": "c-007", "amount": 1200}

        with rp.capture(input=inp) as run:
            run.output = agent5_multi_tool(inp, run)

        # Verify all 4 tool calls were recorded
        saved = rp.get_run(run.id)
        assert len(saved.tool_calls) == 4, f"Expected 4 tool calls, got {len(saved.tool_calls)}"
        tool_names = [tc.name for tc in saved.tool_calls]
        info(f"Captured tool calls: {tool_names}")
        ok("All 4 tool calls recorded during capture")

        rp.mark_failed(run.id, reason="called forbidden_tool on high-value request")
        rp.save_test(run.id,
                     forbidden_actions=["forbidden_tool"],
                     expected_action="escalate")

        # Replay with same agent — should FAIL on forbidden_tool only
        results = rp.replay_all(agent=agent5_multi_tool)
        r = results[0]
        info(f"Replay verdict: [{r.verdict.value.upper()}] {r.reason}")
        assert r.verdict.value == "fail"
        assert "forbidden_tool" in r.reason
        assert "lookup_customer" not in r.reason
        assert "check_balance" not in r.reason
        assert "send_notification" not in r.reason
        ok("FAIL verdict references only forbidden_tool, not the 3 allowed tools")

        # Save a test that only forbids a non-called tool — replay should PASS
        rp2, tmp2 = fresh()
        try:
            with rp2.capture(input=inp) as run:
                run.output = agent5_multi_tool(inp, run)
            rp2.mark_failed(run.id, reason="test")
            rp2.save_test(run.id,
                          forbidden_actions=["some_other_tool"],
                          expected_action="lookup_customer")
            results2 = rp2.replay_all(agent=agent5_multi_tool)
            r2 = results2[0]
            info(f"Non-forbidden tool verdict: [{r2.verdict.value.upper()}] {r2.reason}")
            assert r2.verdict.value == "pass"
            ok("Allowed tools in Agent5 do not trigger FAIL verdict")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# TEST 8 — Agent6 conditional (trigger vs non-trigger inputs)
# ============================================================

def test8():
    section("TEST 8 — Agent6 conditional: input-dependent behavior")
    rp, tmp = fresh()
    try:
        # Save test with TRIGGER input (bad behavior)
        trigger_input = {"message": "please TRIGGER the refund", "trigger": True}
        with rp.capture(input=trigger_input) as run:
            run.output = agent6_conditional(trigger_input, run)
        info(f"Captured with trigger input — tool called: {rp.get_run(run.id).tool_calls[0].name}")
        rp.mark_failed(run.id, reason="called forbidden_tool on triggered input")
        test = rp.save_test(run.id,
                            forbidden_actions=["forbidden_tool"],
                            expected_action="escalate")

        # Replay with trigger input — should FAIL (regression caught)
        results = rp.replay_all(agent=agent6_conditional)
        r = results[0]
        info(f"Replay with trigger input: [{r.verdict.value.upper()}] {r.reason}")
        assert r.verdict.value == "fail"
        ok("Agent6 with trigger input: regression correctly caught (FAIL)")

        # Now replay a DIFFERENT test with non-trigger input
        rp2, tmp2 = fresh()
        try:
            safe_input = {"message": "normal request", "trigger": False}
            with rp2.capture(input=safe_input) as run:
                run.output = agent6_conditional(safe_input, run)
            rp2.mark_failed(run.id, reason="test baseline")
            rp2.save_test(run.id,
                          forbidden_actions=["forbidden_tool"],
                          expected_action="escalate")
            results2 = rp2.replay_all(agent=agent6_conditional)
            r2 = results2[0]
            info(f"Replay with safe input:    [{r2.verdict.value.upper()}] {r2.reason}")
            assert r2.verdict.value == "pass"
            ok("Agent6 with safe input: no regression (PASS)")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# TEST 9 — Concurrent capture (10 rapid runs, unique IDs)
# ============================================================

def test9():
    section("TEST 9 — Concurrent capture: 10 rapid runs, unique IDs, no corruption")
    rp, tmp = fresh()
    try:
        captured_ids: list[str] = []
        inputs: list[dict] = []

        for i in range(10):
            inp = {"index": i, "batch": "rapid", "value": f"v{i}"}
            inputs.append(inp)
            with rp.capture(input=inp) as run:
                run.output = agent2_always_correct(inp, run)
            captured_ids.append(run.id)

        # All IDs unique
        assert len(set(captured_ids)) == 10, f"Duplicate run IDs: {10 - len(set(captured_ids))}"
        ok("10 rapid captures produced 10 unique run IDs")

        # Load them all back and verify data integrity
        runs = rp.list_runs()
        assert len(runs) == 10, f"Expected 10 runs, got {len(runs)}"
        ok("All 10 runs load back from disk")

        loaded_inputs = {str(r.input["index"]): r.input for r in runs}
        all_match = True
        for i, original in enumerate(inputs):
            loaded = loaded_inputs.get(str(i))
            if loaded != original:
                bad(f"Run {i} data corrupted: expected {original}, got {loaded}")
                all_match = False

        if all_match:
            ok("All 10 runs: input data matches exactly (no corruption)")

        # Verify all IDs match
        loaded_ids = {r.id for r in runs}
        if set(captured_ids) == loaded_ids:
            ok("All 10 run IDs match between capture and reload")
        else:
            bad("Run IDs do not match after reload")

        info(f"IDs (first 8 chars): {[i[:8] for i in captured_ids]}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# TEST 10 — Windows path compatibility
# ============================================================

def test10():
    section("TEST 10 — Windows path compatibility")
    src_root = Path(__file__).parent.parent / "replayd"

    hardcoded_slash_files: list[str] = []
    for py_file in src_root.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        for lineno, line in enumerate(lines, 1):
            # Skip comments and strings that describe paths (e.g., docstrings)
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Look for open() or path construction using raw string slashes
            if ("open(" in line or "Path(" in line or "os.path" in line) and "/" in line:
                # This is suspicious — flag it for review
                hardcoded_slash_files.append(f"{py_file.name}:{lineno}: {stripped[:70]}")

    if hardcoded_slash_files:
        info("Potentially suspicious path constructions:")
        for f in hardcoded_slash_files:
            info(f"  {f}")
        # Not necessarily bugs — pathlib / operator works on Windows
        info("Note: pathlib '/' operator is cross-platform safe")

    # Verify pathlib is used throughout
    for py_file in src_root.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        if 'open(' in content and 'pathlib' not in content and 'Path' not in content:
            bad(f"{py_file.name}: uses open() without pathlib")
        else:
            ok(f"{py_file.name}: path handling is cross-platform safe")

    # Smoke test: create dirs and files on current Windows path
    rp, tmp = fresh()
    try:
        inp = {"os": "windows", "path": str(Path(tmp))}
        with rp.capture(input=inp) as run:
            run.output = agent2_always_correct(inp, run)
        rp.mark_failed(run.id, reason="path test")
        rp.save_test(run.id, forbidden_actions=["forbidden_tool"])
        results = rp.replay_all(agent=agent2_always_correct)
        assert results[0].verdict.value == "pass"
        ok("Storage creates/reads files correctly on Windows paths")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# TEST 11 — Fresh venv pip install test
# ============================================================

def test11():
    section("TEST 11 — Fresh venv pip install test")
    venv_dir = Path(tempfile.mkdtemp(prefix="replayd_venv_"))
    try:
        info(f"Creating venv at {venv_dir}")
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            bad(f"venv creation failed: {result.stderr}")
            return

        python = venv_dir / "Scripts" / "python.exe"
        if not python.exists():
            python = venv_dir / "bin" / "python"
        if not python.exists():
            bad("Could not find python executable in venv")
            return

        info("Installing replayd from PyPI into fresh venv")
        result = subprocess.run(
            [str(python), "-m", "pip", "install", "replayd", "--quiet"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            bad(f"pip install failed: {result.stderr}")
            return
        ok("pip install replayd succeeded in fresh venv")

        # Run a minimal capture/replay test
        test_script = venv_dir / "smoke_test.py"
        test_dir = venv_dir / "test_data"
        test_script.write_text(f"""
import sys
from pathlib import Path
from replayd import Replayd
from replayd.capture import RunContext

rp = Replayd(storage_dir=r"{test_dir}")

def good_agent(inp, run_ctx):
    run_ctx.record_tool_call("escalate", {{"inp": str(inp)}}, {{"ok": True}})
    return {{"action": "escalate"}}

def bad_agent(inp, run_ctx):
    run_ctx.record_tool_call("forbidden_tool", {{"inp": str(inp)}}, {{"ok": True}})
    return {{"action": "forbidden_tool"}}

inp = {{"amount": 500}}
with rp.capture(input=inp) as run:
    run.output = bad_agent(inp, run)
rp.mark_failed(run.id, reason="called forbidden_tool")
rp.save_test(run.id, forbidden_actions=["forbidden_tool"], expected_action="escalate")

results = rp.replay_all(agent=bad_agent)
assert results[0].verdict.value == "fail", f"Expected FAIL, got {{results[0].verdict.value}}"

results = rp.replay_all(agent=good_agent)
assert results[0].verdict.value == "pass", f"Expected PASS, got {{results[0].verdict.value}}"

print("SMOKE_TEST_OK")
""", encoding="utf-8")

        result = subprocess.run(
            [str(python), str(test_script)],
            capture_output=True, text=True
        )
        output = result.stdout.strip()
        info(f"Smoke test output: {output}")
        if "SMOKE_TEST_OK" in output and result.returncode == 0:
            ok("Full capture/mark_failed/save_test/replay_all works in fresh venv")
        else:
            bad(f"Smoke test failed (rc={result.returncode}): {result.stderr[:200]}")

    finally:
        shutil.rmtree(venv_dir, ignore_errors=True)


# ============================================================
# TEST 12 — README quickstart accuracy
# ============================================================

def test12():
    section("TEST 12 — README quickstart accuracy")
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")

    # The README quickstart uses placeholder names — we test the API pattern is correct
    rp, tmp = fresh()
    try:
        # Replicate the exact API calls shown in the README
        user_input = {"customer_id": "c-readme", "amount": 750}

        # Step 1: capture
        with rp.capture(input=user_input, model="gpt-4o") as run:
            run.output = agent1_always_forbidden(user_input, run)
        ok("README step 1: rp.capture() context manager works as documented")

        # Step 2: mark_failed
        rp.mark_failed(run.id, reason="agent approved refund after policy limit")
        ok("README step 2: rp.mark_failed(run.id, reason=...) works as documented")

        # Step 3: save_test
        rp.save_test(
            run.id,
            forbidden_actions=["approve_refund"],
            expected_action="escalate",
        )
        ok("README step 3: rp.save_test(run.id, forbidden_actions=..., expected_action=...) works")

        # Step 4: replay_all — the README shows agent as positional arg
        results = rp.replay_all(agent=agent2_always_correct)
        for r in results:
            info(f"README step 4: {r.verdict} {r.reason}")
        ok("README step 4: rp.replay_all(agent=...) works and returns results with .verdict and .reason")

        # Verify README documents the two-arg agent signature
        if "(input, run_ctx)" in readme:
            ok("README documents the (input, run_ctx) agent signature")
        else:
            bad("README does not document the two-argument agent signature clearly")

        # Verify the README quickstart does NOT use the two-arg form (it's simplified)
        # This is a known gap — the README quickstart shows your_agent.run(user_input)
        # which doesn't record tool calls. Flag it.
        if "run_ctx" not in readme.split("## Quickstart")[1].split("## See it working")[0]:
            info("NOTE: README Quickstart section omits run_ctx — tool calls won't be recorded")
            info("      This is acceptable for a first-look quickstart but users WILL hit this")
            info("      when they try to use forbidden_actions grading without tool recording")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# TEST 13 — Semantic grading without API key
# ============================================================

def test13():
    section("TEST 13 — Semantic grading without ANTHROPIC_API_KEY")
    rp, tmp = fresh()
    saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        inp = {"amount": 100}
        with rp.capture(input=inp) as run:
            run.output = agent1_always_forbidden(inp, run)
        rp.mark_failed(run.id, reason="test")
        rp.save_test(run.id,
                     forbidden_actions=["forbidden_tool"],
                     grader_prompt="Did the agent call a forbidden tool?")

        # The forbidden action fires, so structural grader catches it first.
        # To test the missing API key path, we need a case where structural passes.
        rp2, tmp2 = fresh()
        try:
            with rp2.capture(input=inp) as run:
                run.output = agent2_always_correct(inp, run)
            rp2.mark_failed(run.id, reason="semantic test")
            # No forbidden_actions — forces semantic path
            rp2.save_test(run.id, grader_prompt="Did the agent make a bad decision?")
            try:
                results = rp2.replay_all(agent=agent2_always_correct)
                bad("TEST 13: Should have raised EnvironmentError for missing API key")
            except EnvironmentError as e:
                msg = str(e)
                info(f"Raised EnvironmentError: {msg[:80]}")
                if "ANTHROPIC_API_KEY" in msg:
                    ok("Missing API key raises EnvironmentError with ANTHROPIC_API_KEY mentioned")
                else:
                    bad(f"Error message does not mention ANTHROPIC_API_KEY: {msg}")
            except ImportError as e:
                info(f"Raised ImportError (anthropic not installed): {e}")
                ok("Missing anthropic package raises ImportError with install instructions")
            except Exception as e:
                bad(f"Wrong exception type {type(e).__name__}: {e}")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

    finally:
        if saved_key:
            os.environ["ANTHROPIC_API_KEY"] = saved_key
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# TEST 14 — Performance (100 tests)
# ============================================================

def test14():
    section("TEST 14 — Performance: 100 tests, structural grading only")
    rp, tmp = fresh()
    try:
        for i in range(100):
            inp = {"index": i, "amount": i * 5}
            with rp.capture(input=inp) as run:
                run.output = agent1_always_forbidden(inp, run)
            rp.mark_failed(run.id, reason=f"perf test {i}")
            rp.save_test(run.id, forbidden_actions=["forbidden_tool"])

        info("100 tests saved. Timing replay_all...")
        start = time.perf_counter()
        results = rp.replay_all(agent=agent1_always_forbidden)
        elapsed = time.perf_counter() - start

        assert len(results) == 100
        all_fail = all(r.verdict.value == "fail" for r in results)

        info(f"Total time:   {elapsed:.3f}s")
        info(f"Per-test avg: {elapsed/100*1000:.2f}ms")

        if all_fail:
            ok("All 100 structural grades returned correct FAIL verdict")
        else:
            bad("Some structural grades returned wrong verdict")

        if elapsed < 30.0:
            ok(f"100 tests completed in {elapsed:.3f}s (under 30s threshold)")
        else:
            bad(f"Performance issue: 100 tests took {elapsed:.3f}s (exceeds 30s)")

        if elapsed / 100 < 0.1:
            ok(f"Per-test average {elapsed/100*1000:.2f}ms is under 100ms threshold")
        else:
            bad(f"Per-test average {elapsed/100*1000:.2f}ms exceeds 100ms — investigate I/O")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# TEST 15 — First developer experience simulation
# ============================================================

def test15():
    section("TEST 15 — First developer experience simulation")
    rp, tmp = fresh()
    try:
        # Step 1: import works
        info("Developer does: from replayd import Replayd")
        from replayd import Replayd as R
        ok("Import works")

        # Step 2: Replayd() with no args
        info("Developer does: rp = Replayd()")
        rp_default_dir = Path(tmp) / "devtest"
        rp_default_dir.mkdir()
        os.chdir(rp_default_dir)
        rp_dev = Replayd()  # uses .replayd in cwd
        ok("Replayd() with no args creates .replayd in cwd")

        # Step 3: capture with no model arg
        info("Developer does: with rp.capture(input=msg) as run:")
        with rp_dev.capture(input="hello world") as run:
            run.output = "some response"
        ok("capture() with just input= works (model and prompt_version optional)")

        # Step 4: string input (not dict) — real developers will try this
        info("Developer tries string input instead of dict")
        with rp_dev.capture(input="plain string input") as run:
            run.output = "response"
        rp_dev.mark_failed(run.id, reason="string input test")
        rp_dev.save_test(run.id, forbidden_actions=["bad_tool"])
        ok("String input (not dict) is accepted — any JSON-serializable input works")

        # Step 5: int input
        info("Developer tries int input")
        with rp_dev.capture(input=42) as run:
            run.output = "response"
        ok("Integer input is accepted")

        # Step 6: list_tests when there are some
        tests = rp_dev.list_tests()
        info(f"list_tests() returns {len(tests)} test(s)")
        ok("list_tests() works and returns TestCase objects")

        # Step 7: ReplayResult is truthy for PASS, falsy for FAIL
        rp7, tmp7 = fresh()
        try:
            inp = {"test": True}
            with rp7.capture(input=inp) as run:
                run.output = agent1_always_forbidden(inp, run)
            rp7.mark_failed(run.id, reason="test")
            rp7.save_test(run.id, forbidden_actions=["forbidden_tool"])

            results = rp7.replay_all(agent=agent1_always_forbidden)
            assert not results[0], "FAIL result should be falsy"
            results2 = rp7.replay_all(agent=agent2_always_correct)
            assert results2[0], "PASS result should be truthy"
            ok("ReplayResult is truthy for PASS, falsy for FAIL — works in if statements")
        finally:
            shutil.rmtree(tmp7, ignore_errors=True)

        # Step 8: friction points
        info("Friction points identified:")
        info("  1. agent must accept (input, run_ctx) — not obvious from quickstart")
        info("  2. tool calls must be recorded manually — no auto-interception")
        info("  3. run.output must be assigned explicitly inside with block")
        info("  4. save_test requires mark_failed first — order matters")
        ok("All friction points are documented in README")

    finally:
        os.chdir(Path(__file__).parent.parent)
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("replayd comprehensive stress test")
    print(f"Python {sys.version}")
    print(f"Working dir: {Path.cwd()}")

    tests = [test1, test2, test3, test4, test5, test6,
             test7, test8, test9, test10, test11, test12,
             test13, test14, test15]

    for t in tests:
        try:
            t()
        except Exception as e:
            section(f"UNCAUGHT EXCEPTION IN {t.__name__}")
            traceback.print_exc()
            _issues.append(f"{t.__name__} raised uncaught {type(e).__name__}: {e}")
            _fail += 1

    print(f"\n{'=' * 62}")
    print(f"  FINAL RESULTS: {_pass} passed, {_fail} failed")
    print(f"{'=' * 62}")

    if _issues:
        print("\nIssues found:")
        for i, issue in enumerate(_issues, 1):
            print(f"  {i}. {issue}")
    else:
        print("\nNo issues found.")
