"""`python -m unrot.material` -- make learning material for a gap.

Nothing here runs on its own. The gap graph is the product and material is a
pluggable stage on top of it, so "turn it off" means simply not running this --
which is a stronger guarantee than a setting that ten call sites have to honour.
"""

from __future__ import annotations

import argparse
import sys

from ..capture import connect as connect_raw
from ..env import load_env
from ..model import ModelConfig
from ..resolver import resolve_reference, strict
from ..store.__main__ import open_store
from .generate import (
    SOURCES_ONLY,
    TEXTUAL,
    NotGrounded,
    WouldRecurse,
    deliver,
    for_concept,
    material_version,
    sources_only,
    textual,
)
from .search import build_search
from .sources import gather
from .writer import build_writer

load_env()


def _cmd_make(args) -> int:
    conn, raw = open_store(args.home), connect_raw(args.home)
    config = ModelConfig.from_env(model=args.model, api_key=args.api_key)

    search = None
    if not args.offline and config.api_key:
        search = build_search(config)
    elif not config.api_key:
        print(
            "No API key: using only your own code and the sessions themselves."
            " Those are the sources that ground best anyway -- the web ones are"
            " the ones that needed checking.",
            file=sys.stderr,
        )

    found = gather(conn, raw, args.concept_id, search=search)
    print(f"{len(found)} source(s), {sum(1 for s in found if s.verified)} verified:")
    for index, source in enumerate(found, start=1):
        mark = "ok " if source.verified else "-- "
        print(f"  [S{index}] {mark}({source.kind}) {source.title[:70]}")
        if source.note:
            print(f"         {source.note}")

    try:
        if args.format == SOURCES_ONLY:
            made = sources_only(conn, args.concept_id, found)
        else:
            made = textual(
                conn,
                args.concept_id,
                found,
                write=build_writer(config),
                resolve_named=lambda term: resolve_reference(
                    conn, term, decide=strict, recompile=False
                ),
                model_label=config.label,
            )
    except WouldRecurse as exc:
        print(f"\nrefused: {exc}", file=sys.stderr)
        return 3
    except NotGrounded as exc:
        print(f"\nnot grounded: {exc}", file=sys.stderr)
        return 2

    if not args.dry_run:
        deliver(conn, made.material_id)

    print(f"\n{made.format}  {made.material_id}")
    if made.body:
        print()
        print(made.body)
    if made.covered:
        print(f"\nalso names: {', '.join(made.covered)}")
    print(f"\n{material_version(config.label)}")
    return 0


def _cmd_show(args) -> int:
    conn = open_store(args.home)
    rows = for_concept(conn, args.concept_id)
    if not rows:
        print("Nothing made for that concept yet.")
        return 0
    import json

    for row in rows:
        when = row["delivered_at"] or "not delivered"
        print(f"{row['generated_at']}  {row['format']}  ({when})")
        if row["body"]:
            print(f"  {row['body']}")
        for source in json.loads(row["sources"] or "[]"):
            mark = "ok" if source.get("verified") else "--"
            print(f"    {mark} {source.get('ref')}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="unrot.material", description=__doc__)
    parser.add_argument("--home", help="override $UNROT_HOME")
    sub = parser.add_subparsers(dest="command", required=True)

    p_make = sub.add_parser("make", help="generate material for one concept")
    p_make.add_argument("concept_id")
    p_make.add_argument(
        "--format", choices=(TEXTUAL, SOURCES_ONLY), default=TEXTUAL,
        help="textual_with_sources writes prose and must cite;"
        " sources_only writes nothing and hands over the pointers",
    )
    p_make.add_argument("--offline", action="store_true", help="your own code and sessions only, no web")
    p_make.add_argument("--dry-run", action="store_true", help="generate but do not mark it delivered")
    p_make.add_argument("--model")
    p_make.add_argument("--api-key")
    p_make.set_defaults(func=_cmd_make)

    p_show = sub.add_parser("show", help="what has been made for a concept")
    p_show.add_argument("concept_id")
    p_show.set_defaults(func=_cmd_show)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
