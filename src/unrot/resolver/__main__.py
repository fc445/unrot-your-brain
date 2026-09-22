"""`python -m unrot.resolver` -- the command that actually joins the chain up.

Until this existed, capture, detection and the store were three things that
worked and were not connected to each other. `run` is the join: read captured
sessions, detect, resolve, append. Everything else here is the review surface
that the "judgments are events" decision exists to make possible.
"""

from __future__ import annotations

import argparse
import sys

from ..capture import connect as connect_raw
from ..env import describe, env_path, load_env
from ..analyse import analyse_session
from ..detector import build_proposer
from ..model import DEFAULT_MODEL, ModelConfig
from ..store import compile_state
from ..store.__main__ import open_store
from . import deciders, match
from . import prompt as prompt_module
from .resolve import (
    correct,
    judgments,
    merge,
    resolve,
    resolver_version,
)
from .submissions import manual

# Keys, the model id and the endpoint all come from the project's `.env` if one
# is present; none of them are required, and a real environment variable always
# wins over the file.
load_env()


def _deciding(args):
    """Pick a decider, and say plainly which one is running.

    Silently downgrading to the string-only decider would be the worst version
    of this: the user would see concepts quietly failing to merge and have no
    way to know a model was never asked.
    """
    config = ModelConfig.from_env(
        model=args.model, base_url=args.base_url, api_key=args.api_key
    )
    if getattr(args, "no_model", False) or not config.api_key:
        why = "--no-model" if getattr(args, "no_model", False) else "no API key set"
        print(
            f"Resolving without a model ({why}). Exact-name matches will still"
            " resolve; near-duplicates like `K8s` for `Kubernetes` will not, and"
            " will land as separate concepts to merge later.",
            file=sys.stderr,
        )
        return deciders.strict, "none", config
    return deciders.build_decider(config), config.label, config


def _sessions(raw, args) -> list[str]:
    if args.session:
        return args.session
    rows = raw.execute(
        "SELECT s.session_id FROM raw_sessions s"
        " WHERE EXISTS (SELECT 1 FROM raw_turns t WHERE t.session_id = s.session_id"
        "   AND t.role = 'user' AND t.is_meta = 0 AND t.is_sidechain = 0)"
        " ORDER BY (SELECT max(occurred_at) FROM raw_turns t"
        "   WHERE t.session_id = s.session_id) DESC"
    ).fetchall()
    return [r["session_id"] for r in rows][: args.limit]


def _cmd_run(args) -> int:
    raw = connect_raw(args.home)
    conn = open_store(args.home)
    sessions = _sessions(raw, args)
    if not sessions:
        print("No ingested sessions. Run: python -m unrot.capture ingest", file=sys.stderr)
        return 1

    decide, model_label, config = _deciding(args)
    try:
        propose = build_proposer(config)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    already = {
        row["session_id"]
        for row in conn.execute(
            "SELECT session_id FROM compiled_sessions WHERE detector_version = ?",
            (args.detector_version,),
        )
    } if args.detector_version else set()

    totals = {"new": 0, "existing": 0, "alias": 0, "clean": 0}
    for session_id in sessions:
        if session_id in already and not args.force:
            continue
        # The same path the watcher takes, so there is one definition of
        # "analysed" -- including recording the clean case, which is what lets
        # the surface say "we looked".
        analysis = analyse_session(
            conn,
            raw,
            session_id,
            propose=propose,
            decide=decide,
            detector_label=config.label,
            resolver_label=model_label,
            max_candidates=args.max,
        )

        print(f"\n{session_id}  ({analysis.windows_examined} windows)")
        if analysis.clean:
            totals["clean"] += 1
            print("  clean -- nothing was leaned on that you waved through")

        for resolution in analysis.resolutions:
            totals[resolution.decision] += 1
            mark = {"new": "+", "existing": "=", "alias": "~"}[resolution.decision]
            print(f"  {mark} {resolution.canonical_name}  ({resolution.decision})")
            if resolution.decision != "existing":
                print(f"      {resolution.reasoning}")

    print(
        f"\n{len(sessions)} session(s): {totals['clean']} clean,"
        f" {totals['new']} new concept(s), {totals['existing']} repeat(s),"
        f" {totals['alias']} alias(es)"
    )
    print(resolver_version(model_label))
    return 0


def _cmd_add(args) -> int:
    """Journey 10: a term from a meeting, a podcast, a corridor conversation."""
    conn = open_store(args.home)
    decide, model_label, _ = _deciding(args)
    resolution = resolve(
        conn, manual(" ".join(args.text)), decide=decide, model_label=model_label
    )
    print(f"{resolution.decision}: {resolution.canonical_name}  [{resolution.concept_id}]")
    print(f"  {resolution.reasoning}")
    print(f"  judgment {resolution.judgment_event_id} -- correct it with: correct <id>")
    return 0


