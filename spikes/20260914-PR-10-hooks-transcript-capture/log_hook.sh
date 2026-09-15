#!/usr/bin/env bash
# Generic hook logger. Installed by install_hooks.sh under every hook event
# this spike cares about, so we can see, per event, what Claude Code actually
# sends on stdin and whether transcript_path is readable/current at that
# moment. Appends to hook_events.log next to this script; never overwrites.
set -euo pipefail

event="${1:-unknown}"
payload="$(cat)"
logfile="$(cd "$(dirname "$0")" && pwd)/hook_events.log"
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

{
  echo "=== $ts $event ==="
  echo "$payload" | python3 -c '
import json, sys
d = json.load(sys.stdin)
# tool_input can be large/noisy; summarize instead of dumping verbatim
keys = sorted(d.keys())
print("payload keys:", keys)
for k in ("session_id", "hook_event_name", "cwd", "permission_mode", "tool_name", "tool_use_id", "last_assistant_message"):
    if k in d:
        print(f"  {k}: {d[k]!r}")
if isinstance(d.get("tool_input"), dict):
    print("  tool_input keys:", sorted(d["tool_input"].keys()))
'
  tp="$(echo "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("transcript_path",""))')"
  if [ -n "$tp" ] && [ -f "$tp" ]; then
    echo "  transcript_path: $tp (exists, $(wc -l < "$tp" | tr -d ' ') lines, $(stat -f%z "$tp" 2>/dev/null || stat -c%s "$tp") bytes)"
  else
    echo "  transcript_path: $tp (missing or not yet created)"
  fi
} >> "$logfile"

# Always exit 0 / allow continuation — this hook only observes, never blocks.
exit 0
