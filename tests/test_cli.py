"""
Tests for the replayd CLI (replayd/cli.py).

Each test that needs an importable agent module writes a .py file into
tmp_path and prepends that directory to sys.path via monkeypatch. Unique
module names per test prevent sys.modules caching conflicts.
"""

from __future__ import annotations

import sys
import pytest

from replayd import Replayd, __version__
from replayd.cli import main


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------

def test_version_exits_0(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out


# ---------------------------------------------------------------------------
# replayd run
# ---------------------------------------------------------------------------

def _make_agent_file(tmp_path, module_name: str, body: str) -> None:
    (tmp_path / f"{module_name}.py").write_text(body, encoding="utf-8")


def _setup_failing_test(rp: Replayd) -> None:
    """Save one test whose forbidden action is 'bad_tool'."""
    def agent_bad(inp, run_ctx):
        run_ctx.record_tool_call("bad_tool", {}, None)
        return "bad"

    with rp.capture(input="x") as run:
        run.output = agent_bad("x", run)
    rp.mark_failed(run.id, reason="called bad_tool")
    rp.save_test(run.id, forbidden_actions=["bad_tool"])


def test_run_exits_0_when_all_pass(tmp_path, monkeypatch):
    storage = tmp_path / ".replayd"
    rp = Replayd(storage_dir=storage)
    _setup_failing_test(rp)

    # Agent that does NOT call bad_tool — test should pass
    _make_agent_file(tmp_path, "agent_pass_a", "def agent(inp, run_ctx):\n    return 'ok'\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "agent_pass_a", raising=False)

    with pytest.raises(SystemExit) as exc:
        main(["run", "--agent", "agent_pass_a:agent", "--storage", str(storage)])
    assert exc.value.code == 0


def test_run_exits_1_when_any_fail(tmp_path, monkeypatch):
    storage = tmp_path / ".replayd"
    rp = Replayd(storage_dir=storage)
    _setup_failing_test(rp)

    # Agent that calls bad_tool — test should fail
    _make_agent_file(
        tmp_path,
        "agent_fail_b",
        "def agent(inp, run_ctx):\n"
        "    run_ctx.record_tool_call('bad_tool', {}, None)\n"
        "    return 'bad'\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "agent_fail_b", raising=False)

    with pytest.raises(SystemExit) as exc:
        main(["run", "--agent", "agent_fail_b:agent", "--storage", str(storage)])
    assert exc.value.code == 1


def test_run_exits_0_when_no_tests(tmp_path, monkeypatch):
    storage = tmp_path / ".replayd"
    # Storage dir with no tests
    (storage / "runs").mkdir(parents=True)
    (storage / "tests").mkdir(parents=True)

    _make_agent_file(tmp_path, "agent_noop_c", "def agent(inp, run_ctx):\n    return None\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "agent_noop_c", raising=False)

    with pytest.raises(SystemExit) as exc:
        main(["run", "--agent", "agent_noop_c:agent", "--storage", str(storage)])
    assert exc.value.code == 0


def test_run_bad_agent_spec_exits_2(tmp_path):
    """Missing colon in --agent should exit 2 without crashing."""
    with pytest.raises(SystemExit) as exc:
        main(["run", "--agent", "no_colon_here", "--storage", str(tmp_path)])
    assert exc.value.code == 2


def test_run_import_error_exits_2(tmp_path):
    """Nonexistent module in --agent should exit 2 with a readable message."""
    with pytest.raises(SystemExit) as exc:
        main(["run", "--agent", "nonexistent_xyz_module:fn", "--storage", str(tmp_path)])
    assert exc.value.code == 2


def test_run_output_shows_pass_and_fail(tmp_path, monkeypatch, capsys):
    storage = tmp_path / ".replayd"
    rp = Replayd(storage_dir=storage)
    _setup_failing_test(rp)

    _make_agent_file(tmp_path, "agent_pass_d", "def agent(inp, run_ctx):\n    return 'ok'\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "agent_pass_d", raising=False)

    with pytest.raises(SystemExit):
        main(["run", "--agent", "agent_pass_d:agent", "--storage", str(storage)])

    out = capsys.readouterr().out
    assert "[PASS]" in out
