# PR-18 — seeing the store work

**Ticket:** [PR-18](https://linear.app/freddie-cassidy/issue/PR-18) — event log schema and the compile step
**Date:** 2026-09-20

## Goal

PR-18 shipped with 19 unit tests, and tests are good at proving a guarantee still
holds but bad at showing you what the thing *does*. This spike exists to answer
"how do I actually look at what we've built?" — a narrated run through the whole
event-log-to-state loop that leaves a real SQLite file behind to poke at.

It is a demo, not library code, and it cheats where cheating is harmless:
concept ids are hand-written rather than resolver output, and the "detector" and
"grader" are hardcoded. Everything it calls is the real store.

## How to run

```bash
uv run python spikes/20260920-PR-18-store-walkthrough/walkthrough.py
```

Writes `walkthrough.db` next to the script (gitignored, deleted and rebuilt on
each run). `--db PATH` puts it somewhere else. Afterwards:

```bash
sqlite3 -box spikes/20260920-PR-18-store-walkthrough/walkthrough.db \
  'SELECT canonical_name, state, encounter_count FROM compiled_concepts'
```

## What it shows

Nine scenes. The interesting ones:

| Scene | What it demonstrates |
|---|---|
| 1 | The log holds a paraphrase plus a pointer (`sess-1`, lines 40–58), never the transcript text. The S4 privacy boundary, visible. |
| 2 | Confirming an encounter does **not** clear the gap — it adds ground truth to it. Dismissing moves straight to `known`. |
| 3 | A merge keeps the old id resolvable and folds the old name into an alias, and stores the resolver's *reasoning* as a correctable event. |
| 4 | A concept named only in generated material compiles to `referenced`, not `gap`. This is the depth-1 guard working: adding nodes is fine, recursing is not. |
| 5–7 | `gap` → `exposed` (material delivered) → still `exposed` at an `isolated` grade → `known` only at `causal`. The listed→causal boundary is the only transition that closes a gap. |
| 8 | Full regeneration: delete every derived event, replay from a better detector, re-grade from the raw answers still in the log. The graph rebuilds identically; the user's confirmation and explanations are untouched. |
| 9 | What the store refuses — and the one thing it *cannot be asked* to do. |

Scene 9's first case is the one worth reading. A detector calling
`append(conn, "encounter_confirmed", ...)` does not raise — it writes a genuine
`actor='user'` row, because `actor` comes from the event's spec and there is no
parameter through which a caller could say otherwise. The guarantee is enforced
by the absence of an argument rather than by a check.

## Verified empirically

- All nine scenes run clean against the real store; state transitions are as the
  table above describes.
- Regeneration preserves `judgment`, `judged_at`, and explanation `raw_text`
  while `detector_version` advances 0.1.0 → 0.2.0.
- The four `EventValidationError` cases in scene 9 all fire.

## Finding: `uv run python` cannot import `unrot` on this machine

Worth writing down because it cost time and the obvious diagnosis is wrong.

`uv run pytest` works. `uv run python -c "import unrot"` fails with
`ModuleNotFoundError`. The tests only pass because `pyproject.toml` sets
`pythonpath = ["src"]` for pytest specifically — the editable install itself has
never resolved.

The cause is **not** a malformed `.pth` file. It is:

1. `uv` sets the macOS `UF_HIDDEN` flag on everything inside `.venv`, including
   `site-packages/_editable_impl_unrot.pth` (visible as `hidden` in `ls -lO`).
2. CPython 3.13+ `site.addpackage` explicitly skips `.pth` files with
   `UF_HIDDEN` set, silently and with no error.

So no `.pth` in the venv is processed at all — `_virtualenv.pth` is skipped too.
Confirmed by reading `site.py` on this machine and by:

```bash
chflags nohidden .venv/lib/python3.14/site-packages/*.pth   # import works
uv sync                                                      # flag comes back
```

`uv sync` re-applies the flag, so `chflags` is a fix that lasts until the next
sync. That is why this script puts `src` on `sys.path` itself rather than
trusting the install.

**Open:** the repo needs a real answer before anyone else clones it. Options are
a `PYTHONPATH=src` convention, a non-editable install, or an upstream fix
(this is a uv/CPython interaction, not a project bug). Not yet ticketed.

## Open / not covered

- Nothing here exercises the store under concurrent writers, or with a log large
  enough to make full-rebuild compile slow. Both are fine at v1 scale and both
  are guesses until measured.
- The pointer (`session_id`, `line_start`, `line_end`) is stored and survives the
  fold, but nothing yet *resolves* one back to transcript text — there is no
  capture layer until PR-19.
- The `derive_state` policy shown here is the one in `compile.py` and is still
  awaiting review; the scenes will need updating if it changes.
