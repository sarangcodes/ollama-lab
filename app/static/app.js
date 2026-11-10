"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) el.append(k.nodeType ? k : String(k));
  return el;
};
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

let MODELS = [], SYS = null, THIS_MAC = null;

/* ---------- tabs ---------- */
function showTab(name) {
  $$(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  $$(".tab").forEach(t => t.classList.toggle("on", t.id === `tab-${name}`));
  try { localStorage.setItem("tab", name); } catch {}
  history.replaceState(null, "", `#${name}`);
  if (name === "results") loadResults();
}
$$(".tabs button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));

/* ---------- status bar ---------- */
async function refreshStatus() {
  try {
    const s = await api("/api/status");
    SYS = s.system;
    THIS_MAC = s.machine;
    const el = $("#status");
    el.replaceChildren();
    if (!s.ollama) { el.append(h("span", { class: "pill bad" }, `Ollama not running at ${s.host}`)); return; }
    el.append(
      h("span", { class: "pill" }, `Ollama ${s.ollama}`),
      h("span", { class: "pill" }, `${SYS.chip} · ${SYS.ram_gb} GB`),
      h("span", { class: "pill", title: "Memory in use by everything on the Mac" }, `RAM used ${fmt(SYS.ram_used_gb)} GB`),
      h("span", { class: "pill" + (SYS.swap_used_gb > 2 ? " bad" : ""), title: "Swap means memory is overflowing to disk" }, `Swap ${fmt(SYS.swap_used_gb)} GB`),
      h("span", { class: "pill", title: "How much unified memory macOS lets the GPU use" }, `GPU cap ≈ ${SYS.gpu_limit_gb} GB`),
    );
    for (const m of s.running) {
      el.append(h("span", { class: "pill" }, `▶ ${m.name} ${m.loaded_gb} GB · ${m.gpu_pct}% GPU`,
        h("button", { class: "link", onclick: async () => { await api(`/api/unload?model=${encodeURIComponent(m.name)}`, { method: "POST" }); refreshStatus(); } }, "unload")));
    }
  } catch (e) {
    $("#status").replaceChildren(h("span", { class: "pill bad" }, "Lab server not reachable"));
  }
}

/* ---------- models ---------- */
function fitNote(m) {
  if (!SYS) return "";
  if (m.disk_gb > SYS.gpu_limit_gb) return "too big for GPU";
  if (m.disk_gb > SYS.gpu_limit_gb * 0.85) return "tight fit";
  return "";
}
function modelRow(m, name, checked) {
  const note = fitNote(m);
  return h("label", {},
    h("input", { type: "checkbox", name, value: m.name, checked }),
    h("span", {}, m.name),
    h("span", { class: "meta" + (note ? " warn" : "") }, `${m.parameter_size || "?"} · ${fmt(m.disk_gb)} GB${note ? " · " + note : ""}`));
}
async function loadModels() {
  try { MODELS = await api("/api/models"); } catch (e) { MODELS = []; }
  const keepPlay = new Set($$("#play-models input:checked").map(i => i.value));
  const keepBench = new Set($$("#bench-models input:checked").map(i => i.value));
  if (!keepPlay.size && MODELS.length) keepPlay.add((MODELS.find(m => m.params_b >= 2) || MODELS[0]).name);
  $("#play-models").replaceChildren(...(MODELS.length ? MODELS.map(m => modelRow(m, "pm", keepPlay.has(m.name)))
    : [h("span", { class: "hint" }, "No models yet: download one in the Models tab.")]));
  $("#bench-models").replaceChildren(...MODELS.map(m => modelRow(m, "bm", keepBench.has(m.name))));

  const t = $("#models-table");
  t.replaceChildren(
    h("thead", {}, h("tr", {}, ...["Model", "Family", "Params", "Quant", "Disk GB", "Max context", "Capabilities", "Loaded", ""].map(c => h("th", {}, c)))),
    h("tbody", {}, ...MODELS.map(m => h("tr", {},
      h("td", {}, m.name), h("td", {}, m.family || "–"), h("td", { class: "num" }, m.parameter_size || "–"),
      h("td", {}, m.quant || "–"), h("td", { class: "num" }, fmt(m.disk_gb)),
      h("td", { class: "num" }, m.max_context ? m.max_context.toLocaleString() : "–"),
      h("td", {}, (m.capabilities || []).join(", ")),
      h("td", {}, m.loaded ? `${m.loaded.loaded_gb} GB · ${m.loaded.gpu_pct}% GPU` : ""),
      h("td", {}, m.loaded ? h("button", { onclick: async () => { await api(`/api/unload?model=${encodeURIComponent(m.name)}`, { method: "POST" }); loadModels(); refreshStatus(); } }, "Unload") : "")))));
}

$("#pull").addEventListener("click", async () => {
  const name = $("#pull-name").value.trim();
  if (!name) return;
  $("#pull").disabled = true;
  $("#pull-status").textContent = "Starting…";
  try {
    const r = await fetch("/api/pull", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model: name }) });
    await readLines(r, ev => {
      if (ev.error) { $("#pull-status").textContent = "Error: " + ev.error; return; }
      if (ev.total) $("#pull-bar").style.width = `${(100 * (ev.completed || 0) / ev.total).toFixed(1)}%`;
      $("#pull-status").textContent = ev.status + (ev.total ? ` · ${fmt((ev.completed || 0) / 1e9)} / ${fmt(ev.total / 1e9)} GB` : "");
    });
  } finally { $("#pull").disabled = false; loadModels(); }
});

