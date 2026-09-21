"""`python -m unrot.detector` -- run the detector over captured sessions."""

from __future__ import annotations

import argparse
import json
import sys

from ..capture import connect as connect_raw
from ..env import load_env
from . import prompt as prompt_module
from .detect import DEFAULT_MAX_CANDIDATES, detect, detector_version
from .model import ModelConfig, build_proposer
from .windows import build_windows, chunk_windows

# Keys, the model id and the endpoint all come from the project's `.env` if one
# is present; none of them are required, and a real environment variable always
# wins over the file.
load_env()


def _sessions(conn, args) -> list[str]:
    if args.session:
        return args.session
    rows = conn.execute(
        "SELECT s.session_id FROM raw_sessions s"
        " WHERE EXISTS (SELECT 1 FROM raw_turns t WHERE t.session_id = s.session_id"
        "   AND t.role = 'user' AND t.is_meta = 0 AND t.is_sidechain = 0)"
        " ORDER BY (SELECT max(occurred_at) FROM raw_turns t"
        "   WHERE t.session_id = s.session_id) DESC"
    ).fetchall()
    return [r["session_id"] for r in rows][: args.limit]


def _print_candidate(c, *, emitted: bool) -> None:
    mark = "GAP " if emitted else "    "
    print(f"  {mark}#{c.rank} {c.term}  [{c.importance}/{c.signal}]  lines {c.line_start}-{c.line_end}")
    print(f"       {c.paraphrase}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="unrot.detector", description=__doc__)
    parser.add_argument("session", nargs="*", help="session ids (default: all ingested)")
    parser.add_argument("--home", help="override $UNROT_HOME")
    parser.add_argument("--limit", type=int, default=5, help="how many sessions (default 5)")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_CANDIDATES, help="flag budget per session")
    parser.add_argument("--model", help="model id (default: anthropic/claude-sonnet-5 via OpenRouter)")
    parser.add_argument("--base-url", help="OpenAI-compatible endpoint; point at a local server to stay offline")
    parser.add_argument("--api-key", help="defaults to $OPENROUTER_API_KEY, then $OPENAI_API_KEY")
    parser.add_argument("--ranked", action="store_true", help="show everything found, not just what the budget emitted")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--print-prompt", action="store_true", help="render the prompt and exit, calling no model")
    args = parser.parse_args(argv)

    conn = connect_raw(args.home)
    sessions = _sessions(conn, args)
    if not sessions:
        print("No ingested sessions with human turns. Run: python -m unrot.capture ingest")
        return 1

    if args.print_prompt:
        windows = build_windows(conn, sessions[0])
        chunks = chunk_windows(windows)
        print(prompt_module.render(prompt_module.format_windows(chunks[0] if chunks else [])))
        return 0

    config = ModelConfig.from_env(
        model=args.model, base_url=args.base_url, api_key=args.api_key
    )
    try:
        propose = build_proposer(config)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    payload = []
    for session_id in sessions:
        result = detect(
            conn,
            session_id,
            propose=propose,
            model_label=config.label,
            max_candidates=args.max,
        )
        if args.json:
            payload.append(
                {
                    "session_id": result.session_id,
                    "detector_version": result.detector_version,
                    "windows_examined": result.windows_examined,
                    "calls_made": result.calls_made,
                    "emitted": [c.as_dict() for c in result.emitted],
                    "ranked": [c.as_dict() for c in result.ranked],
                }
            )
            continue

        print(f"\n{session_id}  ({result.windows_examined} windows, {result.calls_made} call(s))")
        shown = result.ranked if args.ranked else result.emitted
        if not shown:
            print("  no gaps -- nothing was leaned on that you waved through")
        emitted_ids = {id(c) for c in result.emitted}
        for candidate in shown:
            _print_candidate(candidate, emitted=id(candidate) in emitted_ids)
        if result.suppressed and not args.ranked:
            print(f"  ({result.suppressed} more below the budget; --ranked to see them)")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"\n{detector_version(config.label)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
