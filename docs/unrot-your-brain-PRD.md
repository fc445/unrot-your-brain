# unrot-your-brain — PRD (v1)

## Problem Statement
Developers using Claude Code accept AI-generated output without understanding it — unfamiliar terms, patterns, and tradeoffs slide by because there's no friction forcing comprehension. Over time this erodes the skill and judgment that made them effective in the first place, and it isn't self-correcting: nothing today flags what got skipped.

## Product Shape
unrot-your-brain is a **silent integration** to Claude Code (and other agents later), not something the user interacts with *inside* Claude Code. Capture is passive and invisible — it reads local session transcripts and never injects, blocks, or responds within the agent's workflow.

All interaction happens in a **separate product surface**: its own UI, its own backend, and potentially a remote server so learning is available on the go (away from the machine that produced the transcripts). The agent is a data source, not an interface.

> **Supersedes a prior decision.** `decisions.md` (2026-09-01) recorded "MCP server + `/unrot` slash command as the delivery surface." That is now reversed — the delivery surface is a standalone app, and Claude Code integration is capture-only. `decisions.md` should get a dated entry recording the reversal and its reasoning.

## Goals
1. Detect terms Claude Code used load-bearingly and without explanation, where the user's next turn shows blind acceptance rather than understanding.
2. Hit ~1–2 precise flags per session — precision over recall, since noise kills retention past day three.
3. Validate the detector against a human's own hand-labeled judgment before building delivery infrastructure around it.
4. Capture silently — zero change to, and zero interruption of, the user's existing Claude Code workflow.
5. Let the user add gaps the capture layer can't see, via natural-language input in their own words.
6. Turn confirmed gaps into learning material in whatever format fits the moment and the concept.

## Competitive Landscape
Several tools already read the same `~/.claude/projects/*.jsonl` files (claude-insight, skill-tree, claude-session-analyzer) and Anthropic itself appears to be building a native "AI Fluency" scorecard into Claude settings. All of these score **collaboration behavior/style** — how well someone delegates, describes, or iterates with AI. None check whether the user understood a specific concept.

**Positioning:** unrot-your-brain is not a fluency/behavior tool. It targets **knowledge gaps in the user's daily workflow that AI silently assumes they already have** — the accumulating cost of AI treating unexplained terms as common ground when they aren't. This is a different axis from "how you use AI" and should be described that way to avoid getting read as a weaker clone of the fluency-scorecard category.

The standalone-surface decision reinforces this: the fluency tools all live *inside* the agent as scorecards and hooks. A separate learning product with its own UI competes with learning apps, not with agent plugins.

## Non-Goals (v1)
- **Any in-agent interaction surface** — no MCP server, no slash command, no injected text, no hook-driven prompts. Capture only. (Reverses the prior `/unrot` decision.)
- **General chat-interface support** (Claude Desktop chat, other vendors) — no local transcript file to read the same way; revisit once the dev-focused core loop is validated.
- **Prerequisite graph / curriculum engine** — needs real gap data to design against; premature before v1 ships.
- **Spaced-retrieval learning loop** — same reason; sequenced after the flat gap list proves out.
- **Network-proxy/gateway capture** — rejected outright (see `decisions.md`); would force users off subscription billing.
- **Multi-user / accounts / sharing** — single-user personal tool for v1, even though the remote server raises the question.

## User Journeys

Ordered by priority. Journeys 1–3 are the core loop; 4–8 are edge and trust cases; 9–10 are the newly-added capabilities.

### 1. First real gap surfaced (core loop)
User finishes a normal Claude Code session where the model used a term load-bearingly without explaining it, and the user just said "ok go ahead." Capture happens silently. Later, in the unrot UI, the user sees the flag with the surrounding context from the session → recognizes they genuinely didn't know it → the gap is confirmed and becomes something to learn.

*As a developer, I want to see the 1–2 terms I likely didn't understand from a session, so that I can close real comprehension gaps instead of rubber-stamping AI output.*

### 2. False positive / already understood
User gets a flag for a term they actually knew fine — they didn't ask a clarifying question because they didn't need to. User dismisses or corrects it, and that dismissal is treated as signal for tuning the detector, not just discarded.

*As a developer, I want to tell the tool when it's wrong, so that it gets sharper instead of repeatedly flagging things I know.*