async function readLines(resp, onEvent, signal) {
  const reader = resp.body.getReader(), dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (line) onEvent(JSON.parse(line));
    }
    if (signal?.aborted) { reader.cancel(); break; }
  }
}

/* ---------- playground ---------- */
// Prompts picked because small and large models tend to answer them differently.
const PRESETS = {
  "Sisters": "Sally has 3 brothers. Each of her brothers has 2 sisters. How many sisters does Sally have?",
  "Jugs": "I have a 6-litre jug and a 12-litre jug. How do I measure exactly 6 litres?",
  "Fake paper": "Summarise the key findings of the 2019 paper 'Quantum Banana Networks for Protein Folding' by Dr. Elena Voss.",
  "S-sentence": "Write one sentence of exactly 8 words where every word starts with the letter S.",
  "Intervals": "Write a Python function merge_intervals(intervals) that merges overlapping intervals, plus three assert-based tests including edge cases.",
  "Explain": "Explain to a 12-year-old how HTTPS keeps my password safe, in under 120 words.",
  "Tone": "Rewrite this to sound friendly but firm: 'You missed the deadline again. Send the report today.'",
};
$("#presets").append(...Object.entries(PRESETS).map(([k, v]) => h("button", { onclick: () => { $("#prompt").value = v; $("#prompt").focus(); } }, k)));
for (const id of ["temperature", "top_p", "top_k", "repeat_penalty"]) {
  $(`#p-${id}`).addEventListener("input", e => { $(`#v-${id}`).textContent = e.target.value; });
}

const convos = {};  // model -> [{role, content}]
let abort = null;

function card(model) {
  let c = $(`.out[data-model="${CSS.escape(model)}"]`);
  if (!c) {
    c = h("div", { class: "panel out", "data-model": model },
      h("div", { class: "out-head" }, h("b", {}, model), h("span", { class: "hint state" }, "")),
      h("div", { class: "turns" }), h("div", { class: "stats" }));
    $("#outputs").append(c);
  }
  return c;
}

function options() {
  const o = {};
  for (const k of ["temperature", "top_p", "repeat_penalty"]) o[k] = parseFloat($(`#p-${k}`).value);
  o.top_k = parseInt($("#p-top_k").value, 10);
  o.num_ctx = parseInt($("#p-num_ctx").value, 10);
  o.num_predict = parseInt($("#p-num_predict").value, 10);
  const seed = $("#p-seed").value;
  if (seed !== "") o.seed = parseInt(seed, 10);
  return o;
}

