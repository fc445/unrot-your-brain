import type {
  Check,
  Format,
  Graded,
  Judgment,
  Made,
  Moment,
  Submitted,
  Surface,
} from "./types";

/** Thrown for anything that is not a successful JSON response.
 *
 *  This type is load-bearing for journey 3. "We looked and found nothing" and
 *  "the backend is down" both produce an empty screen unless the failure is
 *  carried as a distinct thing all the way to the renderer, so every call
 *  either returns data or throws this -- never returns an empty result on error.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(
      "Could not reach the unrot backend. Is `python -m unrot.api` running?",
    );
  }
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    let detail = body;
    try {
      detail = (JSON.parse(body) as { detail?: string }).detail ?? body;
    } catch {
      /* a non-JSON error body is still worth showing verbatim */
    }
    throw new ApiError(detail || response.statusText, response.status);
  }
  return (await response.json()) as T;
}

export const getSurface = () => call<Surface>("/api/surface");

export const getMoment = (encounterId: string) =>
  call<Moment>(`/api/encounters/${encodeURIComponent(encounterId)}/moment`);

export const judge = (encounterId: string, verdict: "confirm" | "dismiss") =>
  call<Judgment>(
    `/api/encounters/${encodeURIComponent(encounterId)}/${verdict}`,
    { method: "POST" },
  );

export const getCheck = (conceptId: string) =>
  call<Check>(`/api/concepts/${encodeURIComponent(conceptId)}/check`);

export const explain = (conceptId: string, text: string) =>
  call<Graded>(`/api/concepts/${encodeURIComponent(conceptId)}/explanation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });

export const makeMaterial = (conceptId: string, format: Format) =>
  call<Made>(
    `/api/concepts/${encodeURIComponent(conceptId)}/material?format=${format}`,
    { method: "POST" },
  );

/** Journey 10: a term met somewhere capture never sees. A refusal -- nothing
 *  recognisable to file -- comes back as an ApiError with status 422 and the
 *  resolver's reason as its message. */
export const submitTerm = (text: string) =>
  call<Submitted>("/api/submissions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
