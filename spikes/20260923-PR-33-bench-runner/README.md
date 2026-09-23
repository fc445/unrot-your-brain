# PR-33 spike: benchmark runner and scorer

**Ticket:** [PR-33 — Benchmark page: show how the models perform, from real use and on a fixed labelled set](https://linear.app/freddie-cassidy/issue/PR-33)
**Date:** 2026-09-23

## Goal

PR-33 wants `python -m unrot.bench run` (detector over a hand-labelled set, model × effort × repeats, never touching `~/.unrot`, asks before spending) plus a static page that scores it. Both its inputs are unfinished: PR-27 (the labels, a human job) and PR-32 (the export format). This spike checks whether the runner can be built on today's code, and what PR-27 and PR-32 need to decide first.

## How to run

```
uv sync
uv run python spikes/20260923-PR-33-bench-runner/bench_spike.py run --yes --out spikes/20260923-PR-33-bench-runner/out/sample
uv run python spikes/20260923-PR-33-bench-runner/bench_spike.py score spikes/20260923-PR-33-bench-runner/out/sample
```

Drop `--yes` to get the "spend it? [y/N]" prompt. `--models`, `--effort` and `--repeats` work as in the ticket. Output from the committed run: `sample_output.txt` and `out/sample/`.

## What's here

| File | What it is |
|---|---|
| `labelled/*.jsonl` | Two **synthetic** transcripts: one messy (outbox, dual-write, idempotency, at-least-once) and one clean |
| `labelled/labels.json` | The proposed label format (see below). **Synthetic, not real PR-27 labels** |
| `bench_spike.py run` | Ingests the set into a temp `$UNROT_HOME`, estimates a cost ceiling, runs `detect()` for each model × effort × repeat, and writes `manifest.json`, `labels.json`, `runs.jsonl` and `calls.jsonl` |
| `bench_spike.py score` | Reads **only** that folder. Prints the headline table and a side-by-side view for one session |

## Findings

### Verified (offline, with stub models)

1. **The runner needs no changes to core code.** `detect()` takes a capture-DB connection and a `propose` callable, and never imports the store. Ingesting the labelled `.jsonl` into a temp `$UNROT_HOME` gives a disposable raw DB. The script snapshots `~/.unrot` before and after and asserts nothing changed. (Here it was absent both times, so this is weak evidence. It needs re-checking on a Mac that has a real store.)
2. **A cost ceiling can be worked out offline before spending.** The number of calls is known in advance (`chunk_windows` count). Prompt tokens can be estimated (rendered prompt chars ÷ 4), and `max_tokens` caps completion tokens. Together these give an honest upper bound. The ticket suggests PR-31's `spend.estimate()` instead, but that averages over the **store's** log. It needs the real store open, and it knows nothing about a model you haven't used yet. **Recommendation: use the ceiling, not `spend.estimate()`.**
3. **A failed run is a result, not a crash.** `detect()` raises if any chunk's call fails, e.g. on a length cutoff. The runner catches this and records `ok: false` plus the error, and the `Meter` still records the billed call.
4. **Every headline metric can be recomputed from the output files alone** (a PR-33 acceptance item): precision, recall, flags per session, stability (mean pairwise Jaccard of the flagged terms across repeats), failure rate, p50/max time, and $ per correct flag. Each is shown with its n, and marked `~` when n < 5.
5. **The side-by-side view makes disagreement obvious.** See `sample_output.txt`: steady vs flaky, with ✓ hit / ✗ labelled not-gap / ? unlabelled / · missed.
6. **`Meter.about(**tags)` passes arbitrary tags through to the call payload.** So `run_id`, `effort` and `duration_ms` reach `calls.jsonl` without changing `spend.py`.

### Not verified

- **Real models.** There's no API key, and `openrouter.ai` is blocked by this container's network policy. `live_proposer()` is written but has never run.
- **Reasoning effort / max_tokens plumbing.** `ModelConfig` has neither. The installed `ChatOpenAI` accepts `max_tokens`, `reasoning_effort`, `reasoning` and `extra_body`. The spike passes OpenRouter's `extra_body={"reasoning": {"effort": ...}}`. Whether ling-3.0-flash honours it is untested.
- **Pricing lookup** for non-stub models (OpenRouter `/api/v1/models`). The same network block applies.

## Decisions needed before building for real

1. **Label format (for PR-27).** Proposed: `labels.json` next to the transcripts, one entry per session with `gaps[]` (`term`, `aliases`, `assistant_line`, `kind`) and `not_gaps[]` (`term`, `aliases`, `why`: knew_it / asked). `not_gaps` is what makes a ✗ (confidently wrong) different from a ? (the labeller never considered it). Worth agreeing before labelling starts, so the labels don't need redoing.
2. **Matching flags to labels. This is the big open question.** The spike matches on normalised term or alias only. Real models will say "transactional outbox pattern" where the label says "outbox". Options: (a) aliases only, strict and predictable; (b) aliases plus the same `assistant_line`; (c) an LLM judge, which is flexible but adds its own noise and cost. Recommendation: start with (b), and show every `?` on the page so a human can add aliases.
3. **Do failed runs count as misses in recall?** The spike leaves them out of recall and reports them separately under `fail`. Counting them would let reliability silently drag recall down.
4. **Clean sessions inflate stability.** Two empty flag sets score Jaccard 1.0. Consider reporting stability over messy sessions only, or both.
5. **Where the forced-retry path lives.** PR-32/33 talk about "attempt (first try / forced retry)", but there's no retry path on `main`. The runner needs it before it can report a forced-retry rate.
6. **Output format.** The spike writes `runs.jsonl` + `calls.jsonl` + `manifest.json`, shaped like the PR-32 bundle's `sessions.jsonl` / `calls.jsonl` / `manifest.json`. Once PR-32 fixes its field names, the bench should reuse them so one page reads both.
7. **Resolver benchmarking.** Not attempted. Resolver labels (new / existing / alias) only mean something against a pre-seeded concept graph, so the labelled set would need a store fixture as well as transcripts. Suggest leaving the resolver out of the first cut.

## Suggested build order

1. Agree the label format → PR-27 labels 2–3 real sessions.
2. Add `effort` / `max_tokens` to `ModelConfig`, and `duration_ms` / `attempt` to `model_called` (overlaps with PR-32's "small gaps").
3. Promote `bench_spike.py` into `src/unrot/bench/` (runner + scorer as a library, with tests against stubs, as in `tests/test_detector.py`).
4. Build the static page on the scorer's output, after PR-32's bundle format is settled.

## Aside

`tests/test_capture.py::test_an_unreadable_file_does_not_abort_the_sweep` fails in this container, likely because it runs as root and `chmod 000` doesn't block root. This wasn't investigated and is unrelated to this spike.