async function send() {
  const prompt = $("#prompt").value.trim();
  const models = $$("#play-models input:checked").map(i => i.value);
  if (!prompt || !models.length) { $("#play-note").textContent = models.length ? "Type a prompt first." : "Pick a model."; return; }
  $("#play-note").textContent = models.length > 1 ? "Models run one after another so their timings don't interfere." : "";
  $$(".out").forEach(c => { if (!models.includes(c.dataset.model)) c.remove(); });
  $("#send").disabled = true;
  abort = new AbortController();
  const multi = $("#p-multi").checked, sys = $("#p-system").value.trim();
  for (const model of models) {
    if (abort.signal.aborted) break;
    const c = card(model), turns = $(".turns", c);
    if (!multi) { convos[model] = []; turns.replaceChildren(); }
    convos[model] = convos[model] || [];
    convos[model].push({ role: "user", content: prompt });
    turns.append(h("div", { class: "turn user" }, prompt));
    const think = h("details", { class: "thinking", hidden: true }, h("summary", {}, "Thinking…"), h("div", {}));
    const bot = h("div", { class: "turn bot" });
    turns.append(think, bot);
    $(".state", c).textContent = "loading / thinking…";
    $(".stats", c).replaceChildren();
    const messages = sys ? [{ role: "system", content: sys }, ...convos[model]] : convos[model];
    let text = "";
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, signal: abort.signal,
        body: JSON.stringify({ model, messages, options: options(), think: $("#p-think").checked }) });
      if (!r.ok) throw new Error((await r.json()).detail);
      await readLines(r, ev => {
        if (ev.type === "thinking") { think.hidden = false; $("div", think).textContent += ev.text; }
        else if (ev.type === "content") { text += ev.text; bot.textContent = text; $(".state", c).textContent = "writing…"; }
        else if (ev.type === "error") { bot.append(h("div", { class: "err" }, ev.text)); }
        else if (ev.type === "stats") {
          $(".state", c).textContent = ev.done_reason === "length" ? "stopped at max tokens" : "done";
          $("summary", think).textContent = "Thinking";
          $(".stats", c).replaceChildren(
            h("span", {}, "First token ", h("b", {}, fmt(ev.ttft_s, 2)), " s"),
            h("span", {}, "Writing ", h("b", {}, fmt(ev.gen_tok_s)), " tok/s"),
            h("span", {}, "Reading ", h("b", {}, fmt(ev.prompt_tok_s, 0)), " tok/s"),
            h("span", {}, "Tokens ", h("b", {}, ev.gen_tokens)),
            h("span", {}, "Load ", h("b", {}, fmt(ev.load_s, 2)), " s"),
            h("span", {}, "Total ", h("b", {}, fmt(ev.wall_s, 1)), " s"),
            ev.loaded_gb ? h("span", {}, "Memory ", h("b", {}, ev.loaded_gb), " GB · ", h("b", {}, ev.gpu_pct), "% GPU") : null);
        }
      }, abort.signal);
      convos[model].push({ role: "assistant", content: text });
    } catch (e) {
      $(".state", c).textContent = abort.signal.aborted ? "stopped" : "error";
      if (!abort.signal.aborted) bot.append(h("div", { class: "err" }, e.message));
      convos[model].pop();
    }
  }
  $("#send").disabled = false;
  refreshStatus();
}
$("#send").addEventListener("click", send);
$("#stop").addEventListener("click", () => abort?.abort());
$("#clear").addEventListener("click", () => { for (const k in convos) delete convos[k]; $("#outputs").replaceChildren(); });
$("#prompt").addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); });

