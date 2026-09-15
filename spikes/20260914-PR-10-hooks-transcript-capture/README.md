# Spike: Claude Code hooks for capturing full transcripts

**Ticket:** [PR-10](https://linear.app/freddie-cassidy/issue/PR-10/spike-explore-claude-code-hooks-for-capturing-full-transcripts)
**Date:** 2026-09-14

## Goal

Figure out whether/how Claude Code's hooks system can be used to capture a full session transcript, and how those hooks are installed.

## TL;DR

- Hooks don't carry the full conversation inline — they carry a `transcript_path` pointer to a JSONL file Claude Code already writes to disk for every session, at `~/.claude/projects/<project-slug>/<session-id>.jsonl`. **Capturing "the full transcript" is really just: pick a hook that fires at a useful moment, then copy/read that file.**
- That JSONL file already contains everything: user turns, assistant turns (including `thinking` blocks), `tool_use` and `tool_result` blocks, and session metadata — verified empirically against this very session's own transcript (see below), not just from docs.
- Best hook for "capture when the session is done" looked like `SessionEnd` from the docs alone (`Stop` fires per-turn and the docs warn the transcript file lags the in-memory conversation). But real prior art (entire.io/cli, see below) checkpoints on `Stop` instead, precisely *because* it fires every turn. A live test (see "Live test" below) installed real hooks and triggered them with an actual headless session: all six fired correctly, and for a simple single-turn case the transcript was already current (contained the final assistant message) by the time `Stop` fired — only trailing metadata arrived after.
- Hooks are configured in a `settings.json` (`~/.claude/settings.json` user-level, `.claude/settings.json` project-level/committable, or `.claude/settings.local.json` project-level/gitignored), under a `"hooks"` key keyed by event name.
- The transcript format itself is explicitly called out in the docs as **internal/unstable and not meant to be parsed directly** — it changes between Claude Code versions without notice. This spike takes a "copy the file as evidence" approach (no parsing) for that reason; PR-9's spike (`spikes/20260914-PR-9-jsonl-transcript-parser/`) is the one that actually parses it defensively.

## What was verified vs. assumed

**Verified empirically**, against this session's own live transcript at
`~/.claude/projects/-Users-freddiecassidy-Documents-GitHub-unrot-your-brain/<session-id>.jsonl`:
- The file exists and grows in real time as the session progresses (checked `wc -l` / mtime mid-session).
- Top-level JSONL line `type`s seen in one real session included `user`, `assistant`, `attachment`, `system`, `file-history-snapshot`, plus several Claude-Code-internal bookkeeping types (`last-prompt`, `atis-latch`, `bridge-session`, `queue-operation`, `custom-title`) that aren't documented and are presumably implementation detail.
- Within `user`/`assistant` message content arrays, block `type`s seen included `text`, `thinking`, `tool_use`, and `tool_result` — i.e. the file does capture tool calls and their results, not just chat text.
- `capture_transcript.sh` in this folder, invoked with a synthetic hook-shaped JSON payload (`{"session_id": ..., "transcript_path": ...}`) on stdin, successfully copies the live transcript file out. This is the smallest possible proof that "read `transcript_path` from the hook payload, then read that file" works mechanically.
- **A full live test** (see "Live test" section below): real hooks installed via `install_hooks.py` into a throwaway repo's `.claude/settings.local.json`, triggered by an actual headless `claude -p` run. All six installed hooks (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SessionEnd`) fired with real payloads, tool-scoped matchers (`Bash`) behaved as documented, and the transcript was confirmed to contain the literal output of the tool call Claude ran. The `Stop`-time transcript-lag question was measured directly, not just assumed from docs.

**From official docs only, not independently re-verified**:
- The full list of hook event names (over 30 — see below) and the settings.json schema, beyond the ~9 fields the live test actually exercised.
- Subagent transcript file location (resolved via entire.io/cli's source instead — see "Prior art" — but not independently re-verified against a live subagent run; the live test used a plain `Bash` call, not an `Agent` spawn).
- `transcript_path` lag under harder conditions (multi-turn, concurrent tool calls, a subagent in flight) — the live test only measured a single simple turn.

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
- `install_hooks.py` — the hook-install mechanism extracted/generalized from entireio/cli's `InstallHooks`, merges a set of observation hooks into a target repo's `.claude/settings.local.json`, idempotently. See "Live test" below.
- `log_hook.sh` — generic hook logger installed by `install_hooks.py`; summarizes each hook's payload and transcript-file state into `hook_events.log`.
- `hook_events.log` — the actual, real log from the live test below (trimmed of nothing — it's already small and contains no session content beyond field names/counts).

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

## Live test: installing and actually triggering the hooks

The gap above ("only proved the script works given a synthetic payload") is now closed. `install_hooks.py` and `log_hook.sh` in this folder extract the hook-install *mechanism* from entireio/cli's `InstallHooks`/`installHookEntries` (`cmd/entire/cli/agent/claudecode/hooks.go`) — generalized to write generic observation hooks instead of `entire hooks claude-code ...` commands — and were run for real:

1. `install_hooks.py <target-repo> <log_hook.sh path>` wrote a `.claude/settings.local.json` into a throwaway scratch directory, registering `SessionStart`, `UserPromptSubmit`, `Stop`, `SessionEnd`, `SubagentStop` (simple, unconditional hooks) and `PreToolUse`/`PostToolUse` scoped to the `Bash` tool matcher — mirroring entire.io/cli's simple-hook vs. tool-scoped-hook structure, just with a `Bash` matcher instead of their `Agent`/`TaskCreate|TaskUpdate` ones (easier to trigger reliably in a scripted headless run without forcing a real subagent spawn).
2. Ran a real headless session against that directory: `claude -p "Run 'echo hook-test-marker' using the Bash tool, then reply with exactly: DONE"`.
3. **All six installed hooks fired**, in the expected order (`SessionStart` → `UserPromptSubmit` → `PreToolUse` → `PostToolUse` → `Stop` → `SessionEnd`), each with a real JSON payload on stdin. A trimmed copy of that log is checked in at [`hook_events.log`](hook_events.log).
4. Confirmed the `Bash` tool matcher works exactly like entire.io/cli's `Agent`/`TaskCreate|TaskUpdate` tool-scoped matchers claim — `PreToolUse`/`PostToolUse` fired only for the `Bash` call, with `tool_name`, `tool_use_id`, and `tool_input` present as documented.
5. Confirmed the transcript really does contain the actual tool output: reading the real `transcript_path` from the `Stop` payload and grepping its `tool_result` content returned `'hook-test-marker'` — the literal stdout of the Bash command Claude ran, not a paraphrase.
6. **Measured the `Stop` transcript-lag question directly** (previously only assumed from docs): the transcript had 27 lines at the moment `Stop` fired; by the end of the run it had 29. Diffing those two lines showed they were pure bookkeeping (`system`, `last-prompt` entries) — the actual final assistant message (`"DONE"`) was **already present** in the transcript at line 27, i.e. by the time `Stop` fired. So for this single-turn, no-subagent case, the "transcript may lag" warning in the docs did not manifest as missing conversational content — only trailing metadata arrived after. This doesn't disprove lag as a real risk for more complex/concurrent sessions (entire.io/cli's line-offset workaround still seems like reasonable defensive engineering), but it's one data point that the common case is fine.
7. One incidental finding: the classifier that guards `--dangerously-skip-permissions` blocked that flag outright ("Create Unsafe Agents"). Its own denial message pointed at the correct alternative — adding a scoped `permissions.allow` rule (`Bash(echo:*)`) to `settings.local.json` instead — which worked. Worth remembering for any future scripted/headless testing: don't reach for the skip-permissions flag, use a scoped permission rule.
8. Test repo and the real `~/.claude/projects/...` session directory it created were both deleted immediately after the run — nothing besides the trimmed log and the install/logger scripts themselves was kept.

## Open gaps

- Did not verify `transcript_path` lag under harder conditions — a multi-turn session, concurrent tool calls, or a subagent in flight — only a single simple turn (see point 6 above). entire.io/cli's defensive line-offset tracking still looks like the safer default.
- ~~Did not verify how/whether subagent turns are segregated into a separate transcript file vs. inlined into the parent's.~~ Resolved via entire.io/cli's source: subagent transcripts are separate files at `<transcript_dir>/<session_id>/subagents/agent-<agent_id>.jsonl` — see "Prior art" above. Still not independently re-verified against a live subagent run (the live test above used a plain `Bash` tool call, not an `Agent` subagent spawn, specifically to avoid needing to force that).
- Did not check behavior for headless/`-p` mode sessions beyond what the live test covered — token-usage/session-crons/background-task fields showed up in the `Stop` payload that weren't documented in the official hooks reference, suggesting the payload shape may be richer/different in some modes than the docs describe; not fully catalogued.
- The internal JSONL schema (block types, top-level `type` values) was only sampled from a couple of sessions; the docs explicitly warn it can change between Claude Code versions, so any real implementation should treat this defensively (see PR-9's spike for that).

## Sources

- https://code.claude.com/docs/en/hooks.md
- https://code.claude.com/docs/en/hooks-guide.md
- https://github.com/entireio/cli — specifically `docs/architecture/claude-hooks-integration.md` and `cmd/entire/cli/agent/claudecode/hooks.go`, read locally from the repo checked out at `/Users/freddiecassidy/Documents/GitHub/cli`
