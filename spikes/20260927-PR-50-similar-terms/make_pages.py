"""Write the two human rating pages: `out/label.html` (B) and `out/umbrellas.html` (D).

Both are single self-contained files: no network, no external fonts or
scripts. Progress is kept in localStorage (wrapped in try/catch, so a private
window just loses progress on reload), and "Export" downloads a JSON file.

The label page shows only the pair id and the two terms, in `pairs.json`'s
shuffled order -- no strata, no sources, no model output.

    uv run python make_pages.py            # both pages (umbrellas only if out/umbrellas.json exists)
"""

from __future__ import annotations

import html
import json

from common import OUT, read_json

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --bg: #f7f7f5; --panel: #ffffff; --ink: #1d1d1b; --muted: #6b6b66; --line: #e2e1dc;
  --accent: #2f5bd3; --accent-ink: #ffffff; --chosen: #e8eefc; --warn: #b26a00; --warn-bg: #fff4e0;
  --done: #3c8c5a; --kbd: #efeee9;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161615; --panel: #1f1f1d; --ink: #ecebe6; --muted: #a09f98; --line: #34332f;
    --accent: #7c9cf5; --accent-ink: #10131c; --chosen: #25304d; --warn: #f0b457; --warn-bg: #3a2c12;
    --done: #6fc28e; --kbd: #2b2a27;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 20px 16px 60px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 16px; font-size: 14px; }
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }
.progress { font-variant-numeric: tabular-nums; font-weight: 600; }
.progress small { color: var(--muted); font-weight: 400; }
.spacer { flex: 1; }
button { font: inherit; border: 1px solid var(--line); background: var(--panel); color: var(--ink);
  border-radius: 8px; padding: 6px 12px; cursor: pointer; }
button.primary { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
button:focus-visible, .opt:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 18px; }
.pos { color: var(--muted); font-size: 13px; margin-bottom: 10px; font-variant-numeric: tabular-nums; }
.terms { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; margin-bottom: 16px; align-items: baseline; }
.terms .tag { font-weight: 700; color: var(--muted); }
.terms .term { font-size: 22px; font-weight: 600; overflow-wrap: anywhere; }
.parent { font-size: 22px; font-weight: 700; margin-bottom: 8px; overflow-wrap: anywhere; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 12px; }
.chip { border: 1px solid var(--line); border-radius: 999px; padding: 2px 10px; font-size: 14px; background: var(--bg); }
.check { background: var(--bg); border-left: 3px solid var(--line); padding: 8px 12px; margin: 0 0 14px; font-size: 15px; }
.check .q { color: var(--muted); font-size: 13px; margin-bottom: 4px; }
.opts { display: grid; gap: 6px; }
.opt { display: flex; gap: 10px; align-items: flex-start; border: 1px solid var(--line); border-radius: 8px;
  padding: 8px 10px; cursor: pointer; }
