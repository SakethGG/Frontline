// Frontline UI — vanilla JS, talks to the FastAPI backend.
// Navigation model: Topic (pills) → View (New / All / Starred) → optional Subtopic → cards → Reader panel.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = (s) => (window.marked && window.DOMPurify ? DOMPurify.sanitize(marked.parse(s || "")) : `<pre>${esc(s)}</pre>`);

const PAGE = 60;
const RELEVANT_MIN = 5;
const state = {
  topics: [], sweeps: [], status: null,
  topicId: null, view: "new", subtopic: null, q: "", sweepId: null,
  sort: "relevance", relevantOnly: true,
  items: [], total: 0, offset: 0,
  sel: -1,            // keyboard-selected card index
  readerIdx: -1,      // index of the paper open in the reader (-1 = closed)
  readerTab: "summary",
  polling: null, wasRunning: false,
};
const dives = new Map();  // paper id -> markdown
const chats = new Map();  // paper id -> [{q, a}]

// ---------------------------------------------------------------- prefs

try {
  const saved = JSON.parse(localStorage.getItem("frontline:prefs") || "{}");
  if (saved.sort) state.sort = saved.sort;
  if (typeof saved.relevantOnly === "boolean") state.relevantOnly = saved.relevantOnly;
  if (saved.theme) document.documentElement.dataset.theme = saved.theme;
} catch {}
function savePrefs() {
  try {
    localStorage.setItem("frontline:prefs", JSON.stringify({
      sort: state.sort, relevantOnly: state.relevantOnly, theme: document.documentElement.dataset.theme || "",
    }));
  } catch {}
}

// ---------------------------------------------------------------- helpers

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}
function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}
function fmtDate(iso) {
  if (!iso) return "";
  if (iso.length === 4) return iso;  // Google Scholar only gives the year
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  return isNaN(d) ? iso : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: d.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined });
}
function timeAgo(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
const minRel = () => (state.relevantOnly ? RELEVANT_MIN : 0);
const scoreClass = (r) => (r >= 7 ? "hi" : r >= 4 ? "md" : "lo");
const topicName = (id) => state.topics.find((t) => t.id === id)?.name || "";
const isPerson = (t) => t?.kind === "researcher";
const nameKey = (n) => String(n || "").toLowerCase().replace(/[^a-z]/g, "");
const initials = (n) => String(n || "").split(/[\s-]+/).filter(Boolean).map((w) => w[0]).filter((c) => /\p{Lu}/u.test(c)).slice(0, 2).join("") || "?";
const avatar = (n) => `<span class="avatar" aria-hidden="true">${esc(initials(n))}</span>`;
const compact = (n) => (n >= 1e4 ? `${Math.round(n / 1e3)}k` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n ?? 0));
async function patchPaper(p, body) {
  const updated = await api(`/api/papers/${p.id}`, { method: "PATCH", body });
  const i = state.items.findIndex((x) => x.id === p.id);
  if (i >= 0) state.items[i] = updated;
  const el = $(`.card[data-id="${p.id}"]`);
  if (el) el.outerHTML = cardHTML(updated, i);
  refreshCounts();
  return updated;
}

// ---------------------------------------------------------------- status & sweep

async function loadStatus() {
  state.status = await api("/api/status");
  renderSweep(state.status.sweep);
  if (state.status.sweep.running) startPolling();
}

function renderSweep(sw) {
  const running = !!sw?.running;
  const btn = $("#sweepBtn");
  btn.disabled = running;
  btn.classList.toggle("running", running);
  $("#sweepLabel").textContent = running ? "Sweeping…" : "Sweep now";
  $("#progress").hidden = !running;
  if (!running) return;
  const phases = { searching: "Searching arXiv, Semantic Scholar & OpenAlex…", summarizing: "Reading & summarizing new papers…", digest: "Writing your briefing…", notifying: "Sending notifications…" };
  $("#progressPhase").textContent = phases[sw.phase] || "Working…";
  const total = sw.to_summarize || 0;
  $("#progressCount").textContent = total ? `${sw.summarized} of ${total} papers` : "";
  const pct = sw.phase === "digest" || sw.phase === "notifying" ? 96 : total ? 10 + 85 * (sw.summarized / total) : 6;
  $("#progressBar").style.width = `${pct}%`;
  $("#progressLog").textContent = (sw.log || []).slice(-1)[0] || "";
}

async function startSweep() {
  if ("Notification" in window && Notification.permission === "default") {
    try { await Notification.requestPermission(); } catch {}
  }
  try {
    renderSweep(await api("/api/sweep", { method: "POST", body: {} }));
    startPolling();
  } catch (e) { toast(e.message); }
}

function startPolling() {
  state.wasRunning = true;
  clearInterval(state.polling);
  state.polling = setInterval(async () => {
    let sw;
    try { sw = await api("/api/sweep/status"); } catch { return; }
    renderSweep(sw);
    if (!sw.running) {
      clearInterval(state.polling);
      if (state.wasRunning) onSweepDone(sw);
      state.wasRunning = false;
    }
  }, 1500);
}

async function onSweepDone(sw) {
  const n = sw.new || 0;
  toast(n ? `Found ${n} new paper${n === 1 ? "" : "s"}` : "No new papers since the last sweep", 4000);
  if (n && "Notification" in window && Notification.permission === "granted" && document.hidden) {
    const note = new Notification(`Frontline: ${n} new paper${n === 1 ? "" : "s"}`, { body: "Your research briefing is ready.", tag: "frontline-sweep" });
    note.onclick = () => { window.focus(); note.close(); };
  }
  Object.assign(state, { view: "new", sweepId: null, subtopic: null });
  await refreshAll();
}

