# unrot-your-brain — ideas & research

**Status:** capture doc. Nothing here is committed. Items marked ✅ have a decision from Freddie; everything else is raw. Promote to PRD selectively.
**Last updated:** 2026-09-16

---

## 1. Decisions taken in this session

### ✅ Sourcing is a P0 quality gate, not a nice-to-have
**The failure mode:** the product generates explanations of precisely the things the user cannot evaluate. A hallucinated definition of backpressure lands on someone who by definition can't catch it — and it's now confidently wrong in their head *and* in the graph. This is worse than not flagging at all.

Learning material must carry provenance: where this came from, what it's based on, what to read to verify it. Grounding/retrieval over free generation.

*Research backing (see §6):* hallucinations are hardest to detect precisely for novice users in the domain, and errors compound into persistent misconceptions. RAG/grounding is the standard mitigation.

### ✅ The user controls whether learning material is produced at all
Three levels of user control, all first-class:
1. **Whether** — some users want gap detection and nothing else. "Tell me what I missed, I'll handle it." Material generation must be switchable off entirely.
2. **What style** — within a format, tone and depth are preferences (terse vs. discursive, analogy-heavy vs. formal, code-first vs. prose-first).
3. **Links only** — a legitimate first-class output is *no generated content at all*, just pointers to good existing material. This also sidesteps the hallucination risk entirely for users who want that.

*Implication:* material generation is a pluggable output stage, not baked into the core loop. The gap graph is the product; material is one consumer of it.

### ✅ Inverted trust is a named risk we accept and monitor
"unrot will catch it" becomes licence to accept *more* blindly — the tool causes the disease it treats.

Mitigating factor: **unrot never touches the user's code.** It has no write path, no suggestions, no gate. It cannot be leaned on as a safety net in the way a linter or a test suite can, because it changes nothing about what ships. The risk is psychological, not mechanical.

Worth measuring, not designing around: if clarifying-question rate *drops* after onboarding, that's the signal.

### ✅ "Plant a bug" — flagged as an interesting idea, not yet scoped
Take a confirmed concept, introduce a realistic failure of it into a snippet of the user's own code, ask them to find it. Retrieval practice with genuine transfer, not a flashcard.

*Research backing:* this is an established pedagogy ("erroneous examples", "intentional bugs", "de-constructionism"), and BugSpotter (arXiv 2411.14303) already demonstrates LLM-generated buggy code verified by a test suite for CS education. Prior art exists; the novel part would be doing it against the user's *own* codebase rather than synthetic exercises.

*Caveat:* inherits the sourcing problem — a planted bug that isn't actually a bug teaches the wrong thing.

### ✅ The inversion — high value, careful positioning
Feed the graph back into the agent's context: a generated file saying "Freddie doesn't know X, explain when relevant; Freddie knows Y cold, don't over-explain."

This flips the product from remedial to preventive and makes the personal knowledge model the asset rather than the gap list.

**Positioning constraint (Freddie):** do *not* position this as an alternative "brain" / "second brain". That category is saturated and faddish in 2026 (see §6). The framing should be about *the agent adapting to what you know*, not about *externalising your mind*.

**Design note:** this does not violate the no-in-agent-interaction decision — it's context injection, not an interface. But it's close enough to the line to be a conscious call rather than a drift.

### ✅ Domain heatmap over graph visualisation
A sparse early graph looks pathetic and teaches nothing. A heatmap of gap density by domain over time — "your gaps cluster in distributed systems" — is more honest at low data volume and more useful at high volume.

### ✅ General versioned-decision system parked
Append-only markdown with explicit `supersedes:` references, compiled to current state. Same mechanism as the event-log storage model. Good idea; belongs as a separate later project, plausibly a skill. Hone it on unrot's single use case first.

---

## 2. Detection signals not currently used

The parser already extracts everything needed for most of these. They're free.

