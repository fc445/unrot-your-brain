"""Turn an e2e run's results.json into one self-contained HTML report.

    uv run python spikes/20260927-PR-41-e2e-pipeline-run/build_report.py out/run1

Writes `report.html` beside `results.json`. No network, no external scripts:
the charts are inline SVG and the tooltips are a few lines of JS. The report
quotes detector paraphrases and material, so it stays under `out/` (gitignored)
like the data it is built from.
"""

from __future__ import annotations

import html
import json
import statistics
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

E = html.escape


# ---------------------------------------------------------------------------
# numbers
# ---------------------------------------------------------------------------


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "–"


def money(x):
    if x is None:
        return "–"
    return f"${x:.4f}" if x < 0.1 else f"${x:.2f}"


def secs(x):
    if x is None:
        return "–"
    return f"{x:.0f}s" if x >= 10 else f"{x:.1f}s"


def q(values, p):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def norm(t: str) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in (t or "").casefold()).split())


def jaccard(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def all_meter(r: dict) -> list[dict]:
    """Every billed call, from every part of the run."""
    out = []
    for s in r.get("pipeline", []):
        out += [{**c, "part": "pipeline"} for c in s.get("meter", [])]
    for s in r.get("repeats", []):
        out += [{**c, "part": "repeats"} for c in s.get("meter", [])]
    for s in r.get("resolver_probes", []):
        out += [{**c, "part": "resolver probes"} for c in s.get("meter", [])]
    for s in r.get("material", []):
        out += [{**c, "part": "material"} for c in s.get("meter", [])]
    out += [{**c, "part": "grader"} for c in r.get("grader_meter", [])]
    return out


# ---------------------------------------------------------------------------
# chart primitives (inline SVG, tokens from CSS)
# ---------------------------------------------------------------------------


def hbars(rows, *, width=640, unit="", color="var(--s1)", label_w=210, fmt=str, max_value=None):
    """rows: (label, value, tooltip, [color])"""
    if not rows:
        return "<p class=muted>Nothing to show.</p>"
    h_row, gap = 22, 6
    top = max_value or max(v for _, v, *_ in rows) or 1
    plot_w = width - label_w - 70
    h = len(rows) * (h_row + gap)
    parts = [f'<svg viewBox="0 0 {width} {h}" class=chart role=img>']
    for i, row in enumerate(rows):
        label, value, tip = row[0], row[1], row[2]
        c = row[3] if len(row) > 3 else color
        y = i * (h_row + gap)
        w = max(2, plot_w * value / top) if value else 0
        parts.append(
            f'<g data-tip="{E(tip)}"><rect x=0 y={y} width={width} height={h_row} fill=transparent />'
            f'<text x={label_w - 8} y={y + 15} text-anchor=end class=lbl>{E(label)}</text>'
            f'<rect x={label_w} y={y + 3} width={w:.1f} height={h_row - 6} rx=3 fill="{c}" />'
            f'<text x={label_w + w + 6} y={y + 15} class=val>{E(fmt(value))}{unit}</text></g>'
        )
    parts.append("</svg>")
    return "".join(parts)


def strip(groups, *, width=640, label_w=150, log=False, unit="s", ticks=None):
    """groups: (label, [(value, tooltip, color)]). A dot per call, per stage."""
    import math

    vals = [v for _, pts in groups for v, *_ in pts if v is not None]
    if not vals:
        return "<p class=muted>No timed calls.</p>"
    lo, hi = (max(min(vals), 0.3), max(vals)) if log else (0, max(vals))
    plot_w = width - label_w - 20

    def x(v):
        if log:
            return label_w + plot_w * (math.log10(max(v, lo)) - math.log10(lo)) / ((math.log10(hi) - math.log10(lo)) or 1)
        return label_w + plot_w * v / (hi or 1)

    row_h = 34
    h = len(groups) * row_h + 26
    parts = [f'<svg viewBox="0 0 {width} {h}" class=chart role=img>']
    ticks = ticks or ([1, 3, 10, 30, 100, 300, 1000] if log else None)
    if ticks:
        for t in ticks:
            if lo <= t <= hi * 1.001:
                parts.append(f'<line x1={x(t):.1f} x2={x(t):.1f} y1=0 y2={h - 20} class=grid />'
                             f'<text x={x(t):.1f} y={h - 6} text-anchor=middle class=tick>{t}{unit}</text>')
    for i, (label, pts) in enumerate(groups):
        y = i * row_h + row_h / 2
        parts.append(f'<text x={label_w - 10} y={y + 4} text-anchor=end class=lbl>{E(label)}</text>')
        med = q([v for v, *_ in pts], 0.5)
        for j, (v, tip, c) in enumerate(pts):
            if v is None:
                continue
            jitter = ((j * 7) % 11 - 5) * 1.6
            parts.append(f'<circle cx={x(v):.1f} cy={y + jitter:.1f} r=4.5 fill="{c}" class=dot data-tip="{E(tip)}" />')
        if med is not None:
            parts.append(f'<line x1={x(med):.1f} x2={x(med):.1f} y1={y - 12} y2={y + 12} class=median />'
                         f'<text x={x(med) + 4:.1f} y={y - 9} class=tick>p50 {secs(med)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def scatter(points, *, width=640, height=280, xlab="", ylab="", xfmt=str, yfmt=str):
    """points: (x, y, tooltip, color)."""
    if not points:
        return "<p class=muted>Nothing to show.</p>"
    left, bottom, top_pad, right = 56, 34, 10, 16
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    xmax, ymax = max(xs) * 1.05 or 1, max(ys) * 1.1 or 1
    pw, ph = width - left - right, height - bottom - top_pad

    def X(v):
        return left + pw * v / xmax

    def Y(v):
        return top_pad + ph - ph * v / ymax

    parts = [f'<svg viewBox="0 0 {width} {height}" class=chart role=img>']
    for k in range(5):
        yv = ymax * k / 4
        parts.append(f'<line x1={left} x2={width - right} y1={Y(yv):.1f} y2={Y(yv):.1f} class=grid />'
                     f'<text x={left - 6} y={Y(yv) + 4:.1f} text-anchor=end class=tick>{E(yfmt(yv))}</text>')
        xv = xmax * k / 4
        parts.append(f'<text x={X(xv):.1f} y={height - 16} text-anchor=middle class=tick>{E(xfmt(xv))}</text>')
    parts.append(f'<text x={left + pw / 2} y={height - 2} text-anchor=middle class=axis>{E(xlab)}</text>')
    parts.append(f'<text x=12 y={top_pad + ph / 2} transform="rotate(-90 12 {top_pad + ph / 2})" text-anchor=middle class=axis>{E(ylab)}</text>')
    for x, y, tip, c in points:
        parts.append(f'<circle cx={X(x):.1f} cy={Y(y):.1f} r=5 fill="{c}" class=dot data-tip="{E(tip)}" />')
    parts.append("</svg>")
    return "".join(parts)


def tile(label, value, sub=""):
    return f'<div class=tile><div class=tl>{E(label)}</div><div class=tv>{E(str(value))}</div><div class=ts>{E(sub)}</div></div>'


def table(head, rows, cls=""):
    th = "".join(f"<th>{E(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class=tablewrap><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def badge(text, kind):
    return f'<span class="badge {kind}">{E(text)}</span>'


def chips(terms, kind=""):
    return " ".join(f'<span class="chip {kind}">{E(t)}</span>' for t in terms) or '<span class=muted>none</span>'


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------


def short(sid):
    return sid if sid.startswith("sess-") else sid[:8]


def section_overview(r, findings):
    pipe = r.get("pipeline", [])
    meter = all_meter(r)
    pipe_meter = [c for c in meter if c["part"] == "pipeline"]
    cost = sum(c.get("cost") or 0 for c in meter)
    pipe_cost = sum(c.get("cost") or 0 for c in pipe_meter)
    failed = [s for s in pipe if not s.get("ok")]
    filed = sum(len(s.get("filed", [])) for s in pipe)
    clean = sum(1 for s in pipe if s.get("ok") and not s.get("filed"))
    det = [c for c in pipe_meter if c.get("purpose") == "detection"]
    tiles = [
        tile("sessions analysed", len(pipe), f"{len(failed)} failed · {clean} clean"),
        tile("gaps filed", filed, f"{filed / max(1, len(pipe) - len(failed)):.2f} per session (budget 2)"),
        tile("pipeline wall time", secs(r.get("pipeline_seconds")), f"{r['config']['concurrency']} chunk calls × 6 sessions at once"),
        tile("detection call p50", secs(q([(c.get("duration_ms") or 0) / 1000 for c in det], 0.5)),
             f"p90 {secs(q([(c.get('duration_ms') or 0) / 1000 for c in det], 0.9))}"),
        tile("pipeline cost", money(pipe_cost), f"whole run incl. probes {money(cost)}"),
    ]
    cfg = r["config"]
    items = "".join(f"<li>{f}</li>" for f in findings)
    return f"""
<section id=overview>
<h2>At a glance</h2>
<p class=muted>Model <code>{E(cfg['model'])}</code> · reasoning effort <code>{E(str(cfg['effort']))}</code> ·
max_tokens {cfg['max_tokens']} · detector prompt <code>{E(cfg['detector_prompt'])}</code> · run {E(r['started'])}</p>
<div class=tiles>{''.join(tiles)}</div>
<h3>What stood out</h3>
<ol class=findings>{items}</ol>
</section>"""


def section_funnel(r):
    pipe = [s for s in r.get("pipeline", []) if s.get("ok")]
    shapes = r["shapes"]
    det_rows = [c for c in r["calls"] if c["stage"] == "detection"]
    proposed = sum(len(c.get("returned") or []) for c in det_rows if c.get("ok"))
    ranked = [c for s in pipe for c in s["ranked"]]
    gaps = [c for c in ranked if c["signal"] == "accepted"]
    unclear = [c for c in ranked if c["signal"] == "unclear"]
    detector_emitted = sum(len(s["detector_emitted"]) for s in pipe)
    triage = r.get("triage", [])
    held = sum(1 for t in triage if t["verdict"] == "held_back")
    filed = sum(len(s["filed"]) for s in pipe)
    res = [x for s in pipe for x in s["resolutions"]]
    dec = Counter(x["decision"] for x in res)
    steps = [
        ("sessions", len(pipe), "sessions the pipeline finished"),
        ("windows", sum(shapes[s["session_id"]]["windows"] for s in pipe), "assistant turn + next human turn pairs"),
        ("chunk calls", sum(s.get("calls", 0) for s in pipe), "one detector model call per chunk"),
        ("proposed", proposed, "raw candidates returned by the model, before any filter"),
        ("ranked", len(ranked), "survived coercion: real line, known signal, not 'questioned', deduped"),
        ("accepted (gaps)", len(gaps), f"signal = accepted. {len(unclear)} more were 'unclear' (no next turn) and are never filed"),
        ("gaps after triage", len(gaps) - held, f"{held} held back as 'already knows' by Jev"),
        ("filed", filed, f"after the 2-per-session budget · resolver: {dict(dec)}"),
    ]
    ramp = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]
    rows = [(n, v, f"{n}: {v} — {why}", ramp[i]) for i, (n, v, why) in enumerate(steps[3:])]
    return f"""
<section id=funnel>
<h2>The funnel</h2>
<p>From the {steps[2][1]} detector calls over {steps[1][1]} windows in {steps[0][1]} finished sessions, candidates narrow
stage by stage. Most of the narrowing is the detector itself: 'questioned' terms are dropped, 'unclear' ones
(no human reply after them) are ranked but never filed.</p>
{hbars(rows, label_w=150)}
{table(['stage', 'count', 'what it is'], [[E(n), f'<b>{v}</b>', E(w)] for n, v, w in steps])}
</section>"""


def section_latency(r):
    rows = r["calls"]
    colors = {"detection": "var(--s1)", "familiarity": "var(--s3)", "resolution": "var(--s2)",
              "detection-repeat": "var(--s1)", "resolver-probe": "var(--s2)",
              "material-search": "var(--s7)", "material-write": "var(--s5)"}
    groups = []
    for stage in ["detection", "detection-repeat", "familiarity", "resolution", "resolver-probe",
                  "material-search", "material-write"]:
        pts = []
        for c in rows:
            if c["stage"] != stage:
                continue
            tip = f"{stage} · {secs(c.get('seconds'))}"
            if c.get("prompt_chars"):
                tip += f" · prompt {c['prompt_chars']:,} chars"
            if c.get("session_id"):
                tip += f" · {short(c['session_id'])}"
            if c.get("term") or c.get("input"):
                tip += f" · {c.get('term') or c.get('input')}"
            if not c.get("ok"):
                tip += f" · FAILED {c.get('error', '')[:120]}"
            pts.append((c.get("seconds"), tip, colors[stage] if c.get("ok") else "var(--crit)"))
        if pts:
            groups.append((f"{stage} ({len(pts)})", pts))
    grader = r.get("grader", [])
    for g in ("jev", "llm"):
        pts = [(x["seconds"], f"grader {g} · {x['term']} · {x['expected']} → {x.get('level')} · {secs(x['seconds'])}",
                "var(--s4)" if x.get("ok") else "var(--crit)") for x in grader if x["grader"] == g]
        if pts:
            groups.append((f"grader-{g} ({len(pts)})", pts))

    meter = all_meter(r)
    by = defaultdict(list)
    for c in meter:
        by[c.get("purpose")].append(c)
    trows = []
    for purpose, cs in sorted(by.items(), key=lambda kv: -sum(c.get("cost") or 0 for c in kv[1])):
        durs = [(c.get("duration_ms") or 0) / 1000 for c in cs if c.get("duration_ms")]
        fails = [c for c in cs if not c.get("ok", True)]
        fin = Counter(c.get("finish_reason") for c in cs if c.get("finish_reason"))
        trows.append([
            E(purpose or "?"), str(len(cs)), f"{len(fails)} ({pct(len(fails), len(cs))})",
            secs(q(durs, 0.5)), secs(q(durs, 0.9)), secs(max(durs) if durs else None),
            f"{sum(c.get('prompt_tokens') or 0 for c in cs):,}",
            f"{sum(c.get('completion_tokens') or 0 for c in cs):,}",
            f"{sum(c.get('reasoning_tokens') or 0 for c in cs):,}",
            money(sum(c.get("cost") or 0 for c in cs)),
            E(", ".join(f"{k}×{v}" for k, v in fin.most_common())),
        ])

    hist = r.get("historic", {}).get("calls", [])
    hrows = []
    hb = defaultdict(list)
    for c in hist:
        hb[c.get("purpose")].append(c)
    for purpose, cs in hb.items():
        durs = [(c.get("duration_ms") or 0) / 1000 for c in cs if c.get("duration_ms")]
        fails = [c for c in cs if not c.get("ok", True)]
        hrows.append([E(purpose), str(len(cs)), f"{len(fails)} ({pct(len(fails), len(cs))})",
                      secs(q(durs, 0.5)), secs(q(durs, 0.9)), secs(max(durs) if durs else None),
                      money(sum(c.get("cost") or 0 for c in cs)),
                      E("; ".join(sorted({(c.get('error') or '')[:70] for c in fails})[:3]))])
    return f"""
<section id=latency>
<h2>Where the time goes</h2>
<p>One dot per model call, log scale. Red dots failed. The detector dominates: it reads whole transcripts,
and every other stage waits on it.</p>
{strip(groups, log=True, width=720)}
<h3>Billed calls, by purpose (this run)</h3>
<p class=muted>“failed” here is a billed call that did not return an answer. For detection these are all
<code>finish=length</code>: the model spent the whole 8,000-token ceiling reasoning, and the forced retry (reasoning off,
its own notes attached) then answered. None of the session failures were model failures.</p>
{table(['purpose', 'calls', 'failed', 'p50', 'p90', 'max', 'prompt tok', 'completion tok', 'reasoning tok', 'cost', 'finish'], trows, 'num')}
<h3>For comparison: your real <code>~/.unrot</code> log (read-only)</h3>
{table(['purpose', 'calls', 'failed', 'p50', 'p90', 'max', 'cost', 'errors'], hrows, 'num')}
</section>"""


def section_sessions(r):
    pipe = r.get("pipeline", [])
    shapes = r["shapes"]
    det = defaultdict(list)
    for c in r["calls"]:
        if c["stage"] == "detection":
            det[c["session_id"]].append(c)
    pts = []
    for c in r["calls"]:
        if c["stage"] in ("detection", "detection-repeat") and c.get("prompt_chars"):
            pts.append((c["prompt_chars"] / 1000, c["seconds"],
                        f"{short(c['session_id'])} · {c['prompt_chars']:,} chars · {secs(c['seconds'])}"
                        + ("" if c.get("ok") else " · FAILED"),
                        "var(--s1)" if c.get("ok") else "var(--crit)"))
    rows = []
    for s in sorted(pipe, key=lambda s: -sum(shapes[s["session_id"]]["prompt_chars"] or [0])):
        sh = shapes[s["session_id"]]
        status = badge("ok", "good") if s.get("ok") else badge("failed", "crit")
        ranked = s.get("ranked", [])
        sig = Counter(c["signal"] for c in ranked)
        held = [h["term"] for h in s.get("held_back", [])]
        rows.append([
            f'<code>{E(short(s["session_id"]))}</code><div class=muted>{E(sh["project"])}</div>',
            status, str(sh["windows"]), f"{sh['unpaired_windows']}",
            " + ".join(f"{p // 1000}k" for p in sh["prompt_chars"]) or "–",
            secs(s.get("seconds")),
            f"{sig.get('accepted', 0)} / {sig.get('unclear', 0)}",
            (chips(s.get("filed", []), "gap") if s.get("filed") or not held else "") + (" " + chips(held, "held") if held else ""),
            chips([c["term"] for c in ranked if c["term"] not in s.get("filed", []) and c["term"] not in held], "dim"),
            E(s.get("error", "")[:160]),
        ])
    return f"""
<section id=sessions>
<h2>Detection, session by session</h2>
<p>Latency against prompt size, one dot per detector call (main pass and repeats). If time tracked size,
the dots would climb left to right.</p>
{scatter(pts, xlab='prompt size (k chars)', ylab='seconds', xfmt=lambda v: f'{v:.0f}k', yfmt=lambda v: f'{v:.0f}s')}
<h3>Every session</h3>
<p class=muted>Filed <span class="chip gap">like this</span>, held back by triage <span class="chip held">like this</span>,
found but not filed (unclear, or over budget) <span class="chip dim">like this</span>. Largest prompts first.</p>
{table(['session', '', 'windows', 'no reply', 'prompt', 'time', 'accepted / unclear', 'filed', 'ranked, not filed', 'error'], rows)}
</section>"""


def section_quality(r):
    labels = json.loads((Path(__file__).parent.parent / "20260923-PR-33-bench-runner" / "labelled" / "labels.json").read_text())["sessions"]
    reps = r.get("repeats", [])
    by = defaultdict(list)
    for x in reps:
        by[x["session_id"]].append(x)
    # main pass counts as one more repeat
    for s in r.get("pipeline", []):
        if s["session_id"] in by and s.get("ok"):
            by[s["session_id"]].append({"repeat": "main", "ok": True, "ranked": s["ranked"],
                                        "emitted": s["detector_emitted"]})
    rows, stab = [], []
    for sid, runs in by.items():
        sets = [frozenset(norm(t) for t in x.get("emitted", [])) for x in runs if x.get("ok")]
        pairs = list(combinations(sets, 2))
        j = statistics.mean(jaccard(a, b) for a, b in pairs) if pairs else None
        all_terms = Counter(t for s in sets for t in s)
        stab.append((short(sid), j or 0, f"{short(sid)} · mean pairwise Jaccard {j:.2f} over {len(sets)} runs" if j is not None else "n/a",
                     "var(--s1)" if not sid.startswith("sess-") else "var(--s7)"))
        lab = labels.get(sid)
        pr = ""
        if lab:
            gold = {norm(a) for g in lab["gaps"] for a in [g["term"], *g["aliases"]]}
            not_gap = {norm(a) for g in lab["not_gaps"] for a in [g["term"], *g["aliases"]]}
            tp = sum(1 for s in sets for t in s if t in gold)
            fp_known = sum(1 for s in sets for t in s if t in not_gap)
            unl = sum(1 for s in sets for t in s if t not in gold and t not in not_gap)
            flagged = sum(len(s) for s in sets)
            found = sum(len({g["term"] for g in lab["gaps"] if {norm(a) for a in [g["term"], *g["aliases"]]} & s}) for s in sets)
            recall = f"{found}/{len(lab['gaps']) * len(sets)}" if lab["gaps"] else "n/a"
            pr = f"✓ {tp} · ✗ {fp_known} · ? {unl} of {flagged} flags · gaps found {recall} (summed over runs)"
        per_run = "<br>".join(
            f"<span class=muted>{x['repeat']}:</span> " + (chips(x.get("emitted", []), "gap") if x.get("ok") else badge("failed", "crit"))
            for x in runs)
        rows.append([f"<code>{E(short(sid))}</code>", f"{j:.2f}" if j is not None else "–",
                     E(pr) or "<span class=muted>unlabelled</span>", per_run,
                     E(", ".join(f"{t}×{n}" for t, n in all_terms.most_common()))])
    return f"""
<section id=quality>
<h2>Detection quality: does it say the same thing twice?</h2>
<p>Each session below was detected {max((len(v) for v in by.values()), default=0)} times (repeats + the main pass).
Stability is the mean pairwise Jaccard of the <em>emitted</em> terms (exact, case-folded — so "outbox" vs
"outbox pattern" counts as different). The two <code>sess-</code> sessions are the PR-33 synthetic labelled pair:
✓ labelled gap, ✗ labelled not-a-gap, ? unlabelled.</p>
{hbars(stab, fmt=lambda v: f'{v:.2f}', max_value=1, label_w=110)}
{table(['session', 'Jaccard', 'vs labels', 'emitted per run', 'term counts'], rows)}
</section>"""


def section_triage(r):
    tri = r.get("triage", [])
    persona = r.get("persona", {})
    if not tri:
        return "<section id=triage><h2>Triage</h2><p>No familiarity judgments recorded.</p></section>"
    cut = next((t.get("cut") for t in tri if t.get("cut") is not None), None)
    source = Counter(t.get("cut_source") for t in tri).most_common(1)[0][0]
    width, h, left = 700, 70 + 18 * 0, 20
    pw = width - 2 * left
    parts = [f'<svg viewBox="0 0 {width} 90" class=chart role=img>']
    for k in range(0, 11, 2):
        x = left + pw * k / 10
        parts.append(f'<line x1={x} x2={x} y1=10 y2=60 class=grid /><text x={x} y=78 text-anchor=middle class=tick>{k / 10:.1f}</text>')
    if cut is not None:
        x = left + pw * cut
        parts.append(f'<line x1={x} x2={x} y1=4 y2=64 class=cut /><text x={x + 4} y=12 class=tick>cut {cut:.2f} ({E(source)})</text>')
    for i, t in enumerate(tri):
        x = left + pw * (t.get("p_knows") or 0)
        y = 36 + ((i * 7) % 9 - 4) * 4
        c = {"held_back": "var(--s2)", "spot_check": "var(--s4)", "passed": "var(--s1)"}.get(t["verdict"], "var(--muted)")
        parts.append(f'<circle cx={x:.1f} cy={y} r=5 fill="{c}" class=dot data-tip="{E(t["term"])} · p(knows) {t.get("p_knows", 0):.2f} · {t["verdict"]}" />')
    parts.append("</svg>")
    verdicts = Counter(t["verdict"] for t in tri)
    rows = [[E(t["term"]), f"{t.get('p_knows', 0):.2f}", badge(t["verdict"], {"held_back": "warn", "passed": "good", "spot_check": "info"}.get(t["verdict"], "")),
             f"<code>{E(short(t['session_id']))}</code>"] for t in sorted(tri, key=lambda t: -(t.get("p_knows") or 0))]
    return f"""
<section id=triage>
<h2>Triage: would this person already know it?</h2>
<p>Your real store has 3 dismissals and 0 confirmations, so on your machine triage currently passes everything
(“no map”). To see it work, this run seeded a <b>synthetic persona</b>: {len(persona.get('known', []))} known,
{len(persona.get('unknown', []))} confirmed-unknown (known includes your 3 real dismissals; the rest are
assumptions). Each gap is placed on p(knows); at or right of the cut it is held back.</p>
<div class=legend><span><i style="background:var(--s1)"></i>passed</span><span><i style="background:var(--s2)"></i>held back</span><span><i style="background:var(--s4)"></i>spot check</span></div>
{''.join(parts)}
<p class=muted>{dict(verdicts)} · {len(tri)} judged · {sum(1 for c in r['calls'] if c['stage'] == 'familiarity')} Jev calls ·
{len({t['map_fingerprint'] for t in tri})} different map fingerprints, so the cut was calibrated that many times.
Cut by fingerprint: {E(', '.join(sorted({f"{t['map_fingerprint'][:6]} → {t['cut'] if t['cut'] is not None else 'none'} ({t['cut_source']})" for t in tri})))}</p>
{table(['term', 'p(knows)', 'verdict', 'session'], rows)}
<details><summary>The persona map</summary>
<p><b>Known:</b> {chips([k for k, _ in persona.get('known', [])])}</p>
<p><b>Confirmed unknown:</b> {chips([k for k, _ in persona.get('unknown', [])])}</p></details>
</section>"""


def section_resolver(r):
    pipe = [s for s in r.get("pipeline", []) if s.get("ok")]
    res = [x for s in pipe for x in s["resolutions"]]
    dec = Counter((x["decision"], x["without_model"]) for x in res)
    probes = r.get("resolver_probes", [])
    by = defaultdict(list)
    for p in probes:
        by[p["input"]].append(p)
    rows = []
    for text, ps in by.items():
        p0 = ps[0]
        got = " / ".join(f"{p.get('decision', 'error')}{(' → ' + p['target']) if p.get('target') else ''}" for p in ps)
        ok = all(p["correct"] for p in ps)
        consistent = len({(p.get("decision"), p.get("target")) for p in ps}) == 1
        rows.append([f"<b>{E(text)}</b>", E(" | ".join(p0["expected"])) + (f" → {E(p0['expected_target'])}" if p0["expected_target"] else ""),
                     E(got), badge("as expected", "good") if ok else badge("wrong", "crit"),
                     "" if consistent else badge("flip-flops", "warn"),
                     E((p0.get("reasoning") or p0.get("error") or "")[:220])])
    correct = sum(p["correct"] for p in probes)
    live = table(["decision", "string match (no model)", "count"],
                 [[E(d), "yes" if w else "no", str(n)] for (d, w), n in dec.most_common()])
    return f"""
<section id=resolver>
<h2>Resolver: is this a concept we already have?</h2>
<p>In the main pass the resolver saw each filed gap once:</p>
{live}
<h3>Near-duplicate probes ({correct}/{len(probes)} as expected)</h3>
<p>Each probe is resolved against a fresh copy of the same 8-concept graph, twice. The expected decisions are
my judgment of what a person would want — worth arguing with before any of these become tests.</p>
<p><b>Caveat, and the reason the live pass still made a duplicate:</b> 8 concepts is fewer than the 12-item shortlist, so
the model always saw the right answer here. On a 42-concept graph (your real 34 + these 8), word-overlap
ranking drops the target for <code>idempotent</code>, <code>UDS</code> and <code>notarisation</code> — the model can't pick
what it isn't shown. The model's judgment is good; the menu is the weak link.</p>
{table(['submitted', 'expected', 'got (run 1 / run 2)', '', '', 'reasoning (run 1)'], rows)}
</section>"""


def section_material(r):
    mats = r.get("material", [])
    if not mats:
        return ""
    cards = []
    for m in mats:
        srcs = m.get("sources", [])
        kinds = Counter((s["kind"], s["verified"]) for s in srcs)
        src_rows = "".join(
            f"<li>{badge('verified', 'good') if s['verified'] else badge('unverified', 'warn')} <b>{E(s['kind'])}</b> {E(s['title'])}"
            + (f" <span class=muted>— {E(s['note'][:100])}</span>" if s.get("note") else "") + "</li>" for s in srcs)
        outcome = m["outcome"]
        kind = "good" if outcome == "written" else "crit"
        body = f"<blockquote>{E(m.get('body') or '')}</blockquote>" if m.get("body") else f"<p class=muted>{E(m.get('reason', ''))}</p>"
        cards.append(f"""<div class=card><h4>{E(m['term'])} {badge(outcome, kind)}</h4>
<p class=muted>{secs(m['seconds'])} (gather {secs(m.get('gather_seconds'))}) · {len(srcs)} sources,
{sum(1 for s in srcs if s['verified'])} verified · cited {', '.join('S' + c for c in m.get('citations', [])) or '–'}
· {', '.join(f'{k}{"✓" if v else "✗"}×{n}' for (k, v), n in kinds.items())}</p>
{body}<details><summary>sources</summary><ul class=src>{src_rows}</ul></details></div>""")
    return f"""
<section id=material>
<h2>Material: explaining a gap, from sources it can stand behind</h2>
<p>For the {len(mats)} most recently filed real concepts: gather sources (the session itself, your repo's code,
web search then fetch-and-check), write prose citing only verified ones, refuse if nothing verifies.</p>
<div class=cards>{''.join(cards)}</div>
</section>"""


def section_grader(r):
    gr = r.get("grader", [])
    if not gr:
        return ""
    levels = ["isolated", "listed", "causal"]
    blocks = []
    for g in ("jev", "llm"):
        mine = [x for x in gr if x["grader"] == g]
        m = Counter((x["expected"], x.get("level")) for x in mine)
        acc = sum(1 for x in mine if x.get("level") == x["expected"])
        hdr = "".join(f"<th>{l}</th>" for l in levels)
        rows = ""
        for want in levels:
            n_row = sum(m[(want, l)] for l in levels) or 1
            cells = ""
            for l in levels:
                n = m[(want, l)]
                a = n / n_row
                cls = "diag" if want == l else ""
                cells += f'<td class="cm {cls}" style="--a:{a:.2f}">{n}</td>'
            rows += f"<tr><th>{want}</th>{cells}</tr>"
        blocks.append(f"""<div><h4>{'Jev classifier (default)' if g == 'jev' else 'LLM grader'} — {acc}/{len(mine)}</h4>
<table class=cmat><thead><tr><th>written as ↓ / graded →</th>{hdr}</tr></thead><tbody>{rows}</tbody></table></div>""")
    misses = [x for x in gr if x.get("level") != x["expected"]]
    mrows = [[E(x["grader"]), E(x["term"]), E(x["expected"]), E(x.get("level") or x.get("error", "")),
              E(x["answer"]), E((x.get("reasoning") or "")[:200]) or (
                  E(", ".join(f"{k} {v:.2f}" for k, v in (x.get("probabilities") or {}).items())))]
             for x in misses]
    return f"""
<section id=grader>
<h2>Grader: can it tell listed from causal?</h2>
<p>A golden set: for 4 concepts, one answer written to each SOLO level, each graded twice by each grader.
The listed → causal line is the one the rubric says matters.</p>
<div class=pair>{''.join(blocks)}</div>
<h3>Every miss</h3>
{table(['grader', 'concept', 'written as', 'graded', 'answer', 'reasoning / probabilities'], mrows) if mrows else '<p>None.</p>'}
</section>"""


def section_tests(tests):
    rows = "".join(
        f"<tr><td><b>{E(t['name'])}</b><div class=muted>{E(t['stage'])} · {E(t['kind'])}</div></td>"
        f"<td>{t['why']}</td><td>{t['how']}</td></tr>" for t in tests)
    return f"""
<section id=tests>
<h2>Regression tests this suggests</h2>
<p>Each one is anchored to something this run showed. <b>Offline</b> tests use stubbed models and belong
in <code>tests/</code>; <b>eval</b> tests call a real model and belong in a bench (PR-33), asserting a
rate over repeats rather than one answer.</p>
<div class=tablewrap><table><thead><tr><th>test</th><th>why (from this run)</th><th>shape</th></tr></thead><tbody>{rows}</tbody></table></div>
</section>"""


CSS = """
:root{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;
--ring:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s7:#4a3aa7;
--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--info:#2a78d6;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
--grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s7:#9085e9;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1060px;margin:0 auto;padding:24px 16px 80px}
nav{position:sticky;top:0;background:var(--page);border-bottom:1px solid var(--grid);padding:8px 16px;z-index:5;overflow-x:auto;white-space:nowrap}
nav a{color:var(--ink2);margin-right:14px;text-decoration:none;font-size:13px}nav a:hover{color:var(--ink)}
h1{font-size:26px;margin:8px 0 4px}h2{font-size:20px;margin:0 0 8px}h3{font-size:15px;margin:22px 0 6px}h4{margin:0 0 6px;font-size:15px}
section{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:20px;margin:18px 0}
.muted{color:var(--muted)}code{font-size:12.5px;background:var(--page);padding:1px 4px;border-radius:4px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:12px 0}
.tile{border:1px solid var(--ring);border-radius:10px;padding:12px}.tl{font-size:12px;color:var(--ink2)}.tv{font-size:26px;font-weight:600}.ts{font-size:12px;color:var(--muted)}
.chart{width:100%;height:auto;display:block;margin:8px 0}
.chart text{fill:var(--ink2);font-size:12px}.chart .tick{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}.chart .val{fill:var(--ink);font-size:12px}
.chart .grid{stroke:var(--grid);stroke-width:1}.chart .median{stroke:var(--ink);stroke-width:2}.chart .cut{stroke:var(--crit);stroke-width:2}
.chart .dot{stroke:var(--surface);stroke-width:2}.chart g:hover rect[fill=transparent]{fill:var(--page)}
.chart .axis{fill:var(--muted);font-size:11px}
.tablewrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}th{font-weight:600;color:var(--ink2);font-size:12px}
table.num td:not(:first-child){font-variant-numeric:tabular-nums}
.chip{display:inline-block;border:1px solid var(--ring);border-radius:999px;padding:0 8px;margin:1px 0;font-size:12px;white-space:nowrap}
.chip.gap{border-color:var(--s1);color:var(--ink)}.chip.held{border-color:var(--s2);text-decoration:line-through}.chip.dim{color:var(--muted)}
.badge{display:inline-block;font-size:11px;font-weight:600;border-radius:4px;padding:0 6px;border:1px solid}
.badge.good{color:var(--good);border-color:var(--good)}.badge.crit{color:var(--crit);border-color:var(--crit)}.badge.warn{color:#b87a00;border-color:var(--warn)}.badge.info{color:var(--info);border-color:var(--info)}
.findings li{margin:6px 0}
.legend{display:flex;gap:14px;font-size:12px;color:var(--ink2)}.legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.cards{display:grid;gap:12px}.card{border:1px solid var(--ring);border-radius:10px;padding:14px}
blockquote{margin:8px 0;padding:8px 12px;border-left:3px solid var(--s1);background:var(--page);border-radius:0 6px 6px 0;white-space:pre-wrap}
.src{font-size:13px}.pair{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.cmat td.cm{text-align:center;font-variant-numeric:tabular-nums;background:color-mix(in oklab,var(--crit) calc(var(--a)*45%),transparent)}
.cmat td.cm.diag{background:color-mix(in oklab,var(--s1) calc(var(--a)*55%),transparent)}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--surface);font-size:12px;padding:6px 8px;border-radius:6px;max-width:360px;display:none;z-index:9}
details summary{cursor:pointer;color:var(--ink2);font-size:13px}
"""

JS = """
const tip=document.getElementById('tip');
document.addEventListener('mousemove',e=>{const t=e.target.closest('[data-tip]');
if(!t){tip.style.display='none';return}tip.textContent=t.dataset.tip;tip.style.display='block';
const x=Math.min(e.clientX+14,innerWidth-tip.offsetWidth-8);tip.style.left=x+'px';tip.style.top=(e.clientY+14)+'px'});
"""


def build(run_dir: Path, findings: list[str], tests: list[dict]) -> Path:
    r = json.loads((run_dir / "results.json").read_text())
    sections = [
        section_overview(r, findings), section_funnel(r), section_latency(r), section_sessions(r),
        section_quality(r), section_triage(r), section_resolver(r), section_material(r),
        section_grader(r), section_tests(tests),
    ]
    nav = "".join(f'<a href="#{k}">{v}</a>' for k, v in [
        ("overview", "Overview"), ("funnel", "Funnel"), ("latency", "Latency & cost"), ("sessions", "Sessions"),
        ("quality", "Stability"), ("triage", "Triage"), ("resolver", "Resolver"), ("material", "Material"),
        ("grader", "Grader"), ("tests", "Test ideas")])
    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>unrot e2e run</title><style>{CSS}</style></head>
<body><nav>{nav}</nav><main><h1>unrot, end to end, on real sessions</h1>
<p class=muted>{len(r.get('pipeline', []))} sessions from <code>~/.claude/projects</code> + the PR-33 synthetic pair, through the real
LangGraph pipeline in a throwaway <code>$UNROT_HOME</code>. Hover any dot or bar.</p>
{''.join(sections)}</main><div id=tip></div><script>{JS}</script></body></html>"""
    out = run_dir / "report.html"
    out.write_text(page)
    return out


if __name__ == "__main__":
    run = Path(sys.argv[1])
    notes = run / "findings.json"
    if not notes.exists():
        notes = Path(__file__).parent / "findings.json"
    extra = json.loads(notes.read_text()) if notes.exists() else {"findings": [], "tests": []}
    print(build(run, extra["findings"], extra["tests"]))