/* ---------- benchmark ---------- */
let jobId = null, jobTimer = null;
$("#b-all").addEventListener("click", () => $$("#bench-models input").forEach(i => (i.checked = true)));
$("#b-none").addEventListener("click", () => $$("#bench-models input").forEach(i => (i.checked = false)));
$("#b-start").addEventListener("click", async () => {
  const models = $$("#bench-models input:checked").map(i => i.value);
  try {
    const j = await api("/api/bench", { method: "POST", body: JSON.stringify({
      models, num_ctx: +$("#b-ctx").value, think: $("#b-think").checked, speed_runs: +$("#b-runs").value, run_evals: $("#b-evals").checked }) });
    watchJob(j.id);
  } catch (e) { alert(e.message); }
});
$("#b-cancel").addEventListener("click", () => jobId && api(`/api/bench/${jobId}/cancel`, { method: "POST" }));

function watchJob(id) {
  jobId = id;
  $("#job-panel").hidden = false;
  clearInterval(jobTimer);
  const tick = async () => {
    const j = await api(`/api/bench/${id}`);
    $("#job-title").textContent = `Run ${j.id} · ${j.status}` + (j.current ? ` · ${j.current}` : "");
    $("#job-step").textContent = j.step;
    $("#job-bar").style.width = `${(j.progress * 100).toFixed(1)}%`;
    const log = $("#job-log"), atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
    log.textContent = j.log.join("\n");
    if (atEnd) log.scrollTop = log.scrollHeight;
    $("#b-start").disabled = j.status === "running";
    if (!["running", "queued"].includes(j.status)) { clearInterval(jobTimer); loadModels(); refreshStatus(); }
  };
  tick();
  jobTimer = setInterval(tick, 1500);
}

/* ---------- results ---------- */
const COLS = [
  ["model", "Model", r => r.model],
  ["mac", "Mac", r => r.mac || "–"],
  ["params_b", "Params (B)", r => fmt(r.params_b, 1), "num"],
  ["quant", "Quant", r => r.quant || "–"],
  ["disk_gb", "Disk GB", r => fmt(r.disk_gb), "num"],
  ["loaded_gb", "Loaded GB", r => fmt(r.loaded_gb), "num"],
  ["gpu_pct", "On GPU %", r => fmt(r.gpu_pct, 0), "num"],
  ["cold_load_s", "Cold load s", r => fmt(r.cold_load_s), "num"],
  ["ttft_s", "First token s", r => fmt(r.ttft_s, 2), "num"],
  ["prompt_tok_s", "Read tok/s", r => fmt(r.prompt_tok_s, 0), "num"],
  ["gen_tok_s", "Write tok/s", r => fmt(r.gen_tok_s), "num"],
  ["accuracy", "Accuracy %", r => r.accuracy ?? (r.eval_skipped ? `skipped (${r.eval_skipped})` : "–"), "num"],
  ["cat_math", "Math", r => fmt(r.by_category?.math, 0), "num"],
  ["cat_reasoning", "Reason", r => fmt(r.by_category?.reasoning, 0), "num"],
  ["cat_knowledge", "Know", r => fmt(r.by_category?.knowledge, 0), "num"],
  ["cat_code", "Code", r => fmt(r.by_category?.code, 0), "num"],
  ["cat_instructions", "Format", r => fmt(r.by_category?.instructions, 0), "num"],
  ["needle_found", "Finds fact in 4k", r => r.needle_found === undefined ? "–" : r.needle_found ? "yes" : "no"],
  ["avg_answer_s", "Avg answer s", r => fmt(r.avg_answer_s), "num"],
  ["swap_growth_gb", "Swap +GB", r => fmt(r.swap_growth_gb), "num"],
  ["verdict", "Verdict", r => h("span", { class: "badge " + verdictClass(r.verdict) }, r.verdict || "–")],
  ["think", "Thinking", r => r.think === null || r.think === undefined ? "n/a" : String(r.think)],
  ["created_at", "When", r => r.created_at?.replace("T", " ").slice(0, 16)],
];
const ACC_COL = COLS.find(c => c[0] === "accuracy")[2];
let RESULTS = [], sortKey = "params_b", sortDir = 1;
const verdictClass = v => !v ? "" : /^(Fast|Usable)/.test(v) ? "v-good" : /^(Too slow|Failed)/.test(v) ? "v-bad" : "v-warn";
const sortVal = (r, k) => k.startsWith("cat_") ? r.by_category?.[k.slice(4)] : r[k];