| Signal | What it detects | Notes |
|---|---|---|
| **Vocabulary production** | A term appears only in assistant turns, never in user turns → gap. User starts deploying it correctly three sessions later → learned. | **Highest-value unused signal.** Free longitudinal confirmation with no quiz, no prompt, no interruption. Directly grounded in the receptive/productive vocabulary distinction from SLA research (§6). |
| **Explanatory follow-up rate** | Count of "why did you…", "what happens if…" prompts per session. | Anthropic's RCT found high-comprehension participants averaged **4.2 explanatory follow-ups per session vs. 0.4** for low scorers. Computable directly from transcripts. Strong per-session comprehension proxy. |
| **Latency** | Time between assistant turn and user acceptance. A 400-line diff accepted in 2s ≠ accepted in 4min. | Transcripts carry timestamps. Not proof alone; strong multiplier on a term-level flag. |
| **Did they look?** | Tool calls show whether a file was read before the change was accepted. Claude writes, user never opens it, user says "great". | A gap with no unusual term in it at all — supports S3 (gap-with-type over term). |
| **Correction as negative signal** | User corrects Claude on a term → they definitely know it. | High-confidence "not a gap". Far more reliable than self-report. Useful for calibration. |
| **Repeat-asking** | Same question across three sessions weeks apart → never consolidated. | Different and more interesting than never asking. |
| **Refactoring avoidance** | User works around code rather than restructuring it. | Industry data shows refactoring share collapsed 25% → <10% as AI adoption rose; "developers who don't understand code don't refactor it." Hard to detect from transcripts alone but worth noting. |

---

## 3. Learning material — the defensible version

**Teach from the user's own code.** Don't explain backpressure; explain *the queue in their ingest service*, using the actual code from the actual session where it was flagged. A generic explainer is a commodity any LLM produces. An explanation grounded in the user's codebase is not reproducible by a general learning app — this is the moat.

Requires: the transcript, the file contents, the surrounding decision. All available at capture time. **Interacts directly with S4** (what context is stored) and S5 (transcript retention) — teaching from their own code requires keeping enough of it.

Related formats worth considering:
- **The moment view** — diff-style: here's what Claude said, here's the line you skipped, here's your reply. Showing the moment of acceptance is more persuasive than any abstract flag.
- **Planted bug** (see §1).
- **Links only** (see §1).

---

## 4. New user journeys (continuing from 25)

### 26. User turns off material generation entirely
Wants detection and a list; will learn on their own. Product must be fully useful as a pure detector. *Schema impact: none. Product impact: material generation can't be load-bearing for the value prop.*

### 27. User asks for links, not explanations
"Don't write me an essay, tell me where to read about this." Output is curated pointers with provenance. *Sidesteps the hallucination risk entirely — arguably the safest default for v1.*

### 28. User sets a house style for material
Terse, no analogies, code-first. Persisted preference applied to all future material. Distinct from per-item format choice (journey 9).

### 29. Generated material is wrong and the user catches it
The user *does* know enough to spot an error in an explanation. Needs a report path, and the report is signal about both the generator and the user's actual knowledge level. *An unexpected proof of competence — should update the graph.*

### 30. Concept learned silently, confirmed by vocabulary production
User never engages with a flag, but starts using the term correctly two months later. Status updates with no interaction at all. *Depends entirely on S1 (encounters) and S2 (graded status).*

### 31. Agent receives the knowledge model as context
The inversion. Graph is compiled to a context file the agent reads; explanations adapt to what the user knows. *Needs: a compile target, a staleness policy, and a decision on whether the user reviews the file before it's used.*

### 32. New hire onboarding against an unfamiliar codebase
Gap list becomes an auto-generated onboarding curriculum specific to this repo. *Reframes journey 16 (project-specific jargon) from awkward edge case to the core B2B use case.*

### 33. Team-aggregate gaps, anonymised
"Six of eight engineers don't understand the auth flow" → that's a documentation gap, not eight learning gaps. Valuable without being surveillance **only if it never individuates** — hard privacy line, easily crossed, must be designed in not bolted on.

### 34. User flags "correct, and I don't care"
Flag is right, user genuinely doesn't know it, it's irrelevant to their work. Distinct from dismiss-as-wrong and from snooze. *Without this, dismissal data is polluted and detector tuning is corrupted.*

