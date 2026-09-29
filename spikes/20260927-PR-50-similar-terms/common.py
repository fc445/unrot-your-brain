"""Shared plumbing for the PR-50 spike: paths, stores, sessions, and the classifiers.

Privacy rule for this folder: transcript text is read from the e2e run's
throwaway store and from ~/.unrot, both local, and only ever reduced to *terms,
counts and ids* before anything is written. Nothing under `out/` quotes a window,
and nothing committed quotes a term's paraphrase. The one thing that leaves the
machine is bare pairs of terms (and, in D, bare lists of terms) sent to
OpenRouter -- never a sentence of a transcript.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
OUT = SPIKE / "out"
REPO_MAIN = Path("/Users/freddiecassidy/Documents/GitHub/unrot-your-brain")
E2E = REPO_MAIN / "spikes/20260927-PR-41-e2e-pipeline-run/out/run1"
E2E_HOME = E2E / "home"
E2E_RESULTS = E2E / "results.json"
PR42_CANDIDATES = (
    REPO_MAIN
    / ".claude/worktrees/agent-a797b13ddcddfb125/spikes/20260927-PR-42-concept-isolation/out/candidates.json"
)
PR38_MATCH = REPO_MAIN / ".claude/worktrees/agent-a9fccc076c927bc7e/src/unrot/resolver/match.py"
PR38_TESTS = REPO_MAIN / ".claude/worktrees/agent-a9fccc076c927bc7e/tests/test_resolver.py"
REAL_DB = Path.home() / ".unrot/unrot.db"

LING = "inclusionai/ling-3.0-flash"
JEV = "typesafe/jev-1.13"


def load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(REPO_MAIN / ".env")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


def read_json(path: Path):
    return json.loads(Path(path).read_text())


# ---------------------------------------------------------------------------
# Stores. Every connection here is read-only by URI; nothing is ever written.
# ---------------------------------------------------------------------------


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def raw_conn() -> sqlite3.Connection:
    """run1's raw store, read-only.

    `unrot.capture.ingest.connect()` would work too, but it runs the schema
    script and sets WAL on open; a `mode=ro` connection gives `build_windows`
    the same rows without touching the file.
    """
    return ro(E2E_HOME / "raw" / "raw.db")


def real_conn() -> sqlite3.Connection:
    return ro(REAL_DB)


def run1_conn() -> sqlite3.Connection:
    return ro(E2E_HOME / "unrot.db")


# ---------------------------------------------------------------------------
# PR-38's matcher, imported by path (not merged yet).
# ---------------------------------------------------------------------------


def pr38_match():
    spec = importlib.util.spec_from_file_location("pr38_match", PR38_MATCH)
    import sys

    if "pr38_match" in sys.modules:
        return sys.modules["pr38_match"]
    module = importlib.util.module_from_spec(spec)
    sys.modules["pr38_match"] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def pr38_fixture_names() -> list[str]:
    """The 51 canonical names in PR-38's `_GRAPH` fixture, parsed from the test file."""
    text = PR38_TESTS.read_text()
    block = text.split("_GRAPH = [", 1)[1].split("\n]\n", 1)[0]
    return re.findall(r'^\s*\("([^"]+)"', block, flags=re.M)


# ---------------------------------------------------------------------------
# Term matching inside text (for locating candidates and counting mentions).
# ---------------------------------------------------------------------------

FENCE = re.compile(r"```.*?(```|$)", re.S)


def prose(text: str) -> str:
    return FENCE.sub("\n", text or "")


def term_pattern(term: str) -> re.Pattern:
    """Case-insensitive, word-bounded, with hyphens/spaces interchangeable."""
    parts = [re.escape(p) for p in re.split(r"[\s\-_]+", term.strip()) if p]
    body = r"[\s\-_]?".join(parts)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.I)


# ---------------------------------------------------------------------------
# The transfer test (C). One prompt, both directions, plus "same space".
# ---------------------------------------------------------------------------

TRANSFER_PROMPT = """\
Two technical terms, A and B, from a developer's work.

A: {a}
B: {b}

Imagine someone who can correctly answer this about A: "In a line or two: what \
is A, and why does it work the way it does?"

1. a_explains_b: Could that person ALSO correctly answer the same question about \
B, without learning anything new? Yes if B is the same idea under another name, \
spelling, word form or abbreviation, or if B adds nothing that A's explanation \
does not already contain. No if B needs knowledge that A's explanation does not \
include (B is narrower, more specific, builds on A, or is a different idea).
2. b_explains_a: The same question the other way: could someone who can explain B \
also explain A?
3. same_space: Are A and B about the same subject -- would they be taught in the \
same chapter -- even if they are different concepts?

Judge the terms as a developer would read them. Things that share words are not \
thereby the same: "strong consistency" and "eventual consistency" are opposite \
choices, so neither explains the other, though they are in the same space.
"""


def transfer_schema():
    from pydantic import BaseModel, Field

    class Transfer(BaseModel):
        a_explains_b: bool = Field(description="Could someone who can explain A also explain B?")
        b_explains_a: bool = Field(description="Could someone who can explain B also explain A?")
        same_space: bool = Field(description="Are A and B in the same subject area?")

    return Transfer