async function loadResults() {
  const rows = await api("/api/results" + ($("#r-history").checked ? "?history=true" : ""));
  ALL_RESULTS = rows.map(r => ({ id: r.id, created_at: r.created_at, mac: r.machine?.label, ...r.metrics }));
  // Mac filter: defaults to this Mac; "All Macs" compares machines side by side.
  const macs = [...new Set(ALL_RESULTS.map(r => r.mac).filter(Boolean))].sort();
  const sel = $("#r-mac"), prev = sel.value;
  sel.replaceChildren(h("option", { value: "" }, "All Macs"), ...macs.map(m => h("option", { value: m }, m + (m === THIS_MAC ? " (this Mac)" : ""))));
  if (sel.dataset.touched) sel.value = macs.includes(prev) ? prev : "";
  else sel.value = macs.includes(THIS_MAC) ? THIS_MAC : "";
  applyMacFilter();
}
let ALL_RESULTS = [];
const multiMac = () => new Set(RESULTS.map(r => r.mac)).size > 1;
const label = r => multiMac() ? `${r.model} · ${r.mac}` : r.model;
function applyMacFilter() {
  const mac = $("#r-mac").value;
  RESULTS = mac ? ALL_RESULTS.filter(r => r.mac === mac) : ALL_RESULTS;
  renderTable(); renderTiles(); renderSpeed(); renderAccuracy();
}
$("#r-history").addEventListener("change", loadResults);
$("#r-mac").addEventListener("change", e => { e.target.dataset.touched = "1"; applyMacFilter(); });
$("#r-import").addEventListener("click", () => $("#r-import-file").click());
$("#r-import-file").addEventListener("change", async e => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    const res = await api("/api/results/import", { method: "POST", body: await file.text() });
    $("#r-import-note").textContent = `Imported ${res.added} result(s)` + (res.skipped ? `, ${res.skipped} already here` : "") + ". Pick “All Macs” to compare.";
    loadResults();
  } catch (err) { $("#r-import-note").textContent = `Import failed: ${err.message}`; }
});

function renderTable() {
  const rows = [...RESULTS].sort((a, b) => {
    const x = sortVal(a, sortKey), y = sortVal(b, sortKey);
    if (x === y) return 0; if (x === undefined || x === null) return 1; if (y === undefined || y === null) return -1;
    return (x > y ? 1 : -1) * sortDir;
  });
  $("#results-table").replaceChildren(
    h("thead", {}, h("tr", {}, ...COLS.map(([k, label, , cls]) => h("th", { class: cls, onclick: () => { sortDir = sortKey === k ? -sortDir : 1; sortKey = k; renderTable(); } },
      label + (sortKey === k ? (sortDir > 0 ? " ↑" : " ↓") : ""))))),
    h("tbody", {}, ...(rows.length ? rows.map(r => h("tr", { onclick: () => showDetail(r.id) },
      ...COLS.map(([, , f, cls]) => h("td", { class: cls }, r.error && f === ACC_COL ? "error" : f(r)))))
      : [h("tr", {}, h("td", { colspan: COLS.length, class: "hint" }, "No results yet. Run a benchmark first."))])));
}