// ---------------------------------------------------------------- topics, tabs, subtopics

async function loadTopics() {
  state.topics = await api(`/api/topics?min_relevance=${minRel()}`);
  renderTopics();
  renderSubtopics();
}

function renderTopics() {
  const totalNew = state.topics.reduce((a, t) => a + t.unread_count, 0);
  const pill = (id, name, n, t) => `
    <button class="topic-pill ${state.topicId === id ? "active" : ""} ${isPerson(t) ? "person" : ""}" data-topic="${id ?? ""}"
      ${isPerson(t) ? `title="Papers from ${esc(name)}'s lab${t.profile?.institution ? ` · ${esc(t.profile.institution)}` : ""}"` : ""}>
      ${isPerson(t) ? avatar(name) : ""}${esc(name)}${n ? `<span class="dot" title="${n} new">${n}</span>` : ""}
    </button>`;
  const shown = state.topics.filter((t) => t.enabled || t.paper_count);
  // keyword topics in the pill row; followed researchers in the left sidebar
  $("#topics").innerHTML = (state.topics.length > 1 ? pill(null, "All", totalNew) : "") +
    shown.filter((t) => !isPerson(t)).map((t) => pill(t.id, t.name, t.unread_count, t)).join("") +
    `<button class="topic-pill add" id="addTopicPill" title="Add a research topic">+ Topic</button>`;
  const people = shown.filter(isPerson);
  $("#people").innerHTML = people.map((t) => `
    <button class="person-row ${state.topicId === t.id ? "active" : ""}" data-topic="${t.id}"
      title="Papers from ${esc(t.name)}'s lab${t.profile?.institution ? ` · ${esc(t.profile.institution)}` : ""}">
      ${avatar(t.name)}
      <span class="person-main"><span class="person-name">${esc(t.name)}</span>${t.profile?.institution ? `<span class="person-inst">${esc(t.profile.institution)}</span>` : ""}</span>
      ${t.unread_count ? `<span class="dot" title="${t.unread_count} new">${t.unread_count}</span>` : ""}
    </button>`).join("") +
    (people.length ? "" : `<p class="people-empty">Follow a researcher to see every paper from their lab.</p>`) +
    `<button class="person-row add" id="followPill" title="Follow a researcher's lab">+ Researcher</button>`;
  renderProfile();
  // with a single topic there is no "All" pill; treat it as selected
  if (state.topics.length === 1 && state.topicId === null) $(".topic-pill[data-topic]")?.classList.add("active");
}

function renderSubtopics() {
  const t = state.topics.find((x) => x.id === state.topicId);
  const subs = t ? t.subtopics : [...new Set(state.topics.flatMap((x) => x.subtopics))];
  $("#subtopics").hidden = !subs.length;
  $("#subtopics").innerHTML = `<button class="chip ${!state.subtopic ? "active" : ""}" data-sub="">Everything</button>` +
    subs.map((s) => `<button class="chip ${state.subtopic === s ? "active" : ""}" data-sub="${esc(s)}">${esc(s)}</button>`).join("");
}

async function refreshCounts() {
  const p = new URLSearchParams({ min_relevance: minRel() });
  if (state.topicId) p.set("topic_id", state.topicId);
  const c = await api(`/api/counts?${p}`);
  $("#countNew").textContent = c.new || "";
  $("#countAll").textContent = c.all || "";
  $("#countStarred").textContent = c.starred || "";
  $$("#viewTabs .tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.view === state.view);
    t.setAttribute("aria-selected", t.dataset.view === state.view);
  });
  state.counts = c;
  // keep topic pill badges in sync without a full reload
  api(`/api/topics?min_relevance=${minRel()}`).then((ts) => { state.topics = ts; renderTopics(); });
}

// ---------------------------------------------------------------- briefing

async function loadBriefing() {
  state.sweeps = await api("/api/sweeps");
  const done = state.sweeps.filter((s) => s.status === "done" && s.digest);
  const shown = state.sweepId ? state.sweeps.find((s) => s.id === state.sweepId) : done[0];
  $("#briefing").hidden = !shown?.digest;
  if (!shown?.digest) return;
  const headline = (shown.digest.match(/\*\*(.+?)\*\*/) || [])[1];
  $("#briefingSummary").innerHTML =
    `<b>${shown.stats?.new ?? 0} new papers</b> · ${timeAgo(shown.started_at)}` +
    (headline ? ` · Top pick: ${esc(headline)}` : "");
  $("#briefingMd").innerHTML = md(shown.digest);
  $("#historySelect").innerHTML = `<option value="">All papers</option>` + state.sweeps
    .filter((s) => s.status === "done")
    .map((s) => `<option value="${s.id}" ${state.sweepId === s.id ? "selected" : ""}>${new Date(s.started_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} · ${s.stats?.new ?? 0} new${s.trigger === "scheduled" ? " (auto)" : ""}</option>`)
    .join("");
}

// ---------------------------------------------------------------- feed

