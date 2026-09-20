-- unrot event log.
--
-- Append-only. Current state is COMPILED from `events`, never mutated in place.
-- Everything in a `compiled_*` table is disposable: drop them all, re-run the
-- fold, and you get the same answer back. `events` is the only thing that matters.
--
-- SQLite for v1 (local-only surface), but deliberately free of SQLite-only
-- types so the log ports to Postgres when the remote server lands. Append-only
-- is a convention we keep, not a database feature we bought.

-- ---------------------------------------------------------------------------
-- THE LOG. The only table anything ever writes to.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    -- ULID: lexicographically sortable by time, minted without coordination.
    -- Gives S7 (logs from two machines merge by sorting) with no locking.
    event_id     TEXT PRIMARY KEY,

    event_type   TEXT NOT NULL,

    -- S5's load-bearing column. 'user' events are ground truth and survive
    -- every regeneration; 'system' events are derived and disposable.
    -- Enforced here so a confirm can never be recorded as machine output.
    actor        TEXT NOT NULL CHECK (actor IN ('user', 'system')),

    occurred_at  TEXT NOT NULL,   -- ISO-8601 UTC, when the thing happened
    recorded_at  TEXT NOT NULL,   -- ISO-8601 UTC, when we wrote it down
    origin       TEXT NOT NULL,   -- which machine produced it (S7)

    subject_type TEXT,            -- 'concept' | 'encounter' | 'material'
    subject_id   TEXT,

    -- S16 lives here. A paraphrase (or any derived statement) can be superseded
    -- by a later one WITHOUT destroying the original and WITHOUT orphaning the
    -- user judgment attached to it. The policy for when to do that is deferred;
    -- the ability to express it is not.
    supersedes   TEXT REFERENCES events(event_id),

    -- S5: which version produced this derived state. JSON, e.g.
    -- {"detector_version": "...", "grader_version": "...", "model_version": "..."}
    -- A silent model upgrade otherwise changes the meaning of history.
    provenance   TEXT,

    payload      TEXT NOT NULL    -- JSON, shape validated per event type in events.py
);

CREATE INDEX IF NOT EXISTS idx_events_subject ON events (subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_events_type    ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_actor   ON events (actor);
CREATE INDEX IF NOT EXISTS idx_events_order   ON events (event_id);
CREATE INDEX IF NOT EXISTS idx_events_supersedes ON events (supersedes);

-- ---------------------------------------------------------------------------
-- COMPILED STATE. Entirely derived. Safe to DROP and rebuild at any time.
-- ---------------------------------------------------------------------------

-- S1: concepts and encounters are separate things.
-- A concept with zero encounters is valid (that is `referenced`).
-- A gap REQUIRES an encounter. That asymmetry is the whole reason for the split.
--
-- S3: named `compiled_concepts`, not `terms`. `gap_type` is 'term' for v1 but
-- the column exists so 'decision' and 'tool_call' are sayable later without a
-- migration. Naming the table `terms` would silently decide they don't exist.
CREATE TABLE IF NOT EXISTS compiled_concepts (
    concept_id      TEXT PRIMARY KEY,
    canonical_name  TEXT NOT NULL,
    gap_type        TEXT NOT NULL DEFAULT 'term',
    -- S2: status is NOT stored on write. It is derived in compile.py, so
    -- swapping the rule later is a code change plus a re-run, not a migration.
    state           TEXT NOT NULL,
    aliases         TEXT NOT NULL DEFAULT '[]',
    encounter_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at   TEXT,
    last_seen_at    TEXT,
    merged_into     TEXT,          -- set when this concept was merged away
    latest_level    TEXT,          -- 'isolated' | 'listed' | 'causal'
    latest_level_at TEXT
);

CREATE TABLE IF NOT EXISTS compiled_encounters (
    encounter_id     TEXT PRIMARY KEY,
    concept_id       TEXT NOT NULL,
    source           TEXT NOT NULL,   -- 'transcript' | 'manual'
    -- S4, portable layer: the paraphrase must STAND ALONE. On mobile the
    -- pointer cannot resolve, so a paraphrase that only makes sense beside the
    -- code makes the remote experience empty.
    paraphrase       TEXT,
    -- S4, local layer: pointer back into the retained raw transcript copy.
    -- Resolves against unrot's own copy (PR-15), never ~/.claude/projects/.
    session_id       TEXT,
    line_start       INTEGER,
    line_end         INTEGER,
    detector_version TEXT,
    -- User judgment. NULL means unjudged. Regeneration must never touch a row
    -- where this is non-NULL (PR-26's v1 rule).
    judgment         TEXT,            -- 'confirmed' | 'dismissed'
    judged_at        TEXT,
    occurred_at      TEXT NOT NULL,
    -- Which event supplied the paraphrase currently shown.
    paraphrase_event_id   TEXT,
    -- 1 when an earlier paraphrase was superseded by a later one. The earlier
    -- event is still in the log -- nothing is destroyed, and the judgment above
    -- is attached to encounter_id rather than to any paraphrase event, so a
    -- supersession can never orphan it.
    paraphrase_superseded INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_enc_concept ON compiled_encounters (concept_id);
CREATE INDEX IF NOT EXISTS idx_enc_judged  ON compiled_encounters (judgment);

-- Material is a first-class entity, not a column on concept: an explanation of
-- eventual consistency necessarily also covers partitions and quorum.
CREATE TABLE IF NOT EXISTS compiled_material (
    material_id  TEXT PRIMARY KEY,
    format       TEXT NOT NULL,   -- 'textual_with_sources' | 'sources_only'
    sources      TEXT NOT NULL DEFAULT '[]',
    body         TEXT,            -- NULL for sources_only: nothing is generated
    generated_at TEXT NOT NULL,
    delivered_at TEXT
);

CREATE TABLE IF NOT EXISTS compiled_material_concepts (
    material_id TEXT NOT NULL,
    concept_id  TEXT NOT NULL,
    PRIMARY KEY (material_id, concept_id)
);

-- S2's irreversible half. `raw_text` and `prompt_text` are ground truth and
-- permanent; `level`/`rubric`/`grader_version` are derived and disposable.
-- Storing the question verbatim beside the answer is what makes re-grading the
-- entire history possible when the rubric or the wording changes.
CREATE TABLE IF NOT EXISTS compiled_explanations (
    explanation_id TEXT PRIMARY KEY,   -- the explanation_submitted event_id
    concept_id     TEXT NOT NULL,
    encounter_id   TEXT,
    raw_text       TEXT NOT NULL,
    prompt_text    TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    submitted_at   TEXT NOT NULL,
    rubric         TEXT,
    level          TEXT,
    grader_version TEXT,
    graded_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_expl_concept ON compiled_explanations (concept_id);

CREATE TABLE IF NOT EXISTS compile_meta (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    compiled_through TEXT,
    compiled_at      TEXT,
    event_count      INTEGER NOT NULL DEFAULT 0
);
