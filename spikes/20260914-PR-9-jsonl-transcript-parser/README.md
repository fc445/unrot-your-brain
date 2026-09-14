# PR-9: Sketch JSONL transcript parser for Claude Code sessions

**Date:** 20260914
**Ticket:** [PR-9](https://linear.app) — "Sketch JSONL transcript parser for Claude Code sessions" (project: unrot-your-brain, label: `agent`)

**Goal:** a defensive parser for Claude Code session transcripts at `~/.claude/projects/<project>/<session-id>.jsonl`, extracting user turns, assistant turns, tool calls, and tool results — without crashing when the (undocumented, internal, versioned) line schema drifts.

## Files

- `jsonl_transcript_parser.py` — the spike script. Stdlib-only, read-only against `~/.claude`.
- `sample_output.txt` — a real run against this very session's own transcript (`--latest`), captured as proof of behavior.

## Run it

```
python3 jsonl_transcript_parser.py --latest
python3 jsonl_transcript_parser.py <path-to-session.jsonl>
python3 jsonl_transcript_parser.py --latest --show-samples 5
```

`--latest` picks the most recently modified `.jsonl` anywhere under `~/.claude/projects`.

## What was verified empirically

Checked against 14 real local session files (~7.2MB total), spanning Claude Code versions 2.1.234–2.1.266 and entrypoints `cli` / `claude-desktop` / `sdk-cli`. No VS Code or JetBrains extension sessions were available locally to test against — that gap is still open.

- One JSON object per line, keyed by a top-level `type`. Only `user` and `assistant` carry conversation content. Everything else observed (`attachment`, `permission-mode`, `mode`, `bridge-session`, `atis-latch`, `last-prompt`, `ai-title`, `custom-title`, `queue-operation`, `file-history-delta`, `file-history-snapshot`, `system`) is session/UI bookkeeping — safe to skip, but the parser tallies unknown types by name instead of silently dropping them, so a genuinely new type is visible in the summary rather than invisible.
- `assistant.message.content` is a block array: `text`, `thinking`, `tool_use` (`{id, name, input}`). `thinking` blocks are seen but deliberately not extracted by this spike.
- `user.message.content` is either a plain string (an ordinary human turn) **or** a block array mixing `text` and `tool_result` (`{tool_use_id, content, is_error}`) blocks. Tool *results* live inside `user`-typed lines, not their own line type — easy to miss if you assume symmetry with `tool_use`.
- Some `user`/`text` lines carry `isMeta: true` at the top level — these are injected skill/system-reminder text, not something the human typed. The spike flags `is_meta` on extracted user turns so a downstream consumer can filter them.

## Design of the defensive behavior

Every line is parsed inside a `try`/`except` and a malformed-JSON line is counted (`malformed_json_lines`) and skipped, not raised. Every content-block loop checks `isinstance` before indexing and falls back to a skip-counter (`skipped_blocks`) rather than raising `KeyError`/`TypeError` on a missing or renamed field. An unrecognized top-level `type` is tallied in `unknown_types` rather than ignored outright, so schema drift shows up as a number in the summary instead of a silent hole.

Verified against: a garbage (non-JSON) line, a line with a wrong-shaped `content` field, a wholly invented future `type`, and an empty (0-byte) file — none of these crash the script.

## Known gaps / next steps

- No VS Code or JetBrains extension session files were available locally, so the "must work identically across CLI, VS Code, JetBrains, Desktop" requirement in the ticket is only empirically confirmed for `cli` / `claude-desktop` / `sdk-cli` entrypoints so far. Get a sample from each remaining surface before calling the schema assumptions complete.
- `thinking` blocks and image content blocks are currently counted as skipped/ignored, not extracted — decide if the real parser needs them.
- This is a sketch/spike per the ticket, not the shipped parser: no packaging, no tests, no CLI ergonomics beyond what's needed to prove the schema out.
