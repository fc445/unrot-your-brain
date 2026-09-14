#!/usr/bin/env bash
# SessionEnd hook: copy the session's JSONL transcript out of ~/.claude/projects/
# into this spike's output/ dir, for inspection.
#
# Claude Code invokes command hooks with the event payload as JSON on stdin.
# This script only needs session_id and transcript_path from that payload.
set -euo pipefail

payload="$(cat)"
transcript_path="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["transcript_path"])' <<<"$payload")"
session_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])' <<<"$payload")"

out_dir="$(dirname "$0")/output"
mkdir -p "$out_dir"
cp "$transcript_path" "$out_dir/${session_id}.jsonl"

echo "captured transcript for session $session_id -> $out_dir/${session_id}.jsonl" >&2