### 3. Clean session, no flags
User's session had good clarifying questions throughout. Nothing gets flagged. The UI must make "we looked and found nothing" legible as a positive result, distinct from "capture didn't run" or "analysis failed."

*As a developer, I want silence to be a trustworthy signal, so that an empty list reassures me rather than making me wonder if it's broken.*

### 4. The "what is it in one line?" check
Rather than asking "do you know X?", the tool asks the user to explain the flagged term in one line. Answer is graded loosely — it functions as a real check rather than a self-report survey. Result determines whether the flag was a genuine gap or the user just happened not to ask.

*As a developer, I want to be asked to explain rather than to self-assess, so that false confidence can't quietly pass the check.*

### 5. Gap density without overwhelm
A messy session produces far more candidate terms than the 1–2 budget allows. The user needs to trust the tool isn't arbitrarily hiding things — some visibility into "there were more candidates, these won and here's roughly why."

*As a developer, I want to know when there was more beneath the surface, so that a small list reads as curation rather than as the tool missing things.*

### 6. Fake seeded term (calibration)
Occasionally a made-up term is seeded into a check to see whether the user confidently claims familiarity with something that doesn't exist. Failing it quietly adjusts how much weight their self-report carries — without confronting or embarrassing them.

*As a developer, I want the system to calibrate to how honestly I self-report, so that its judgments about me get more accurate over time.*

### 7. Cold start on a fresh project
User points the tool at a brand-new project with only one or two sessions. There isn't enough behavioral history to say much. The product needs an honest "not enough data yet" state rather than manufacturing flags to look useful.

*As a new user, I want the tool to admit when it doesn't know enough yet, so that its later confidence means something.*

### 8. Revisiting an old gap weeks later (P2)
User is asked to re-explain something flagged three weeks ago, in a totally different context, to check whether it actually stuck. This is the spaced-retrieval loop — deferred from v1, but the differentiator per `ideas.md`, so v1's data model shouldn't foreclose it.

*As a developer, I want to be re-tested on old gaps at a distance, so that I find out what genuinely stuck versus what I just nodded at twice.*

### 9. Learning material in the format that fits
A confirmed gap becomes learning material, and the right format varies by concept and by context: a short written explanation, a worked example in the user's own codebase, a diagram for something structural, a flashcard for a definition, a longer read for something foundational, a quiz for retrieval practice. The user can also ask for a different format when the default doesn't land ("this didn't click, show me an example instead").

*As a developer, I want the explanation delivered in the format that suits the concept and my current context, so that I actually learn it rather than skimming a wall of text.*

**Open sub-questions:** which formats are in scope for v1 (one default with manual override? a small fixed set?), whether format selection is automatic or user-chosen, and whether material is generated on demand or precomputed.

### 10. Manually submitting an overheard gap
The user hears a term in a meeting, a podcast, a corridor conversation — somewhere the capture layer will never see. They type it into unrot in natural language, roughly as they encountered it ("someone said our service needs backpressure handling, no idea what that means"). The system ingests it, resolves what concept is actually being referred to, and treats it as a first-class gap alongside the auto-detected ones.

*As a developer, I want to throw a half-remembered term at the tool in my own words, so that gaps from outside my terminal don't get lost.*

**Notes:** input may be vague, misheard, or misspelled — resolution needs to handle "I think it was something like…". This is also the journey that most benefits from the remote server / on-the-go access, since it happens away from the dev machine.

## Structural Decisions — BLOCKING

Seven modelling decisions that must be settled **before any schema is designed or any code is written**. Each is cheap to decide now and expensive-to-impossible to retrofit once real data accumulates. These are not open questions to be resolved in flight — the walking skeleton does not start until they're closed.

S1–S4 are the atom-shape decisions. S5 is the meta-decision that sets how reversible the others are, and should arguably be taken first. S6–S7 are lower-urgency but same class.

### S1. Is the atom a concept, or an encounter with a concept?
The same concept will be met repeatedly — flagged in March in one project, again in June in another. As a single row, the second encounter overwrites the first and the history is lost. As two independent rows, there's no way to see that the same thing keeps tripping the user up.

Repetition is plausibly the most valuable signal the product has: a concept surfacing three times across six months means something categorically different from one surfacing once. Splitting a stable `concept` from a stream of `encounter` records pointing at it costs almost nothing now, and is miserable to retrofit.

*Determines whether journeys 11 (repeat encounters), 8 (revisit weeks later) and 13 (silent resolution) are representable at all.*