function renderTiles() {
  const ok = RESULTS.filter(r => !r.error && r.gen_tok_s);
  const onGpu = ok.filter(r => (r.gpu_pct ?? 0) >= 99.5);
  const best = (arr, f) => arr.reduce((a, b) => (a === null || f(b) > f(a) ? b : a), null);
  const biggest = best(onGpu, r => r.params_b || 0);
  const fastest = best(ok, r => r.gen_tok_s);
  const accurate = best(ok.filter(r => r.accuracy !== null && r.accuracy !== undefined), r => r.accuracy * 1000 + r.gen_tok_s / 1000);
  const sweet = best(onGpu.filter(r => r.gen_tok_s >= 10 && r.accuracy !== null && r.accuracy !== undefined), r => r.accuracy * 1000 + r.gen_tok_s / 1000);
  const tile = (label, r, sub) => h("div", { class: "tile" }, h("div", { class: "label" }, label),
    h("div", { class: "value" }, r ? r.model : "–"), r && multiMac() ? h("div", { class: "sub" }, r.mac) : null, h("div", { class: "sub" }, r ? sub(r) : "run a benchmark"));
  $("#tiles").replaceChildren(
    tile("Biggest model that fits on the GPU", biggest, r => `${fmt(r.params_b)}B params · ${fmt(r.loaded_gb)} GB · ${fmt(r.gen_tok_s)} tok/s`),
    tile("Sweet spot (best accuracy at ≥ 10 tok/s)", sweet, r => `${fmt(r.accuracy, 0)}% accurate · ${fmt(r.gen_tok_s)} tok/s`),
    tile("Most accurate", accurate, r => `${fmt(r.accuracy, 0)}% · ${fmt(r.gen_tok_s)} tok/s`),
    tile("Fastest", fastest, r => `${fmt(r.gen_tok_s)} tok/s · first token ${fmt(r.ttft_s, 2)} s`));
}

/* Charts are hand-built SVG: one series per chart, a hover tooltip on every mark. */
const SVGNS = "http://www.w3.org/2000/svg";
const s = (tag, attrs = {}, text) => { const el = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); if (text !== undefined) el.textContent = text; return el; };
const tip = $("#tip");
function hover(el, html) {
  el.addEventListener("mousemove", e => { tip.hidden = false; tip.innerHTML = html; tip.style.left = `${e.clientX + 12}px`; tip.style.top = `${e.clientY + 12}px`; });
  el.addEventListener("mouseleave", () => (tip.hidden = true));
}
const esc = t => String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
// Round axis ticks: a step of 1, 2, 2.5 or 5 × 10^k, about four ticks to the top.
function niceTicks(v) {
  const raw = (v || 1) / 4, p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * p).find(st => st >= raw);
  const top = Math.ceil((v || 1) / step) * step;
  return { step, top, ticks: Array.from({ length: Math.round(top / step) + 1 }, (_, i) => +(i * step).toFixed(6)) };
}

function renderSpeed() {
  const rows = RESULTS.filter(r => r.gen_tok_s).sort((a, b) => (a.params_b || 0) - (b.params_b || 0));
  const el = $("#chart-speed");
  if (!rows.length) { el.replaceChildren(h("p", { class: "hint" }, "No data yet.")); return; }
  const W = multiMac() ? 680 : 560, L = multiMac() ? 270 : 150, R = 50, rowH = 26, T = 8, H = T + rows.length * rowH + 24;
  const { top: max, ticks } = niceTicks(Math.max(...rows.map(r => r.gen_tok_s), 12));
  const x = v => L + (v / max) * (W - L - R);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Writing speed by model" });
  for (const v of ticks) {
    svg.append(s("line", { x1: x(v), x2: x(v), y1: T, y2: H - 20, class: "gridline" }), s("text", { x: x(v), y: H - 6, "text-anchor": "middle" }, Math.round(v)));
  }
  rows.forEach((r, i) => {
    const y = T + i * rowH, w = Math.max(2, x(r.gen_tok_s) - L);
    svg.append(s("text", { x: L - 8, y: y + rowH / 2 + 4, "text-anchor": "end" }, label(r)));
    // bar with the 4px rounded end away from the baseline
    svg.append(s("path", { d: `M${L},${y + 5}h${w - 4}a4,4 0 0 1 4,4v${rowH - 18}a4,4 0 0 1 -4,4h${-(w - 4)}z`, fill: "var(--s1)", opacity: (r.gpu_pct ?? 100) < 99.5 ? 0.45 : 1 }));
    svg.append(s("text", { x: L + w + 6, y: y + rowH / 2 + 4 }, fmt(r.gen_tok_s)));
    const hit = s("rect", { x: 0, y, width: W, height: rowH, class: "hit" });
    hover(hit, `<b>${esc(label(r))}</b><br>${fmt(r.gen_tok_s)} tok/s writing · ${fmt(r.prompt_tok_s, 0)} tok/s reading<br>first token ${fmt(r.ttft_s, 2)} s · ${fmt(r.gpu_pct, 0)}% on GPU`);
    hit.addEventListener("click", () => showDetail(r.id));
    svg.append(hit);
  });
  svg.append(s("line", { x1: x(10), x2: x(10), y1: T, y2: H - 20, class: "ref" }), s("line", { x1: L, x2: L, y1: T, y2: H - 20, class: "axis" }));
  el.replaceChildren(svg);
}

