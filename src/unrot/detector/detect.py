"""Run the detector over a captured session.

Deliberately crude, per the ticket: this only has to be good enough to exercise
the chain. What it must NOT do is manufacture. An honest empty answer is the
thing journey 3 depends on, and a detector that always finds something is worse
than one that sometimes finds nothing -- noise is what kills this past day three.

Two filters do most of the work before any ranking happens:

* `questioned` is dropped outright. Asking what a term means is evidence of
  understanding, which is a reason to stay quiet, not to flag.
* `unclear` is kept in the ranked list but never emitted. A gap requires an
  observed acceptance; without a next turn there is nothing to have accepted.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from . import prompt as prompt_module
from .candidates import IMPORTANCE, SIGNALS, Candidate, DetectionResult
from .windows import build_windows, chunk_windows

DETECTOR_VERSION = "0.1.0"

#: The flag budget. Precision over recall -- the ticket's ~1-2 per session.
DEFAULT_MAX_CANDIDATES = 2

#: Ranking order. `central` beats `supporting`; an observed acceptance beats a
#: session that simply ended. Ties break on term so the order is total, which is
#: what makes a re-run comparable to the last one.
_IMPORTANCE_RANK = {"central": 0, "supporting": 1}
_SIGNAL_RANK = {"accepted": 0, "unclear": 1, "questioned": 2}


def detector_version(model_label: str) -> str:
    """Encodes what produced a judgment: version, model, and prompt.

    S5's requirement, made concrete. A model swap or a prompt edit changes this
    string, so history records that the machine changed rather than leaving it
    indistinguishable from the user's world having changed.
    """
    return f"detector/{DETECTOR_VERSION}+{model_label}+{prompt_module.prompt_id()}"


def detect(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    propose,
    model_label: str,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    chunk_budget: int = 40_000,
    concurrency: int = 1,
) -> DetectionResult:
    """Detect candidate gaps in one captured session.

    `propose` takes a rendered prompt and returns raw candidate dicts. Passing it
    in rather than building it here keeps this function free of langchain, of a
    network, and of any opinion about which model is answering.

    With `concurrency` above 1, a session long enough to need several chunks
    sends up to that many at once. The chunks are independent -- each is a
    separate prompt over separate windows -- and their answers are gathered back
    in chunk order, so a term flagged in two chunks is still kept from the
    earlier one, exactly as it was when they ran in turn. A chunk that fails
    fails the session, as before; the others still in flight finish, and are
    billed, because a call cannot be taken back once it is sent.
    """
    version = detector_version(model_label)
    windows = build_windows(conn, session_id)
    chunks = chunk_windows(windows, budget=chunk_budget)

    by_line = {w.assistant_line: w for w in windows}
    prompts = [
        prompt_module.render(prompt_module.format_windows(chunk), max_candidates=max_candidates)
        for chunk in chunks
    ]
    if concurrency > 1 and len(prompts) > 1:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(prompts))) as pool:
            answers = list(pool.map(propose, prompts))
    else:
        answers = [propose(p) for p in prompts]
    raw: list[dict] = [item for answer in answers for item in answer]

    seen: set[str] = set()
    candidates: list[Candidate] = []
    for item in raw:
        candidate = _coerce(item, session_id, version, by_line)
        if candidate is None:
            continue
        # A term flagged in two chunks is one gap, not two.
        key = candidate.term.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)

    ranked = sorted(
        candidates,
        key=lambda c: (
            _SIGNAL_RANK.get(c.signal, 9),
            _IMPORTANCE_RANK.get(c.importance, 9),
            c.line_start,
            c.term.casefold(),
        ),
    )
    ranked = [
        Candidate(**{**c.as_dict(), "rank": i}) for i, c in enumerate(ranked, start=1)
    ]

    # Only an observed acceptance is a gap. No floor: a clean session emits
    # nothing, and that is the answer, not a failure to find one.
    emitted = [c for c in ranked if c.is_gap][:max_candidates]

    return DetectionResult(
        session_id=session_id,
        detector_version=version,
        emitted=emitted,
        ranked=ranked,
        windows_examined=len(windows),
        calls_made=len(chunks),
    )


def _coerce(item: dict, session_id: str, version: str, by_line: dict) -> Candidate | None:
    """Turn one model-proposed dict into a Candidate, or drop it.

    The model is an untrusted source here: it can hallucinate a line number, a
    signal we do not have, or an empty term. Anything that cannot be anchored to
    a real window is discarded rather than stored with a pointer that resolves
    to the wrong place -- a wrong pointer is worse than a missing candidate.
    """
    if not isinstance(item, dict):
        return None
    term = str(item.get("term") or "").strip()
    paraphrase = str(item.get("paraphrase") or "").strip()
    if not term or not paraphrase:
        return None

    signal = str(item.get("signal") or "").strip().lower()
    if signal not in SIGNALS:
        return None
    if signal == "questioned":
        # Evidence of understanding. Not a gap, and not worth ranking either.
        return None

    importance = str(item.get("importance") or "").strip().lower()
    if importance not in IMPORTANCE:
        importance = "supporting"

    try:
        assistant_line = int(item.get("assistant_line"))
    except (TypeError, ValueError):
        return None
    window = by_line.get(assistant_line)
    if window is None:
        return None

    line_end = window.line_end
    acceptance = item.get("acceptance_line")
    if acceptance is not None:
        try:
            claimed = int(acceptance)
        except (TypeError, ValueError):
            claimed = None
        # Trust the window over the model: it knows which human turn actually
        # followed, and the model is guessing from text it was shown.
        if claimed is not None and window.human_line and claimed == window.human_line:
            line_end = claimed

    return Candidate(
        term=term,
        paraphrase=paraphrase,
        session_id=session_id,
        line_start=window.line_start,
        line_end=line_end,
        signal=signal,
        importance=importance,
        detector_version=version,
    )
