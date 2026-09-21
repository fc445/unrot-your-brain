import type { Material } from "../types";

/** Material, with where it came from kept visible.
 *
 *  Provenance is a P0 gate rather than a footnote: this explains precisely the
 *  things the reader cannot evaluate, so the sources are shown beside it rather
 *  than tucked behind a toggle. An unverified pointer is labelled as one -- it
 *  can appear in a sources-only list, where you click it and judge for
 *  yourself, but never behind a sentence. */
export function MaterialBlock({ material }: { material: Material }) {
  const prose = material.format === "textual_with_sources";

  return (
    <div className="material">
      <div className="material-head">
        <span className="pip" data-tone={prose ? "learning" : "quiet"}>
          {prose ? "explained" : "where to read"}
        </span>
        {prose && (
          <span className="material-note">
            Every claim is tagged to a source below.
          </span>
        )}
      </div>

      {material.body && <p className="material-body">{material.body}</p>}

      <ol className="sources">
        {material.sources.map((source, index) => (
          <li key={source.ref} data-verified={source.verified}>
            <span className="source-tag">S{index + 1}</span>
            <span className="source-kind">{source.kind}</span>
            {source.kind === "web" ? (
              <a href={source.ref} target="_blank" rel="noreferrer noopener">
                {source.title}
              </a>
            ) : (
              <span className="source-title">{source.title}</span>
            )}
            {!source.verified && (
              <span className="source-note">{source.note ?? "unchecked"}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