### 35. Knowledge decays
User used to know this. Knowledge rots — it's in the product's name — yet the current model only moves toward "confirmed". *S2 again: status needs to move both ways.*

### 36. Blocked by prerequisite
Teaching eventual consistency before partitions wastes both. Even without the full graph, a single "needs something else first" marker is cheap and prevents bad material.

### 37. Voice check on mobile
The one-line check as a spoken interaction. Lower friction than typing while walking — and **speaking an explanation is a better comprehension test than writing one**, because it removes the opportunity to edit toward fluency.

### 38. Ambient surface, not a notification
Terminal MOTD, new-tab page, widget. The nagging version gets uninstalled; the version that's simply *there* when you glance gets used.

---

## 5. UI / interaction ideas

- **Heatmap by domain over time** ✅ — honest at low data, useful at high data. Preferred over graph viz.
- **The moment view** — replay the point of acceptance rather than abstracting it into a flag.
- **Ambient placement** over notifications (journey 38).
- **Silence must be legible** (existing journey 3) — "we looked, found nothing" is a feature, and the heatmap is a good surface for showing sustained clean sessions.
- **Framing matters more than detection.** A daily list of your failures is a product people delete in week two. Progress-shaped, not deficit-shaped.

---

## 6. Research findings

### Comprehension debt is now a named, quantified industry problem
The term **"comprehension debt"** has emerged in 2026 as the label for exactly the problem unrot targets — <cite index="86-1">the gap between how much code exists in a system and how much any human genuinely understands</cite>. This is strong positioning language and is *not* the same axis as "AI fluency". Worth considering as the category unrot sits in.

Supporting data points worth citing in the PRD problem statement:
- <cite index="90-8">Anthropic's 2026 RCT (N=52 junior devs): AI-assisted group scored 50% on comprehension vs. 67% for hand-coding control (Cohen's d=0.738, p=0.01)</cite>.
- <cite index="90-8">Stack Overflow's 2026 survey: 76% of developers admit generating code they don't fully understand</cite>.
- <cite index="90-8">Analysis of 304K AI-authored commits across 6,275 repos found 24.2% of AI-introduced issues survive to the latest revision</cite>.
- <cite index="90-15">GitClear's analysis of 211M changed lines (2020–2024): code cloning rose from 8.3% to 12.3% of changes while refactoring's share of changed lines collapsed from 25% to under 10%</cite> — <cite index="90-17">developers who don't understand code don't refactor it; they work around it</cite>.
- METR: developers expected AI to speed them up 24%, were actually 19% slower, and *still* believed afterwards they'd been sped up by 20% — a ~40-point gap between perceived and actual performance.

### ⚠️ VibeCheck — direct prior art that challenges a core design decision
<cite index="90-21">A between-subjects RCT (arXiv 2602.20206, N=78) tested an IDE plugin that intercepts AI-generated code insertions and requires the developer to produce a causal explanation before accepting them, with an LLM judge evaluating each explanation against the SOLO taxonomy — a learning-depth rubric distinguishing vague restatement from genuine causal understanding. Restatements are rejected; relational, causal explanations pass.</cite>

Results: <cite index="90-23">unrestricted AI use dropped bug-repair success to 23.1% versus a 69.2% manual baseline; the explanation gate recovered it to 61.5% while preserving 89.1% task completion, at a cost of roughly 14 extra minutes per session</cite>.

**Why this matters to us — three things:**

1. **It cuts against the silent/async decision.** The strongest empirical evidence for this class of intervention is for *synchronous, in-flow friction at the moment of acceptance*. unrot deliberately chose silent, async, pull-based, no-agent-interaction. <cite index="90-24">The researchers note the gate works because it forces encoding at the moment of acceptance, before the developer has moved on to the next task.</cite> That mechanism is unavailable to an async tool. **This tension needs an explicit answer in the PRD.**

2. **But the retention data supports our position.** <cite index="90-24">72% of participants called the gate "annoying"</cite> — in a paid study with no opt-out. A voluntary tool with that friction profile has a retention problem, which is precisely the bet unrot is making.

