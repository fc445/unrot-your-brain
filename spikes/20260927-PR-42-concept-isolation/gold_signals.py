"""Every per-term signal, for the ticket's gold terms only.

    uv run python gold_signals.py > out/gold_signals.md

Sends the 36 gold terms (bare, no context) to Wikipedia's public API; results
are cached in out/wiki_cache.json.
"""

from common import GAPS, LOCAL, SPIKE, SYNTH_GAPS, SYNTH_NOT_GAPS, by_prefix, load_sessions
from signals import camel, code_shape, in_repo, repo_for, wiki_phrase_hits, wiki_titles, zipf_min

rows = (
    [(p, g, "gap") for p, gs in GAPS.items() for g in gs]
    + [(p, g, "local") for p, gs in LOCAL.items() for g in gs]
    + [("sess-messy", g, "synth gap") for g in SYNTH_GAPS["sess-messy"]]
    + [("sess-messy", g, "synth not-gap") for g in SYNTH_NOT_GAPS["sess-messy"]]
)
sessions = load_sessions()
terms = sorted({g for _, g, _ in rows})
titles, hits = wiki_titles(terms), wiki_phrase_hits(terms)
worktree = SPIKE.parents[1]

print("| kind | term | session | min Zipf | Wikipedia title | Wikipedia phrase hits | in session's repo | CamelCase | code-shaped |")
print("|---|---|---|---|---|---|---|---|---|")
for p, g, kind in rows:
    root = repo_for(by_prefix(sessions, p).project_dir, worktree)
    repo = in_repo(g, root)
    print(
        f"| {kind} | {g} | {p} | {zipf_min(g):.2f} | {titles[g]} | {hits[g]} | "
        f"{'n/a' if repo is None else repo} | {camel(g)} | {code_shape(g)} |"
    )
