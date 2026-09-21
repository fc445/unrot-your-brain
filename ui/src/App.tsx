import { useCallback, useEffect, useState } from "react";
import { ApiError, getSurface, judge } from "./api";
import { EmptyState } from "./components/EmptyState";
import { GapCard } from "./components/GapCard";
import type { Bucket, Graded, Surface } from "./types";

/** Section copy. The order matters: what needs you, then what you are working
 *  on, then what is behind you. Ending on "closed" is the whole shame-spiral
 *  guard -- the last thing on the page is progress, not deficit. */
const SECTIONS: { bucket: Bucket; title: string; note: string }[] = [
  {
    bucket: "open",
    title: "Waiting on you",
    note: "Leaned on in a session, and waved through",
  },
  {
    bucket: "learning",
    title: "To learn",
    note: "You said you didn't know these",
  },
  {
    bucket: "closed",
    title: "Closed",
    note: "Either you knew it, or you explained it",
  },
];

export default function App() {
  const [surface, setSurface] = useState<Surface | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // The result of the check that was just answered, held here rather than in the
  // card because a `causal` grade moves the card to a different section and
  // destroys it on the way.
  const [justGraded, setJustGraded] = useState<Graded | null>(null);

  const load = useCallback(async () => {
    try {
      setSurface(await getSurface());
      setFailure(null);
    } catch (error: unknown) {
      // Journey 3's hard requirement: a failure must never render as an empty
      // list. It is carried as its own state all the way to the renderer.
      setFailure(
        error instanceof ApiError
          ? error.message
          : "Something broke while loading your gaps.",
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onJudge(encounterId: string, verdict: "confirm" | "dismiss") {
    setBusy(encounterId);
    try {
      await judge(encounterId, verdict);
      // Re-read rather than patching local state: the judgment recompiles the
      // whole graph, and a confirm can move a concept between sections. Asking
      // the store what happened is cheaper than predicting it correctly here.
      await load();
    } catch (error: unknown) {
      setFailure(
        error instanceof ApiError ? error.message : "That judgment did not save.",
      );
    } finally {
      setBusy(null);
    }
  }

  if (failure) {
    return (
      <div className="shell">
        <Masthead />
        <EmptyState
          state="failed"
          headline="Something broke"
          detail={failure}
        />
      </div>
    );
  }

  if (!surface) {
    return (
      <div className="shell">
        <Masthead />
        <p className="loading">Reading the log…</p>
      </div>
    );
  }

  const { capture, counts } = surface;
  const populated = SECTIONS.filter((s) => (counts[s.bucket] ?? 0) > 0);
  // An empty state is a full-page answer that carries its own headline and its
  // own next step, so the page-level heading would only repeat it back.
  // Bound to a local so the "not gaps" narrowing survives into the render.
  const state = surface.state;
  const emptyState =
    state !== "gaps" && populated.length === 0 ? state : null;

  return (
    <div className="shell">
      <Masthead
        tally={
          <>
            <span>
              <b>{capture.sessions_analysed}</b> of {capture.sessions} sessions
              examined
            </span>
            <span>
              <b>{counts.open ?? 0}</b> waiting
            </span>
            <span>
              <b>{counts.closed ?? 0}</b> closed
            </span>
          </>
        }
      />

      {!emptyState && (
        <>
          <h2 className="headline">{surface.headline}</h2>
          <p className="subhead">{surface.detail}</p>
        </>
      )}

      {/* Seeded data must never read as a finding about the user. */}
      {surface.fixtures > 0 && (
        <p className="notice">
          <span className="pip" data-tone="quiet">
            fixtures
          </span>
          {surface.fixtures} of these events are development fixtures standing in
          for the resolver, which is not built yet. Remove them with{" "}
          <code>python -m unrot.store seed --clear</code>.
        </p>
      )}

      {emptyState ? (
        <EmptyState
          state={emptyState}
          headline={surface.headline}
          detail={surface.detail}
        />
      ) : (
        populated.map((section) => (
          <section className="section" key={section.bucket} data-bucket={section.bucket}>
            <div className="section-head">
              <h2>{section.title}</h2>
              <span className="n">{counts[section.bucket]}</span>
              <p>{section.note}</p>
            </div>
            {surface.concepts
              .filter((c) => c.bucket === section.bucket)
              .map((concept) => (
                <GapCard
                  key={concept.concept_id}
                  concept={concept}
                  busy={busy !== null && concept.encounters.some((e) => e.encounter_id === busy)}
                  onJudge={onJudge}
                  onGraded={(result) => {
                    setJustGraded(result);
                    void load();
                  }}
                  justGraded={
                    justGraded?.concept?.concept_id === concept.concept_id
                      ? justGraded
                      : undefined
                  }
                  onDismissResult={() => setJustGraded(null)}
                />
              ))}
          </section>
        ))
      )}
    </div>
  );
}

function Masthead({ tally }: { tally?: React.ReactNode }) {
  return (
    <header className="masthead">
      <h1 className="wordmark">
        unrot <span>/ your brain</span>
      </h1>
      {tally && <div className="tally">{tally}</div>}
    </header>
  );
}
