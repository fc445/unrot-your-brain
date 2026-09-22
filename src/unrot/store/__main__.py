"""`python -m unrot.store` -- open, inspect, compile and seed the event log.

Imports `unrot.capture.paths` for the layout. That is the one direction of
coupling worth accepting: `paths` is a dependency-free description of where
things live, and the alternative -- writing `$UNROT_HOME/unrot.db` out a second
time here -- is exactly how two layers end up disagreeing about the location of
the same file.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from ..capture import paths
from . import fixtures
from .compile import compile_state
from .db import connect


def open_store(home: str | Path | None = None) -> sqlite3.Connection:
    """Open the event log at `$UNROT_HOME/unrot.db`, creating it if needed."""
    root = paths.home(home)
    root.mkdir(parents=True, exist_ok=True)
    return connect(paths.store_db_path(root))


def open_raw(home: str | Path | None = None) -> sqlite3.Connection | None:
    """Open the raw capture store read-only, or None if capture has never run."""
    path = paths.raw_db_path(paths.home(home))
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _cmd_compile(args) -> int:
    conn = open_store(args.home)
    result = compile_state(conn)
    print(
        f"compiled {result.event_count} event(s) -> {result.concepts} concept(s),"
        f" {result.encounters} encounter(s)"
    )
    return 0


def _cmd_stats(args) -> int:
    conn = open_store(args.home)
    compile_state(conn)
    rows = conn.execute(
        "SELECT state, count(*) AS n FROM compiled_concepts"
        " WHERE merged_into IS NULL GROUP BY state ORDER BY state"
    ).fetchall()
    total = conn.execute("SELECT count(*) FROM events").fetchone()[0]
    print(f"{paths.store_db_path(paths.home(args.home))}")
    print(f"  {total} event(s), {fixtures.count(conn)} of them fixtures")
    if not rows:
        print("  no concepts yet")
    for row in rows:
        print(f"  {row['state']:<12} {row['n']}")
    return 0


def _cmd_metrics(args) -> int:
    """PR-28's leading metrics for a window. Reads the log; writes nothing."""
    from ..metrics import compute, render, window

    conn = open_store(args.home)
    try:
        since = window(args.days, args.since)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    report = compute(conn, since=since)
    print(report.as_json() if args.json else render(report))
    return 0


def _cmd_seed(args) -> int:
    conn = open_store(args.home)
    if args.clear:
        removed = fixtures.clear(conn)
        compile_state(conn)
        print(f"removed {removed} fixture event(s) and recompiled")
        return 0

    existing = fixtures.count(conn)
    if existing and not args.force:
        print(
            f"{existing} fixture event(s) already seeded."
            " Use --force to add more, or --clear to remove them.",
            file=sys.stderr,
        )
        return 1

    raw = open_raw(args.home)
    counts = fixtures.seed(conn, raw_conn=raw)
    result = compile_state(conn)
    anchored = "anchored to real captured sessions" if raw else "synthetic pointers only"
    print(
        f"seeded {counts['concepts']} concept(s), {counts['encounters']} encounter(s),"
        f" {counts['judgments']} judgment(s) -- {anchored}"
    )
    print(f"compiled {result.event_count} event(s). Remove with: seed --clear")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="unrot.store", description=__doc__)
    parser.add_argument("--home", help="override $UNROT_HOME")
    sub = parser.add_subparsers(dest="command", required=True)

    p_compile = sub.add_parser("compile", help="re-fold the log into compiled state")
    p_compile.set_defaults(func=_cmd_compile)

    p_stats = sub.add_parser("stats", help="what is in the log")
    p_stats.set_defaults(func=_cmd_stats)

    p_metrics = sub.add_parser(
        "metrics", help="the week-of-self-use numbers PR-28 asks for (reads only)"
    )
    p_metrics.add_argument("--days", type=int, help="the last N days (default 7)")
    p_metrics.add_argument("--since", help="from this date instead, e.g. 2026-09-22")
    p_metrics.add_argument("--json", action="store_true", help="machine-readable")
    p_metrics.set_defaults(func=_cmd_metrics)

    p_seed = sub.add_parser(
        "seed", help="write development fixtures (stand-in for the unbuilt resolver)"
    )
    p_seed.add_argument("--clear", action="store_true", help="remove fixtures instead")
    p_seed.add_argument("--force", action="store_true", help="seed again even if present")
    p_seed.set_defaults(func=_cmd_seed)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
