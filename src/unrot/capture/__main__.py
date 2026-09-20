"""`python -m unrot.capture` -- the manual trigger.

Manual on purpose: a watcher or daemon is out of the v1 skeleton, and anything
that runs inside Claude Code is out of the product entirely. This is a command
you run against files that are already on your disk.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import paths
from .ingest import connect, human_turns, ingest_all, ingest_file, resolve_pointer


def _cmd_ingest(args) -> int:
    conn = connect(args.home)
    if args.path:
        results = [ingest_file(p, conn=conn, root=args.home) for p in args.path]
    else:
        results = ingest_all(conn=conn, root=args.home, limit=args.limit)

    changed = [r for r in results if r.changed]
    for r in results:
        if r.changed or args.verbose:
            print(
                f"  {r.status:<10} {r.session_id}  +{r.lines_added} lines,"
                f" +{r.turns_added} turns  ({r.lines_total} total)"
            )
    drift = sum(r.malformed_lines for r in results)
    print(
        f"\n{len(changed)} of {len(results)} session(s) changed."
        + (f"  {drift} malformed line(s) -- schema may have drifted." if drift else "")
    )
    return 0


def _cmd_list(args) -> int:
    conn = connect(args.home)
    # Ordered by when the session happened, not when we happened to read it --
    # a bulk first ingest stamps every row within the same second, which makes
    # last_ingested_at a tiebreaker rather than an order.
    rows = conn.execute(
        "SELECT s.*,"
        " (SELECT count(*) FROM raw_turns t WHERE t.session_id = s.session_id"
        "   AND t.role = 'user' AND t.is_meta = 0 AND t.is_sidechain = 0) AS human_turns,"
        " (SELECT max(occurred_at) FROM raw_turns t WHERE t.session_id = s.session_id)"
        "   AS last_activity"
        " FROM raw_sessions s"
        " ORDER BY COALESCE(last_activity, s.last_ingested_at) DESC"
    ).fetchall()
    if not rows:
        print("Nothing ingested yet. Try: python -m unrot.capture ingest")
        return 0
    print(f"{'when':<11} {'session':<38} {'lines':>7} {'human':>6} {'entrypoint':<16} cwd")
    for row in rows:
        when = (row["last_activity"] or row["last_ingested_at"] or "")[:10]
        print(
            f"{when:<11} {row['session_id']:<38} {row['lines_ingested']:>7}"
            f" {row['human_turns']:>6} {(row['entrypoint'] or '-'):<16}"
            f" {row['cwd'] or '-'}"
        )
    print(f"\n{len(rows)} session(s) in {paths.raw_dir(paths.home(args.home))}")
    return 0


def _cmd_show(args) -> int:
    lines = resolve_pointer(
        args.session, args.start, args.end or args.start, root=args.home
    )
    for offset, raw in enumerate(lines, start=args.start):
        if args.raw:
            print(f"{offset}: {raw}")
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            print(f"{offset}: <unparseable> {raw[:120]}")
            continue
        print(f"{offset}: {record.get('type')}  {json.dumps(record)[:200]}")
    return 0


def _cmd_turns(args) -> int:
    conn = connect(args.home)
    for row in human_turns(conn, args.session):
        text = (row["text"] or "").strip().replace("\n", " ")
        print(f"  line {row['line_no']:>5}  {text[:140]}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="unrot.capture", description=__doc__)
    parser.add_argument("--home", help="override $UNROT_HOME")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="copy transcripts into the raw store")
    p_ingest.add_argument("path", nargs="*", help="specific .jsonl files (default: all)")
    p_ingest.add_argument("--limit", type=int, help="only the N most recent sessions")
    p_ingest.add_argument("-v", "--verbose", action="store_true", help="show unchanged too")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_list = sub.add_parser("list", help="what has been ingested")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="resolve a pointer back to transcript lines")
    p_show.add_argument("session")
    p_show.add_argument("start", type=int)
    p_show.add_argument("end", type=int, nargs="?")
    p_show.add_argument("--raw", action="store_true", help="print lines verbatim")
    p_show.set_defaults(func=_cmd_show)

    p_turns = sub.add_parser("turns", help="what a human actually typed in a session")
    p_turns.add_argument("session")
    p_turns.set_defaults(func=_cmd_turns)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
