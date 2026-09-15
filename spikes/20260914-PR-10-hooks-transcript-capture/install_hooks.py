#!/usr/bin/env python3
"""Install (or merge in) a set of observation hooks into a target repo's
.claude/settings.local.json.

This is the hook-install *mechanism* extracted from entireio/cli
(cmd/entire/cli/agent/claudecode/hooks.go: InstallHooks / installHookEntries),
generalized so this spike can point it at log_hook.sh instead of `entire hooks
claude-code ...`. Entire's version does more (force-reinstall/migration of
stale hook commands from older CLI versions, permissions-block edits) — this
keeps only the two structural ideas needed to test the mechanism itself:

  1. "Simple" hooks (SessionStart, UserPromptSubmit, Stop, SessionEnd,
     SubagentStop): one matcher entry with an empty matcher string, hooks
     apply unconditionally for that event.
  2. Tool-scoped hooks (PreToolUse/PostToolUse): a matcher entry keyed by
     tool name(s) so the hook only fires for matching tool calls. Entire uses
     "Agent" and "TaskCreate|TaskUpdate" as matchers; this script uses "Bash"
     so a plain `claude -p` run with a Bash tool call is enough to trigger it
     without having to force a real subagent spawn.

Both are idempotent (checks whether the exact command already exists under
that matcher before adding), same as entire's `hookCommandExists*` checks —
so re-running this against an already-configured repo is a no-op.

Usage: install_hooks.py <target_repo_dir> <log_hook_script_path>
"""
import json
import sys
from pathlib import Path

SIMPLE_EVENTS = ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "SubagentStop"]
TOOL_SCOPED_EVENTS = {"PreToolUse": "Bash", "PostToolUse": "Bash"}


def command_exists(matchers, matcher, command):
    for m in matchers:
        if m.get("matcher", "") == matcher:
            for h in m.get("hooks", []):
                if h.get("command") == command:
                    return True
    return False


def add_hook(matchers, matcher, command):
    for m in matchers:
        if m.get("matcher", "") == matcher:
            m.setdefault("hooks", []).append({"type": "command", "command": command})
            return matchers
    matchers.append({"matcher": matcher, "hooks": [{"type": "command", "command": command}]})
    return matchers


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    target_dir = Path(sys.argv[1])
    logger = Path(sys.argv[2]).resolve()

    settings_path = target_dir / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())

    hooks = settings.setdefault("hooks", {})
    added = 0

    for event in SIMPLE_EVENTS:
        matchers = hooks.setdefault(event, [])
        command = f'"{logger}" {event}'
        if not command_exists(matchers, "", command):
            add_hook(matchers, "", command)
            added += 1

    for event, matcher in TOOL_SCOPED_EVENTS.items():
        matchers = hooks.setdefault(event, [])
        command = f'"{logger}" {event}'
        if not command_exists(matchers, matcher, command):
            add_hook(matchers, matcher, command)
            added += 1

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"installed {added} hook(s) into {settings_path}")


if __name__ == "__main__":
    main()
