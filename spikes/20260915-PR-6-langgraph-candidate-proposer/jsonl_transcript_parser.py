#!/usr/bin/env python3
"""
Defensive parser for Claude Code session transcripts (from PR-9's spike,
spikes/20260914-PR-9-jsonl-transcript-parser/).

Reads a ~/.claude/projects/<project>/<session-id>.jsonl file and pulls out
user turns, assistant turns, tool calls, and tool results. The line schema
is internal to Claude Code and drifts between releases, so every step here
is written to degrade rather than raise: unknown line types are counted and
skipped, malformed JSON lines are counted and skipped, and missing/renamed
fields fall back to None instead of raising KeyError.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


KNOWN_TURN_TYPES = {"user", "assistant"}


@dataclass
class UserTurn:
    line_no: int
    text: str
    is_meta: bool


@dataclass
class AssistantTurn:
    line_no: int
    text: str


@dataclass
class ToolCall:
    line_no: int
    tool_use_id: Optional[str]
    name: Optional[str]
    input: Any


@dataclass
class ToolResult:
    line_no: int
    tool_use_id: Optional[str]
    is_error: bool
    content: Any


@dataclass
class ParseReport:
    total_lines: int = 0
    malformed_json_lines: int = 0
    unknown_types: dict = field(default_factory=dict)
    user_turns: list = field(default_factory=list)
    assistant_turns: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    tool_results: list = field(default_factory=list)
    skipped_blocks: int = 0


def _extract_text_from_content_blocks(blocks: list) -> str:
    texts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
    return "\n".join(texts)


def parse_user_record(record: dict, line_no: int, report: ParseReport) -> None:
    message = record.get("message")
    if not isinstance(message, dict):
        report.skipped_blocks += 1
        return

    content = message.get("content")

    if isinstance(content, str):
        report.user_turns.append(
            UserTurn(line_no=line_no, text=content, is_meta=bool(record.get("isMeta")))
        )
        return

    if not isinstance(content, list):
        report.skipped_blocks += 1
        return

    text_blocks = []
    for block in content:
        if not isinstance(block, dict):
            report.skipped_blocks += 1
            continue

        block_type = block.get("type")

        if block_type == "tool_result":
            report.tool_results.append(
                ToolResult(
                    line_no=line_no,
                    tool_use_id=block.get("tool_use_id"),
                    is_error=bool(block.get("is_error")),
                    content=block.get("content"),
                )
            )
        elif block_type == "text":
            text_blocks.append(block)
        else:
            report.skipped_blocks += 1

    if text_blocks:
        report.user_turns.append(
            UserTurn(
                line_no=line_no,
                text=_extract_text_from_content_blocks(text_blocks),
                is_meta=bool(record.get("isMeta")),
            )
        )


def parse_assistant_record(record: dict, line_no: int, report: ParseReport) -> None:
    message = record.get("message")
    if not isinstance(message, dict):
        report.skipped_blocks += 1
        return

    content = message.get("content")
    if not isinstance(content, list):
        report.skipped_blocks += 1
        return

    text_blocks = []
    for block in content:
        if not isinstance(block, dict):
            report.skipped_blocks += 1
            continue

        block_type = block.get("type")

        if block_type == "tool_use":
            report.tool_calls.append(
                ToolCall(
                    line_no=line_no,
                    tool_use_id=block.get("id"),
                    name=block.get("name"),
                    input=block.get("input"),
                )
            )
        elif block_type == "text":
            text_blocks.append(block)
        elif block_type == "thinking":
            pass
        else:
            report.skipped_blocks += 1

    if text_blocks:
        report.assistant_turns.append(
            AssistantTurn(line_no=line_no, text=_extract_text_from_content_blocks(text_blocks))
        )


def parse_file(path: Path) -> ParseReport:
    report = ParseReport()

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, raw_line in enumerate(f, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            report.total_lines += 1

            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                report.malformed_json_lines += 1
                continue

            if not isinstance(record, dict):
                report.malformed_json_lines += 1
                continue

            record_type = record.get("type")

            try:
                if record_type == "user":
                    parse_user_record(record, line_no, report)
                elif record_type == "assistant":
                    parse_assistant_record(record, line_no, report)
                else:
                    key = record_type if isinstance(record_type, str) else "<no type field>"
                    report.unknown_types[key] = report.unknown_types.get(key, 0) + 1
            except Exception as exc:  # noqa: BLE001 - defensive by design
                report.malformed_json_lines += 1
                print(f"  [warn] line {line_no}: failed to parse {record_type!r} record: {exc}", file=sys.stderr)

    return report


def find_latest_session() -> Optional[Path]:
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return None
    sessions = list(root.glob("*/*.jsonl"))
    if not sessions:
        return None
    return max(sessions, key=lambda p: p.stat().st_mtime)