function queryString(offset) {
  const p = new URLSearchParams({ sort: state.sort, min_relevance: minRel(), limit: PAGE, offset });
  if (state.topicId) p.set("topic_id", state.topicId);
  if (state.subtopic) p.set("subtopic", state.subtopic);
  if (state.q) p.set("q", state.q);
  if (state.view === "new") p.set("unread", "true");
  if (state.view === "starred") p.set("starred", "true");
  if (state.sweepId) p.set("sweep_id", state.sweepId);
  return p.toString();
}

async function loadPapers(append = false) {
  const offset = append ? state.offset + PAGE : 0;
  const data = await api(`/api/papers?${queryString(offset)}`);
  state.offset = offset;
  state.total = data.total;
  state.items = append ? state.items.concat(data.items) : data.items;
  if (!append) state.sel = -1;
  renderFeed();
  const ex = new URLSearchParams();
  if (state.sweepId) ex.set("sweep_id", state.sweepId);
  if (state.view === "starred") ex.set("starred", "true");
  if (state.topicId) ex.set("topic_id", state.topicId);
  $("#exportBtn").href = `/api/export.md?${ex}`;
}

// Short author list (HTML). On a followed researcher's paper, always show them, in bold:
// PIs are usually last author, so a plain "first 3 +N" would hide them.
function authorLine(p) {
  const t = state.topics.find((x) => x.id === p.topic_id);
  const pi = isPerson(t) ? p.authors.findIndex((a) => nameKey(a) === nameKey(t.name)) : -1;
  const fmt = (a, j) => (j === pi ? `<b class="pi">${esc(a)}</b>` : esc(a));
  const n = p.authors.length;
  if (n <= 3) return p.authors.map(fmt).join(", ");
  if (pi < 3) return `${p.authors.slice(0, 3).map(fmt).join(", ")} +${n - 3}`;
  return `${p.authors.slice(0, 2).map(fmt).join(", ")}, … ${fmt(p.authors[pi], pi)} <span title="${n} authors">+${n - 3}</span>`;
}

function cardHTML(p, i) {
  const s = p.summary || {};
  const authors = authorLine(p);
  const tags = (s.subtopics || []).filter((x) => x !== "Other").slice(0, 3);
  return `
  <article class="card ${p.is_read ? "read" : ""} ${i === state.sel ? "selected" : ""}" data-id="${p.id}" data-idx="${i}" tabindex="-1">
    <div class="card-meta">
      <span class="score ${scoreClass(p.relevance)}" title="Relevance to your topic">${p.relevance}/10</span>
      ${!p.is_read ? `<span class="new">NEW</span>` : ""}
      <span>${fmtDate(p.published)}</span>
      ${!state.topicId && state.topics.length > 1 ? `<span>·</span><span class="topic">${esc(topicName(p.topic_id))}</span>` : ""}
    </div>
    <h3>${esc(p.title)}</h3>
    <div class="authors">${authors}</div>
    <p class="tldr">${esc(s.tldr || "")}</p>
    ${s.key_points?.length ? `<ul class="points">${s.key_points.slice(0, 3).map((k) => `<li>${esc(k)}</li>`).join("")}</ul>` : ""}
    ${tags.length || p.code_url ? `<div class="tags">${tags.map((x) => `<span class="tag">${esc(x)}</span>`).join("")}${p.code_url ? `<span class="tag">code</span>` : ""}</div>` : ""}
    <div class="card-foot">
      <button class="act ${p.starred ? "on" : ""}" data-act="star" title="${p.starred ? "Unstar" : "Star"} (s)">${p.starred ? "★ Starred" : "☆ Star"}</button>
      <button class="act" data-act="read" title="Toggle read (u)">${p.is_read ? "Mark unread" : "✓ Done"}</button>
      <button class="act open" data-act="open">Open ›</button>
    </div>
  </article>`;
}

function renderFeed() {
  const bits = [];
  if (state.sweepId) bits.push(`from one sweep <button class="text-btn" data-clear="sweep">show all</button>`);
  if (state.q) bits.push(`matching “${esc(state.q)}” <button class="text-btn" data-clear="q">clear</button>`);
  $("#feedStatus").innerHTML = state.items.length
    ? `${state.total} paper${state.total === 1 ? "" : "s"}${bits.length ? " · " + bits.join(" · ") : ""}`
    : bits.join(" · ");
  $("#grid").innerHTML = state.items.map(cardHTML).join("");
  $("#moreBtn").hidden = state.items.length >= state.total;
  renderEmpty();
}

function renderEmpty() {
  const el = $("#empty");
  el.hidden = state.items.length > 0;
  if (state.items.length) return;
  const anyPapers = state.topics.some((t) => t.paper_count);
  if (!anyPapers) {
    el.innerHTML = `<h3>No papers yet</h3><p>Sweep arXiv, Semantic Scholar and OpenAlex for the latest work in your topics.</p>
      <button class="primary-btn" data-empty="sweep">⟳ Sweep now</button>`;
  } else if (state.q || state.subtopic || state.sweepId) {
    el.innerHTML = `<h3>Nothing matches</h3><p>No papers fit these filters.</p><button class="primary-btn" data-empty="clear">Clear filters</button>`;
  } else if (state.view === "new") {
    el.innerHTML = `<h3>You're all caught up</h3><p>No unread papers${state.relevantOnly ? " above 5/10 relevance" : ""}. Sweep again later, or browse everything.</p>
      <button class="primary-btn" data-empty="all">See all papers</button>`;
  } else if (state.view === "starred") {
    el.innerHTML = `<h3>No starred papers</h3><p>Press ☆ Star on a paper (or <kbd>s</kbd>) to save it here.</p>`;
  } else {
    el.innerHTML = `<h3>No papers here</h3><p>${state.relevantOnly ? "Turn off “Relevant only” to see lower-scored papers." : "Try another topic."}</p>`;
  }
}

