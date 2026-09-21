"""Where material is allowed to come from, and what it takes to count.

This module is the P0 gate. `decisions.md` is unusually blunt about why:

    The product generates explanations of precisely the things the user cannot
    evaluate. A hallucinated definition lands on someone who by definition can't
    catch it, and is then wrong in their head *and* in the graph. Worse than not
    flagging at all.

So a source is not a string a model produced. It is something that was **reached
and read**, and `excerpt` holds what came back. A source that could not be
reached is kept, marked unverified, and is not allowed anywhere near generated
prose -- the user can click an unverified link and judge it themselves, which is
the one thing they cannot do with a sentence.

Three kinds, in descending order of how much they are worth:

* `code` -- the user's own repository. "Explain the queue in *their* ingest
  service, not backpressure in the abstract" is recorded as the moat, and it is
  the only source kind that is unarguably about their situation.
* `transcript` -- the exchange where the term came up. Always verifiable,
  because unrot keeps its own copy, and it is the S4 pointer path getting an
  end-to-end exercise that neither shipped format would otherwise give it.
* `web` -- retrieved, then fetched to check it exists and says something about
  the term.
"""

from __future__ import annotations

import re
import sqlite3
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

#: How much of a source is kept. Enough to ground a paragraph, short enough that
#: a dozen of them still fit in a prompt beside the rest of the instructions.
EXCERPT = 700

#: Directories that are never someone's own code in any useful sense.
_SKIP = {
    ".git", ".venv", "node_modules", "__pycache__", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages",
}

_CODE_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".java", ".kt",
    ".sql", ".sh", ".md", ".toml", ".yaml", ".yml",
}


@dataclass(frozen=True)
class Source:
    kind: str                 # 'code' | 'transcript' | 'web'
    ref: str                  # path, session pointer, or url
    title: str
    excerpt: str | None = None
    #: True only when the content in `excerpt` was actually read back. Generated
    #: prose may cite nothing else.
    verified: bool = False
    note: str | None = None   # why it could not be verified, when it could not

    def as_dict(self) -> dict:
        return asdict(self)


def _clip(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:EXCERPT]


# ---------------------------------------------------------------------------
# The transcript the term came up in
# ---------------------------------------------------------------------------


def from_transcript(
    raw_conn: sqlite3.Connection | None,
    session_id: str | None,
    line_start: int | None,
    line_end: int | None,
) -> Source | None:
    """The exchange itself, as a source. Verified by definition -- we kept it.

    Worth more than it looks. Everything else explains the concept in general;
    this is the only source that says *where you met it*, which is what the
    material is supposed to be about.
    """
    if raw_conn is None or not session_id:
        return None
    rows = raw_conn.execute(
        "SELECT role, text FROM raw_turns WHERE session_id = ?"
        "   AND line_no BETWEEN ? AND ? AND text IS NOT NULL"
        "   AND is_meta = 0 AND is_sidechain = 0"
        " ORDER BY line_no, seq LIMIT 8",
        (session_id, line_start or 1, line_end or line_start or 1),
    ).fetchall()
    if not rows:
        return None
    body = " ".join(f"{r['role']}: {r['text']}" for r in rows)
    return Source(
        kind="transcript",
        ref=f"{session_id}:{line_start}-{line_end}",
        title="The session where this came up",
        excerpt=_clip(body),
        verified=True,
    )


# ---------------------------------------------------------------------------
# The user's own code
# ---------------------------------------------------------------------------


def from_code(root: str | Path | None, term: str, *, limit: int = 3) -> list[Source]:
    """Places in the user's own repository where the term actually appears.

    Searched rather than asked for: a model naming a file in someone's codebase
    is guessing, and a citation that points at a file which does not exist is
    the same defect as an invented URL. Every hit here was read off disk.
    """
    if not root:
        return []
    base = Path(root).expanduser()
    if not base.is_dir():
        return []

    needle = re.compile(re.escape(term), re.IGNORECASE)
    found: list[Source] = []
    for path in sorted(base.rglob("*")):
        if len(found) >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in _CODE_SUFFIXES:
            continue
        if any(part in _SKIP for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = needle.search(text)
        if not match:
            continue
        line_no = text.count("\n", 0, match.start()) + 1
        start = max(0, match.start() - 220)
        found.append(
            Source(
                kind="code",
                ref=f"{path.relative_to(base)}:{line_no}",
                title=f"Your own code: {path.relative_to(base)}",
                excerpt=_clip(text[start : match.end() + 320]),
                verified=True,
            )
        )
    return found


def repo_for(raw_conn: sqlite3.Connection | None, session_id: str | None) -> str | None:
    """Which checkout a session was working in, as capture recorded it."""
    if raw_conn is None or not session_id:
        return None
    row = raw_conn.execute(
        "SELECT cwd FROM raw_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row["cwd"] if row else None


# ---------------------------------------------------------------------------
# The web
# ---------------------------------------------------------------------------


def verify_web(source: Source, *, term: str, timeout: float = 8.0) -> Source:
    """Fetch a candidate link and read it back, or mark it unverified.

    Not a status check. A page that returns 200 and never mentions the term is
    not a source for the term, and treating one as though it were is how a
    citation ends up decorating a claim it does not support.
    """
    request = urllib.request.Request(
        source.ref,
        headers={"User-Agent": "unrot/0.1 (+learning-material source check)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 400:
                return Source(**{**source.as_dict(), "verified": False,
                                 "note": f"returned HTTP {response.status}"})
            raw = response.read(400_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return Source(**{**source.as_dict(), "verified": False,
                         "note": f"could not be reached ({type(exc).__name__})"})

    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    if term.lower() not in text.lower():
        return Source(**{**source.as_dict(), "verified": False,
                         "note": "reachable, but does not mention the term"})

    index = text.lower().index(term.lower())
    return Source(
        **{
            **source.as_dict(),
            "excerpt": _clip(text[max(0, index - 260) : index + 540]),
            "verified": True,
            "note": None,
        }
    )


def gather(
    conn: sqlite3.Connection,
    raw_conn: sqlite3.Connection | None,
    concept_id: str,
    *,
    search=None,
    check_web: bool = True,
) -> list[Source]:
    """Everything we can honestly stand behind for one concept.

    `search` is injected, like every other model call in this codebase, so the
    gathering and verification rules are testable without a network.
    """
    concept = conn.execute(
        "SELECT canonical_name FROM compiled_concepts WHERE concept_id = ?",
        (concept_id,),
    ).fetchone()
    if concept is None:
        raise ValueError(f"no concept {concept_id!r}")
    term = concept["canonical_name"]

    encounters = conn.execute(
        "SELECT session_id, line_start, line_end FROM compiled_encounters"
        " WHERE concept_id = ? ORDER BY occurred_at DESC",
        (concept_id,),
    ).fetchall()

    out: list[Source] = []
    seen_repos: set[str] = set()
    for row in encounters:
        found = from_transcript(
            raw_conn, row["session_id"], row["line_start"], row["line_end"]
        )
        if found:
            out.append(found)
        repo = repo_for(raw_conn, row["session_id"])
        if repo and repo not in seen_repos:
            seen_repos.add(repo)
            out.extend(from_code(repo, term))

    if search is not None:
        for candidate in search(term):
            out.append(verify_web(candidate, term=term) if check_web else candidate)

    return out