3. **The synthesis is the inversion.** <cite index="90-26">The VibeCheck researchers propose "adaptive friction fading" as future work — progressively reducing gate frequency as a developer demonstrates mastery of a component, concentrating friction where comprehension risk is highest.</cite> **That requires a per-user knowledge model, which is exactly what unrot builds.** unrot isn't a competitor to the explanation gate; it's the missing input that makes an adaptive gate possible. This is a strong strategic frame and it aligns with journey 31.

Also useful: **the SOLO taxonomy is an off-the-shelf rubric for grading explanations by depth** — a concrete, defensible answer to S2 (graded vs. binary status) that doesn't require inventing a scale.

### Illusion of explanatory depth — validates the one-line check, and supports S3
Rozenblit & Keil's IOED work is the direct academic grounding for "explain it in one line" over yes/no self-report: <cite index="64-1">people feel they understand the world in far more detail and depth than they really do, and asking them to produce a step-by-step explanation collapses the illusion</cite>.

Critically for **S3 (is the atom a term at all?)**: <cite index="70-1">the illusion is far stronger for explanatory knowledge than for facts, procedures, or narratives</cite>. Term definitions are closer to facts. *Mechanisms and decisions* are exactly where the illusion is strongest — which is direct evidence that **unexamined-decision gaps are the higher-value class**, not an edge case.

### Receptive vs. productive vocabulary — validates the production signal
Established in second-language acquisition: <cite index="37-1">understanding words when listening or reading does not always mean a learner can produce them in speaking or writing</cite>, and <cite index="34-1">receptive knowledge is generally acquired before productive knowledge</cite>. Production is the stronger evidence of real knowledge — so a term the user starts *using* is confirmed learning, and a term they only ever receive is not.

### Knowledge tracing — the academic name for what the graph is
Building a model of a learner's evolving knowledge state is a mature field ("knowledge tracing"), and LLM-based systems now combine it with retrieval to personalise instruction. Useful for prior art, terminology, and evaluation methods — and it means the graph has an established literature rather than being novel-and-unvalidated.

### Erroneous examples / intentional bugs — validates "plant a bug"
Established CS-education pedagogy with an existing LLM tool: <cite index="44-1">BugSpotter generates buggy code from a problem description and verifies the synthesised bugs via a test suite, with students designing failing test cases — practising problem comprehension, bug localisation and code comprehension</cite>. The novelty for unrot would be doing this against the user's own codebase and their own confirmed gaps.

### "Second brain" is a crowded, faddish category — avoid the framing
2026 listicles compare a dozen-plus PKM tools all claiming AI-powered "thinking partner" status. Confirms Freddie's instinct: the inversion should be framed as *the agent adapting to what you know*, not as externalising your mind.

### Developer onboarding — the B2B wedge has real numbers
<cite index="74-1">Onboarding's substantive half is ramp-up: learning the systems, codebase, conventions and tribal knowledge — and time-to-productivity is, to a first approximation, codebase-understanding speed</cite>. Typical ramp is 2–4 months. One analysis puts the cost of poor tribal-knowledge transfer at $200–300K annually for six hires, with senior engineers spending 30–40% of their week answering questions.

This is the strongest external validation for journeys 32 and 33.

---

## 7. Answered: what would an MCP server give the agent?

Asked in session. MCP servers expose three primitive types to a connected agent:

- **Tools** — functions the agent can call (query the gap graph, submit a manual gap, mark something learned).
- **Resources** — read-only data the agent can list and read, referenced by `@server:path`, letting the model pull in external content without inlining it.
- **Prompts** — reusable prompt templates the client can invoke.

Claude Code discovers these via `tools/list`, `prompts/list` and `resources/list` on connect, and supports `list_changed` notifications so a server can update its offerings without a reconnect.

**Relevance to unrot:** the earlier decision to drop MCP was about *not building an interaction surface inside the agent* — and that still holds for tools and prompts. But **resources are a read path, not an interaction**, and are the natural mechanism for journey 31 (the inversion): expose the compiled knowledge model as a resource the agent can read, with no slash command, no injected text, and no interruption.

**This deserves a revisit of the MCP decision, narrowly scoped to resources only.** It doesn't reopen the delivery-surface question.

