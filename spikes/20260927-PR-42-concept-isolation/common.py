"""Shared plumbing for the PR-42 spike: sessions, gold terms, normalisation, matching.

Nothing here quotes transcript text into a committed file. Sessions are read from
the e2e run's throwaway store (gitignored), and anything derived from them is
written under this spike's `out/` (also gitignored).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import snowballstemmer

SPIKE = Path(__file__).resolve().parent
OUT = SPIKE / "out"
REPO_MAIN = Path("/Users/freddiecassidy/Documents/GitHub/unrot-your-brain")
E2E = REPO_MAIN / "spikes/20260927-PR-41-e2e-pipeline-run/out/run1"
HOME = E2E / "home"
RESULTS = E2E / "results.json"

# --------------------------------------------------------------------------
# Gold: the ticket's list, pinned to the session each one was found in.
# --------------------------------------------------------------------------
# "Plausible real gaps" from the e2e run (ticket PR-42), keyed by session-id
# prefix. TCC/xattr were found in two sessions and notarisation in two, so they
# are counted in each. These are the detector's own finds, judged plausible by
# a person -- NOT blind labels (see README, "What this gold set is not").
GAPS: dict[str, list[str]] = {
    "efacfa27": ["launchd"],
    "02cbbad7": ["LaunchAgent", "named volume"],
    "65de8a45": ["copyleft", "MCP servers"],
    "75704410": ["FSEvents"],
    "13d5c0d2": ["Full Keyboard Access", "Liquid Glass", "merge commit", "VoiceOver"],
    "3830fa9c": ["TCC", "xattr"],
    "d2930d59": ["TCC", "xattr"],
    "0805c0ae": ["hardened runtime", "notarisation", "actionlint"],
    "33d84b15": ["appcast", "EdDSA", "ad-hoc signing", "Developer ID"],
    "584e48ee": ["remote code execution"],
    "069485ab": ["reflog", "remote-tracking ref"],
    "d0bd9f60": ["tree-sitter"],
    "bbc1eb50": ["Stop hook"],
    "19bfdad8": ["notarisation"],
}

# Project-local labels the detector wrongly proposed, from the same run.
LOCAL: dict[str, list[str]] = {
    "2868a6fa": ["provenance gate", "format 1"],
    "13d5c0d2": ["promotion PR"],
    "dd20e74e": ["comprehension check", "the funnel"],
    "f409f1f5": ["forced retry", "frozen sidecar", "source-link model"],
}

# The PR-33 synthetic pair. Candidate recall here means "the term reaches the
# judge": the not-gaps are concepts too, and deciding they were understood is
# the judge's job, not the extractor's.
SYNTH_GAPS = {"sess-messy": ["outbox", "dual-write"]}
SYNTH_NOT_GAPS = {"sess-messy": ["idempotent", "at-least-once"]}


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------
@dataclass
class Session:
    session_id: str
    project_dir: str | None
    windows: list  # unrot.detector.windows.Window

    @property
    def assistant_text(self) -> str:
        return "\n\n".join(w.assistant_text for w in self.windows)


def load_sessions(min_windows: int = 1) -> list[Session]:
    from unrot.capture.ingest import connect as connect_raw
    from unrot.detector.windows import build_windows

    shapes = json.loads(RESULTS.read_text())["shapes"]
    conn = connect_raw(HOME)
    ids = [r[0] for r in conn.execute("SELECT DISTINCT session_id FROM raw_turns ORDER BY session_id")]
    sessions = []
    for sid in ids:
        windows = build_windows(conn, sid)
        if len(windows) < min_windows:
            continue
        cwd = (shapes.get(sid) or {}).get("cwd")
        sessions.append(Session(sid, cwd, windows))
    return sessions


def by_prefix(sessions: list[Session], prefix: str) -> Session:
    return next(s for s in sessions if s.session_id.startswith(prefix))


# --------------------------------------------------------------------------
# Text preparation
# --------------------------------------------------------------------------
FENCE = re.compile(r"```.*?(```|$)", re.S)
INLINE_CODE = re.compile(r"`([^`\n]+)`")


def prose(text: str) -> str:
    """Assistant text with fenced code blocks removed. Inline code is kept."""
    return FENCE.sub("\n", text)


def backticked(text: str) -> set[str]:
    return {m.strip() for m in INLINE_CODE.findall(prose(text))}


# --------------------------------------------------------------------------
# Normalisation (H6) and matching
# --------------------------------------------------------------------------
_stem = snowballstemmer.stemmer("english")
_TOKEN = re.compile(r"[a-z0-9]+")
_LEADING = {"the", "a", "an", "this", "that", "these", "those", "its", "their", "our", "your", "my"}


def tokens(term: str) -> list[str]:
    t = term.casefold().replace("`", "")
    # British/American -ise/-ize (notarisation/notarization) before stemming.
    t = re.sub(r"iz(e|ed|es|ing|ation|ations)\b", r"is\1", t)
    toks = _TOKEN.findall(t)
    while toks and toks[0] in _LEADING:
        toks = toks[1:]
    return toks


def norm(term: str, stem: bool = True) -> tuple[str, ...]:
    toks = tokens(term)
    return tuple(_stem.stemWords(toks) if stem else toks)


def matches(candidate: str, gold: str, *, stem: bool = True, slack: int = 2) -> bool:
    """A candidate covers a gold term if it equals it after normalisation, or
    contains it as a contiguous span and is at most `slack` tokens longer
    ("hardened runtime entitlement" covers "hardened runtime")."""
    c, g = norm(candidate, stem), norm(gold, stem)
    if not g or not c:
        return False
    if c == g:
        return True
    if len(c) > len(g) + slack:
        return False
    return any(c[i : i + len(g)] == g for i in range(len(c) - len(g) + 1))


def covered(candidates, gold: str, **kw) -> bool:
    return any(matches(c, gold, **kw) for c in candidates)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str))
