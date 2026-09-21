// Mirrors src/unrot/api/schemas.py. Kept hand-written rather than generated:
// the API is small, and a generator is a build step to maintain for a contract
// that fits on one screen.

/** Which bucket a concept renders in. Decided by the backend, never here --
 *  the rule lives next to `derive_state` in Python so that changing it is one
 *  edit rather than two that can drift apart. */
export type Bucket = "open" | "learning" | "closed";

/** What the whole surface is in. `failed` is the one state the server never
 *  sends, because a response saying "I failed" is a response that arrived. */
export type SurfaceState =
  | "gaps"
  | "clean"
  | "cold_start"
  | "not_captured"
  | "not_analysed"
  | "failed";

export interface Encounter {
  encounter_id: string;
  /** 'transcript' | 'manual'. Shown as provenance, never used to rank or style
   *  a card differently -- a gap you typed in yourself is as real as one found. */
  source: string;
  paraphrase: string | null;
  judgment: "confirmed" | "dismissed" | null;
  judged_at: string | null;
  occurred_at: string;
  detector_version: string | null;
  session_id: string | null;
  line_start: number | null;
  line_end: number | null;
  /** Whether the raw transcript is still on this machine. False on the portable
   *  layer by construction, so the card must read fine from the paraphrase. */
  resolvable: boolean;
}

export interface Concept {
  concept_id: string;
  name: string;
  gap_type: string;
  state: string;
  bucket: Bucket;
  aliases: string[];
  encounter_count: number;
  unjudged: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  latest_level: string | null;
  encounters: Encounter[];
}

export interface Capture {
  sessions: number;
  human_turns: number;
  last_activity: string | null;
  sessions_with_flags: number;
}

export interface Surface {
  state: SurfaceState;
  headline: string;
  detail: string;
  capture: Capture;
  counts: Record<string, number>;
  /** Non-zero means some of what is on screen is seeded development data. */
  fixtures: number;
  concepts: Concept[];
}

export interface MomentTurn {
  line_no: number;
  role: string;
  text: string;
  is_meta: boolean;
  is_sidechain: boolean;
}

export interface Moment {
  encounter_id: string;
  resolvable: boolean;
  reason: string | null;
  session_id: string | null;
  line_start: number | null;
  line_end: number | null;
  turns: MomentTurn[];
}

export interface Judgment {
  encounter_id: string;
  concept: Concept | null;
  surface: SurfaceState;
  counts: Record<string, number>;
}
