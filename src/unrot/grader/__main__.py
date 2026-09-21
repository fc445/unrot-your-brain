"""`python -m unrot.grader` -- grade what is pending, or re-grade everything.

The surface grades an answer the moment it is written, so most of the time
nothing here needs running. It exists for the two cases the surface cannot
cover: an answer stored while no grader was available, and the whole corpus
needing re-grading after the rubric changed.

`regrade` is also PR-24's acceptance test made into a command. If throwing every
grade away and rebuilding them from the raw answers lands back on the same
state, then nothing irreplaceable was ever living in a grade -- which is the
thing that keeps the rubric swappable.
"""

from __future__ import annotations

import argparse
import sys

from ..env import load_env
from ..model import DEFAULT_MODEL, ModelConfig
from ..store.__main__ import open_store
from . import prompt as prompt_module
from .grade import grade, grader_version, history, regrade, ungraded
from .jev import DEFAULT_JEV_MODEL, build_jev_grader
from .model import build_grader, keyword_grader

load_env()


def _grader(args):
    """A grader, and an honest label for whichever one it is.

    Never silently downgrades: a keyword count and a model's judgment are
    different things, and a grade recorded as one when it was the other would
    quietly corrupt exactly the history this ticket exists to keep clean.
    """
    config = ModelConfig.from_env(model=args.model, api_key=args.api_key)
    if not args.no_model and config.api_key and args.grader == "jev":
        return build_jev_grader(config), DEFAULT_JEV_MODEL.replace("/", "-")
    if args.no_model or not config.api_key:
        why = "--no-model" if args.no_model else "no API key set"
        print(
            f"Grading offline ({why}): looking for connecting words rather than"
            " judging the answer. Grades will say so, and can be re-graded later"
            " with `regrade`.",
            file=sys.stderr,
        )
        return keyword_grader, "keyword"
    return build_grader(config), config.label


def _cmd_pending(args) -> int:
    conn = open_store(args.home)
    rows = ungraded(conn)
    if not rows:
        print("Nothing ungraded.")
        return 0
    for row in rows:
        print(f"{row['explanation_id']}  {row['concept_id']}")
        print(f"    asked : {row['prompt_text']}")
        print(f"    wrote : {row['raw_text'][:100]}")
    print(f"\n{len(rows)} answer(s) stored without a grade.")
    return 0


def _cmd_grade(args) -> int:
    conn = open_store(args.home)
    rows = ungraded(conn)
    if not rows:
        print("Nothing ungraded.")
        return 0
    grade_fn, label = _grader(args)
    for row in rows:
        result = grade(
            conn, row["explanation_id"], grade_fn=grade_fn, model_label=label
        )
        print(f"  {result.level:<9} {result.concept_id}")
        print(f"            {result.reasoning}")
    print(f"\n{len(rows)} graded. {grader_version(label)}")
    return 0


def _cmd_regrade(args) -> int:
    conn = open_store(args.home)
    total = conn.execute("SELECT count(*) FROM compiled_explanations").fetchone()[0]
    if not total:
        print("No explanations to re-grade.")
        return 0
    if not args.yes:
        print(
            f"This discards every grade on {total} answer(s) and rebuilds them"
            " from the raw text. The answers themselves are untouched."
            " Re-run with --yes.",
            file=sys.stderr,
        )
        return 1

    grade_fn, label = _grader(args)
    before = {
        row["explanation_id"]: row["level"]
        for row in conn.execute("SELECT * FROM compiled_explanations")
    }
    results = regrade(conn, grade_fn=grade_fn, model_label=label)

    moved = 0
    for result in results:
        was = before.get(result.explanation_id)
        arrow = "" if was == result.level else f"   ({was or 'ungraded'} -> {result.level})"
        moved += 0 if was == result.level else 1
        print(f"  {result.level:<9} {result.concept_id}{arrow}")
    print(f"\n{len(results)} re-graded, {moved} changed. {grader_version(label)}")
    return 0


def _cmd_show(args) -> int:
    conn = open_store(args.home)
    rows = history(conn, args.concept_id)
    if not rows:
        print(f"No explanations for {args.concept_id}.")
        return 0
    for row in rows:
        print(f"{row['submitted_at']}  [{row['level'] or 'ungraded'}]")
        print(f"    asked ({row['prompt_version']}): {row['prompt_text']}")
        print(f"    wrote: {row['raw_text']}")
        if row["reasoning"]:
            print(f"    grader: {row['reasoning']}")
    return 0


def _cmd_question(args) -> int:
    """Print the current wording, and the rubric it will be graded against."""
    del args
    from .check import check_version, question

    print(f"question ({check_version()}):")
    print(f"  {question('<term>')}")
    print(f"\nrubric ({prompt_module.RUBRIC}, {prompt_module.prompt_id()}):")
    print(prompt_module.TEMPLATE)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="unrot.grader", description=__doc__)
    parser.add_argument("--home", help="override $UNROT_HOME")
    sub = parser.add_subparsers(dest="command", required=True)

    def model_flags(p):
        p.add_argument("--model", help=f"model id (default: $UNROT_MODEL, else {DEFAULT_MODEL})")
        p.add_argument("--api-key", help="defaults to $OPENROUTER_API_KEY")
        p.add_argument("--no-model", action="store_true", help="grade offline, on keywords")
        p.add_argument(
            "--grader",
            choices=("jev", "llm"),
            default="jev",
            help="jev: a classifier, fast and returns a distribution (default)."
            " llm: a reasoning model, slower but writes feedback",
        )

    p_pending = sub.add_parser("pending", help="answers stored without a grade")
    p_pending.set_defaults(func=_cmd_pending)

    p_grade = sub.add_parser("grade", help="grade everything still ungraded")
    model_flags(p_grade)
    p_grade.set_defaults(func=_cmd_grade)

    p_regrade = sub.add_parser(
        "regrade", help="discard every grade and rebuild them from the raw answers"
    )
    p_regrade.add_argument("--yes", action="store_true", help="required; this discards grades")
    model_flags(p_regrade)
    p_regrade.set_defaults(func=_cmd_regrade)

    p_show = sub.add_parser("show", help="every attempt at one concept, oldest first")
    p_show.add_argument("concept_id")
    p_show.set_defaults(func=_cmd_show)

    p_question = sub.add_parser("question", help="the current wording and rubric")
    p_question.set_defaults(func=_cmd_question)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