### S2. Is status binary or graded?
`confirmed / dismissed / unknown` is tempting for its simplicity, but it collapses something real: a user can usually define a term without knowing when to reach for it, and may know it in one context but not another (journeys 14, 15). With a boolean, the one-line check can only ever return pass or fail, and partial understanding has nowhere to live.

**Counterargument worth taking seriously:** a graded scale that never gets populated meaningfully is a boolean with extra steps and more surface area to be wrong.

### S3. Is the atom a *term* at all?
The current detection strategy is built entirely on flagging terms the model used without explaining. But the higher-value gaps are often **decisions** — the model chose optimistic over pessimistic locking, or a queue over a stream, and the user said "fine, go ahead." There is no unexplained word to flag, and yet this is plausibly the more damaging thing to wave through (journey 24).

Modelling a `gap` with a `type` (`term` being one type, `unexamined_decision` another) leaves room for this. Naming the table `terms` silently decides that class of gap doesn't exist. Related: journey 25, gaps arising from tool calls rather than conversational turns.

### S4. What is actually stored as context?
Currently assumed to be a raw transcript excerpt — the most useful thing for showing the user *why* something was flagged. But it may contain client code, credentials, or NDA'd material, and the remote-server plan means it leaves the machine (journey 18).

The alternatives — a redacted excerpt, or an LLM-generated paraphrase — are both safer and both less useful for recall. **Hardest of the four to reverse: data already synced to a remote server cannot be retroactively unsent.**

### S5. Is the store a source of truth, or a derived cache?
**This governs how much S1–S4 actually cost.** If transcripts are retained and gaps can be *regenerated* by re-running an improved detector over history, the store is disposable and a schema mistake is cheap to correct. If transcripts are discarded and the store is all that survives, every modelling mistake is permanent.

**Preferred direction (Freddie, 2026-09-16): derived and re-runnable.** Improving the engine should retroactively improve the whole history, not just apply from that point forward. Implies: retaining transcripts (or a durable normalised form), recording which detector version produced each gap, and treating regeneration as a first-class operation rather than a one-off migration. Also implies user judgments (confirm/dismiss/one-line answers) must survive regeneration — they're human input, not derived data, and must not be wiped by a re-run.

*Tension to resolve:* re-running requires keeping raw transcripts, which cuts directly against S4's privacy concern. **S4 and S5 must be decided together.** See **Storage & Ingestion Model** below for the directional answer that emerged from this.

### S6. Are concepts mutable in place, or versioned?
A concept's canonical name, aliases, and learning material drift over time — but encounters point back at it. If concepts are mutable in place, editing one silently rewrites the meaning of every historical encounter attached to it. A close cousin of S1, and possibly part of the same decision.

### S7. Identity across machines
Only bites if the remote server happens: laptop and desktop both producing encounters for the same concept, needing reconciliation. Probably deferrable, but the kind of thing that becomes a painful migration if the model assumes a single origin.

**Owner:** Freddie. These are product-shape calls, not engineering detail.

### Status (2026-09-16)
| | Decision | State |
|---|---|---|
| **S1** concept vs encounter | Split — encounters are events, concepts are compiled state | ✅ resolved by the event-log model |
| **S2** binary vs graded status | Collapsed SOLO (isolated / listed / causal); raw explanation text retained so any future rubric can re-grade history | ✅ resolved |
| **S3** term vs gap-with-type | Needs a spike, experimentation, possibly user guidance | ⏳ **open — does not block v1 for internal use** |
| **S4** stored context | Two-layer: raw retained locally, paraphrase-with-pointer syncs | ✅ resolved |
| **S5** source of truth vs cache | Derived and re-runnable; raw transcripts retained | ✅ resolved |
| **S6** mutable vs versioned concepts | Append-only log is versioned by construction | ✅ resolved by the event-log model |
| **S7** cross-machine identity | Logs merge; staleness accepted, no locking | ✅ resolved |

Resolutions detailed in **Storage & Ingestion Model** below and §10–11 of the ideas doc.

**S3 is explicitly downgraded from blocking.** Whether the atom is a term or a typed gap needs empirical work; it does not need settling before an internal-use v1. Build with a term-shaped atom and keep the spike open.

## Storage & Ingestion Model

Emerged from the S5 discussion (2026-09-16). Directional, not yet locked — but it resolves most of S5–S7 in one move.