### The direction of the integration matters (Freddie, 2026-09-16)
The killed MCP/skill integration was conceived as a way for the user to *pull gaps out of* unrot inside the agent — a `/unrot` command, an interaction surface. That's still dead.

The live version points the other way, and is **not about the agent reporting gaps into unrot either**. It's about unrot telling the agent **how to pitch its explanations**:

> *Explain this to someone who doesn't know it. Skip the explanation for someone who does.*

That's a calibration instruction, not a report and not an interface. The agent never surfaces anything to the user about unrot, never asks a question on its behalf, never interrupts. It simply adjusts explanation depth per concept based on a model of what this person already knows.

**Why this is the cleanest form of the inversion:**
- It's read-only — the agent consumes the model, it doesn't write to it. Capture stays in the transcript layer where it already is.
- It's invisible — the user experiences it as the agent explaining things better, not as a second product intruding.
- It preserves the no-interaction principle exactly: there is still no unrot UI inside the agent.
- It inverts the value proposition from remedial to preventive without any behaviour change from the user.

**Mechanism options, in rough order of intrusiveness:**
1. **A generated context file** (`CLAUDE.md`-style, or an Agent Skill) the user chooses to include — zero integration, no server, works today, fully inspectable.
2. **MCP resource** — the compiled knowledge model exposed read-only, referenced by the agent as needed. More current than a file, no tools, no prompts, no commands.
3. **Agent Skill** — packages the calibration instruction alongside the model, so the *behaviour* ("explain at this depth for concepts marked unknown") travels with the data rather than relying on the agent inferring what to do with a list.

Option 1 is testable in the walking skeleton at near-zero cost and would validate whether the effect is real before any integration work. **Open question:** does the user review the compiled model before the agent sees it? Getting told less about something you don't actually know is the obvious failure mode, and it's a silent one.

---

## 8. Open questions raised here

- Does the silent/async design need an answer to the VibeCheck in-flow finding, or do we accept being weaker-but-retained? (Product/Freddie — this is close to a structural decision.)
- Should "comprehension debt" replace or supplement the current positioning language?
- Is links-only the *default* for v1 material, given it sidesteps the hallucination risk entirely?
- Does the inversion (journey 31) reopen the MCP decision for resources specifically?
- Is SOLO the grading rubric for the one-line check, and does it answer S2?
- Does teaching-from-your-own-code force S4/S5 toward transcript retention?
- Does the user review the compiled knowledge model before the agent consumes it (§7)?

---

## 9. Enterprise / commercial direction

**Status: not being tackled now.** Parked deliberately — recorded so the v1 schema doesn't foreclose it. Nothing here should influence the walking skeleton beyond the two "decide early" items at the end.

### The trap to avoid first
A tool that measures individual developer comprehension is one product decision away from being a surveillance and performance-management instrument. If unrot scores reach managers, three things follow: the OSS community dies, developers game the signal, and the signal being gamed is *blind acceptance* — the precise behaviour the detector depends on. **The measurement destroys itself.**

So the enterprise product is almost certainly **not** "see how well your developers understand their code." That needs to be architecturally impossible, not a policy promise. The aggregate is more valuable anyway, and it's about the codebase and the organisation rather than the people in it.

### Four pitches, ordered by defensibility

**1. Comprehension debt as a codebase risk map.** Which parts of the system does nobody actually understand? Existing bus-factor tooling infers this from git blame — who *wrote* it. unrot can measure who *understands* it, behaviourally. Different things, and the second is what matters during an incident. Novel, aggregate by nature, no individual exposure.

**2. Documentation gap detection.** Six of eight engineers hit the same gap on the auth flow → that's one missing document, not six learning gaps. Output flips from a curriculum to a prioritised docs backlog. Easy sale: actionable work, no judgment about anyone. (Journey 33.)

**3. Onboarding acceleration.** Auto-generated, repo-specific curriculum from a new hire's own sessions. Cleanest ROI story because the research supplies the numbers (§6): 2–4 month typical ramp, ~$200–300K annual cost for six hires at a team with poor knowledge transfer, time-to-productivity ≈ codebase-understanding speed. Measurable against time-to-first-meaningful-PR. (Journey 32.)

