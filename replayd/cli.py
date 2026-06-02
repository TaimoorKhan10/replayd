"""
Command-line interface for replayd.

    replayd run --agent module.path:agent_fn
    replayd --version

The --agent value must be an importable dotted path followed by a colon and
the callable name. The module must be on sys.path (i.e. the package is
installed, or the working directory contains it).

Exit codes:
    0  all tests passed, or no tests found
    1  one or more tests failed
    2  bad arguments (missing/malformed --agent, import error)
"""

from __future__ import annotations

import argparse
import importlib
import sys

from replayd import __version__


def _import_agent(spec: str):
    """Import 'module.path:callable_name'. Exits with code 2 on any error."""
    if ":" not in spec:
        print(
            f"error: --agent must be 'module.path:callable_name', got {spec!r}",
            file=sys.stderr,
        )
        sys.exit(2)
    module_path, _, attr = spec.partition(":")
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        print(f"error: cannot import {module_path!r}: {exc}", file=sys.stderr)
        sys.exit(2)
    if not hasattr(mod, attr):
        print(f"error: {module_path!r} has no attribute {attr!r}", file=sys.stderr)
        sys.exit(2)
    return getattr(mod, attr)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="replayd",
        description="replayd — replay failed agent runs as regression tests",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"replayd {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run",
        help="replay all saved regression tests and exit 1 if any fail",
    )
    run_parser.add_argument(
        "--agent",
        required=True,
        metavar="module.path:fn",
        help="importable path to the agent callable",
    )
    run_parser.add_argument(
        "--storage",
        default=".replayd",
        metavar="DIR",
        help="storage directory (default: .replayd)",
    )

    args = parser.parse_args(argv)

    if args.command == "run":
        from replayd.core import Replayd

        agent = _import_agent(args.agent)
        rp = Replayd(storage_dir=args.storage)
        results = rp.replay_all(agent=agent)

        if not results:
            print(f"no tests found in {args.storage}")
            sys.exit(0)

        any_fail = False
        for r in results:
            tag = "PASS" if r else "FAIL"
            print(f"[{tag}] {r.reason}")
            if not r:
                any_fail = True

        sys.exit(1 if any_fail else 0)

    parser.print_help()
    sys.exit(0)
