# Spike: Claude Code hooks for capturing full transcripts

**Ticket:** [PR-10](https://linear.app/freddie-cassidy/issue/PR-10/spike-explore-claude-code-hooks-for-capturing-full-transcripts)
**Date:** 2026-09-14

## Goal

Figure out whether/how Claude Code's hooks system can be used to capture a full session transcript, and how those hooks are installed.

## TL;DR

- Hooks don't carry the full conversation inline — they carry a `transcript_path` pointer to a JSONL file Claude Code already writes to disk for every session, at `~/.claude/projects/<project-slug>/<session-id>.jsonl`. **Capturing "the full transcript" is really just: pick a hook that fires at a useful moment, then copy/read that file.**
- That JSONL file already contains everything: user turns, assistant turns (including `thinking` blocks), `tool_use` and `tool_result` blocks, and session metadata — verified empirically against this very session's own transcript (see below), not just from docs.
- Best hook for "capture when the session is done" looked like `SessionEnd` from the docs alone (`Stop` fires per-turn and the docs warn the transcript file lags the in-memory conversation). But real prior art (entire.io/cli, see below) checkpoints on `Stop` instead, precisely *because* it fires every turn, and works around the lag by tracking a transcript line-count offset between hook firings rather than trusting the file to be complete. Worth following that lead rather than defaulting to `SessionEnd`.
- Hooks are configured in a `settings.json` (`~/.claude/settings.json` user-level, `.claude/settings.json` project-level/committable, or `.claude/settings.local.json` project-level/gitignored), under a `"hooks"` key keyed by event name.
- The transcript format itself is explicitly called out in the docs as **internal/unstable and not meant to be parsed directly** — it changes between Claude Code versions without notice. This spike takes a "copy the file as evidence" approach (no parsing) for that reason; PR-9's spike (`spikes/20260914-PR-9-jsonl-transcript-parser/`) is the one that actually parses it defensively.

## What was verified vs. assumed

**Verified empirically**, against this session's own live transcript at
`~/.claude/projects/-Users-freddiecassidy-Documents-GitHub-unrot-your-brain/<session-id>.jsonl`:
- The file exists and grows in real time as the session progresses (checked `wc -l` / mtime mid-session).
- Top-level JSONL line `type`s seen in one real session included `user`, `assistant`, `attachment`, `system`, `file-history-snapshot`, plus several Claude-Code-internal bookkeeping types (`last-prompt`, `atis-latch`, `bridge-session`, `queue-operation`, `custom-title`) that aren't documented and are presumably implementation detail.
- Within `user`/`assistant` message content arrays, block `type`s seen included `text`, `thinking`, `tool_use`, and `tool_result` — i.e. the file does capture tool calls and their results, not just chat text.
- `capture_transcript.sh` in this folder, invoked with a synthetic hook-shaped JSON payload (`{"session_id": ..., "transcript_path": ...}`) on stdin, successfully copies the live transcript file out. This is the smallest possible proof that "read `transcript_path` from the hook payload, then read that file" works mechanically.

**From official docs only, not independently re-verified against a live hook firing** (`https://code.claude.com/docs/en/hooks.md`, `https://code.claude.com/docs/en/hooks-guide.md`):
- The full list of hook event names (over 30 — see below) and the settings.json schema.
- The exact set of fields on each event's JSON input.
- That `transcript_path` writes lag the in-memory conversation (i.e. the async/staleness claim) — plausible given async I/O, but not something this spike triggered and measured directly (e.g. by diffing file content immediately before/after a `Stop` hook fires).
- Subagent transcript behavior — the docs don't clearly say whether a subagent's turns land in the same transcript file as the parent session or elsewhere; this session's own transcript (which did include a subagent call, see the `Agent` tool use in this conversation) would be one place to check but wasn't diffed against subagent-specific output as part of this spike.

## Hook events relevant to transcript capture

Full list per docs (`hooks.md`), lifecycle order — only the capture-relevant ones are described here, the rest exist for permissions/UI/config-change use cases:

| Event | Fires | Useful for capture because |
|---|---|---|
| `SessionStart` | session begins/resumes | mark the start of a capture window |
| `UserPromptSubmit` | before each user prompt is processed | per-turn hook point if you want streaming capture instead of end-of-session |
| `PostToolUse` / `PostToolUseFailure` | after each tool call resolves | per-tool-call hook point |
| `SubagentStop` | a subagent finishes | per-subagent hook point; also exposes `last_assistant_message` |
| `Stop` | Claude finishes a turn | per-turn hook point; exposes `last_assistant_message` directly, so you don't even need to read the transcript file for "what did Claude just say" |
| `PreCompact` | before context compaction | last chance to capture pre-compaction state if that matters |
| `SessionEnd` | session terminates | **recommended point for "capture the whole thing"** — most likely to see a complete, flushed transcript |

## Hook configuration schema

```json
{
  "hooks": {
    "<EventName>": [
      {
        "matcher": "<optional filter, event-specific — e.g. tool name for PostToolUse>",
        "hooks": [
          { "type": "command", "command": "path/to/script.sh" }
        ]
      }
    ]
  }
}
```

Valid locations, in increasing precedence: managed/enterprise policy settings, `~/.claude/settings.json` (user), `.claude/settings.json` (project, committable), `.claude/settings.local.json` (project, gitignored — the fastest place to try this locally without affecting teammates). `type` can also be `http`, `mcp_tool`, `prompt`, or `agent` for non-shell-script hooks, but `command` is what this spike exercises.

See [`settings.example.json`](settings.example.json) for a working `SessionEnd` hook wired to [`capture_transcript.sh`](capture_transcript.sh).

## Hook input payload

Every hook gets JSON on stdin. Fields common to all events, per docs:

```json
{
  "session_id": "...",
  "transcript_path": "/Users/you/.claude/projects/<project-slug>/<session-id>.jsonl",
  "cwd": "...",
  "hook_event_name": "SessionEnd",
  "permission_mode": "default"
}
```

`SessionEnd` additionally carries a `reason` (`"clear" | "resume" | "logout" | "prompt_input_exit" | "other"`), matcher-filterable. `Stop`/`SubagentStop` additionally carry `last_assistant_message`.

## Artifacts in this folder

- `settings.example.json` — a `SessionEnd` hook config, referencing `$CLAUDE_PROJECT_DIR` so it's portable.
- `capture_transcript.sh` — the hook script: reads `session_id` and `transcript_path` from stdin JSON, copies the transcript into `output/<session_id>.jsonl`.
- `output/` — scratch dir the script creates and writes into (not checked in — it would just be a copy of a real session transcript, which could contain anything discussed in that session). The smoke test below created and then deleted one.

### How to run it

1. Copy the hook block from `settings.example.json` into `.claude/settings.local.json` (merge into the `"hooks"` object if one already exists).
2. Start or continue a Claude Code session in this repo, then end it (`/exit`, or close the terminal).
3. Check `spikes/20260914-PR-10-hooks-transcript-capture/output/<session-id>.jsonl` — it should be a copy of `~/.claude/projects/.../<session-id>.jsonl`.

This spike's own smoke test skipped steps 1–2 and instead piped a synthetic payload directly into the script (`echo '{"session_id":...,"transcript_path":...}' | ./capture_transcript.sh`) pointing at this session's real, live, in-progress transcript file — confirmed the file copies correctly and already contains full multi-block message content (text/thinking/tool_use/tool_result).

## Prior art: entire.io/cli

[entireio/cli](https://github.com/entireio/cli) (available locally as a second working directory in this session, at `/Users/freddiecassidy/Documents/GitHub/cli`) does close to exactly what PR-10 is scoping out, in production, across six agents (Claude Code, Codex, Copilot CLI, Cursor, Factory AI Droid, Gemini CLI, plus a Pi extension). It installs hooks into `.claude/settings.json` and uses them to build a full checkpoint/commit history of AI sessions outside the user's branch. This is real, shipped prior art worth mirroring rather than re-discovering from scratch — verified by reading its own architecture doc and source, not just its README:

- `docs/architecture/claude-hooks-integration.md` in that repo documents its six Claude Code hooks end to end: `SessionStart`, `UserPromptSubmit`, `Stop`, `PreToolUse[Agent]`, `PostToolUse[Agent]`, `PostToolUse[TaskCreate|TaskUpdate]`. (There's also a `session-end`/`subagent-stop` pair of hook *commands* defined in `cmd/entire/cli/agent/claudecode/hooks.go` that the architecture doc doesn't cover — the shipped six above are what's actually wired up.)
- **Confirms this spike's guess wrong in one place**: they checkpoint on `Stop`, not `SessionEnd` — and their own doc explains why implicitly: `Stop` fires after *every* turn, giving per-turn checkpoints, whereas `SessionEnd` would only give one shot at the end. They accept the "transcript may lag" risk (my assumption from the official docs above) by tracking a line-count offset (`CheckpointTranscriptStart` / `StepTranscriptStart`) between hook firings rather than trusting the file to be complete instantly.
- They read the transcript via the `transcript_path` the hook receives, same mechanism this spike used — confirming that's the correct/only way in.
- **Subagent transcripts are separate files**, not inlined in the parent — Claude Code writes them to `<transcript_dir>/<session_id>/subagents/agent-<agent_id>.jsonl` (with a `.meta.json` sidecar), which resolves one of this spike's open gaps below. Their code falls back to a legacy sibling path `<transcript_dir>/agent-<agent_id>.jsonl` for older Claude Code versions.
- **Tool matcher names matter and drift**: their code has a whole guard (`hooks.go`, `agent_hook_config_guard_test.go`) against a real historical bug where they targeted a `"Task"` tool matcher that never existed (the actual subagent-dispatch tool is `"Agent"`) and a `"TodoWrite"` matcher that stopped firing after Claude Code v2.1.142 replaced it with `TaskCreate`/`TaskUpdate`. Hooks with a matcher that doesn't match any real tool name install cleanly and silently never fire — worth remembering for PR-10's own hook config.
- They apply a `TranscriptSanitizer` (`cmd/entire/cli/agent/agent.go`) before persisting captured transcripts — i.e. even the "just copy the file" approach in production strips non-portable/sensitive state first rather than storing the raw JSONL verbatim.
- Full metadata (modified/new/deleted files, token usage, commit messages) is derived by *parsing* the transcript for `Write`/`Edit` tool uses and assistant token-usage fields, not just archiving it — confirms the raw JSONL really does carry enough structure to reconstruct "what changed and why," which is the premise this whole spike/PR-10 effort is testing.

This is a strong argument for a follow-up spike (or straight into implementation) that borrows entire.io/cli's hook set and matcher names directly rather than re-deriving them, and that checkpoints on `Stop` rather than `SessionEnd`.

## Open gaps

- Did not actually register the hook in `settings.json` and trigger it via a real `SessionEnd` event (i.e. didn't end a real Claude Code session to observe the hook fire) — only proved the script works given a hook-shaped payload. Doing that is the natural next step before relying on this for real.
- Did not verify the claimed async-lag behavior of `transcript_path` on `Stop` (i.e. didn't measure whether the file is missing the just-emitted turn at the moment a `Stop` hook fires).
- ~~Did not verify how/whether subagent turns are segregated into a separate transcript file vs. inlined into the parent's.~~ Resolved via entire.io/cli's source: subagent transcripts are separate files at `<transcript_dir>/<session_id>/subagents/agent-<agent_id>.jsonl` — see the "Prior art" section below. Not independently re-verified against a live subagent run in this repo.
- Did not check behavior for headless/`-p` mode sessions or the `CLAUDE_CODE_SKIP_PROMPT_HISTORY`-style suppression the docs research turned up — needs confirming against real headless runs before assuming capture works there too.
- The internal JSONL schema (block types, top-level `type` values) was only sampled from one session; the docs explicitly warn it can change between Claude Code versions, so any real implementation should treat this defensively (see PR-9's spike for that).

## Sources

- https://code.claude.com/docs/en/hooks.md
- https://code.claude.com/docs/en/hooks-guide.md
- https://github.com/entireio/cli — specifically `docs/architecture/claude-hooks-integration.md` and `cmd/entire/cli/agent/claudecode/hooks.go`, read locally from the repo checked out at `/Users/freddiecassidy/Documents/GitHub/cli`
