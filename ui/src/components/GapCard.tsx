import { useState } from "react";
import { Moment } from "./Moment";
import type { Concept, Encounter } from "../types";

function when(iso: string | null): string {
  if (!iso) return "unknown";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "unknown";
  const days = Math.floor((Date.now() - date.getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

interface Props {
  concept: Concept;
  busy: boolean;
  onJudge: (encounterId: string, verdict: "confirm" | "dismiss") => void;
}

export function GapCard({ concept, busy, onJudge }: Props) {
  const [open, setOpen] = useState(false);

  // The most recent unjudged encounter is the one a verdict applies to. A
  // concept can hold several -- that repetition is the point of splitting
  // concept from encounter -- but you are only ever asked about one at a time.
  const pending: Encounter | undefined =
    concept.encounters.find((e) => e.judgment === null) ?? concept.encounters[0];
  const shown = pending ?? concept.encounters[0];

  return (
    <article className="card" data-bucket={concept.bucket} data-busy={busy}>
      <header className="card-head">
        <h3 className="term">{concept.name}</h3>

        {/* Repetition is arguably the most valuable signal the product has: the
            same thing tripping you up in three different sessions means
            something categorically different from once. */}
        {concept.encounter_count > 1 && (
          <span className="pip" data-tone="open">
            came up {concept.encounter_count}×
          </span>
        )}

        {concept.bucket === "learning" && (
          <span className="pip" data-tone="learning">
            you said you didn&apos;t know it
          </span>
        )}
        {concept.bucket === "closed" && (
          <span className="pip" data-tone="closed">
            {concept.latest_level === "causal" ? "explained it" : "you knew it"}
          </span>
        )}

        {/* Provenance, not ranking. A gap you typed in yourself is as real as
            one we found, and is treated identically everywhere else. */}
        {shown?.source === "manual" && (
          <span className="pip" data-tone="quiet">
            added by hand
          </span>
        )}
      </header>

      {shown?.paraphrase && <p className="paraphrase">{shown.paraphrase}</p>}

      <div className="card-meta">
        <span>{when(shown?.occurred_at ?? concept.last_seen_at)}</span>
        {shown?.session_id && shown.source !== "manual" && (
          <span>
            session <code>{shown.session_id.slice(0, 8)}</code>
          </span>
        )}
        {concept.aliases.length > 0 && <span>also: {concept.aliases.join(", ")}</span>}
      </div>

      <div className="actions">
        {pending?.judgment === null && (
          <>
            {/* Confirming does NOT close the gap. It means "I genuinely did not
                know this", which is where learning it starts. Dismissing is the
                one that closes, because the flag was simply wrong. */}
            <button
              className="btn"
              data-variant="confirm"
              disabled={busy}
              onClick={() => onJudge(pending.encounter_id, "confirm")}
            >
              I didn&apos;t know this
            </button>
            <button
              className="btn"
              data-variant="dismiss"
              disabled={busy}
              onClick={() => onJudge(pending.encounter_id, "dismiss")}
            >
              I knew this
            </button>
          </>
        )}
        {shown?.resolvable && (
          <button
            className="btn"
            data-variant="ghost"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
          >
            {open ? "Hide the moment" : "Show the moment"}
          </button>
        )}
      </div>

      {open && shown && <Moment encounterId={shown.encounter_id} />}
    </article>
  );
}