function select(i, scroll = true) {
  if (!state.items.length) return;
  state.sel = Math.max(0, Math.min(i, state.items.length - 1));
  $$(".card.selected").forEach((c) => c.classList.remove("selected"));
  const el = $(`.card[data-idx="${state.sel}"]`);
  if (el) { el.classList.add("selected"); if (scroll) el.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
}

async function onCardClick(e) {
  const card = e.target.closest(".card");
  if (!card) return;
  const i = +card.dataset.idx;
  const p = state.items[i];
  const act = e.target.closest("[data-act]")?.dataset.act;
  select(i, false);
  if (act === "star") return patchPaper(p, { starred: !p.starred });
  if (act === "read") return patchPaper(p, { is_read: !p.is_read });
  openReader(i);
}

// ---------------------------------------------------------------- reader

function bibtex(p) {
  const first = (p.authors[0] || "anon").split(" ").pop().toLowerCase().replace(/[^a-z]/g, "");
  const year = (p.published || "").slice(0, 4);
  const word = (p.title.match(/[A-Za-z]{4,}/) || ["paper"])[0].toLowerCase();
  const fields = [
    `  title = {${p.title}}`, `  author = {${p.authors.join(" and ")}}`, year && `  year = {${year}}`,
    p.arxiv_id && `  eprint = {${p.arxiv_id}}`, p.arxiv_id && `  archivePrefix = {arXiv}`,
    p.doi && `  doi = {${p.doi}}`, p.venue && !p.arxiv_id && `  journal = {${p.venue}}`, `  url = {${p.url}}`,
  ].filter(Boolean);
  return `@${p.arxiv_id ? "misc" : "article"}{${first}${year}${word},\n${fields.join(",\n")}\n}`;
}

function openReader(i) {
  const p = state.items[i];
  if (!p) return;
  state.readerIdx = i;
  select(i, false); // the panel covers the grid; scroll to the card on close instead
  const s = p.summary || {};
  $("#rMeta").innerHTML = `
    <span class="score ${scoreClass(p.relevance)}">${p.relevance}/10</span>
    <span>${fmtDate(p.published)}</span><span>·</span><span>${esc(p.sources.join(" + "))}</span>
    ${p.venue && p.venue !== "arXiv" ? `<span>·</span><span>${esc(p.venue)}</span>` : ""}
    ${state.topics.length > 1 ? `<span>·</span><span>${esc(topicName(p.topic_id))}</span>` : ""}`;
  $("#rTitle").textContent = p.title;
  $("#rAuthors").textContent = p.authors.join(", ");
  $("#rLinks").innerHTML = `
    <a class="primary-btn" href="${esc(p.url)}" target="_blank" rel="noopener">Open paper ↗</a>
    ${p.pdf_url ? `<a class="ghost-btn" href="${esc(p.pdf_url)}" target="_blank" rel="noopener">PDF</a>` : ""}
    ${p.code_url ? `<a class="ghost-btn" href="${esc(p.code_url)}" target="_blank" rel="noopener">Code</a>` : ""}
    <button class="ghost-btn" id="rCite">Copy citation</button>`;
  const sec = (title, body) => (body ? `<div class="sec"><h4>${title}</h4><p>${esc(body)}</p></div>` : "");
  $("#rSummary").innerHTML = `
    <p class="tldr-big">${esc(s.tldr || "")}</p>
    ${s.key_points?.length ? `<div class="sec"><h4>Key points</h4><ul>${s.key_points.map((k) => `<li>${esc(k)}</li>`).join("")}</ul></div>` : ""}
    ${sec("Problem", s.problem)}${sec("Method", s.method)}${sec("Results", s.results)}
    ${sec("Why it matters", s.why_it_matters)}${sec("Limitations", s.limitations)}
    ${s.relevance_reason ? `<div class="sec why"><b>Why ${p.relevance}/10:</b> ${esc(s.relevance_reason)}</div>` : ""}
    ${p.abstract ? `<details class="sec"><summary><h4 style="display:inline">Original abstract</h4></summary><p class="abstract">${esc(p.abstract)}</p></details>` : ""}`;
  renderReaderActions(p);
  $("#readerPos").textContent = `${i + 1} of ${state.total}`;
  $("#prevBtn").disabled = i === 0;
  $("#nextBtn").disabled = i >= state.total - 1;
  $("#rDive").innerHTML = dives.has(p.id) ? md(dives.get(p.id)) : "";
  renderChat();
  switchReaderTab(state.readerTab === "dive" && !dives.has(p.id) ? "summary" : state.readerTab);
  $("#readerScroll").scrollTop = 0;
  $("#reader").classList.add("open");
  $("#reader").setAttribute("aria-hidden", "false");
  $("#scrim").hidden = false;
  document.body.classList.add("reader-open");
  if (!p.is_read) patchPaper(p, { is_read: true }).then((u) => { if (state.readerIdx === i) renderReaderActions(u); });
}

function renderReaderActions(p) {
  $("#rStar").textContent = p.starred ? "★ Starred" : "☆ Star";
  $("#rStar").classList.toggle("on", !!p.starred);
  $("#rRead").textContent = p.is_read ? "Mark unread" : "Mark read";
}

function closeReader() {
  const wasOpen = state.readerIdx >= 0;
  state.readerIdx = -1;
  $("#reader").classList.remove("open");
  $("#reader").setAttribute("aria-hidden", "true");
  $("#scrim").hidden = true;
  document.body.classList.remove("reader-open");
  if (wasOpen) $(`.card[data-idx="${state.sel}"]`)?.scrollIntoView({ block: "nearest" });
}

async function stepReader(delta) {
  let i = state.readerIdx + delta;
  if (i >= state.items.length && state.items.length < state.total) await loadPapers(true);
  if (i >= 0 && i < state.items.length) openReader(i);
}

function switchReaderTab(name) {
  state.readerTab = name;
  $$(".rtab").forEach((t) => t.classList.toggle("active", t.dataset.rtab === name));
  $$(".rpanel").forEach((p) => (p.hidden = p.dataset.rpanel !== name));
  $("#askForm").hidden = name !== "ask";
  if (name === "dive") loadDive(false);
  if (name === "ask") setTimeout(() => $("#askInput").focus(), 50);
}

async function loadDive(refresh) {
  const p = state.items[state.readerIdx];
  if (!p || (dives.has(p.id) && !refresh)) return;
  $("#rRegen").hidden = true;
  $("#rDive").innerHTML = `<div class="loading">${esc(state.status?.provider || "The AI")} is reading the full paper — this can take a minute…</div>`;
  try {
    const { markdown } = await api(`/api/papers/${p.id}/deep-dive${refresh ? "?refresh=true" : ""}`, { method: "POST" });
    if (state.status?.ai) dives.set(p.id, markdown);
    if (state.items[state.readerIdx]?.id !== p.id) return;
    $("#rDive").innerHTML = md(markdown);
    $("#rRegen").hidden = !state.status?.ai;
  } catch (e) { $("#rDive").innerHTML = `<p>Couldn't generate the deep dive: ${esc(e.message)}</p>`; }
}

const SUGGESTIONS = ["What's the key idea in plain words?", "What robot / setup did they use?", "How does it compare to prior work?", "What are the main limitations?"];

function renderChat(pending) {
  const p = state.items[state.readerIdx];
  if (!p) return;
  const hist = chats.get(p.id) || [];
  $("#rChat").innerHTML = (!hist.length && !pending
    ? `<p class="hint">${esc(state.status?.provider || "The AI")} reads the full PDF when it's available and answers questions about it.</p>
       <div class="suggest">${SUGGESTIONS.map((q) => `<button class="chip" data-suggest="${esc(q)}">${esc(q)}</button>`).join("")}</div>`
    : "") +
    hist.map((t) => `<div class="msg q">${esc(t.q)}</div><div class="msg a md">${md(t.a)}</div>`).join("") +
    (pending ? `<div class="msg q">${esc(pending)}</div><div class="msg a"><span class="loading">Thinking…</span></div>` : "");
  $("#readerScroll").scrollTop = 1e6;
}

async function ask(q) {
  const p = state.items[state.readerIdx];
  if (!q || !p) return;
  $("#askInput").value = "";
  renderChat(q);
  try {
    const hist = chats.get(p.id) || [];
    const { answer } = await api(`/api/papers/${p.id}/ask`, { method: "POST", body: { question: q, history: hist } });
    chats.set(p.id, [...hist, { q, a: answer }]);
  } catch (err) { toast(err.message); }
  if (state.items[state.readerIdx]?.id === p.id) renderChat();
}

// ---------------------------------------------------------------- settings & topic editor

function openSettings() {
  $("#topicAdmin").innerHTML = state.topics.map((t) => `
    <li><button data-edit="${t.id}">
      ${isPerson(t) ? avatar(t.name) : ""}
      <span class="t-main"><div class="t-name">${esc(t.name)} ${t.enabled ? "" : `<span class="off">paused</span>`}</div>
      <div class="t-sub">${esc(isPerson(t) ? `Researcher · ${t.profile?.institution || "lab feed"}` : t.queries.join(" · "))}</div></span>
      <span class="t-sub">${t.paper_count} papers ›</span>
    </button></li>`).join("") || `<li class="hint">No topics yet.</li>`;
  const s = state.status || {};
  $("#statusBox").innerHTML = `
    <div>Summaries: ${s.ai ? `<span class="ok">${esc(s.provider)} (${esc(s.model)})</span>` : `<span class="warn">basic: ${esc(s.provider)} isn't set up${s.ai_reason ? ` (${esc(s.ai_reason)})` : ""}. Add ${s.provider === "OpenAI" ? "OPENAI_API_KEY" : "ANTHROPIC_API_KEY"} to .env and restart</span>`}</div>
    <div>Phone / email alerts: ${s.channels?.length ? `<span class="ok">${s.channels.join(", ")}</span>` : "off — set NTFY_TOPIC or SMTP_* in .env"}</div>
    <div>Automatic sweeps: ${s.auto_sweep_hours > 0 ? `<span class="ok">every ${s.auto_sweep_hours} h</span>` : "off — set FRONTLINE_AUTO_SWEEP_HOURS in .env"}</div>`;
  $("#settingsModal").showModal();
}

function openTopicEditor(topic) {
  const f = $("#topicForm");
  f.reset();
  $("#tmTitle").textContent = topic ? `Edit “${topic.name}”` : "Add a topic";
  $("#tmDelete").hidden = !topic;
  f.dataset.id = topic?.id ?? "";
  if (topic) {
    f.name.value = topic.name;
    f.description.value = topic.description;
    f.queries.value = topic.queries.join("\n");
    f.subtopics.value = topic.subtopics.join("\n");
    f.arxiv_categories.value = topic.arxiv_categories.join(", ");
    f.lookback_days.value = topic.lookback_days;
    f.enabled.checked = !!topic.enabled;
  }
  $("#topicModal").showModal();
}

async function saveTopic(e) {
  e.preventDefault();
  const f = e.target;
  const lines = (v) => v.split("\n").map((s) => s.trim()).filter(Boolean);
  const body = {
    name: f.name.value.trim(), description: f.description.value.trim(),
    queries: lines(f.queries.value), subtopics: lines(f.subtopics.value),
    arxiv_categories: f.arxiv_categories.value.split(",").map((s) => s.trim()).filter(Boolean),
    lookback_days: +f.lookback_days.value || 7, enabled: f.enabled.checked,
  };
  try {
    const id = f.dataset.id;
    const t = await api(id ? `/api/topics/${id}` : "/api/topics", { method: id ? "PUT" : "POST", body });
    $("#topicModal").close();
    $("#settingsModal").close();
    toast(id ? "Topic saved" : "Topic added — press Sweep now to find papers", 3500);
    Object.assign(state, { topicId: t.id, subtopic: null });
    await refreshAll();
  } catch (err) { toast(err.message); }
}

async function deleteTopic() {
  const id = +$("#topicForm").dataset.id;
  const t = state.topics.find((x) => x.id === id);
  if (!t || !confirm(`Delete “${t.name}” and its ${t.paper_count} saved papers?`)) return;
  await api(`/api/topics/${id}`, { method: "DELETE" });
  $("#topicModal").close();
  $("#settingsModal").close();
  if (state.topicId === id) state.topicId = null;
  await refreshAll();
}

// ---------------------------------------------------------------- followed researchers

function renderProfile() {
  const t = state.topics.find((x) => x.id === state.topicId);
  const el = $("#profile");
  el.hidden = !isPerson(t);
  if (!isPerson(t)) return;
  const pr = t.profile || {};
  const months = Math.round(t.lookback_days / 30);
  const q = encodeURIComponent(`${t.name} ${pr.institution || ""}`.trim());
  el.innerHTML = `
    ${avatar(t.name)}
    <div class="profile-main">
      <h2>${esc(t.name)}${t.enabled ? "" : ` <span class="off">paused</span>`}</h2>
      ${pr.institution ? `<div class="inst">${esc(pr.institution)}</div>` : ""}
      <div class="stats">
        <span><b>${t.paper_count}</b> lab papers here (last ${months} months)</span>
        ${pr.h_index ? `<span>h-index <b>${pr.h_index}</b></span>` : ""}
        ${pr.cited_by_count ? `<span><b>${compact(pr.cited_by_count)}</b> citations</span>` : ""}
        ${pr.works_count ? `<span><b>${pr.works_count}</b> works</span>` : ""}
      </div>
      ${pr.areas?.length ? `<div class="areas">${pr.areas.map((a) => `<span class="area">${esc(a)}</span>`).join("")}</div>` : ""}
      <div class="links">
        <a href="${pr.scholar_id ? `https://scholar.google.com/citations?user=${esc(pr.scholar_id)}` : `https://scholar.google.com/scholar?q=${q}`}" target="_blank" rel="noopener">Google Scholar ↗</a>
        <a href="https://arxiv.org/search/?searchtype=author&query=${encodeURIComponent(t.name)}" target="_blank" rel="noopener">arXiv ↗</a>
        ${pr.openalex_id ? `<a href="https://openalex.org/authors/${esc(pr.openalex_id)}" target="_blank" rel="noopener">OpenAlex ↗</a>` : ""}
        ${pr.orcid ? `<a href="https://orcid.org/${esc(pr.orcid)}" target="_blank" rel="noopener">ORCID ↗</a>` : ""}
      </div>
    </div>
    <div class="profile-actions">
      <button class="ghost-btn" data-profile="pause" title="${t.enabled ? "Skip this researcher in sweeps" : "Include in sweeps again"}">${t.enabled ? "Pause" : "Resume"}</button>
      <button class="ghost-btn" data-profile="unfollow">Unfollow</button>
    </div>`;
}

function openFollow() {
  $("#candidates").innerHTML = "";
  $("#followName").value = "";
  $("#followModal").showModal();
  $("#followName").focus();
}

async function searchResearchers() {
  const name = $("#followName").value.trim();
  if (name.length < 3) return toast("Type at least 3 characters");
  const list = $("#candidates");
  const isLink = name.includes("scholar.google.");
  list.innerHTML = `<li class="loading">${isLink ? "Reading their Google Scholar profile…" : `Looking up “${esc(name)}”…`}</li>`;
  let found;
  try { found = await api(`/api/researchers/search?name=${encodeURIComponent(name)}`); }
  catch (e) { list.innerHTML = `<li class="hint">Search failed: ${esc(e.message)}</li>`; return; }
  list.innerHTML = found.map((c) => `
    <li class="candidate">
      ${avatar(c.name)}
      <div class="c-main">
        <div class="c-name">${esc(c.name)}</div>
        <div class="c-sub">${esc(c.institution || "Institution unknown")} · h-index ${c.h_index} · ${c.works_count} works</div>
        ${c.areas.length ? `<div class="c-areas">Works on <b>${c.areas.slice(0, 3).map(esc).join("</b>, <b>")}</b></div>` : ""}
        ${c.scholar_id ? `<div class="c-areas">From their <b>Google Scholar</b> profile${c.openalex_matched ? ` · also found in OpenAlex (${c.openalex_matched} papers matched)` : ""}. New papers are also searched on arXiv, OpenAlex and Semantic Scholar.</div>` : ""}
      </div>
      ${c.followed_topic_id
        ? `<button class="ghost-btn" data-view-feed="${c.followed_topic_id}">Following ›</button>`
        : `<button class="primary-btn" data-follow="${esc(c.openalex_id)}" data-scholar="${esc(c.scholar_id || "")}">Follow</button>`}
    </li>`).join("") || `<li class="hint">${isLink ? "Couldn't match that Scholar profile to an author." : `No researchers found for “${esc(name)}”. Try their full name, or paste their Google Scholar profile link.`}</li>`;
}

async function follow(openalexId, btn, scholarId) {
  btn.disabled = true;
  btn.textContent = "Following…";
  try {
    const body = { openalex_id: openalexId, ...(scholarId ? { scholar_id: scholarId } : {}) };
    const { topic, sweep_started } = await api("/api/researchers", { method: "POST", body });
    $("#followModal").close();
    $("#settingsModal").close();
    toast(sweep_started
      ? `Following ${topic.name}. Fetching their lab's papers from the last ${Math.round(topic.lookback_days / 30)} months…`
      : `Following ${topic.name}. Their papers arrive with the sweep that's running now or the next one.`, 5000);
    Object.assign(state, { topicId: topic.id, subtopic: null, view: "all" });
    await refreshAll();
    if (sweep_started) { renderSweep({ running: true, phase: "searching" }); startPolling(); }
  } catch (e) {
    toast(e.message);
    btn.disabled = false;
    btn.textContent = "Follow";
  }
}

