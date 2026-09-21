import { useEffect, useState } from "react";
import { ApiError, getMoment } from "../api";
import type { Moment as MomentData } from "../types";

/** The moment view: replay the point of acceptance rather than abstracting it
 *  into a flag. What the model said, and what you said back.
 *
 *  This is S4's local layer. The paraphrase on the card is what would travel to
 *  a phone; this only exists on the machine that produced the transcript, and
 *  resolves against unrot's own retained copy rather than `~/.claude`, which
 *  may have rotated the original away. When it cannot resolve, the card above
 *  still has to stand on its own -- which is why the paraphrase is written to.
 */
export function Moment({ encounterId }: { encounterId: string }) {
  const [data, setData] = useState<MomentData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getMoment(encounterId)
      .then((result) => live && setData(result))
      .catch((e: unknown) =>
        live
          ? setError(e instanceof ApiError ? e.message : "Could not load it.")
          : undefined,
      );
    return () => {
      live = false;
    };
  }, [encounterId]);

  if (error) {
    return <div className="moment"><p className="moment-empty">{error}</p></div>;
  }
  if (!data) {
    return <div className="moment"><p className="moment-empty">Loading…</p></div>;
  }
  if (!data.resolvable || data.turns.length === 0) {
    return (
      <div className="moment">
        <p className="moment-empty">
          {data.reason ?? "Nothing readable at that pointer."}
        </p>
      </div>
    );
  }

  return (
    <div className="moment">
      <div className="moment-head">
        <span>session {data.session_id?.slice(0, 8)}</span>
        <span>
          lines {data.line_start}–{data.line_end}
        </span>
      </div>
      {data.turns.map((turn) => (
        <div className="turn" key={`${turn.line_no}-${turn.role}`} data-role={turn.role}>
          <span className="turn-role">
            {turn.role === "user" ? "you" : turn.role} · line {turn.line_no}
          </span>
          <pre>{turn.text}</pre>
        </div>
      ))}
    </div>
  );
}
