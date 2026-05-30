"""
JSON-file storage for captured runs and test cases.

Layout on disk:
    .replayd/
        runs/<run-id>.json
        tests/<test-id>.json
"""

from __future__ import annotations

import json
from pathlib import Path

from replayd.models import CapturedRun, TestCase


class Storage:
    def __init__(self, base_dir: str | Path = ".replayd") -> None:
        self._base = Path(base_dir)
        self._runs_dir = self._base / "runs"
        self._tests_dir = self._base / "tests"
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._tests_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def save_run(self, run: CapturedRun) -> None:
        path = self._runs_dir / f"{run.id}.json"
        path.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")

    def load_run(self, run_id: str) -> CapturedRun:
        path = self._runs_dir / f"{run_id}.json"
        if not path.exists():
            raise KeyError(f"Run not found: {run_id}")
        return CapturedRun.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_runs(self) -> list[CapturedRun]:
        return [
            CapturedRun.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(self._runs_dir.glob("*.json"))
        ]

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def save_test(self, test: TestCase) -> None:
        path = self._tests_dir / f"{test.id}.json"
        path.write_text(json.dumps(test.to_dict(), indent=2), encoding="utf-8")

    def load_test(self, test_id: str) -> TestCase:
        path = self._tests_dir / f"{test_id}.json"
        if not path.exists():
            raise KeyError(f"Test not found: {test_id}")
        return TestCase.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_tests(self) -> list[TestCase]:
        return [
            TestCase.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(self._tests_dir.glob("*.json"))
        ]