async function toggleResearcher() {
  const t = state.topics.find((x) => x.id === state.topicId);
  await api(`/api/researchers/${t.id}`, { method: "PATCH", body: { enabled: !t.enabled } });
  toast(t.enabled ? `Paused ${t.name}: skipped in sweeps` : `Resumed ${t.name}`);
  await loadTopics();
}

async function unfollow() {
  const t = state.topics.find((x) => x.id === state.topicId);
  if (!t || !confirm(`Unfollow ${t.name}? Their ${t.paper_count} saved papers will be removed (starred ones too).`)) return;
  await api(`/api/topics/${t.id}`, { method: "DELETE" });
  toast(`Unfollowed ${t.name}`);
  state.topicId = null;
  await refreshAll();
}

// ---------------------------------------------------------------- navigation

async function refreshAll() {
  closeReader();
  await loadTopics();
  await Promise.all([refreshCounts(), loadBriefing(), loadPapers()]);
}

function go(changes) {
  Object.assign(state, changes);
  if ("topicId" in changes) renderSubtopics();
  renderTopics();
  $$("#subtopics .chip").forEach((c) => c.classList.toggle("active", (c.dataset.sub || null) === state.subtopic));
  closeReader();
  refreshCounts();
  loadPapers();
  if ("sweepId" in changes) loadBriefing();
}

