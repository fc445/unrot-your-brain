"""Finding candidate sources on the web.

Retrieval, not generation: what comes back here are links a search index
returned, which `sources.verify_web` then fetches and reads. The model is never
asked for a URL, because a model asked for a URL will produce one that looks
right, and a citation that looks right is worse than none at all -- it is the
exact failure the P0 gate exists to stop.

Goes through OpenRouter's web plugin, so it is the same provider and the same
key as everything else rather than another integration to configure.
"""

from __future__ import annotations

import json
import time
import urllib.request

from ..model import NO_KEY_MESSAGE, ModelConfig
from ..spend import record_http
from .sources import Source

#: Enough to choose from after verification drops some, few enough that fetching
#: them all keeps the whole thing interactive.
MAX_RESULTS = 5


def build_search(
    config: ModelConfig | None = None, *, max_results: int = MAX_RESULTS, meter=None
):
    """Return `search(term) -> list[Source]` of unverified candidates."""
    config = config or ModelConfig.from_env()
    if not config.api_key:
        raise RuntimeError(NO_KEY_MESSAGE)
    url = config.base_url.rstrip("/").removesuffix("/v1") + "/v1/chat/completions"

    def search(term: str) -> list[Source]:
        body = json.dumps(
            {
                "model": config.model,
                "plugins": [{"id": "web", "max_results": max_results}],
                # The reply is thrown away. Only the annotations are wanted, and
                # asking for a short answer keeps the call from paying to
                # generate prose that gets discarded.
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"Find good explanations of '{term}' as used in"
                            " software engineering. Answer in one sentence."
                        ),
                    }
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.load(response)
        except Exception as exc:
            record_http(meter, "material", config, error=exc, started=started)
            raise
        record_http(meter, "material", config, payload=payload, started=started)

        message = (payload.get("choices") or [{}])[0].get("message") or {}
        out, seen = [], set()
        for annotation in message.get("annotations") or []:
            citation = annotation.get("url_citation") or annotation
            link = citation.get("url")
            if not link or link in seen:
                continue
            seen.add(link)
            out.append(
                Source(
                    kind="web",
                    ref=link,
                    title=(citation.get("title") or link)[:140],
                    verified=False,
                )
            )
        return out

    return search