def _cmd_merge(args) -> int:
    conn = open_store(args.home)
    merge(conn, args.from_concept, args.into_concept, reasoning=args.reason)
    print(f"merged {args.from_concept} into {args.into_concept}")
    return 0


def _cmd_correct(args) -> int:
    conn = open_store(args.home)
    try:
        correct(
            conn, args.event_id, reasoning=args.reason, merge_into=args.merge_into
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"recorded a correction against {args.event_id}")
    if args.merge_into:
        print(f"  and merged its concept into {args.merge_into}")
    return 0


def _cmd_log(args) -> int:
    import json

    conn = open_store(args.home)
    rows = judgments(conn, limit=args.limit)
    if not rows:
        print("No resolver judgments yet. Run: python -m unrot.resolver run")
        return 0
    for row in rows:
        payload = json.loads(row["payload"])
        mark = "corrected" if row["corrected_by"] else payload["decision"]
        print(f"{row['event_id']}  [{mark}]  {payload['input_text']}")
        print(f"    {payload['reasoning']}")
        if row["correction"]:
            print(f"    -> you said: {row['correction']}")
    return 0


def _cmd_env(args) -> int:
    """What configuration is in effect, and which file it came from."""
    del args
    loaded = load_env()
    if loaded:
        for path in loaded:
            print(f"read {path}")
    else:
        print(f"no .env found. Copy .env.example to {env_path()} to make one.")
    print()
    for name, status, help_text in describe():
        print(f"  {name:<20} {status}")
        print(f"  {'':<20} {help_text}")
    return 0


def _cmd_print_prompt(args) -> int:
    """See what the model is actually asked, against the real graph. No call made."""
    conn = open_store(args.home)
    submission = manual(" ".join(args.text))
    known = match.current(conn)
    print(prompt_module.render(submission, match.shortlist(known, submission.text)))
    return 0


def _cmd_recompile(args) -> int:
    conn = open_store(args.home)
    result = compile_state(conn)
    print(
        f"compiled {result.event_count} event(s) -> {result.concepts} concept(s),"
        f" {result.encounters} encounter(s), {result.sessions_analysed} session(s) analysed"
    )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="unrot.resolver", description=__doc__)
    parser.add_argument("--home", help="override $UNROT_HOME")
    sub = parser.add_subparsers(dest="command", required=True)

    def model_flags(p):
        p.add_argument("--model", help=f"model id (default: $UNROT_MODEL, else {DEFAULT_MODEL})")
        p.add_argument("--base-url", help="OpenAI-compatible endpoint; point at a local server to stay offline")
        p.add_argument("--api-key", help="defaults to $OPENROUTER_API_KEY, then $OPENAI_API_KEY")

    p_run = sub.add_parser("run", help="detect over captured sessions and resolve into the graph")
    p_run.add_argument("session", nargs="*", help="session ids (default: the most recent)")
    p_run.add_argument("--limit", type=int, default=5, help="how many sessions (default 5)")
    p_run.add_argument("--max", type=int, default=2, help="flag budget per session")
    p_run.add_argument("--no-model", action="store_true", help="string matching only; never asks a model")
    p_run.add_argument("--detector-version", help="skip sessions already analysed by this version")
    p_run.add_argument("--force", action="store_true", help="re-run sessions already analysed")
    model_flags(p_run)
    p_run.set_defaults(func=_cmd_run)

    p_add = sub.add_parser("add", help="submit a term in your own words (journey 10)")
    p_add.add_argument("text", nargs="+", help='e.g. "something about backpressure?"')
    p_add.add_argument("--no-model", action="store_true", help="string matching only")
    model_flags(p_add)
    p_add.set_defaults(func=_cmd_add)

    p_merge = sub.add_parser("merge", help="fold one concept into another")
    p_merge.add_argument("from_concept")
    p_merge.add_argument("into_concept")
    p_merge.add_argument("--reason", required=True, help="why -- this is kept and is correctable")
    p_merge.set_defaults(func=_cmd_merge)

    p_correct = sub.add_parser("correct", help="say a resolver judgment was wrong")
    p_correct.add_argument("event_id", help="from `log`")
    p_correct.add_argument("--reason", required=True)
    p_correct.add_argument("--merge-into", help="also fold the concept it created into this one")
    p_correct.set_defaults(func=_cmd_correct)

    p_log = sub.add_parser("log", help="what the resolver decided, and what you said about it")
    p_log.add_argument("--limit", type=int, default=20)
    p_log.set_defaults(func=_cmd_log)

    p_env = sub.add_parser("env", help="what model and endpoint are configured, and from where")
    p_env.set_defaults(func=_cmd_env)

    p_rec = sub.add_parser("recompile", help="re-fold the log")
    p_rec.set_defaults(func=_cmd_recompile)

    p_prompt = sub.add_parser(
        "print-prompt", help="render the resolution prompt against your real graph, calling no model"
    )
    p_prompt.add_argument("text", nargs="+")
    p_prompt.set_defaults(func=_cmd_print_prompt)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