**4. Agent effectiveness, not learning — the sleeper.** The §7 inversion at org scale: every agent in the company gets calibration on what this team knows and doesn't. Explanations pitch correctly, boilerplate gets skipped, wrong assumptions stop compounding. A productivity pitch powered by a learning tool, with **no surveillance smell at all** — nothing is reported about anyone, the model only shapes how agents talk. Easiest sale, hardest to refuse, and it makes the knowledge model infrastructure rather than a report.

### The one to be wary of
**Regulated-industry compliance.** "The AI wrote it and we didn't fully review it" won't survive post-incident scrutiny in healthcare, finance or government. An auditable record of demonstrated comprehension at the point of acceptance is a genuine compliance artifact and probably carries the highest willingness-to-pay here.

But it pushes toward the synchronous gate *and* per-individual records — the two things that break everything above. Real market; likely a different product.

### Commercial shape
Open core, with an honest split: aggregation genuinely requires a server, so the line isn't artificial. OSS stays local, single-user and **fully capable**. Enterprise adds cross-user aggregation, SSO, policy, deployment and integrations.

**The discipline that matters: the OSS version must never feel like a demo.** It's the distribution channel — developers install it themselves, the org buys the layer above. Crippling it destroys the wedge that made the enterprise product possible.

### Decide early, not late
- **Licence.** Currently Apache 2.0. Fine for adoption, but it permits a cloud vendor to run the aggregation layer as a service. Worth a conscious look at BSL or AGPL *before* there's anything worth taking.
- **S4 becomes procurement, not preference.** Transcripts containing client code and credentials leaving the machine is a deal-blocker in enterprise sales, not a design nicety. In Europe, aggregate comprehension data may additionally require works council approval as employee monitoring.
- **Who owns the knowledge model when someone leaves?** Personal tool: obviously theirs. Enterprise: contested. Needs an answer before a customer asks.

### Long shot
Cross-org anonymised aggregate: which concepts do developers *universally* fail to understand when AI explains them? A DORA-style annual report and a strong marketing engine. Needs scale that doesn't exist yet — but it's the kind of asset that only exists if the schema supports it from the start.

---

## 10. ✅ S4 + S5 resolved — two-layer storage

**Decision (Freddie, 2026-09-16): store both.** Raw transcripts *and* LLM-generated paraphrases that reference specific transcripts, so the raw can be used again later.

| Layer | Contents | Syncs? | Purpose |
|---|---|---|---|
| **Raw transcripts** | Full session JSONL | **Never** | Source of truth for re-runs; resolves pointers for teaching-from-your-own-code |
| **Graph events** | LLM paraphrase + pointer (session id + line range) | Yes | The portable, shareable layer |

**Why this resolves both blocking decisions:**
- **S5 ✅ re-runnable** — an improved detector can regenerate the whole history because the raw is retained.
- **S4 ✅ privacy** — only the paraphrase leaves the machine. Client code, credentials and NDA'd material stay local.
- **Teaching-from-your-own-code survives** — the pointer resolves to real code when the user is at their machine.

**The insight that makes it clean:** Claude Code *already* writes these transcripts to the user's disk. unrot retaining them adds no new exposure. The exposure is the **sync**, not the storage — so the privacy boundary is the sync line. Be liberal locally, strict remotely.

### Sub-decisions still open
1. **Copy or reference the transcript?** Referencing `~/.claude/projects/` means pointers rot when Claude Code rotates files or the user cleans up. Copying is safer and cheap (~7MB per 14 sessions observed locally). *Leaning: copy.*
2. **Paraphrases must stand alone.** On mobile the pointer can't resolve. If a paraphrase only makes sense beside the code, the remote experience is empty. Write them to be self-sufficient — this is a prompt requirement, not a schema one.
3. **Does re-running rewrite old paraphrases?** A better detector produces better paraphrases, but old ones may already carry user judgments. Needs a rule.

---

## 11. Semantics between nodes

