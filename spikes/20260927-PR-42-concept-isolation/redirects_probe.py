"""PR-38 probe: do Wikipedia redirects map variant spellings to one canonical title?

    uv run python redirects_probe.py

Bare terms only (the resolver-probe names from the e2e run), one batched request.
"""

import requests

from signals import API, UA

TERMS = ["K8s", "Kubernetes", "idempotent", "Idempotency", "Idempotence", "notarization", "notarisation",
         "UDS", "Unix domain socket", "xattr", "Extended file attributes", "EdDSA", "launchd", "LaunchAgent"]

r = requests.get(API, params={"action": "query", "titles": "|".join(TERMS), "redirects": 1, "format": "json", "formatversion": 2}, headers=UA, timeout=30)
q = r.json()["query"]
norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
red = {x["from"]: x["to"] for x in q.get("redirects", [])}
pages = {p["title"]: p for p in q["pages"]}
for t in TERMS:
    n = norm.get(t, t)
    target = red.get(n, n)
    p = pages.get(target, {})
    print(f"{t:26s} -> {target if not p.get('missing') else '(missing)'}")
