import type { Graded, Level } from "../types";

const ORDER: Level[] = ["isolated", "listed", "causal"];

/** What each level means, in words aimed at the person who just wrote an answer.
 *
 *  Phrased as a description of the answer, never of the person. "This was one
 *  fact" is a note about a sentence; "you only knew one fact" is a verdict about
 *  someone, and a tool that delivers those weekly is one people delete. */
const LEVELS: Record<Level, { label: string; blurb: string; tone: string }> = {
  isolated: {
    label: "one fact",
    blurb: "A single point, without much around it.",
    tone: "open",
  },
  listed: {
    label: "a list",
    blurb:
      "Correct pieces, sitting side by side. This is what reciting a good explanation produces \u2014 which is not the same as having joined them up.",
    tone: "open",
  },
  causal: {
    label: "joined up",
    blurb: "You linked the pieces \u2014 why it works, not just what it does.",
    tone: "closed",
  },
};

export function CheckResult({
  result,
  onDismiss,
}: {
  result: Graded;
  onDismiss: () => void;
}) {
  const level = result.explanation.level;
  const meta = level ? LEVELS[level] : null;

  return (
    <div className="check">
      <div className="check-result">
        <span className="pip" data-tone={meta?.tone ?? "quiet"}>
          {meta?.label ?? "not graded"}
        </span>
        <p>
          {meta?.blurb ??
            "Your answer is saved. Nothing graded it yet \u2014 run `unrot.grader grade` and it will be picked up."}
        </p>
      </div>

      {result.explanation.reasoning && (
        <p className="check-note">{result.explanation.reasoning}</p>
      )}

      {/* The classifier writes no prose, so the distribution is the feedback:
          it says how near the answer came to the one boundary that matters,
          which a bare label cannot. */}
      {result.explanation.probabilities && (
        <div className="levels">
          {ORDER.map((name) => {
            const p = result.explanation.probabilities?.[name] ?? 0;
            return (
              <div
                className="level"
                key={name}
                data-picked={name === result.explanation.level}
              >
                <span className="level-name">{LEVELS[name].label}</span>
                <span className="level-bar">
                  <span style={{ width: `${Math.round(p * 100)}%` }} />
                </span>
                <span className="level-pct">{Math.round(p * 100)}%</span>
              </div>
            );
          })}
        </div>
      )}

      {!result.graded && (
        <p className="check-note">
          Graded offline by looking for connecting words, not by judging the
          answer \u2014 treat it loosely. <code>unrot.grader regrade --yes</code>{" "}
          once a model is configured.
        </p>
      )}

      <blockquote className="check-answer">{result.explanation.raw_text}</blockquote>

      <div className="actions">
        <button className="btn" data-variant="ghost" onClick={onDismiss}>
          Done
        </button>
      </div>
    </div>
  );
}
