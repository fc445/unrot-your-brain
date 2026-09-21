import type { SurfaceState } from "../types";

/** The empty states, which are a named v1 acceptance criterion rather than a
 *  polish item: "an empty gap list is visually distinct from an error state".
 *
 *  An empty screen can mean four different things, and only one of them is bad
 *  news. Collapsing them into one blank list is what makes a user wonder
 *  whether the tool is broken -- which costs more trust than any false positive.
 *  So each state gets its own mark, its own sentence, and where there is
 *  something to do about it, the exact command.
 */
const COPY: Record<
  Exclude<SurfaceState, "gaps">,
  { mark: string; next?: { label: string; command: string } }
> = {
  clean: { mark: "◎" },
  cold_start: {
    mark: "◔",
    next: { label: "Capture more", command: "python -m unrot.capture ingest" },
  },
  not_captured: {
    mark: "○",
    next: { label: "Run capture", command: "python -m unrot.capture ingest" },
  },
  not_analysed: {
    mark: "◍",
    next: { label: "Run the detector", command: "python -m unrot.detector" },
  },
  failed: { mark: "✕" },
};

interface Props {
  state: Exclude<SurfaceState, "gaps">;
  headline: string;
  detail: string;
}

export function EmptyState({ state, headline, detail }: Props) {
  const { mark, next } = COPY[state];
  return (
    <section className="state" data-kind={state}>
      <span className="state-mark" aria-hidden="true">
        {mark}
      </span>
      <h2>{headline}</h2>
      <p>{detail}</p>
      {next && (
        <div className="next-step">
          <span>{next.label}</span>
          <code>{next.command}</code>
        </div>
      )}
    </section>
  );
}
