import { useState } from "react";
import { ApiError, submitTerm } from "../api";
import type { Submitted } from "../types";

/** Journey 10, at the desk: a term heard in a meeting, typed roughly as it was
 *  heard. It goes through the same resolver as a detected gap and lands beside
 *  them, so nothing here decides anything -- it shows what the resolver said,
 *  including when it found nothing to file. */
export function AddGap({ onAdded }: { onAdded: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Submitted | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setResult(null);
    setRefusal(null);
    try {
      const made = await submitTerm(text);
      setResult(made);
      setText("");
      onAdded();
    } catch (error: unknown) {
      // 422 is the resolver reading it and finding no concept: the gate
      // working, shown in its own words, with what was typed kept for a retry.
      setRefusal(
        error instanceof ApiError ? error.message : "That did not save.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="add-gap" onSubmit={submit}>
      <label htmlFor="add-gap-input">Heard something you didn&apos;t follow?</label>
      <div className="add-gap-row">
        <input
          id="add-gap-input"
          className="check-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="something about backpressure?"
          maxLength={600}
          disabled={busy}
        />
        <button className="btn" type="submit" disabled={busy || !text.trim()}>
          {busy ? "Adding…" : "Add"}
        </button>
      </div>
      {result && (
        <p className="add-gap-result">
          {result.decision === "new"
            ? <>New gap: <b>{result.canonical_name}</b>. </>
            : <>Filed under <b>{result.canonical_name}</b>, which you&apos;ve met before. </>}
          <span className="add-gap-why">{result.reasoning}</span>
          {result.model_unavailable && <> {result.model_unavailable}</>}
        </p>
      )}
      {refusal && (
        <p className="add-gap-result" data-tone="refused">
          Not filed. {refusal}
        </p>
      )}
    </form>
  );
}
