"""The data dictionary written into every export as README.md.

A Python string rather than a data file, so the frozen core needs no extra
`datas` entry to carry it (see packaging/unrot-core.spec).
"""

README = """\
# unrot export

A snapshot of one person's unrot log, written for analysis. Nothing in it was
uploaded anywhere; it was written to this folder on their machine.

## What unrot is

unrot reads the person's Claude Code sessions and flags terms they went along
with without understanding ("gaps"). The person confirms or dismisses each
flag. Four model-backed agents do the work:

| Agent | Job | Its ground truth |
|---|---|---|
| Detector | find terms the person accepted without understanding | the person's verdict on each flag (`flags.jsonl` `verdict`) |
| Resolver | file a term as a new concept, an existing one, or an alias | the person's corrections, and merges (`resolutions.jsonl`) |
| Grader | grade the person's explanation of a concept (SOLO level) | none directly |
| Material writer | write learning material for a concept | whether it was delivered, and refusals (`materials.jsonl`) |

## Files

Every row in every `.jsonl` file has `fixture`. `true` means development seed
data: exclude it from any finding.

Timestamps are ISO-8601 UTC. Costs are USD. `null` means unknown, never zero.

### manifest.json

When it was exported, the unrot version, the date range covered, whether text
was included, row counts per file, and the model configuration in effect at
export time (model, endpoint host, whether it was local). Never the API key.

### events.jsonl: the source of truth

The raw append-only event log, one event per line, in order. Every other file
is derived from it. `actor` is `user` (the person's own judgments, which are ground
truth) or `system` (machine output). `provenance` records which versioned
component produced a system event. Unless `include_text` is set, text fields are
removed, and the row lists them in `withheld`.

### flags.jsonl: detector flags, as the app shows them now

One row per detected encounter. Joins: `encounter_id` to `detections.jsonl`,
`concept_id` to `resolutions.jsonl`.

- `term`: what the detector flagged. `concept`: the concept it was filed under.
- `signal`: `accepted` (the person carried on without asking) or `unclear`.
  `importance`: `central` or `supporting`. `rank`: 1 = the detector's top pick.
  All three are `null` for flags made before detector runs were recorded.
- `detector_version`: `detector/VERSION+MODEL+PROMPT`. `model` is the MODEL part.
- `verdict`: `confirmed` (a real gap), `dismissed` (not a gap), or `null` (not yet
  judged). `seconds_to_verdict`: time from flag to verdict.

A regeneration (re-running the detector over old sessions) replaces unjudged
flags, so this file only holds the latest run's flags. For history, use
`detections.jsonl`.

### detections.jsonl: every detector run, in full

One row per candidate per run, including candidates the per-session budget
suppressed (`emitted: false`). Runs are never deleted, so this file is where to
compare models and versions, and to measure stability across repeated runs of
one session. `run_id` groups the rows of one run. `encounter_id` and `verdict`
are set only when `emitted` is true. The id comes from where the flag sits,
so an older run's flag still joins to a verdict given after a later run.

### sessions.jsonl: one row per analysed session

- `candidates_emitted` / `clean`: from the latest analysis.
- `runs`: how many detector runs are recorded. `latest_run_*`: windows examined,
  model calls made, and candidates found and emitted in the most recent run.
- `model_calls`, `failed_calls`, `cost_usd`, `model_ms`: summed over all
  detection and resolution calls for the session, across every run.
- `repo`: the name of the folder the session ran in, never the full path.
  `human_turns`: turns the person typed. Both `null` if capture data was absent.

### resolutions.jsonl: one row per resolver judgment

`decision` is `new`, `existing` or `alias`. `decided_without_model: true` means
an exact string match settled it and no model was asked. Leave those out when
judging a model. `corrected`: the person later said this judgment was wrong.
`merged_later`: the concept was later merged into another one.

### grades.jsonl: one row per explanation

`graded: false` means the grader has not run on it (yet). `level` is `isolated`,
`listed` or `causal`. `probabilities` and `confidence` come only from a
classifier grader. `answer_chars` is the length of the answer. The text itself
(`answer`, `question`, `grader_reasoning`) is present only with `include_text`.

### calls.jsonl: one row per model call

`purpose`: `detection`, `resolution`, `grading` or `material`. `ok: false` rows
are billed failures. `finish_reason: length` means the model ran out of room.
`cost`: `null` means unpriced (not free), and `local: true` means no bill.
`duration_ms`: wall-clock time, recorded from this version onward. Tokens:
`prompt_tokens`, `completion_tokens`, `reasoning_tokens`. `session_id` or
`concept_id` says what the call was about.

### materials.jsonl: one row per attempt to write material

`outcome`: `generated` or `refused`. A refusal's `reason` is `would_recurse`
(asked about a concept the person never met) or `not_grounded` (nothing
reliable to cite). `delivered`: the person received it.

## Questions worth asking first

- Detector precision by model and version: confirmed / (confirmed + dismissed),
  from `flags.jsonl`, ignoring `verdict: null`.
- Flags per session against the target of one or two, and the share of clean
  sessions (`sessions.jsonl`).
- Stability: for sessions with `runs` > 1, how much the emitted terms agree
  between runs (`detections.jsonl`, grouped by `run_id`).
- Resolver error rate: `corrected` or `merged_later` per model-made judgment.
- Grader: the distribution of levels, and how often `graded` is false.
- Cost and reliability: cost per confirmed gap. Failure rate and
  `finish_reason: length` rate by purpose and model. Latency distribution.

## Caveats

- Samples are small, often a handful of sessions. Say how many rows a rate
  comes from, and don't read much into anything under about 5.
- Some things are not in the log at all: whether flags are making the person
  lean on unrot instead of asking questions, and whether the list feels like a
  record of failures. Don't infer them.
- Before `detector_ran` was recorded, only emitted flags survive, and only the
  latest regeneration's. `detections.jsonl` starts when that recording began.
"""
