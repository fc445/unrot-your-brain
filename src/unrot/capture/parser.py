"""Parser for Claude Code session transcripts. Productionised from the PR-9 spike.

We ship our own rather than take an OSS one (PR-12). The available tools are
built for browsing, cost analysis and search, and none of them need the single
distinction unrot is built on: separating genuine human turns from `tool_result`
blocks that merely arrive in a `user`-typed line. A "user turn" that is really a
tool result is not a human accepting anything, and acceptance is the signal.

The line schema is internal to Claude Code and drifts between releases, so every
step here degrades rather than raises. Drift shows up as a number in the stats
(`malformed_lines`, `unknown_types`, `skipped_blocks`) instead of as a silent
hole -- a parser that quietly returns less is worse than one that says so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

#: Line types that carry conversation content. Everything else is session or UI
#: bookkeeping (attachment, queue-operation, file-history-delta, ...) and is
#: tallied by name rather than dropped, so a genuinely new type is visible.
CONTENT_TYPES = frozenset({"user", "assistant"})

#: What a parsed row can be. The first two are the ones detection cares about;
#: the rest are retained so that counts are honest and a later detector can
#: change its mind without a re-ingest.
ROLES = (
    "user",         # a human actually typed this
    "tool_result",  # tool output, delivered inside a user-typed line
    "assistant",
    "thinking",     # assistant reasoning; retained, but not shown to the user
    "tool_use",
    "image",        # placeholder: we record that one was here, not its bytes
)


@dataclass(frozen=True)
class Turn:
    """One content block, addressed by the line it came from."""

    line_no: int
    seq: int
    role: str
    text: str | None = None
    is_meta: bool = False
    is_sidechain: bool = False
    tool_name: str | None = None
    tool_use_id: str | None = None
    is_error: bool | None = None
    timestamp: str | None = None


@dataclass
class LineResult:
    turns: list[Turn] = field(default_factory=list)
    malformed: bool = False
    unknown_type: str | None = None
    skipped_blocks: int = 0
    meta: dict | None = None


@dataclass
class ParseStats:
    """What the parser could not make sense of. Drift, as a number."""

    lines: int = 0
    malformed_lines: int = 0
    skipped_blocks: int = 0
    unknown_types: dict[str, int] = field(default_factory=dict)

    def absorb(self, result: LineResult) -> None:
        self.lines += 1
        if result.malformed:
            self.malformed_lines += 1
        self.skipped_blocks += result.skipped_blocks
        if result.unknown_type:
            self.unknown_types[result.unknown_type] = (
                self.unknown_types.get(result.unknown_type, 0) + 1
            )


#: Session-level fields worth keeping off the first line that carries them.
_META_KEYS = ("sessionId", "entrypoint", "version", "cwd", "gitBranch", "userType")


def parse_line(raw: str, line_no: int) -> LineResult:
    """Parse one JSONL line into zero or more turns. Never raises."""
    result = LineResult()
    stripped = raw.strip()
    if not stripped:
        return result

    try:
        record = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        result.malformed = True
        return result

    if not isinstance(record, dict):
        result.malformed = True
        return result

    result.meta = {k: record[k] for k in _META_KEYS if isinstance(record.get(k), str)}

    line_type = record.get("type")
    if line_type not in CONTENT_TYPES:
        result.unknown_type = str(line_type)
        return result

    message = record.get("message")
    if not isinstance(message, dict):
        result.skipped_blocks += 1
        return result

    # `isMeta` marks injected skill text and system reminders -- text that
    # arrives in a user line but that no human typed. Detection must be able to
    # exclude it, so it is a column rather than a filter applied at ingest.
    common = {
        "line_no": line_no,
        "is_meta": bool(record.get("isMeta")),
        "is_sidechain": bool(record.get("isSidechain")),
        "timestamp": record.get("timestamp") if isinstance(record.get("timestamp"), str) else None,
    }
    content = message.get("content")

    # A plain string is always an ordinary human turn.
    if isinstance(content, str):
        if content.strip():
            result.turns.append(Turn(seq=0, role=line_type, text=content, **common))
        return result

    if not isinstance(content, list):
        result.skipped_blocks += 1
        return result

    texts: list[str] = []
    seq = 0
    for block in content:
        if not isinstance(block, dict):
            result.skipped_blocks += 1
            continue
        block_type = block.get("type")

        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                texts.append(text)
            else:
                result.skipped_blocks += 1

        elif block_type == "tool_result":
            seq += 1
            result.turns.append(
                Turn(
                    seq=seq,
                    role="tool_result",
                    text=_flatten(block.get("content")),
                    tool_use_id=_as_str(block.get("tool_use_id")),
                    is_error=bool(block.get("is_error")),
                    **common,
                )
            )

        elif block_type == "tool_use":
            seq += 1
            result.turns.append(
                Turn(
                    seq=seq,
                    role="tool_use",
                    text=_flatten(block.get("input")),
                    tool_name=_as_str(block.get("name")),
                    tool_use_id=_as_str(block.get("id")),
                    **common,
                )
            )

        elif block_type == "thinking":
            # Retained and marked, but this is reasoning the user never saw. A
            # concept that appears only here was arguably never encountered --
            # that call belongs to the detector, so keep the row and let it
            # decide rather than deciding by discarding.
            seq += 1
            result.turns.append(
                Turn(
                    seq=seq,
                    role="thinking",
                    text=_as_str(block.get("thinking")) or _as_str(block.get("text")),
                    **common,
                )
            )

        elif block_type == "image":
            seq += 1
            result.turns.append(Turn(seq=seq, role="image", text=None, **common))

        else:
            result.skipped_blocks += 1

    if texts:
        joined = "\n".join(texts)
        if joined.strip():
            # seq 0 keeps the human's own words first on the line, ahead of any
            # tool traffic that happened to be delivered alongside them.
            result.turns.append(Turn(seq=0, role=line_type, text=joined, **common))

    return result


def parse_lines(lines, start_line_no: int = 1):
    """Parse an iterable of raw lines, yielding (turns, stats) at the end."""
    stats = ParseStats()
    turns: list[Turn] = []
    meta: dict = {}
    for offset, raw in enumerate(lines):
        result = parse_line(raw, start_line_no + offset)
        stats.absorb(result)
        turns.extend(result.turns)
        if result.meta:
            for key, value in result.meta.items():
                meta.setdefault(key, value)
    return turns, stats, meta


def _as_str(value) -> str | None:
    return value if isinstance(value, str) else None


def _flatten(value) -> str | None:
    """Tool payloads are string, block list, or arbitrary JSON. Keep them all as text."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return "\n".join(parts)
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return None
