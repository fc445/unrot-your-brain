import { useEffect, useRef, useState } from "react";
import { ApiError, explain, getCheck } from "../api";
import type { Check as CheckData, Graded } from "../types";

interface Props {
  conceptId: string;
  onGraded: (result: Graded) => void;
  onCancel: () => void;
}

export function Check({ conceptId, onGraded, onCancel }: Props) {
  const [check, setCheck] = useState<CheckData | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let live = true;
    getCheck(conceptId)
      .then((c) => {
        if (!live) return;
        setCheck(c);
        box.current?.focus();
      })
      .catch((e: unknown) =>
        live ? setError(e instanceof ApiError ? e.message : "Could not load it.") : undefined,
      );
    return () => {
      live = false;
    };
  }, [conceptId]);

  async function submit() {
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      onGraded(await explain(conceptId, text));
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "That did not save.");
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return <div className="check"><p className="check-note">{error}</p></div>;
  }

  return (
    <div className="check">
      <p className="check-question">{check?.prompt_text ?? "\u2026"}</p>
      <textarea
        ref={box}
        className="check-input"
        rows={3}
        value={text}
        placeholder="In your own words. Getting it wrong here costs nothing."
        disabled={busy || !check}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit();
          if (e.key === "Escape") onCancel();
        }}
      />
      <div className="actions">
        <button
          className="btn"
          data-variant="confirm"
          disabled={busy || !text.trim()}
          onClick={() => void submit()}
        >
          {busy ? "Checking\u2026" : "Submit"}
        </button>
        <button className="btn" data-variant="ghost" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