def verdict(a_to_b: bool, b_to_a: bool, same_space: bool) -> str:
    """The ticket's mapping from two directional answers to a relation.

    a_to_b means "knowing A is enough for B". If only that holds, A contains
    B's knowledge and more: A extends B (B is broader or a prerequisite).
    """
    if a_to_b and b_to_a:
        return "identity"
    if a_to_b:
        return "a_extends_b"
    if b_to_a:
        return "b_extends_a"
    return "related" if same_space else "unrelated"


class Deadline(TimeoutError):
    """A model call that did not return within its wall-clock budget."""


def with_deadline(fn, seconds: float):
    """Run `fn()` in a daemon thread and give up after `seconds`.

    Belt and braces over the HTTP client's own timeout: the first full D run
    hung for ~15 hours after its last log line with nothing in flight that a
    request timeout covered. A daemon thread that never returns is abandoned
    rather than joined, so it can neither block the pool nor the exit.
    """
    import threading

    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise Deadline(f"no answer after {seconds:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


#: Wall-clock budget per model call. ling-low's slowest C call took 95 s.
CALL_DEADLINE = 240.0


@dataclass
class CallResult:
    answer: dict | None
    seconds: float
    cost: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    error: str | None = None


def _meter_totals(meter) -> tuple[float | None, int | None, int | None, int | None]:
    cost = pt = ct = rt = None
    for call in meter.calls:
        if call.cost is not None:
            cost = (cost or 0.0) + call.cost
        for name in ("prompt_tokens", "completion_tokens", "reasoning_tokens"):
            v = getattr(call, name)
            if v is not None:
                if name == "prompt_tokens":
                    pt = (pt or 0) + v
                elif name == "completion_tokens":
                    ct = (ct or 0) + v
                else:
                    rt = (rt or 0) + v
    return cost, pt, ct, rt


def ling_transfer(a: str, b: str, *, reasoning: str) -> CallResult:
    """One transfer-test call. `reasoning` is "low" (the product's setting) or "off".

    "off" uses `Structured.forced`, the product's own reasoning-disabled client
    (`reasoning: {enabled: false}`), called directly with the same prompt.
    """
    from unrot.model import ModelConfig, structured_client
    from unrot.spend import Meter

    meter = Meter()
    config = ModelConfig.from_env(model=LING, reasoning_effort="low")
    client = structured_client(config, transfer_schema(), meter=meter, purpose="pr50-transfer")
    prompt = TRANSFER_PROMPT.format(a=a, b=b)
    started = time.monotonic()
    try:
        run = (lambda: client.forced.invoke(prompt)) if reasoning == "off" else (lambda: client.invoke(prompt))
        out = with_deadline(run, CALL_DEADLINE)
        answer = out.model_dump() if hasattr(out, "model_dump") else dict(out)
        error = None
    except Exception as exc:  # noqa: BLE001
        answer, error = None, f"{type(exc).__name__}: {exc}"[:300]
    seconds = time.monotonic() - started
    cost, pt, ct, rt = _meter_totals(meter)
    return CallResult(answer, seconds, cost, pt, ct, rt, error)


JEV_INSTRUCTIONS = (
    "Imagine someone who can correctly answer, in a line or two, what {x} is and why"
    " it works the way it does. Could they ALSO correctly explain {y} without"
    " learning anything new?"
)


def jev_questions():
    from langchain_typesafe import Choice

    def direction(x: str, y: str):
        return Choice(
            instructions=JEV_INSTRUCTIONS.format(x=x, y=y),
            criteria={
                "yes": f"{y} is the same idea as {x} under another name, spelling, word form or"
                f" abbreviation, or adds nothing that an explanation of {x} does not already contain.",
                "no": f"{y} needs knowledge an explanation of {x} does not include: it is narrower,"
                f" more specific, builds on {x}, or is a different idea. Sharing words is not"
                " sharing meaning.",
            },
        )

    return {
        "a_explains_b": direction("A", "B"),
        "b_explains_a": direction("B", "A"),
        "same_space": Choice(
            instructions="Are A and B about the same subject -- would they be taught in the same chapter?",
            criteria={
                "yes": "Same subject area, even if they are different concepts.",
                "no": "Different subject areas.",
            },
        ),
    }


def jev_transfer(a: str, b: str) -> CallResult:
    from unrot.model import ModelConfig, decision_client
    from unrot.spend import Meter

    meter = Meter()
    config = ModelConfig.from_env()
    classify = decision_client(config, model=JEV, meter=meter, purpose="pr50-transfer-jev")
    state = f"Two technical terms from a developer's work.\nA: {a}\nB: {b}"
    started = time.monotonic()
    try:
        response = classify(state, jev_questions())
        answer = {}
        for key in ("a_explains_b", "b_explains_a", "same_space"):
            got = response.answers.get(key)
            probs = dict(getattr(got, "probabilities", None) or {})
            answer[key] = probs.get("yes", 0.0) >= 0.5 if probs else None
            answer[f"p_{key}"] = probs.get("yes")
        error = None
    except Exception as exc:  # noqa: BLE001
        answer, error = None, f"{type(exc).__name__}: {exc}"[:300]
    seconds = time.monotonic() - started
    cost, pt, ct, rt = _meter_totals(meter)
    return CallResult(answer, seconds, cost, pt, ct, rt, error)