### Material is many-to-many
An explanation of eventual consistency necessarily covers partitions and quorum. Therefore:
- Material is **its own entity**, not a column on concept
- Join table between material and the concepts it covers
- A gap can have several materials; a material can serve several gaps

**Consequence that feeds S2:** if material for A also explains B, is B taught? No — but it isn't untouched either. That's a third state, **exposed**: not confirmed, not unseen.

### Node states
| State | Meaning | Has an encounter? |
|---|---|---|
| **gap** | encountered, not understood | yes |
| **known** | encountered, understood | yes |
| **referenced** | named in material, never encountered by the user | **no** |

A *referenced* node is scaffolding: a reference point for clustering and future edges. It is never surfaced, never counts toward the 1–2 flag budget, never gets its own material generated.

**This vindicates the S1 split.** A concept with zero encounters is valid; a gap requires an encounter. Two different things — which is precisely why concept and encounter are separate.

**Payoff:** when the user later hits "quorum" in a real session, it's already in the graph, positioned relative to eventual consistency. The relationship was discovered before it was needed.

### ⚠️ Graph explosion — the brake is depth, not addition
Explosion comes from **recursion**, not from adding nodes. Material names ~5 concepts; if each generates material naming 5 more, that's 125 by depth 3, none of it originating with the user.

**Rule: depth 1 only.** A concept enters the graph if it was named in material the user actually received. It does not then generate its own material, and its neighbours are not pulled in. **Expansion is driven by encounters, never by the graph's own contents.**

### Edge types (candidates, not committed)
Ranked by value:
1. **prerequisite** — A needed to understand B. Directional. The curriculum-generating edge.
2. **contrasts-with** — optimistic vs pessimistic locking. **Probably the native edge for decision-type gaps** (S3), since a decision gap is usually a choice between two things whose tradeoff isn't understood.
3. **broader / narrower** — what the domain heatmap runs on. Cheap, useful early.
4. **co-occurs** — appeared in the same session. Free to compute, weak, decent seed.
5. ~~same-as~~ — **not an edge.** That's identity, resolved at ingestion by the resolver. Modelling it as a relationship would give two mechanisms one job.

### ✅ Edges deferred (Freddie, 2026-09-16)
**Not adding edges this early.** The similarity/clustering work stands on its own without typed relationships. Edges wait for real data.

*Schema implication when they do arrive:* edges as events with a **free-text `type`, no enum**. Structure without committing to an unearned ontology.

### Vector similarity — where it works and where it fails

**Right tool for:**
1. **Ingestion resolution** — near-duplicate detection ("K8s"/"Kubernetes", misspellings, half-remembered terms from journey 10). Highest-value use, and it lives at the **resolver**, not in the graph.
2. **Domain clustering for the heatmap** — clusters emerge with no ontology defined. The heatmap needs proximity, not typed edges. **This is the v1-relevant use.**
3. **Candidate generation for edges** — bounds the infinite-edges problem by only considering near neighbours. (Deferred with edges.)

**Where it breaks:** similarity cannot distinguish *same as* from *opposite of in the same space*. Optimistic and pessimistic locking have near-identical embeddings — same domain, same vocabulary, same context — and cosine similarity reads that as "the same thing". That is exactly the relationship that matters for decision-type gaps. Similarity is also **symmetric** while prerequisites are **directional**: quorum→eventual-consistency and eventual-consistency→quorum score identically.

### The ensemble (Freddie's inversion — better than a pipeline)
LLM proposes similar subjects, similarity then **verifies** them:

| LLM | Similarity | Action |
|---|---|---|
| proposes relationship | close | high confidence — store |
| proposes relationship | distant | likely hallucinated — drop |
| finds no relationship | close | same domain, no real link — use for clustering only |

Clean division of labour: similarity answers *are these in the same space*; the LLM answers *what is the relationship, and in which direction*. Neither can do the other's job. **Precision-over-recall must apply to edges too** — arguably tighter than for flags, since a wrong flag is noise but a wrong prerequisite edge actively misroutes learning.

