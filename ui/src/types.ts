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

/** SOLO, collapsed to three. The only boundary that carries weight is
 *  listed -> causal: that is where "I can recite what the model told me"
 *  separates from "I understood it". */
export type Level = "isolated" | "listed" | "causal";

export interface Explanation {
  explanation_id: string;
  raw_text: string;
  /** The question this answer was given to, stored verbatim. Shown back with
   *  the answer, because the wording is expected to change and the same words
   *  mean different things under different questions. */
  prompt_text: string;
  prompt_version: string;
  submitted_at: string;
  /** null while ungraded, which is a normal state: the answer is stored before
   *  grading is attempted, so a failed grader never costs what you wrote. */
  level: Level | null;
  reasoning: string | null;
  /** Only present for a classifier grade. Absent means "not measured", not
   *  "flat", so it is never filled in with zeroes. */
  probabilities: Record<Level, number> | null;
  confidence: number | null;
  grader_version: string | null;
}

export interface Check {
  concept_id: string;
  name: string;
  prompt_text: string;
  prompt_version: string;
}

export interface Graded {
  explanation: Explanation;
  concept: Concept | null;
  counts: Record<string, number>;
  /** False when the answer was stored but no real grader ran. You must not be
   *  told you failed a check that never happened. */
  graded: boolean;
}

export type Format = "textual_with_sources" | "sources_only";

export interface MaterialSource {
  kind: string;
  ref: string;
  title: string;
  excerpt: string | null;
  /** Whether the content was actually reached and read. Generated prose may
   *  cite nothing else; a sources-only list may show an unverified pointer,
   *  because you can click it and judge for yourself. */
  verified: boolean;
  note: string | null;
}

export interface Material {
  material_id: string;
  format: Format;
  body: string | null;
  sources: MaterialSource[];
  generated_at: string;
  delivered_at: string | null;
}

export interface Made {
  material: Material;
  concept: Concept | null;
  counts: Record<string, number>;
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
  latest_level: Level | null;
  encounters: Encounter[];
  explanations: Explanation[];
  material: Material[];
}

export interface Capture {
  sessions: number;
  human_turns: number;
  last_activity: string | null;
  /** Sessions the detector has actually examined, and how many of those it
   *  examined and found nothing in. Real numbers only because the resolver
   *  records `session_analysed` for the zero case too -- without that, a clean
   *  session and an unexamined one are the same absence of rows. */
  sessions_analysed: number;
  sessions_clean: number;
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
