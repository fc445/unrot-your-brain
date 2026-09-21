import { useState } from "react";
import { Check } from "./Check";
import { CheckResult } from "./CheckResult";
import { MaterialBlock } from "./MaterialBlock";
import { Moment } from "./Moment";
import type { Concept, Encounter, Format, Graded, Level } from "../types";

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

const LEVEL_LABEL: Record<Level, string> = {
  isolated: "one fact",
  listed: "a list",
  causal: "joined up",
};

interface Props {
  concept: Concept;
  busy: boolean;
  onJudge: (encounterId: string, verdict: "confirm" | "dismiss") => void;
  onGraded: (result: Graded) => void;
  /** Held above this card on purpose. A `causal` grade moves the concept into
   *  another section, so React destroys this card and builds a new one -- state
   *  kept here would go with it, and the grader's reasoning is the only
   *  feedback the check produces. */
  justGraded?: Graded;
  onDismissResult: () => void;
  onMaterial: (conceptId: string, format: Format) => void;
  /** Non-null while this card is waiting on generation, or explaining why it
   *  refused. Refusing is the provenance gate working, not an error, so it is
   *  worded as a result rather than a failure. */
  materialState?: { busy: boolean; error: string | null };
}

export function GapCard({
  concept,
  busy,
  onJudge,
  onGraded,
  justGraded,
  onDismissResult,
  onMaterial,
  materialState,
}: Props) {
  const [open, setOpen] = useState(false);
  const [checking, setChecking] = useState(false);

  // The most recent unjudged encounter is the one a verdict applies to. A
  // concept can hold several -- that repetition is the point of splitting
  // concept from encounter -- but you are only ever asked about one at a time.
  const pending: Encounter | undefined =
    concept.encounters.find((e) => e.judgment === null) ?? concept.encounters[0];
  const shown = pending ?? concept.encounters[0];

  // The moment comes from whichever encounter actually has a transcript behind
  // it, which is not always the one being shown. A concept met once in a
  // session and once by hand shows the hand-typed encounter (it is newer) and
  // would otherwise offer no moment at all -- hiding a real transcript the user
  // could have looked at.
  const withMoment = concept.encounters.find((e) => e.resolvable);
  const latest = concept.explanations[concept.explanations.length - 1];

  // While the check is open, everything that could contain the answer comes off
  // screen. The paraphrase is usually a definition of the term -- "Git rebase is
  // the operation that takes commits from one branch and reapplies them onto
  // another base" -- so leaving it up would turn an explanation into a reading
  // exercise, and the check would measure nothing at all.
  // ...until it has been answered. Once the grade is back there is nothing left
  // to give away, and the rest of the card has to come back so the result can be
  // read beside the term it was about.
  const revealing = !checking;

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
            one we found, and is treated identically everywhere else.
            Only when EVERY encounter was hand-typed: a concept met once in a
            session and once by hand was not "added by hand", and saying so
            beside a transcript moment reads as a contradiction. */}
        {concept.encounters.every((e) => e.source === "manual") && (
          <span className="pip" data-tone="quiet">
            added by hand
          </span>
        )}
      </header>

      {revealing && shown?.paraphrase && (
        <p className="paraphrase">{shown.paraphrase}</p>
      )}

      {checking && !justGraded && (
        <Check
          conceptId={concept.concept_id}
          onGraded={(result) => {
            setChecking(false);
            onGraded(result);
          }}
          onCancel={() => setChecking(false)}
        />
      )}

      {justGraded && (
        <CheckResult result={justGraded} onDismiss={onDismissResult} />
      )}

      {revealing &&
        concept.material.map((m) => (
          <MaterialBlock key={m.material_id} material={m} />
        ))}

      {revealing && materialState?.error && (
        <p className="material-refused">{materialState.error}</p>
      )}

      {revealing && <div className="card-meta">
        <span>{when(shown?.occurred_at ?? concept.last_seen_at)}</span>
        {shown?.session_id && shown.source !== "manual" && (
          <span>
            session <code>{shown.session_id.slice(0, 8)}</code>
          </span>
        )}
        {concept.aliases.length > 0 && <span>also: {concept.aliases.join(", ")}</span>}
        {latest?.level && (
          <span>
            you explained it: <b>{LEVEL_LABEL[latest.level]}</b>
          </span>
        )}
      </div>}

      {revealing && <div className="actions">
        {/* Only while the concept is still asking. A concept met several times
            keeps unjudged encounters after you answer -- we never asked about
            those -- but it has had its answer, so it must stop presenting the
            question. Gating on `pending` alone left a settled card still
            offering a verdict. */}
        {concept.bucket === "open" && pending?.judgment === null && (
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
        {/* Offered wherever the concept is still a gap, including after a
            confirm. Self-report is what this replaces -- people confirm
            familiarity with things they do not know -- so explaining it is the
            path that can actually close a gap, and dismissing is only for a
            flag that was simply wrong. */}
        {concept.state === "gap" && (
          <button
            className="btn"
            data-variant="confirm"
            disabled={busy}
            onClick={() => {
              setOpen(false);
              onDismissResult();
              setChecking(true);
            }}
          >
            {latest ? "Try explaining it again" : "Explain it"}
          </button>
        )}
        {/* Never automatic. The gap graph is the product; this is a pluggable
            stage on top of it, so material exists only when it is asked for. */}
        {concept.state !== "referenced" && (
          <>
            <button
              className="btn"
              data-variant="ghost"
              disabled={materialState?.busy}
              onClick={() => onMaterial(concept.concept_id, "textual_with_sources")}
            >
              {materialState?.busy ? "Working\u2026" : "Explain it to me"}
            </button>
            <button
              className="btn"
              data-variant="ghost"
              disabled={materialState?.busy}
              onClick={() => onMaterial(concept.concept_id, "sources_only")}
            >
              Just show me where to read
            </button>
          </>
        )}
        {withMoment && (
          <button
            className="btn"
            data-variant="ghost"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
          >
            {open ? "Hide the moment" : "Show the moment"}
          </button>
        )}
      </div>}

      {revealing && open && withMoment && (
        <Moment encounterId={withMoment.encounter_id} />
      )}
    </article>
  );
}