function renderAccuracy() {
  const rows = RESULTS.filter(r => r.loaded_gb && r.accuracy !== null && r.accuracy !== undefined);
  const el = $("#chart-acc");
  if (!rows.length) { el.replaceChildren(h("p", { class: "hint" }, "No data yet.")); $("#legend-acc").replaceChildren(); return; }
  // Colour follows the model family: the three most common families get a hue each, the rest share grey.
  const counts = {};
  rows.forEach(r => (counts[r.family || "other"] = (counts[r.family || "other"] || 0) + 1));
  const fams = Object.keys(counts).sort((a, b) => counts[b] - counts[a] || a.localeCompare(b)).slice(0, 3).sort();
  const colour = f => (fams.includes(f) ? `var(--s${fams.indexOf(f) + 1})` : "var(--other)");
  const W = 560, L = 40, R = 20, T = 12, B = 34, H = 300;
  const gpu = SYS?.gpu_limit_gb || 16;
  const { top: xmax, ticks: xticks } = niceTicks(Math.max(gpu * 1.15, ...rows.map(r => r.loaded_gb)));
  const x = v => L + (v / xmax) * (W - L - R), y = v => T + (1 - v / 100) * (H - T - B);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Accuracy against memory used" });
  svg.append(s("rect", { x: x(gpu), y: T, width: Math.max(0, x(xmax) - x(gpu)), height: H - T - B, fill: "var(--shade)" }),
    s("text", { x: x(gpu) + 4, y: T + 12 }, `GPU cap ${gpu} GB`));
  for (let v = 0; v <= 100; v += 25) svg.append(s("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "gridline" }), s("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end" }, v));
  for (const v of xticks) svg.append(s("text", { x: x(v), y: H - B + 16, "text-anchor": "middle" }, `${v} GB`));
  svg.append(s("line", { x1: L, x2: W - R, y1: y(0), y2: y(0), class: "axis" }));
  const labelAll = rows.length <= 10;
  rows.forEach(r => {
    const cx = x(r.loaded_gb), cy = y(r.accuracy);
    svg.append(s("circle", { cx, cy, r: 6, fill: colour(r.family || "other"), stroke: "var(--surface)", "stroke-width": 2 }));
    if (labelAll) svg.append(s("text", { x: cx + 9, y: cy - 8 }, label(r)));
    const hit = s("circle", { cx, cy, r: 14, class: "hit" });
    hover(hit, `<b>${esc(label(r))}</b><br>${fmt(r.accuracy, 0)}% accurate · ${fmt(r.loaded_gb)} GB loaded<br>${fmt(r.gen_tok_s)} tok/s · ${esc(r.verdict || "")}`);
    hit.addEventListener("click", () => showDetail(r.id));
    svg.append(hit);
  });
  el.replaceChildren(svg);
  const legend = fams.map(f => h("span", {}, h("i", { style: `background:${colour(f)}` }), f));
  if (Object.keys(counts).some(f => !fams.includes(f))) legend.push(h("span", {}, h("i", { style: "background:var(--other)" }), "other"));
  $("#legend-acc").replaceChildren(...legend);
}

async function showDetail(id) {
  const r = await api(`/api/results/${id}`), m = r.metrics, d = r.details || {};
  const body = $("#detail-body");
  body.replaceChildren(
    h("h2", {}, `${m.model} · ${m.verdict || ""}`),
    h("p", { class: "hint" }, `${r.machine?.label || "unknown Mac"} · GPU cap ${r.machine?.gpu_limit_gb ?? "?"} GB · ${r.created_at} · context ${r.settings.num_ctx} · thinking ${m.think ?? "n/a"}`),
    m.error ? h("pre", { class: "err" }, m.error) : null,
    h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, ...[
      ["Memory loaded", `${fmt(m.loaded_gb)} GB (${fmt(m.gpu_pct, 0)}% on GPU)`],
      ["Cold load", `${fmt(m.cold_load_s)} s`], ["First token (warm)", `${fmt(m.ttft_s, 2)} s`],
      ["Writing speed", `${fmt(m.gen_tok_s)} tok/s`], ["Reading speed", `${fmt(m.prompt_tok_s, 0)} tok/s on ${m.long_prompt_tokens ?? "–"} tokens (first token ${fmt(m.long_ttft_s, 1)} s)`],
      ["RAM peak / swap growth", `${fmt(m.ram_peak_gb)} GB / ${fmt(m.swap_growth_gb)} GB`],
      ["Accuracy", m.accuracy !== null && m.accuracy !== undefined ? `${m.accuracy}% (${Object.entries(m.by_category || {}).map(([k, v]) => `${k} ${v}%`).join(", ")})` : (m.eval_skipped || "–")],
    ].map(([k, v]) => h("tr", {}, h("th", {}, k), h("td", {}, v)))))),
    d.evals ? h("h3", {}, "Test answers") : null,
    d.evals ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ...["", "Test", "Expected", "Model answered", "Tokens", "Time s"].map(c => h("th", {}, c)))),
      h("tbody", {}, ...d.evals.map(e => h("tr", {},
        h("td", { class: e.ok ? "v-good" : "v-bad" }, e.ok ? "✓" : "✗"), h("td", {}, e.id), h("td", {}, e.expected),
        h("td", { style: "white-space:normal;max-width:360px" }, e.answer + (e.done_reason === "length" ? " (cut off)" : "")),
        h("td", { class: "num" }, e.gen_tokens), h("td", { class: "num" }, fmt(e.wall_s))))))) : null,
    d.needle ? h("h3", {}, "Long-prompt fact finding") : null,
    d.needle ? h("p", {}, `Hidden code ${d.needle.code} in ${d.needle.prompt_tokens} tokens → replied: `, h("code", {}, d.needle.reply)) : null,
    d.speed_sample ? h("h3", {}, "Speed-test sample") : null,
    d.speed_sample ? h("pre", {}, d.speed_sample) : null,
    h("div", { class: "actions" }, h("button", { onclick: async () => { if (confirm("Delete this result?")) { await api(`/api/results/${id}`, { method: "DELETE" }); $("#detail").close(); loadResults(); } } }, "Delete result")));
  $("#detail").showModal();
}

/* ---------- start ---------- */
(async () => {
  await refreshStatus();
  await loadModels();
  api("/api/evals").then(e => ($("#eval-count").textContent = e.length)).catch(() => {});
  const running = (await api("/api/bench")).find(j => j.status === "running");
  if (running) watchJob(running.id);
  let tab = location.hash.slice(1);
  if (!$(`#tab-${tab}`)) { tab = "play"; try { tab = localStorage.getItem("tab") || "play"; } catch {} }
  showTab(tab);
  setInterval(refreshStatus, 4000);
})();
