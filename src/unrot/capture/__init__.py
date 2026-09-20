"""Capture: Claude Code transcripts into unrot's own local, never-synced store."""

from .ingest import (
    IngestResult,
    connect,
    human_turns,
    ingest_all,
    ingest_file,
    resolve_pointer,
)
from .parser import ROLES, ParseStats, Turn, parse_line, parse_lines

__all__ = [
    "IngestResult",
    "ParseStats",
    "ROLES",
    "Turn",
    "connect",
    "human_turns",
    "ingest_all",
    "ingest_file",
    "parse_line",
    "parse_lines",
    "resolve_pointer",
]
