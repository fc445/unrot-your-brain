#!/usr/bin/env python3
"""
Thin LangGraph proposer over a Claude Code transcript (unrot-your-brain, PR-6).

PR-6 asked: does a manual-only v1 graph get anywhere, or is a thin proposer
mandatory? Rather than hand-labeling 10 sessions to answer that indirectly,
this runs an actual (unpolished, unvalidated) proposer over a real session
and prints what it finds — look at the output and judge for yourself whether
it's finding real gaps or noise.

Two-node graph:
  load_transcript    -> parses the .jsonl (via jsonl_transcript_parser) and
                         pairs each assistant turn with the human's next turn
  propose_candidates -> one structured-output LLM call that flags terms used
                         load-bearingly and without explanation, and classifies
                         the human's next turn as accepted / questioned / unclear

Model access goes through an OpenAI-compatible endpoint (OpenRouter by default,
or any local server such as Ollama/LM Studio) rather than a direct provider SDK.

Usage:
    export OPENROUTER_API_KEY=...
    python3 candidate_proposer.py --latest
    python3 candidate_proposer.py <path-to-session.jsonl>

    # local model instead of OpenRouter:
    python3 candidate_proposer.py --latest \
        --base-url http://localhost:11434/v1 --api-key ollama --model llama3.1

Not a product: no storage, no MCP server, no budget-limiting to 1-2 flags.
Just enough to look at real output and make the sequencing call.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI

sys.path.insert(0, str(Path(__file__).parent))
from jsonl_transcript_parser import parse_file, find_latest_session  # noqa: E402


DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"


class Candidate(BaseModel):
    """A single candidate concept the assistant used load-bearingly."""

    term: str = Field(description="The term or concept, as named in the transcript")
    context: str = Field(description="One-sentence paraphrase of where/how it appeared")
    assistant_line: int = Field(description="Line number of the assistant turn where it appeared")
    signal: Literal["accepted", "questioned", "unclear"] = Field(
        description=(
            "accepted = the human's next turn shows blind acceptance with no sign of "
            "understanding; questioned = the human asked what it meant, or used/explained "
            "it correctly themselves; unclear = no usable next turn to judge from"
        )
    )


class CandidateList(BaseModel):
    """All candidate concepts found across one transcript."""

    candidates: list[Candidate]


class DetectorState(TypedDict):
    session_path: str
    windows: list[dict]
    candidates: list[Candidate]


def build_windows(session_path: Path) -> list[dict]:
    """Pair each assistant turn with the next non-meta human turn, if any."""
    report = parse_file(session_path)
    user_turns = sorted(
        (t for t in report.user_turns if not t.is_meta), key=lambda t: t.line_no
    )

    windows = []
    for a in report.assistant_turns:
        if not a.text.strip():
            continue
        following = next((u for u in user_turns if u.line_no > a.line_no), None)
        windows.append(
            {
                "assistant_line": a.line_no,
                "assistant_text": a.text,
                "next_user_text": following.text if following else None,
            }
        )
    return windows


def load_transcript(state: DetectorState) -> dict:
    windows = build_windows(Path(state["session_path"]))
    return {"windows": windows}


PROMPT_TEMPLATE = """You are looking at excerpts from a real Claude Code session: an \
assistant turn, paired with whatever the human said immediately afterward (if anything).

Find terms or concepts the assistant used LOAD-BEARINGLY -- i.e. the human needed to \
understand the term to follow what happened next -- WITHOUT explaining it inline. Skip \
anything the assistant already defined, and skip trivial/well-known terms (e.g. \
"function", "variable", "file").

For each one you find, classify the human's next turn:
- "accepted": they moved on, said "ok"/"go ahead"/similar, with no sign they understood the term
- "questioned": they asked what it meant, pushed back, or used/explained it correctly themselves
- "unclear": there's no next turn to judge (end of session, or the next line isn't a real reply)

Be sparing. A real session should surface a small number of genuine candidates, not every \
noun that was mentioned.

Transcript excerpts:
{excerpts}
"""


def make_propose_candidates(model_name: str, base_url: str, api_key: str):
    def propose_candidates(state: DetectorState) -> dict:
        model = ChatOpenAI(model=model_name, base_url=base_url, api_key=api_key, temperature=0)
        structured_model = model.with_structured_output(CandidateList)

        excerpts = []
        for w in state["windows"]:
            block = f"--- assistant (line {w['assistant_line']}) ---\n{w['assistant_text']}\n"
            if w["next_user_text"]:
                block += f"--- human, next turn ---\n{w['next_user_text']}\n"
            else:
                block += "--- human, next turn ---\n(none)\n"
            excerpts.append(block)

        prompt = PROMPT_TEMPLATE.format(excerpts="\n".join(excerpts))
        result: CandidateList = structured_model.invoke(prompt)
        return {"candidates": result.candidates}

    return propose_candidates


def build_graph(model_name: str, base_url: str, api_key: str):
    graph = StateGraph(DetectorState)
    graph.add_node("load_transcript", load_transcript)
    graph.add_node("propose_candidates", make_propose_candidates(model_name, base_url, api_key))
    graph.add_edge(START, "load_transcript")
    graph.add_edge("load_transcript", "propose_candidates")
    graph.add_edge("propose_candidates", END)
    return graph.compile()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session_file", nargs="?", help="Path to a .jsonl session file")
    parser.add_argument("--latest", action="store_true", help="Use the most recently modified session under ~/.claude/projects")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model id to request from the endpoint (default: {DEFAULT_MODEL})")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"OpenAI-compatible endpoint (default: OpenRouter, {DEFAULT_BASE_URL})")
    parser.add_argument("--api-key", default=None, help="API key; defaults to $OPENROUTER_API_KEY, falling back to $OPENAI_API_KEY")
    args = parser.parse_args()

    if args.latest:
        path = find_latest_session()
        if path is None:
            print("No session files found under ~/.claude/projects", file=sys.stderr)
            sys.exit(1)
    elif args.session_file:
        path = Path(args.session_file).expanduser()
    else:
        parser.print_help()
        sys.exit(1)

    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    api_key = args.api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("No API key found: pass --api-key or set $OPENROUTER_API_KEY (or $OPENAI_API_KEY for a local server that ignores it).", file=sys.stderr)
        sys.exit(1)

    app = build_graph(args.model, args.base_url, api_key)
    result = app.invoke({"session_path": str(path)})

    print(f"Session: {path}")
    print(f"Windows examined: {len(result['windows'])}")
    print(f"Candidates surfaced: {len(result['candidates'])}\n")
    for c in result["candidates"]:
        print(f"[{c.signal:>10}] {c.term}  (L{c.assistant_line})")
        print(f"             {c.context}\n")


if __name__ == "__main__":
    main()
