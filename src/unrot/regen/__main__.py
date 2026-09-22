"""`python -m unrot.regen` -- re-run the detector over retained history.

S5 said regeneration is a first-class operation rather than a one-off
migration, and this is where that stops being a claim. It is also the reason the
skeleton includes it at all: if it were deferred, the first time it were needed
would be the first time it were tried, on history that mattered.

Safe to interrupt. Work commits per session, so ctrl-c leaves a shorter log
rather than a broken one, and the next run picks up where this one stopped.
"""

from __future__ import annotations

import argparse
import sys

from ..capture import connect as connect_raw
from ..detector import build_proposer, detector_version
from ..env import load_env
from ..model import DEFAULT_MODEL, ModelConfig
from ..resolver import deciders
from ..store.__main__ import open_store
from . import plan, regenerate

load_env()


def _cmd_plan(args) -> int:
    """What a run would do, without doing any of it or calling a model."""
    conn, raw = open_store(args.home), connect_raw(args.home)
    config = ModelConfig.from_env(model=args.model)
    preview = plan(conn, raw, model_label=config.label)

    print(f"detector: {preview.detector_version}")
    print(f"  {preview.captured} session(s) captured")
    print(f"  {preview.already_done} already at this version -- would be skipped")
    print(f"  {len(preview.to_run)} to re-run")
    print(f"  {preview.protected} encounter(s) carry a judgment and will not be touched")
    return 0


def _cmd_run(args) -> int:
    conn, raw = open_store(args.home), connect_raw(args.home)
    config = ModelConfig.from_env(model=args.model, api_key=args.api_key)
    try:
        propose = build_proposer(config)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    decide = (
        deciders.strict
        if args.no_resolver_model or not config.api_key
        else deciders.build_decider(config)
    )

    sessions = args.session or None
    if sessions is None and args.limit:
        sessions = [
            r["session_id"]
            for r in raw.execute(
                "SELECT s.session_id FROM raw_sessions s WHERE EXISTS"
                " (SELECT 1 FROM raw_turns t WHERE t.session_id = s.session_id"
                "   AND t.role='user' AND t.is_meta=0 AND t.is_sidechain=0)"
                " ORDER BY s.session_id LIMIT ?",
                (args.limit,),
            )
        ]

    totals = {"protected": 0, "removed": 0, "recorded": 0, "done": 0, "skipped": 0}
    try:
        for result in regenerate(
            conn,
            raw,
            propose=propose,
            decide=decide,
            model_label=config.label,
            sessions=sessions,
            max_candidates=args.max,
            force=args.force,
        ):
            if result.skipped:
                totals["skipped"] += 1
                continue
            totals["done"] += 1
            for key in ("protected", "removed", "recorded"):
                totals[key] += getattr(result, key)
            print(
                f"  {result.session_id[:8]}  kept {result.protected} judged,"
                f" dropped {result.removed} stale, recorded {result.recorded}"
            )
    except KeyboardInterrupt:
        # Not an error path. Committing per session is what makes stopping here
        # a supported way to stop, and the next run resumes from the same place.
        print(
            f"\nstopped after {totals['done']} session(s)."
            " The log is intact; re-run to carry on.",
            file=sys.stderr,
        )
        return 130

    print(
        f"\n{totals['done']} re-run, {totals['skipped']} already current."
        f" {totals['protected']} judged encounter(s) left untouched,"
        f" {totals['removed']} stale dropped, {totals['recorded']} recorded."
    )
    print(detector_version(config.label))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="unrot.regen", description=__doc__)
    parser.add_argument("--home", help="override $UNROT_HOME")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="what a run would do; calls no model")
    p_plan.add_argument("--model")
    p_plan.set_defaults(func=_cmd_plan)

    p_run = sub.add_parser("run", help="re-run the detector over history")
    p_run.add_argument("session", nargs="*", help="specific sessions (default: all)")
    p_run.add_argument("--limit", type=int, help="only the first N sessions")
    p_run.add_argument("--max", type=int, default=2, help="flag budget per session")
    p_run.add_argument("--force", action="store_true", help="re-run sessions already at this version")
    p_run.add_argument("--no-resolver-model", action="store_true", help="string matching only for resolution")
    p_run.add_argument("--model", help=f"model id (default: $UNROT_MODEL, else {DEFAULT_MODEL})")
    p_run.add_argument("--api-key")
    p_run.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
