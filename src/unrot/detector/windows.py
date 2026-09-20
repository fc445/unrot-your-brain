"""Pairing what the assistant said with what the human said back.

The window is the unit of judgment: a thing the assistant asserted, and the
human's immediate response to it. Everything the detector decides is decided
about a pair.

Built from the capture store rather than from `.jsonl` directly, which fixes the
PR-6 spike's known bug for free. The spike filtered only `isMeta` when looking
for "the human's next turn", so a tool result echoed back into the conversation
could be read as a person saying "fine, carry on". Capture already separates
those, so "the next thing a human actually typed" is a query rather than a
heuristic.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Window:
    assistant_line: int
    assistant_text: str
    #: None when the session ended, or when nothing human followed.
    human_line: int | None = None
    human_text: str | None = None

    @property
    def line_start(self) -> int:
        return self.assistant_line

    @property
    def line_end(self) -> int:
        return self.human_line or self.assistant_line

    @property
    def size(self) -> int:
        return len(self.assistant_text) + len(self.human_text or "")


def build_windows(
    conn: sqlite3.Connection, session_id: str, *, min_chars: int = 40
) -> list[Window]:
    """Pair each assistant turn with the next turn a human actually typed."""
    assistant = list(
        conn.execute(
            "SELECT line_no, text FROM raw_turns WHERE session_id = ?"
            " AND role = 'assistant' AND text IS NOT NULL ORDER BY line_no",
            (session_id,),
        )
    )
    human = list(
        conn.execute(
            "SELECT line_no, text FROM raw_turns WHERE session_id = ?"
            " AND role = 'user' AND is_meta = 0 AND is_sidechain = 0"
            " AND text IS NOT NULL ORDER BY line_no",
            (session_id,),
        )
    )

    windows: list[Window] = []
    for row in assistant:
        text = (row["text"] or "").strip()
        # A one-line acknowledgement cannot contain a load-bearing claim, and
        # sending it costs tokens to be told so.
        if len(text) < min_chars:
            continue
        following = next((h for h in human if h["line_no"] > row["line_no"]), None)
        windows.append(
            Window(
                assistant_line=row["line_no"],
                assistant_text=text,
                human_line=following["line_no"] if following else None,
                human_text=(following["text"] or "").strip() if following else None,
            )
        )
    return windows


def group_windows(windows: list[Window]) -> list[list[Window]]:
    """Group consecutive assistant turns that share the same human reply.

    A run of assistant turns followed by one human turn is what actually
    happened in the conversation: the person replied once, to all of it. Treating
    each assistant turn as its own window and repeating the reply after every one
    is both a worse model of the exchange and wildly expensive -- on real
    sessions here the human's text was repeated 9-12 times, turning 26k
    characters of assistant text into a 239k-character prompt.
    """
    groups: list[list[Window]] = []
    for window in windows:
        if groups and groups[-1][0].human_line == window.human_line:
            groups[-1].append(window)
        else:
            groups.append([window])
    return groups


def group_size(group: list[Window]) -> int:
    """Rendered size: every assistant turn, and the shared human turn once."""
    return sum(len(w.assistant_text) for w in group) + len(group[0].human_text or "")


def chunk_windows(windows: list[Window], *, budget: int = 40_000) -> list[list[Window]]:
    """Split into batches the model can hold at once.

    Packs whole groups, so a reply is never separated from the turns it was
    replying to -- splitting there would ask the model to judge an acceptance it
    cannot see. A single group larger than the budget goes in a chunk of its own
    rather than being broken up.
    """
    if not windows:
        return []
    chunks: list[list[Window]] = []
    current: list[Window] = []
    used = 0
    for group in group_windows(windows):
        size = group_size(group)
        if current and used + size > budget:
            chunks.append(current)
            current, used = [], 0
        current.extend(group)
        used += size
    if current:
        chunks.append(current)
    return chunks