.opt input { margin-top: 4px; }
.opt.chosen { background: var(--chosen); border-color: var(--accent); }
.opt .main { font-weight: 600; }
.opt .detail { color: var(--muted); font-size: 14px; }
kbd { background: var(--kbd); border: 1px solid var(--line); border-radius: 4px; padding: 0 5px; font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.extras { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; margin-top: 12px; }
.unsure { display: flex; gap: 6px; align-items: center; cursor: pointer; }
.unsure.on { color: var(--warn); font-weight: 600; }
textarea { width: 100%; min-height: 44px; font: inherit; font-size: 14px; color: var(--ink); background: var(--bg);
  border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; margin-top: 10px; resize: vertical; }
.nav { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
.grid { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 18px; }
.cell { width: 34px; height: 26px; border-radius: 5px; border: 1px solid var(--line); background: var(--panel);
  font-size: 11px; display: flex; align-items: center; justify-content: center; cursor: pointer; color: var(--muted);
  font-variant-numeric: tabular-nums; padding: 0; }
.cell.done { background: var(--done); color: var(--accent-ink); border-color: var(--done); }
.cell.unsure { box-shadow: inset 0 -3px 0 var(--warn); }
.cell.here { outline: 2px solid var(--accent); outline-offset: 1px; }
details { margin-top: 18px; font-size: 14px; color: var(--muted); }
details summary { cursor: pointer; }
details dl { margin: 8px 0 0; }
details dt { font-weight: 600; color: var(--ink); margin-top: 6px; }
details dd { margin: 0 0 0 0; }
.keys { font-size: 13px; color: var(--muted); margin-top: 12px; }
.note { font-size: 13px; color: var(--warn); background: var(--warn-bg); border-radius: 6px; padding: 6px 10px; margin: 8px 0; display: none; }
pre#json { display: none; white-space: pre-wrap; font-size: 12px; background: var(--panel); border: 1px solid var(--line);
  border-radius: 8px; padding: 10px; max-height: 300px; overflow: auto; }
@media (max-width: 480px) { .terms .term, .parent { font-size: 19px; } }
</style>
</head>
<body>
<main>
  <h1>__TITLE__</h1>
  <p class="sub">__INTRO__</p>
  <div class="bar">
    <span class="progress" id="progress"></span>
    <span class="spacer"></span>
    <button id="showjson" type="button">Show JSON</button>
    <button id="export" class="primary" type="button">Export __EXPORT__</button>
  </div>
  <div class="note" id="storagenote">Progress can't be saved in this browser (storage is blocked), so export before closing the tab.</div>
  <pre id="json"></pre>
  <section class="card" id="card" aria-live="polite"></section>
  <div class="nav">
    <button id="prev" type="button">&larr; Prev</button>
    <button id="next" type="button">Next &rarr;</button>
    <button id="nextopen" type="button">Next unrated</button>
  </div>
  <p class="keys"><kbd>1</kbd>&ndash;<kbd>__NOPTS__</kbd> choose and move on &middot; <kbd>U</kbd> unsure / ambiguous &middot;
    <kbd>N</kbd> note (<kbd>Esc</kbd> to leave it) &middot; <kbd>&larr;</kbd> <kbd>&rarr;</kbd> move &middot; <kbd>Space</kbd> next unrated</p>
  <div class="grid" id="grid"></div>
  <details><summary>What the options mean</summary><dl>__DEFS__</dl></details>
</main>
<script>
const CONFIG = __CONFIG__;
const ITEMS = CONFIG.items;
const OPTS = CONFIG.options;
let state = {};   // id -> {value, unsure, note, updated_at}
let pos = 0;
let storageOk = true;

function load() {
  try {
    const raw = window.localStorage.getItem(CONFIG.storageKey);
    if (raw) state = JSON.parse(raw) || {};
    const p = window.localStorage.getItem(CONFIG.storageKey + ":pos");
    if (p !== null) pos = Math.min(Math.max(0, parseInt(p, 10) || 0), ITEMS.length - 1);
  } catch (e) { storageOk = false; state = {}; }
}
function save() {
  try {
    window.localStorage.setItem(CONFIG.storageKey, JSON.stringify(state));
    window.localStorage.setItem(CONFIG.storageKey + ":pos", String(pos));
  } catch (e) { storageOk = false; }
  document.getElementById("storagenote").style.display = storageOk ? "none" : "block";
}
function esc(s) { return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function fill(t, item) { return t.replace(/\{a\}/g, "“" + item.a + "”").replace(/\{b\}/g, "“" + item.b + "”"); }
function entry(id) { return state[id] || (state[id] = {value: null, unsure: false, note: ""}); }
function done(id) { return !!(state[id] && state[id].value); }

function renderItem(item) {
  if (CONFIG.kind === "pair") {
    return '<div class="terms"><span class="tag">A</span><span class="term">' + esc(item.a) +
      '</span><span class="tag">B</span><span class="term">' + esc(item.b) + '</span></div>';
  }
  const kids = item.children.map(c => '<span class="chip">' + esc(c) + '</span>').join("");
  return '<div class="parent">' + esc(item.parent) + '</div>' +
    '<div class="pos">Proposed umbrella for:</div><div class="chips">' + kids + '</div>' +
    '<div class="check"><div class="q">' + esc(item.question) + '</div>' + esc(item.check_answer) + '</div>';
}

function render() {
  const item = ITEMS[pos];
  const e = state[item.id] || {value: null, unsure: false, note: ""};
  const opts = OPTS.map((o, i) => {
    const on = e.value === o.value;
    return '<label class="opt' + (on ? ' chosen' : '') + '"><input type="radio" name="opt" value="' + o.value + '"' +
      (on ? ' checked' : '') + '><span><span class="main"><kbd>' + (i + 1) + '</kbd> ' + esc(o.label) + '</span>' +
      (o.detail ? '<br><span class="detail">' + esc(CONFIG.kind === "pair" ? fill(o.detail, item) : o.detail) + '</span>' : '') +
      '</span></label>';
  }).join("");
  document.getElementById("card").innerHTML =
    '<div class="pos">' + (pos + 1) + ' of ' + ITEMS.length + ' &middot; ' + esc(item.id) + '</div>' +
    renderItem(item) + '<div class="opts">' + opts + '</div>' +
    '<div class="extras"><label class="unsure' + (e.unsure ? ' on' : '') + '"><input type="checkbox" id="unsure"' +
    (e.unsure ? ' checked' : '') + '> <kbd>U</kbd> Unsure / ambiguous</label></div>' +
    '<textarea id="note" placeholder="Optional note (N)">' + esc(e.note || "") + '</textarea>';
  document.querySelectorAll('input[name="opt"]').forEach(r => r.addEventListener("change", () => choose(r.value, true)));
  document.getElementById("unsure").addEventListener("change", ev => { entry(item.id).unsure = ev.target.checked; touch(item.id); render(); });
  document.getElementById("note").addEventListener("input", ev => { entry(item.id).note = ev.target.value; touch(item.id); renderMeta(); });
  renderMeta();
}
function renderMeta() {
  const n = ITEMS.filter(i => done(i.id)).length;
  const u = ITEMS.filter(i => state[i.id] && state[i.id].unsure).length;
  document.getElementById("progress").innerHTML = n + " / " + ITEMS.length + " rated <small>&middot; " + u + " unsure</small>";
  document.getElementById("grid").innerHTML = ITEMS.map((it, i) =>
    '<button type="button" class="cell' + (done(it.id) ? ' done' : '') + (state[it.id] && state[it.id].unsure ? ' unsure' : '') +
    (i === pos ? ' here' : '') + '" data-i="' + i + '" title="' + esc(it.id) + '">' + (i + 1) + '</button>').join("");
  document.querySelectorAll(".cell").forEach(c => c.addEventListener("click", () => go(parseInt(c.dataset.i, 10))));
  if (document.getElementById("json").style.display === "block") document.getElementById("json").textContent = JSON.stringify(payload(), null, 1);
}
function touch(id) { entry(id).updated_at = new Date().toISOString(); save(); }
let advanceTimer = null;
function choose(value, advance) {
  const id = ITEMS[pos].id;
  entry(id).value = value; touch(id); render();
  if (advance && pos < ITEMS.length - 1) {
    clearTimeout(advanceTimer);
    advanceTimer = setTimeout(() => go(pos + 1), 180);
  }
}
function go(i) { pos = Math.min(Math.max(0, i), ITEMS.length - 1); save(); render(); }
function nextOpen() {
  for (let k = 1; k <= ITEMS.length; k++) {
    const i = (pos + k) % ITEMS.length;
    if (!done(ITEMS[i].id)) return go(i);
  }
}
function payload() {
  return {
    page: CONFIG.pageId, exported_at: new Date().toISOString(),
    n_items: ITEMS.length, n_rated: ITEMS.filter(i => done(i.id)).length,
    options: OPTS.map(o => o.value),
    ratings: ITEMS.map(it => {
      const e = state[it.id] || {};
      const base = CONFIG.kind === "pair" ? {id: it.id, a: it.a, b: it.b, label: e.value || null}
                                          : {id: it.id, parent: it.parent, children: it.children, rating: e.value || null};
      return Object.assign(base, {unsure: !!e.unsure, note: e.note || "", updated_at: e.updated_at || null});
    })
  };
}
document.getElementById("export").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(payload(), null, 1)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = CONFIG.exportName;
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
});
document.getElementById("showjson").addEventListener("click", () => {
  const el = document.getElementById("json");
  const on = el.style.display !== "block";
  el.style.display = on ? "block" : "none";
  document.getElementById("showjson").textContent = on ? "Hide JSON" : "Show JSON";
  renderMeta();
});
document.getElementById("prev").addEventListener("click", () => go(pos - 1));
document.getElementById("next").addEventListener("click", () => go(pos + 1));
document.getElementById("nextopen").addEventListener("click", nextOpen);
document.addEventListener("keydown", ev => {
  const inNote = ev.target && ev.target.id === "note";
  if (inNote) { if (ev.key === "Escape") ev.target.blur(); return; }
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const n = parseInt(ev.key, 10);
  if (n >= 1 && n <= OPTS.length) { ev.preventDefault(); choose(OPTS[n - 1].value, true); return; }
  const k = ev.key.toLowerCase();
  if (k === "u") { ev.preventDefault(); const id = ITEMS[pos].id; entry(id).unsure = !entry(id).unsure; touch(id); render(); }
  else if (k === "n") { ev.preventDefault(); document.getElementById("note").focus(); }
  else if (ev.key === "ArrowRight" || k === "j") { ev.preventDefault(); go(pos + 1); }
  else if (ev.key === "ArrowLeft" || k === "k") { ev.preventDefault(); go(pos - 1); }
  else if (ev.key === " ") { ev.preventDefault(); nextOpen(); }
});
load(); save(); render();
</script>
</body>
</html>
"""

PAIR_OPTIONS = [
    {"value": "same", "label": "Same",
     "detail": "{a} and {b} are one concept under another name, spelling, word form or abbreviation."},
    {"value": "same_nothing_extra", "label": "Same, nothing extra to learn",
     "detail": "Worded differently or a touch more specific, but explaining {a} already explains {b}, and the reverse."},
    {"value": "b_extends_a", "label": "B extends A (A is broader, or a prerequisite)",
     "detail": "{b} needs knowledge beyond {a}: it is narrower, or builds on it. Knowing {b} gives you {a}, not the reverse."},
    {"value": "a_extends_b", "label": "A extends B (B is broader, or a prerequisite)",
     "detail": "{a} needs knowledge beyond {b}: it is narrower, or builds on it. Knowing {a} gives you {b}, not the reverse."},
    {"value": "related_different", "label": "Related, but different",
     "detail": "Same space, separate things to learn: neither explains the other (alternatives, opposites, neighbours)."},
    {"value": "unrelated", "label": "Unrelated", "detail": "Different subjects."},
]

PAIR_DEFS = [
    ("Same", "One concept. Examples not in this set: Postgres / PostgreSQL, regex / regular expression."),
    ("Same, nothing extra to learn", "Not literally the same string or scope, but a one-or-two-line explanation of one is an explanation of the other. Example: JSON / JSON payload."),
    ("B extends A / A extends B", "One-way. The extended one needs everything in the other plus something more: a narrower case, a specific tool, or something built on top. Example: HTTP / HTTP/2 server push."),
    ("Related, but different", "Same subject, separate concepts. Example: TCP / UDP."),
    ("Unrelated", "Different subjects, even if they appeared in the same session. Example: regex / CORS."),
    ("Unsure / ambiguous", "Tick it alongside your best guess when the pair could reasonably go two ways, or you don't know one of the terms. The note is optional."),
]

UMBRELLA_OPTIONS = [
    {"value": "right_sized", "label": "Right-sized concept",
     "detail": "One card: the check question below has a real answer in a line or two."},
    {"value": "too_broad", "label": "Too broad (a topic)",
     "detail": "An area, not a concept: better as a heatmap domain than a card."},
    {"value": "not_a_unit", "label": "Not really a unit",
     "detail": "These children don't belong under one parent, or the parent is not a thing people learn."},
]

UMBRELLA_DEFS = [
    ("Right-sized concept", "Could be asked as “In a line or two: what is X, and why does it work the way it does?” and answered well. Knowing it would NOT mean you know each child."),
    ("Too broad (a topic)", "The honest answer to the check would be a chapter, not two lines."),
    ("Not really a unit", "The grouping is an accident of one session, or the parent name is made up."),
    ("The check answer", "Written by the model for context only. Rate the parent, not the answer's quality."),
]


def page(*, title, intro, page_id, storage_key, export_name, kind, options, defs, items) -> str:
    config = {
        "pageId": page_id, "storageKey": storage_key, "exportName": export_name,
        "kind": kind, "options": options, "items": items,
    }
    defs_html = "".join(f"<dt>{html.escape(t)}</dt><dd>{html.escape(d)}</dd>" for t, d in defs)
    # `</` inside the JSON would close the script tag; escape it.
    config_js = json.dumps(config, ensure_ascii=False).replace("</", "<\\/")
    return (
        TEMPLATE.replace("__TITLE__", html.escape(title))
        .replace("__INTRO__", intro)
        .replace("__EXPORT__", html.escape(export_name))
        .replace("__NOPTS__", str(len(options)))
        .replace("__DEFS__", defs_html)
        .replace("__CONFIG__", config_js)
    )


def main() -> None:
    pairs = read_json(OUT / "pairs.json")
    (OUT / "label.html").write_text(
        page(
            title="Similar terms: label pairs",
            intro=(
                "For each pair, how do A and B relate? Go with your first read; if it could go two ways, "
                "pick one and tick <b>unsure</b>. When done, press <b>Export</b> and save the file as "
                "<code>out/labels.json</code> in this spike folder."
            ),
            page_id="pr50-label",
            storage_key="pr50-label-v1",
            export_name="labels.json",
            kind="pair",
            options=PAIR_OPTIONS,
            defs=PAIR_DEFS,
            items=[{"id": p["id"], "a": p["a"], "b": p["b"]} for p in pairs],
        )
    )
    print("wrote", OUT / "label.html", len(pairs), "pairs")

    path = OUT / "umbrellas.json"
    if path.exists():
        umbrellas = read_json(path)["proposals"]
        items = [
            {
                "id": u["id"], "parent": u["parent"], "children": u["children"],
                "question": u["question"], "check_answer": u["check_answer"],
            }
            for u in umbrellas
        ]
        (OUT / "umbrellas.html").write_text(
            page(
                title="Umbrella concepts: rate the parents",
                intro=(
                    "Each card is a parent name the model proposed for terms that came up together in one "
                    "decision window (or, for a few, in one session's detector runs). Is the parent a "
                    "right-sized concept to ask about? When done, press "
                    "<b>Export</b> and save the file as <code>out/umbrella_ratings.json</code>."
                ),
                page_id="pr50-umbrellas",
                storage_key="pr50-umbrellas-v1",
                export_name="umbrella_ratings.json",
                kind="umbrella",
                options=UMBRELLA_OPTIONS,
                defs=UMBRELLA_DEFS,
                items=items,
            )
        )
        print("wrote", OUT / "umbrellas.html", len(items), "umbrellas")


if __name__ == "__main__":
    main()
