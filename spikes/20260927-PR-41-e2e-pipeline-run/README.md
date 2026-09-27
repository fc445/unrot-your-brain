# e2e pipeline run: every stage, on real sessions

**Ticket:** [PR-41 — Turn the e2e harness into a repeatable eval, with the 2026-09-27 run as its baseline](https://linear.app/freddie-cassidy/issue/PR-41). The spike ran before the ticket existed; the ticket is where it goes next. Bugs it found: PR-37, PR-38, PR-39, PR-40 (fixed in #20–#23), follow-ups PR-42 to PR-48.
**Date:** 2026-09-27

## Goal

See how each stage of the pipeline performs on real data, not stubs, and collect ideas for regression tests. The stages are capture → windows/chunks → detect → rank → triage → resolve → record → material → grade. Before this, each stage had offline tests with stubbed models. Nothing had run the whole chain with a live model over a realistic set of sessions and looked at what each stage actually did.

## How to run

From the repo root, with `OPENROUTER_API_KEY` set in `.env`:

```bash
uv run python spikes/20260927-PR-41-e2e-pipeline-run/e2e_run.py --plan-only          # capture + plan, no model calls
uv run python spikes/20260927-PR-41-e2e-pipeline-run/e2e_run.py --out spikes/20260927-PR-41-e2e-pipeline-run/out/run1 --exclude <id of the session you're in>
uv run python spikes/20260927-PR-41-e2e-pipeline-run/build_report.py spikes/20260927-PR-41-e2e-pipeline-run/out/run1
open spikes/20260927-PR-41-e2e-pipeline-run/out/run1/report.html
```

A full run took about 37 minutes: 19 of those were the main pass, and the rest were repeats and probes. The whole thing cost $0.12. `--skip pipeline|repeats|resolver|material|grader`, `--limit`, `--repeats` and `--material` trim it down.

**`out/` is gitignored on purpose.** The results and the report quote detector paraphrases, transcript-derived material and code paths from your own sessions, and raw material never leaves the machine (S4).

## What's here

| File | What it is |
|---|---|
| `e2e_run.py` | Ingests every transcript in `~/.claude/projects`, plus the PR-33 synthetic labelled pair, into a throwaway `$UNROT_HOME`. It then runs the real `pipeline.run_session` graph, 6 sessions at once like the app, with every model wrapped to record input size, output, time and errors next to what the `Meter` billed. After the main pass it runs five probes: detection repeats, a seeded triage map, resolver near-duplicates, material, and a grader golden set. It reads `~/.unrot` read-only, to compare with the historic log. |
| `build_report.py` | `results.json` → a self-contained `report.html`: inline SVG charts, hover tooltips, no network. |
| `findings.json` | A hand-written reading of run1: the report's "What stood out" and "Regression tests" sections. |

## Findings (run1: 36 real sessions + 2 synthetic, `inclusionai/ling-3.0-flash`, effort low)

### Verified

1. **Concurrency bug: `database is locked`.** 4 of 36 sessions failed in `_record`. `triage()` appends `familiarity_judged` without committing, so `_triage` releases `FILING` while holding an open write transaction, and the next session that files deadlocks against it until SQLite's timeout. **Reproduced offline** with a stub judge (`conn.in_transaction` is True after `triage()`, and a second connection gets `database is locked`). It is latent on the real Mac only because the real map (3 known, 0 confirmed) is below `MIN_EACH`, so triage appends nothing yet.
2. **The resolver shortlist loses the right answer on a realistic graph.** `idempotent` was filed as a new concept beside `idempotency`. `_overlap` scores morphological variants, abbreviations and spelling variants at 0, so the shortlist falls back to the 12 most-encountered concepts. **Checked offline** on a 42-concept graph (the real store's 34 plus 8): `idempotent`, `UDS` and `notarisation` all lose their target. The live near-duplicate probes went 22/22, but only because their graph had 8 concepts, fewer than `SHORTLIST`. The model decides well; what it gets shown is the problem.
3. **Detection is unstable.** Mean pairwise Jaccard of emitted terms over 4 runs of the same session: 0.11–0.50 on four real sessions. On the synthetic labelled session, the main pass found outbox (✓) and idempotent (✗, labelled as known), while three repeats found nothing (each proposed only `at-least-once` as questioned, which was correctly dropped). `dual-write` was never found in 4 runs. `sess-clean` was empty in all 4 runs.
4. **Detection latency is mostly reasoning.** p50 129s and p90 245s per call. It averaged about 4k reasoning tokens at effort `low`, and 16 of 76 calls (21%) hit the 8,000-token ceiling, all of them rescued by the forced retry. Latency tracks prompt size only loosely (r≈0.6). Cost is negligible: $0.057 for 36 sessions.
5. **Project-local labels get through**, despite the prompt's rule: `provenance gate`, `format 1`, `promotion PR`, `comprehension check`, `the funnel`, `forced retry`, `frozen sidecar` and `source-link model`, about 8 of 39 proposals. The resolver then renamed `format 1` to an invented canonical name.
6. **Triage recalibrates as the map's glosses change.** The fingerprint includes each concept's latest paraphrase, so this run calibrated 3 times: 72 Jev calls for 22 judgments. The cut moved from `unreliable` to 0.31 partway through, so a verdict depends on session order. `idempotent` at p=0.92 passed under the unreliable cut, and `remote code execution` at p=0.32 was held back under the 0.31 cut.
7. **Duplicate transcripts are detected twice.** `e1e8432a` and `f9aa8fa8` share an identical 38k-char chunk.
8. **Material 4/4 written and cited only verified sources, and the grader went 24/24 (Jev and LLM).** Both look healthy, but the grader golden set is easy: every answer is written clearly to shape.

### Not verified / caveats

- **One run.** Every rate here has a small n. Stability comes from 4 runs × 6 sessions.
- **Latency may be inflated by the run's own concurrency:** up to 24 detection calls in flight. The real log (`~/.unrot`, 52 calls) shows p50 79s, against 129s here. Same shape, lower level.
- **The triage map is a synthetic persona.** It has 8 known and 8 confirmed-unknown concepts. Only the 3 known concepts from real dismissals are real; the rest are assumptions. Triage behaviour on a real map is untested.
- **The resolver probes' expected answers and the grader golden answers are my own judgment**, written for this spike. Argue with them before they become fixtures.
- **Labels**: only the PR-33 synthetic pair is labelled, so precision and recall on real sessions are unknown. Real PR-27 labels are still the gap.
- Findings 1, 2, 5 (the invented name) and 6 became PR-37 to PR-40 and are fixed in separate PRs (#20–#23); nothing in this folder changes core code.

## Regression tests this suggests

The full list, with the evidence and the proposed shape of each test, is at the end of the report (and in `findings.json`). In short:

- **Offline, for `tests/`:** triage leaves no open transaction, and two concurrent `run_session`s both finish; the shortlist keeps the target on a graph of more than 12 concepts; the resolver doesn't invent canonical names; the triage cut is stable under paraphrase churn; the forced retry rescues a length cutoff; identical transcript content is planned once.
- **Eval, for the PR-33 bench:** detection recall and precision on labelled sessions; stability (Jaccard over repeats); no project-local labels; a clean session stays clean; the grader on hard listed/causal cases. Record today's numbers as the baseline before choosing thresholds.