### Knock-on for PR-13
PR-13 already spikes semantic-similarity vs zero-shot for *gap detection*. Same embedding infrastructure serves resolution, clustering and (later) edge candidates. **PR-13 is higher-value than its original non-blocking scoping suggested.**

**S5 caveat:** embeddings are derived state. Store the model version alongside them, or a model upgrade silently changes every similarity in the graph.

---

## 12. ✅ S2 resolved — collapsed SOLO, raw text retained

**Decision (Freddie, 2026-09-16): collapsed SOLO, 3 levels.** Explicitly marked as changeable later.

| Level | Example ("idempotency") | Shows |
|---|---|---|
| **isolated** | "You can call it twice." | One fact, no structure |
| **listed** | "You can call it twice, uses a key, it's for retries." | Correct facts, unconnected |
| **causal** | "Calling it twice has the same effect as once, so a client can retry after a timeout without double-charging — hence the dedup key." | Facts linked causally, explains *why* |

Full SOLO is 5 levels (Biggs & Collis, 1982): prestructural, unistructural, multistructural, relational, extended abstract. Levels 1 and 5 are rare in practice, so the middle three carry the signal.

**The boundary that matters is listed → causal.** That is where "I can recite what the AI told me" separates from "I understood it" — someone who just read Claude's explanation can produce a *listed* answer from recall. VibeCheck set its pass bar exactly there. It also aligns with Rozenblit & Keil: the illusion of explanatory depth is strongest for causal knowledge.

### What the decision actually binds
S2 was largely **dissolved by the event-log model**. Status is no longer a column — it is compiled from events. Swapping the rubric later is a change to the compile step plus a re-run, not a migration.

**The one irreversible part: store the raw text of the user's explanation.**

```
explanation_submitted { concept_id, raw_text, timestamp, prompt_version }
explanation_graded    { concept_id, rubric: "solo-3", level, grader_version }
```

Two events, not one. The first is ground truth and permanent; the second is derived and disposable. Keep the raw text and any future rubric can re-grade the entire history. Discard it and the scale becomes permanent by accident.

### ⚠️ The genuinely irreversible bit is the *prompt*
"One line, what is it?" elicits **listed** answers. Measuring **causal** understanding requires asking for causation — "why does it work that way?". Old answers can be re-graded; a question the user has moved on from cannot be re-asked.

*Open:* the check may need to be two lines rather than one, since a causal explanation rarely fits in one.

### Caveats to carry
- LLM grading drifts — same answer, different day, different level. Store `grader_version` (same problem as embeddings).
- Freddie flagged the rubric as likely to change. The two-event split is what makes that cheap.

---

## 13. Knowledge evolving over time

**Freddie, 2026-09-16:** show users their knowledge of subjects evolving.

**This is nearly free.** The append-only event log *is* a time series — every state is already timestamped and ordered. No extra schema; it's a compile target and a view, not a new entity.

### What it makes visible
1. **Per-concept timeline** — first encountered → flagged → exposed via material → isolated → listed → causal. The shape of one concept's history.
2. **Decay made legible** (journey 35) — a concept that reached *causal* in March and grades *listed* in September. Only possible because status moves both ways and history is retained.
3. **Repetition made legible** (journey 11) — the same concept surfacing three times over six months, previously the strongest unused signal.
4. **Silent learning** (journey 30) — vocabulary-production events plot on the same timeline, so a concept confirmed with zero interaction still shows a trajectory.
5. **Aggregate view** — the domain heatmap (§5) animated over time is the same data at a different zoom.

### Why it matters more than it looks
It directly counters the **shame-spiral risk** from §1. A daily list of your failures is a product people delete in week two. A record of things moving from *unknown* to *causal* is progress-shaped, and dopamine-shaped, and it's the same data.

**This is arguably the strongest argument for the event log over a mutable store.** A row that gets overwritten has no trajectory to show.

### What it requires
Non-interaction events must be recorded as densely as interaction events. Vocabulary production, silent resolution, re-encounters — these fill the gaps between explicit user actions, and without them a timeline is three dots and a lot of white space.

*Open:* is the timeline a v1 surface, or does it wait until there's enough history to look like anything? A two-week-old timeline is as unimpressive as a sparse graph.