### Append-only event log, compiled to current state
The graph is stored as an **append-only log of events** (`concept_created`, `alias_merged`, `status_changed`, `superseded_by`, …), with current state *compiled* from that log rather than mutated in place.

This gives S5 (re-runnable), S6 (versioned concepts) and S7 (cross-machine merge) largely for free — append-only logs reconcile far more easily than mutable rows. It also gives a language model the chronological reasoning trail, not just the end state, which matters when the primary consumer of the graph is an LLM.

**Storage: Postgres, not ClickHouse.** ClickHouse is built for analytical scans over very large volumes; this workload is constant small writes and current-state lookups for a single user's graph. Postgres handles append-only fine — an insert-only table plus a view or materialised view that compiles current state. *The append-only discipline is a convention that's kept, not a database feature that's bought.*

### Nothing writes to the graph directly
Every write — automatic transcript ingestion **and** manual natural-language submission (journey 10) — goes through a **resolution step** before anything is appended. A term may be misspelled, misheard, a near-duplicate of an existing concept, or a genuine new one; an LLM sits in that loop and decides.

Consequence: **conflicts are prevented at ingestion, not reconciled at compile.** The resolver is the serialisation point, so compile stays a dumb fold over events. All the intelligence lives in one reviewable place.

### Resolver judgments are themselves events
"Merged X into Y because it looked like a misspelling" is a judgment that can be wrong, and journey 19 requires the user to be able to correct it. If only the *outcome* is appended, there is nothing to correct against. Resolver decisions and their reasoning are therefore appended as first-class, reviewable events.

### Staleness is accepted, not locked against
The resolver reads current state, reasons, then appends — so a concurrent write (backlog import running during a manual submission, or a second machine) could leave it reasoning against state that has since moved. **Decision: read the latest state and accept the staleness.** Worst case is a duplicate concept that a later pass merges — and since merges are themselves correctable events, the system already has the machinery to clean up after itself. Not worth building locking for a single user.

### Deliberately not built here
A **general-purpose versioned-decision system** — append-only markdown files with explicit `supersedes:` references, compiled to a current-state view, readable both as raw chronology and as compiled state.

The mechanism is the same one described above, and the idea is a good one, but building the general version inside unrot would roughly double the project. **Parked as a separate later project**, plausibly packaged as a skill. `decisions.md` is already a manual instance of the pattern (this session produced a supersession — the MCP/slash-command reversal). Hone the mechanism on unrot's single use case first; abstract once it's been wrong a few times.

## Requirements

### v1 = a walking skeleton
v1 is a **thin slice through every layer**, not one layer built well. Every capability in the Product Shape is present and connected; none of it is good yet. The goal is an end-to-end flow you can actually run on yourself and form a judgment about — "is this loop worth having" is as risky an unknown as "does the detector work," and only an end-to-end version tests both.

**Deliberately crude in v1:** detector precision, UI polish, concept-resolution accuracy, learning-material quality, format variety. **Deliberately present in v1:** every link in the chain, because a missing link is what a skeleton exists to expose.

**The chain, end to end:**

1. **Capture** — read transcripts from `~/.claude/projects/`. Silent, read-only, no agent interaction. Manual trigger is fine for v1; a watcher/daemon can come later.
2. **Detect** — zero-shot analyzer flags terms. Crude is acceptable; one flag per session that's roughly right is enough to exercise the flow.
3. **Store** — persist gaps. **Shape is determined by S1–S7 above; the earlier placeholder `term | context | source | first_seen | status | session_ref` is explicitly not the answer** — it assumes a term-shaped atom, a flat row per concept, a binary status, a raw excerpt, and a write-once store, i.e. it pre-answers the blocking decisions wrongly-by-default. Schema matters more than implementation. SQLite is fine.
4. **Manual input** — natural-language submission of an overheard term, resolved to a concept, landing in the same store as detected gaps (journey 10). Resolution can be a single LLM call; vagueness handling can be poor.
5. **Surface** — a minimal UI listing gaps: confirm, dismiss, answer the one-line check. Ugly is fine. Empty state must still read as "found nothing," not "broken" (journey 3).
6. **Learn** — generate learning material for a confirmed gap in **at least two formats**, so the multi-format capability is exercised rather than assumed. Manual format switch is fine; automatic selection is not needed.

