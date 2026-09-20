-- unrot raw store. LOCAL ONLY. Nothing in this database ever syncs.
--
-- It lives in its own file, under raw/, so the S4 boundary is a path prefix
-- rather than a column somebody has to remember to check before uploading.
--
-- The split inside here mirrors the event log's: the copied .jsonl on disk is
-- ground truth and permanent; everything in `raw_turns` is a parse of it and is
-- disposable. Drop the table, re-parse the copies, get the same rows back. That
-- is what lets the parser improve without re-reading ~/.claude -- which may by
-- then have rotated the originals away.

CREATE TABLE IF NOT EXISTS raw_sessions (
    -- The FILENAME stem, which is what a pointer addresses and what our copy is
    -- named. Deliberately not the sessionId found inside the records: after a
    -- compaction those disagree, and the file is the thing that exists.
    session_id          TEXT PRIMARY KEY,
    reported_session_id TEXT,     -- sessionId as the records themselves claim it

    source_path         TEXT NOT NULL,  -- where it was read from, for provenance only
    copy_path           TEXT NOT NULL,  -- unrot's copy; pointers resolve against THIS

    -- Incremental re-ingest. A live session grows while we watch it, so we
    -- remember how far we got and hash what we took. If the hash of the source's
    -- first `bytes_ingested` bytes still matches, the file was appended to and
    -- we can take only the tail. If it does not, the file was rewritten and the
    -- honest answer is to start it over.
    bytes_ingested      INTEGER NOT NULL DEFAULT 0,
    lines_ingested      INTEGER NOT NULL DEFAULT 0,
    prefix_sha256       TEXT NOT NULL,

    entrypoint          TEXT,     -- cli | claude-desktop | sdk-cli | ...
    cc_version          TEXT,
    cwd                 TEXT,     -- which repo this session was working in
    git_branch          TEXT,

    first_ingested_at   TEXT NOT NULL,
    last_ingested_at    TEXT NOT NULL,
    rewrites            INTEGER NOT NULL DEFAULT 0,

    -- Schema drift, kept as numbers. A parser that silently returns less than it
    -- used to is worse than one that says so.
    malformed_lines     INTEGER NOT NULL DEFAULT 0,
    skipped_blocks      INTEGER NOT NULL DEFAULT 0,
    unknown_types       TEXT NOT NULL DEFAULT '{}'
);

-- One row per content block. Keyed by (session, line, seq), so re-ingesting a
-- line overwrites rather than duplicates -- idempotency is the primary key's
-- job, not a check somebody has to remember to write.
CREATE TABLE IF NOT EXISTS raw_turns (
    session_id   TEXT NOT NULL,
    line_no      INTEGER NOT NULL,   -- 1-based, matches the copy exactly
    seq          INTEGER NOT NULL,   -- block order within the line; 0 = the human's own text

    -- The distinction the whole product rests on. A `tool_result` arrives in a
    -- user-typed line but no human wrote it, and `is_meta` marks injected skill
    -- and system-reminder text that also arrives as "user". Blind acceptance is
    -- only meaningful for rows where a person actually typed something.
    role         TEXT NOT NULL,
    text         TEXT,
    is_meta      INTEGER NOT NULL DEFAULT 0,
    is_sidechain INTEGER NOT NULL DEFAULT 0,  -- subagent traffic; the user never saw it

    tool_name    TEXT,
    tool_use_id  TEXT,
    is_error     INTEGER,
    occurred_at  TEXT,

    PRIMARY KEY (session_id, line_no, seq)
);

CREATE INDEX IF NOT EXISTS idx_turns_role    ON raw_turns (session_id, role);
CREATE INDEX IF NOT EXISTS idx_turns_human   ON raw_turns (role, is_meta, is_sidechain);
CREATE INDEX IF NOT EXISTS idx_turns_tool_id ON raw_turns (tool_use_id);