function bind() {
  $("#sweepBtn").onclick = startSweep;
  $("#settingsBtn").onclick = openSettings;
  $("#helpBtn").onclick = () => $("#helpModal").showModal();
  $("#themeBtn").onclick = () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    savePrefs();
  };

  $("#topics").onclick = (e) => {
    if (e.target.closest("#addTopicPill")) return openTopicEditor(null);
    if (e.target.closest("#followPill")) return openFollow();
    const b = e.target.closest("[data-topic]");
    if (b) go({ topicId: b.dataset.topic ? +b.dataset.topic : null, subtopic: null });
  };
  $("#people").onclick = (e) => {
    if (e.target.closest("#followPill")) return openFollow();
    const b = e.target.closest("[data-topic]");
    if (b) go({ topicId: +b.dataset.topic, subtopic: null });
  };
  $("#viewTabs").onclick = (e) => {
    const b = e.target.closest("[data-view]");
    if (b) go({ view: b.dataset.view });
  };
  $("#subtopics").onclick = (e) => {
    const b = e.target.closest("[data-sub]");
    if (b) go({ subtopic: b.dataset.sub || null });
  };
  $("#relevantOnly").checked = state.relevantOnly;
  $("#relevantOnly").onchange = (e) => { state.relevantOnly = e.target.checked; savePrefs(); loadTopics(); go({}); };
  $("#sort").value = state.sort;
  $("#sort").onchange = (e) => { state.sort = e.target.value; savePrefs(); loadPapers(); };

  let t;
  $("#search").oninput = (e) => { clearTimeout(t); t = setTimeout(() => go({ q: e.target.value.trim() }), 250); };
  $("#feedStatus").onclick = (e) => {
    const c = e.target.closest("[data-clear]")?.dataset.clear;
    if (c === "sweep") go({ sweepId: null });
    if (c === "q") { $("#search").value = ""; go({ q: "" }); }
  };
  $("#empty").onclick = (e) => {
    const a = e.target.closest("[data-empty]")?.dataset.empty;
    if (a === "sweep") startSweep();
    if (a === "all") go({ view: "all" });
    if (a === "clear") { $("#search").value = ""; go({ q: "", subtopic: null, sweepId: null }); }
  };

  $("#briefingToggle").onclick = () => {
    const open = $("#briefingBody").hidden;
    $("#briefingBody").hidden = !open;
    $("#briefingToggle").setAttribute("aria-expanded", open);
  };
  $("#historySelect").onchange = (e) => go({ sweepId: e.target.value ? +e.target.value : null, view: "all" });

  $("#grid").onclick = onCardClick;
  $("#moreBtn").onclick = () => loadPapers(true);

  // reader
  $("#scrim").onclick = closeReader;
  $("#closeReader").onclick = closeReader;
  $("#prevBtn").onclick = () => stepReader(-1);
  $("#nextBtn").onclick = () => stepReader(1);
  $("#rStar").onclick = async () => renderReaderActions(await patchPaper(state.items[state.readerIdx], { starred: !state.items[state.readerIdx].starred }));
  $("#rRead").onclick = async () => renderReaderActions(await patchPaper(state.items[state.readerIdx], { is_read: !state.items[state.readerIdx].is_read }));
  $$(".rtab").forEach((b) => (b.onclick = () => switchReaderTab(b.dataset.rtab)));
  $("#rRegen").onclick = () => loadDive(true);
  $("#reader").addEventListener("click", async (e) => {
    if (e.target.id === "rCite") { await navigator.clipboard.writeText(bibtex(state.items[state.readerIdx])); toast("BibTeX copied"); }
    const s = e.target.closest("[data-suggest]");
    if (s) ask(s.dataset.suggest);
  });
  $("#askForm").onsubmit = (e) => { e.preventDefault(); ask($("#askInput").value.trim()); };

  // settings & topics
  $("#addTopicBtn").onclick = () => openTopicEditor(null);
  $("#topicAdmin").onclick = (e) => {
    const b = e.target.closest("[data-edit]");
    if (!b) return;
    const t = state.topics.find((x) => x.id === +b.dataset.edit);
    // researchers have no search phrases to edit; open their feed (pause / unfollow live there)
    if (isPerson(t)) { $("#settingsModal").close(); go({ topicId: t.id, subtopic: null }); }
    else openTopicEditor(t);
  };
  $("#followBtn").onclick = openFollow;
  $("#followForm").onsubmit = (e) => { e.preventDefault(); searchResearchers(); };
  $("#candidates").onclick = (e) => {
    const f = e.target.closest("[data-follow]");
    if (f) return follow(f.dataset.follow, f, f.dataset.scholar);
    const v = e.target.closest("[data-view-feed]");
    if (v) { $("#followModal").close(); $("#settingsModal").close(); go({ topicId: +v.dataset.viewFeed, subtopic: null }); }
  };
  $("#profile").onclick = (e) => {
    const a = e.target.closest("[data-profile]")?.dataset.profile;
    if (a === "pause") toggleResearcher();
    if (a === "unfollow") unfollow();
  };
  $("#topicForm").onsubmit = saveTopic;
  $("#tmDelete").onclick = deleteTopic;
  $$("[data-close]").forEach((b) => (b.onclick = () => b.closest("dialog").close()));
  $$("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));

  document.addEventListener("keydown", onKey);
}