**Acceptance criteria (the end-to-end test):**
- [ ] A real Claude Code session can be captured, analyzed, and produce at least one gap in the store — without the session noticing.
- [ ] A manually submitted vague term ("something about backpressure?") resolves and appears alongside detected gaps.
- [ ] A gap can be confirmed, dismissed, or answered via the one-line check, and the outcome persists.
- [ ] A confirmed gap produces learning material in two different formats.
- [ ] An empty gap list is visually distinct from an error state.
- [ ] Freddie can run the whole loop on himself for a week and say whether it's worth continuing.

**Out of the skeleton (not thin versions — genuinely absent):** remote server, watcher daemon, seeded fake terms, candidate-density visibility, spaced retrieval, prerequisite graph, automatic format selection.

### Validation, adjusted
The hand-labeling step from `brief.md` doesn't disappear — it gets lighter and moves. Rather than gating everything on 10 labeled sessions up front, label 2–3 to sanity-check the detector isn't wildly off, build the skeleton, then use real usage to decide whether fuller validation is worth doing. The original sequencing assumed detector-first; end-to-end-first means the detector only needs to be *good enough to exercise the chain*.

**Risk worth naming:** with a crude detector, a disappointing week of usage is ambiguous — bad detection or bad concept? Keep the 2–3 labeled sessions specifically so you can tell those apart afterwards.

### Nice-to-Have (P1)
- Remote server / hosted backend for on-the-go access (strongly implied by journey 10, but a local-only v1 is still usable).
- Multiple learning-material formats with automatic format selection.
- Seeded fake terms for self-report calibration (journey 6).
- Candidate-density visibility (journey 5).

### Future Considerations (P2)
- Prerequisite graph linking gaps (e.g. "eventual consistency" → "quorum," "partition").
- Spaced-retrieval loop (journey 8).
- Additional agent integrations beyond Claude Code.
- General chat-interface capture (proxy/extension based).

## Success Metrics
**Leading (detector validation phase):**
- Flag volume: ~1–2 flags/session across the 10 hand-labeled sessions.
- Precision against hand-labels: go/no-go judgment call.

**Leading (once the product surface exists):**
- % of surfaced flags the user engages with (confirms, dismisses, or answers) vs. ignores.
- % of gaps that are manually submitted vs. auto-detected — tells you whether journey 10 is a nice-to-have or actually the main input.
- Dismissal rate as a proxy for detector precision in the wild.

**Lagging:**
- Day-30 retention — the stated bar for whether precision-over-recall was the right call.
- Whether users return to learning material more than once per gap.

## Open Questions
*S1–S7 are deliberately **not** listed here — they are blocking structural decisions (see above), not questions to resolve in flight.*

- **(product/Freddie)** Which learning-material formats are in scope for v1, and is format chosen automatically or by the user?
- **(product/Freddie)** Does the remote server land in v1 or after? Journey 10 leans on it, but local-only is a viable first cut.
- **(engineering)** How does manual natural-language input resolve to a concept — same analyzer, or a distinct resolution step?
- **(engineering — spike, PR-12)** OSS JSONL parsers vs. custom PR-9 parser.
- **(engineering — spike, PR-13, non-blocking)** Zero-shot vs. semantic-similarity detection.
- **(engineering)** Does the shipped parser need `thinking` blocks or image content blocks, currently skipped in PR-9?
- **(engineering)** VS Code/JetBrains transcript schema unverified locally (PR-9 gap).
- **(product/Freddie)** Local-only analysis vs. API call for the analyzer — privacy-first positioning vs. cost. The remote-server plan makes this more pressing, since transcripts would leave the machine.
- **(product/Freddie)** What's the precision bar for go/no-go?
- **(product/Freddie)** When can the 10 sessions get hand-labeled? Still the one human-blocking step.

## Timeline Considerations
- No hard external deadline.
- **v1 is the walking skeleton** — one milestone, not a detector phase followed by a product phase. This supersedes `brief.md`'s "smallest version = analyzer plus validation only," which assumed a detector-first build order.
- **S1–S7 close first.** No schema, no code until then.
- Build order within the skeleton: store schema first (hardest to change later) → capture + detect → UI → manual input → learning material. Each step should leave the chain runnable end to end.
- The go/no-go moves to the end: after a week of real self-use, not after detector scoring.
- PR-9 done. PR-12 and PR-13 open.