function onKey(e) {
  if (document.querySelector("dialog[open]")) return;
  const typing = e.target.matches("input, textarea, select");
  if (e.key === "Escape") {
    if (typing) { e.target.blur(); return; }
    if (state.readerIdx >= 0) closeReader();
    return;
  }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  const reading = state.readerIdx >= 0;
  const cur = state.items[reading ? state.readerIdx : state.sel];
  switch (e.key) {
    case "/": e.preventDefault(); $("#search").focus(); break;
    case "?": $("#helpModal").showModal(); break;
    case "S": startSweep(); break;
    case "j": case "ArrowDown": if (reading) stepReader(1); else select(state.sel + 1); e.preventDefault(); break;
    case "k": case "ArrowUp": if (reading) stepReader(-1); else select(state.sel - 1); e.preventDefault(); break;
    case "Enter": case "o": if (!reading && state.sel >= 0) openReader(state.sel); break;
    case "s": if (cur) patchPaper(cur, { starred: !cur.starred }).then((u) => reading && renderReaderActions(u)); break;
    case "u": if (cur) patchPaper(cur, { is_read: !cur.is_read }).then((u) => reading && renderReaderActions(u)); break;
    case "d": if (cur) { if (!reading) openReader(state.items.indexOf(cur)); switchReaderTab("dive"); } break;
    case "1": go({ view: "new" }); break;
    case "2": go({ view: "all" }); break;
    case "3": go({ view: "starred" }); break;
  }
}

(async function init() {
  const sweepParam = new URLSearchParams(location.search).get("sweep");
  if (sweepParam) Object.assign(state, { sweepId: +sweepParam, view: "all" });
  bind();
  await loadTopics();
  await refreshCounts();
  // land on "New" when there's something new, otherwise show everything
  if (!sweepParam && !state.counts.new && state.counts.all) state.view = "all";
  await Promise.all([loadStatus(), loadBriefing(), loadPapers(), refreshCounts()]);
  // ?paper=<id> deep-links straight into the reader
  const paperParam = +new URLSearchParams(location.search).get("paper");
  const idx = state.items.findIndex((p) => p.id === paperParam);
  if (idx >= 0) openReader(idx);
  setTimeout(loadStatus, 4000); // pick up the background credential probe result
})();
