// Ideas: a pure ideation space. Quick capture, then develop ideas into proposals and roadmaps,
// link the papers they build on, ask the AI to critique / brainstorm / draft, and promote to a project.
(() => {
  const I = {
    placeholder: "Search ideas…",
    ideas: [], projects: [], q: "", kind: "", status: "", tag: null,
    layout: pref("ideas.layout") || "grid", openId: null, preview: false,
  };
  Sections.ideas = I;
  const KINDS = { idea: "💡 Idea", project: "🚀 Project", proposal: "📝 Proposal", roadmap: "🗺 Roadmap" };
  const STATUSES = { spark: "Spark", exploring: "Exploring", developing: "Developing", active: "Active", parked: "Parked" };
  const TEMPLATES = {
    project: "## What I want to build\n\n## Why it's worth doing\n\n## First steps\n- \n",
    proposal: "## Problem\nWhat's broken or missing, and for whom?\n\n## Hypothesis\n\n## Approach\n\n## Evaluation\nPlatforms / datasets, baselines, metrics.\n\n## Risks\n\n## Resources & collaborators\n",
    roadmap: "## Goal\nWhere this should be in 6–12 months.\n\n## Why now\n\n## Open questions\n",
  };
  const assists = new Map();  // idea id -> { mode, markdown }
  let paperPool = null;       // library papers, for the "link a paper" search

  // ---------------------------------------------------------------- loading

  I.show = async () => {
    [I.ideas, I.projects] = await Promise.all([api("/api/ideas"), api("/api/projects").catch(() => [])]);
    render();
  };

  I.create = async ({ title, kind = "idea", body, papers = [] }) => {
    const idea = await api("/api/ideas", { method: "POST", body: { title, kind, body: body ?? TEMPLATES[kind] ?? "", papers } });
    if (state.section !== "ideas") showSection("ideas");
    await I.show();
    I.open(idea.id);
    return idea;
  };

  I.open = async (id) => {
    if (!I.ideas.length) await I.show();
    const idea = I.ideas.find((x) => x.id === id);
    if (!idea) return;
    I.openId = id;
    I.preview = !!idea.body && idea.body.length > 0 && pref("ideas.preview") === true;
    renderDrawer(idea);
  };

  // ---------------------------------------------------------------- list

  // Every idea carries the moment it was captured; the list runs oldest → newest (pinned ones stay on top).
  const stamp = (iso) => new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
  const byAge = (a, b) => (b.pinned - a.pinned) || a.created_at.localeCompare(b.created_at) || a.id - b.id;
  const plain = (s) => String(s || "").replace(/^#+\s.*$/gm, "").replace(/[*_>`#-]/g, "").replace(/\s+/g, " ").trim();
  const matches = (i) => (!I.kind || i.kind === I.kind) && (!I.tag || i.tags.includes(I.tag)) &&
    (!I.q || [i.title, i.body, i.tags.join(" "), i.milestones.map((m) => m.title).join(" "), (i.notes || []).map((n) => n.text).join(" ")].join(" ").toLowerCase().includes(I.q.toLowerCase()));

  function render() {
    $$("#ideaTabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.ikind === I.kind));
    $$("#ideaLayout button").forEach((b) => b.classList.toggle("on", b.dataset.layout === I.layout));
    $("#ideaStatus").innerHTML = `<option value="">Any status</option>` +
      Object.entries(STATUSES).map(([k, v]) => `<option value="${k}" ${I.status === k ? "selected" : ""}>${v}</option>`).join("");
    const tags = [...new Set(I.ideas.flatMap((i) => i.tags))].sort();
    $("#ideaTags").hidden = !tags.length;
    $("#ideaTags").innerHTML = `<button class="chip ${!I.tag ? "active" : ""}" data-itag="">All tags</button>` +
      tags.map((t) => `<button class="chip ${I.tag === t ? "active" : ""}" data-itag="${esc(t)}">${esc(t)}</button>`).join("");

    const xs = I.ideas.filter(matches).sort(byAge);
    const board = $("#ideaBoard");
    if (I.layout === "board") {
      board.className = "board";
      board.innerHTML = Object.entries(STATUSES).map(([k, v]) => {
        const col = xs.filter((i) => i.status === k);
        return `<section class="board-col" data-drop-status="${k}">
          <h3>${v} <span class="badge">${col.length || ""}</span></h3>
          <div class="board-cards">${col.map(cardHTML).join("")}</div></section>`;
      }).join("");
    } else {
      board.className = "idea-grid";
      board.innerHTML = xs.filter((i) => !I.status || i.status === I.status).map(cardHTML).join("");
    }
    const shown = I.layout === "board" ? xs.length : xs.filter((i) => !I.status || i.status === I.status).length;
    const empty = $("#ideaEmpty");
    empty.hidden = shown > 0;
    empty.innerHTML = I.ideas.length
      ? `<h3>Nothing matches</h3><p>No ideas fit these filters.</p>`
      : `<h3>A blank page</h3><p>Type an idea above and press Enter. Rough is fine: sparks become proposals and roadmaps later.</p>`;
  }

  function cardHTML(i) {
    const done = i.milestones.filter((m) => m.done).length;
    const proj = I.projects.find((p) => p.idea_id === i.id);
    return `
    <article class="idea-card kind-${i.kind} ${i.id === I.openId ? "selected" : ""}" data-iid="${i.id}" draggable="true">
      <div class="ic-head">
        <span class="pill kind-${i.kind}">${KINDS[i.kind]}</span>
        <span class="pill status-${i.status}">${STATUSES[i.status]}</span>
        <span class="spacer"></span>
        <button class="star-btn ${i.pinned ? "on" : ""}" data-iact="pin" title="${i.pinned ? "Unpin" : "Pin to top"}" aria-label="Pin">${i.pinned ? "★" : "☆"}</button>
      </div>
      <h3>${esc(i.title)}</h3>
      ${plain(i.body) ? `<p class="ic-body">${esc(plain(i.body).slice(0, 200))}</p>` : ""}
      ${i.milestones.length ? `<div class="mini-progress" title="${done} of ${i.milestones.length} milestones"><span style="width:${(100 * done) / i.milestones.length}%"></span></div>` : ""}
      <div class="ic-foot">
        ${i.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}
        ${i.papers.length ? `<span class="meta">📎 ${i.papers.length}</span>` : ""}
        ${i.notes?.length ? `<span class="meta" title="${plural(i.notes.length, "quick note")}">🗒 ${i.notes.length}</span>` : ""}
        ${i.milestones.length ? `<span class="meta">◇ ${done}/${i.milestones.length}</span>` : ""}
        ${proj ? `<span class="meta">🎯 project</span>` : ""}
        <span class="spacer"></span><time class="meta ic-stamp" datetime="${esc(i.created_at)}" title="Captured ${esc(stamp(i.created_at))}${i.updated_at !== i.created_at ? ` · edited ${timeAgo(i.updated_at)}` : ""}">🕒 ${esc(stamp(i.created_at))}</time>
      </div>
    </article>`;
  }

  // unsaved typing in the open editor, so a re-render never drops it
  function pendingEdits(id) {
    const cur = I.ideas.find((x) => x.id === id);
    if (I.openId !== id || !cur) return {};
    const out = {};
    const b = $('#drawer [data-f="body"]'), t = $('#drawer [data-f="title"]');
    if (b && b.value !== cur.body) out.body = b.value;
    if (t && t.value.trim() && t.value.trim() !== cur.title) out.title = t.value.trim();
    const qn = readQuickNotes(cur);
    if (qn) out.notes = qn;
    return out;
  }

  async function patch(id, body, { rerender = true } = {}) {
    const updated = await api(`/api/ideas/${id}`, { method: "PATCH", body: { ...pendingEdits(id), ...body } });
    const k = I.ideas.findIndex((x) => x.id === id);
    if (k >= 0) I.ideas[k] = updated;
    if (rerender) render();
    return updated;
  }

  // ---------------------------------------------------------------- quick notes
  // Small sticky notes beside the long-form notes: each is {text, color, at}, edited in place.

  const NOTE_COLORS = ["#fff27a", "#ff8b7b", "#8fe3a4", "#7cc4ff", "#c9a7ff", "#ffb86b"];
  const nextColor = (i) => NOTE_COLORS[(i.notes || []).length % NOTE_COLORS.length];

  // the open drawer's quick notes with any unsaved typing, or null if nothing changed
  function readQuickNotes(cur) {
    const boxes = $$("#drawer [data-qn]");
    if (!boxes.length) return null;
    let changed = false;
    const notes = (cur.notes || []).map((n, k) => {
      const box = boxes.find((b) => +b.dataset.qn === k);
      if (!box || box.value === n.text) return n;
      changed = true;
      return { ...n, text: box.value };
    });
    return changed ? notes : null;
  }

  const fitNote = (ta) => { ta.style.height = "auto"; ta.style.height = `${ta.scrollHeight}px`; };

  const quickNotesHTML = (i) => `
      <div class="editor-head"><h4 class="sec-title">Quick notes ${i.notes?.length ? `<span class="hint inline">${i.notes.length}</span>` : ""}</h4></div>
      <div class="qnotes">${(i.notes || []).map((n, k) => `
        <div class="qnote" style="--qn:${esc(n.color || NOTE_COLORS[k % NOTE_COLORS.length])}">
          <textarea data-qn="${k}" rows="2" aria-label="Quick note">${esc(n.text)}</textarea>
          <div class="qn-foot">
            <span>${n.at ? timeAgo(n.at) : ""}</span>
            <span class="spacer"></span>
            <button class="qn-color" data-qn-color="${k}" title="Change colour" aria-label="Change colour"></button>
            <button class="icon-btn tiny" data-qn-rm="${k}" title="Delete note" aria-label="Delete note">✕</button>
          </div>
        </div>`).join("")}
        <form class="qnote add" data-x="qn-add" style="--qn:${nextColor(i)}">
          <textarea rows="2" placeholder="+ Jot a quick note (Enter to add, Shift+Enter for a new line)" aria-label="New quick note"></textarea>
        </form>
      </div>`;

  // ---------------------------------------------------------------- drawer editor

  function renderDrawer(i) {
    const proj = I.projects.find((p) => p.idea_id === i.id);
    const done = i.milestones.filter((m) => m.done).length;
    const a = assists.get(i.id);
    openDrawer(`
      <div class="reader-nav"><span class="drawer-kicker">${KINDS[i.kind]}</span><span class="save-state" id="ideaSaved"></span></div>
      <div class="reader-actions">
        <button class="ghost-btn ${i.pinned ? "on" : ""}" data-x="pin">${i.pinned ? "★ Pinned" : "☆ Pin"}</button>
        <button class="icon-btn" data-x="close" title="Close (Esc)" aria-label="Close">✕</button>
      </div>`, `
      <input class="title-input" data-f="title" value="${esc(i.title)}" aria-label="Title">
      <div class="lib-controls">
        <label class="inline">Kind <select data-f="kind">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${i.kind === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        <label class="inline">Status <select data-f="status">${Object.entries(STATUSES).map(([k, v]) => `<option value="${k}" ${i.status === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        <span class="hint inline">Captured ${esc(stamp(i.created_at))}</span>
      </div>
      <div class="tag-editor">
        ${i.tags.map((t) => `<span class="tag removable">${esc(t)}<button data-untag="${esc(t)}" aria-label="Remove tag">×</button></span>`).join("")}
        <input data-x="tag-input" placeholder="+ tag" list="ideaTagList" aria-label="Add a tag">
        <datalist id="ideaTagList">${[...new Set(I.ideas.flatMap((x) => x.tags))].filter((t) => !i.tags.includes(t)).map((t) => `<option value="${esc(t)}">`).join("")}</datalist>
      </div>

      <div class="editor-head">
        <h4 class="sec-title">Notes</h4>
        <div class="seg small"><button data-x="write" class="${I.preview ? "" : "on"}">Write</button><button data-x="preview" class="${I.preview ? "on" : ""}">Preview</button></div>
      </div>
      ${I.preview
        ? `<div class="md preview-box" data-x="write">${md(i.body) || `<p class="hint">Nothing written yet.</p>`}</div>`
        : `<textarea class="notes big" data-f="body" placeholder="Brain-dump freely. Markdown works: ## headings, - bullets, **bold**.">${esc(i.body)}</textarea>`}
      ${quickNotesHTML(i)}

      <div class="editor-head"><h4 class="sec-title">Milestones ${i.milestones.length ? `<span class="hint inline">${done}/${i.milestones.length}</span>` : ""}</h4></div>
      ${i.milestones.length ? `<div class="mini-progress wide"><span style="width:${(100 * done) / i.milestones.length}%"></span></div>` : ""}
      <ol class="milestones">${i.milestones.map((m, k) => `
        <li class="${m.done ? "done" : ""}">
          <input type="checkbox" data-ms-toggle="${k}" ${m.done ? "checked" : ""} aria-label="Done">
          <span class="ms-main"><b>${esc(m.title)}</b>${m.weeks ? ` <span class="hint inline">~${m.weeks} wk</span>` : ""}${m.detail ? `<small>${esc(m.detail)}</small>` : ""}</span>
          <span class="ms-move"><button class="icon-btn tiny" data-ms-up="${k}" ${k === 0 ? "disabled" : ""} aria-label="Move up">↑</button><button class="icon-btn tiny" data-ms-rm="${k}" aria-label="Remove">✕</button></span>
        </li>`).join("")}</ol>
      <form class="mini-add" data-x="ms-add"><input placeholder="+ Add a milestone (Enter)" aria-label="New milestone"></form>

      <div class="editor-head"><h4 class="sec-title">Builds on</h4></div>
      <ul class="linked">${i.papers.map((p, k) => `
        <li><span class="pill">${p.source === "library" ? "Library" : "Radar"}</span>
          <button class="text-btn" data-open-paper="${k}">${esc(p.title || "Untitled paper")}</button>
          <button class="icon-btn tiny" data-unlink="${k}" aria-label="Unlink">✕</button></li>`).join("") || `<li class="hint">Link the papers this idea builds on or competes with. The AI assistant reads them too.</li>`}</ul>
      <div class="paper-search">
        <input data-x="paper-q" placeholder="Search your library and the radar to link a paper…" autocomplete="off" aria-label="Find a paper to link">
        <ul class="paper-results" id="ideaPaperResults"></ul>
      </div>

      <div class="assistant">
        <div class="editor-head"><h4 class="sec-title">Assistant</h4><span class="hint inline">${state.status?.ai ? `${esc(state.status.provider)} reads your notes, milestones and linked papers` : "Needs an AI key in .env"}</span></div>
        <div class="assist-actions">
          <button class="ghost-btn" data-assist="critique" title="Strengths, weak spots, novelty check, next steps">🔍 Pressure-test</button>
          <button class="ghost-btn" data-assist="expand" title="Six directions this could go">🌱 Brainstorm</button>
          <button class="ghost-btn" data-assist="proposal" title="A full proposal draft">📝 Draft proposal</button>
          <button class="ghost-btn" data-assist="roadmap" title="Adds milestones">🗺 Plan milestones</button>
        </div>
        <div id="ideaAssist">${a ? assistHTML(a) : ""}</div>
      </div>

      <div class="drawer-foot">
        ${proj ? `<button class="primary-btn" data-x="goto-project">🎯 Open project “${esc(proj.name)}”</button>`
               : `<button class="primary-btn" data-x="project" title="Creates a project; open milestones become its tasks">🚀 Start a project from this</button>`}
        <span class="spacer"></span>
        <button class="danger-btn" data-x="delete">Delete</button>
      </div>`);
    $$("#drawer [data-qn]").forEach(fitNote);
  }

  const ASSIST_TITLE = { critique: "Pressure-test", expand: "Brainstorm", proposal: "Proposal draft" };
  const assistHTML = (a) => a.loading
    ? `<div class="assist-out"><span class="loading">Thinking about your idea…</span></div>`
    : `<div class="assist-out"><div class="md">${md(a.markdown)}</div>
        <div class="assist-foot"><button class="ghost-btn" data-x="assist-append">Append to notes</button>
        <button class="ghost-btn" data-x="assist-copy">Copy</button><button class="text-btn" data-x="assist-dismiss">Dismiss</button></div></div>`;

  async function runAssist(i, mode) {
    if (mode === "roadmap") {
      $("#ideaAssist").innerHTML = assistHTML({ loading: true });
      try {
        const { idea, added } = await api(`/api/ideas/${i.id}/assist`, { method: "POST", body: { mode } });
        I.ideas[I.ideas.findIndex((x) => x.id === i.id)] = idea;
        toast(`Added ${plural(added, "milestone")}`);
        if (I.openId === i.id) renderDrawer(idea);
        render();
      } catch (e) { toast(e.message, 5000); $("#ideaAssist").innerHTML = ""; }
      return;
    }
    assists.set(i.id, { mode, loading: true });
    $("#ideaAssist").innerHTML = assistHTML({ loading: true });
    try {
      const { markdown } = await api(`/api/ideas/${i.id}/assist`, { method: "POST", body: { mode } });
      assists.set(i.id, { mode, markdown });
    } catch (e) { assists.delete(i.id); toast(e.message, 5000); }
    if (I.openId === i.id) $("#ideaAssist").innerHTML = assists.has(i.id) ? assistHTML(assists.get(i.id)) : "";
  }

  // paper linking: library papers are searched locally, the radar via its search API
  const searchPapers = debounce(async (q) => {
    const out = $("#ideaPaperResults");
    if (!out) return;
    if (q.length < 2) { out.innerHTML = ""; return; }
    paperPool ??= await api("/api/library").catch(() => []);
    const ql = q.toLowerCase();
    const lib = paperPool.filter((r) => `${r.title} ${r.authors.join(" ")}`.toLowerCase().includes(ql)).slice(0, 5)
      .map((r) => ({ source: "library", id: r.id, title: r.title }));
    const radar = (await api(`/api/papers?${new URLSearchParams({ q, limit: 6, sort: "relevance" })}`).catch(() => ({ items: [] }))).items
      .map((p) => ({ source: "radar", id: p.id, title: p.title }));
    out.innerHTML = [...lib, ...radar].map((p) => `<li><button data-link='${esc(JSON.stringify(p))}'><span class="pill">${p.source === "library" ? "Library" : "Radar"}</span> ${esc(p.title)}</button></li>`).join("")
      || `<li class="hint">No papers match “${esc(q)}”.</li>`;
  }, 250);

  const saveField = debounce(async (id, field, value) => {
    const saved = $("#ideaSaved");
    try {
      await patch(id, { [field]: value }, { rerender: true });
      if (saved && I.openId === id) { saved.textContent = "Saved"; setTimeout(() => saved && (saved.textContent = ""), 1500); }
    } catch (e) { toast(e.message); }
  }, 700);

  async function drawerClick(e) {
    if (state.section !== "ideas" || I.openId === null) return;
    const i = I.ideas.find((x) => x.id === I.openId);
    if (!i) return;
    const x = e.target.closest("[data-x]")?.dataset.x;
    const el = (attr) => e.target.closest(`[${attr}]`)?.getAttribute(attr);
    let v;
    if ((v = el("data-untag")) !== null && v !== undefined) return renderDrawer(await patch(i.id, { tags: i.tags.filter((t) => t !== v) }));
    if ((v = el("data-ms-toggle")) != null) {
      const ms = i.milestones.map((m, k) => (k === +v ? { ...m, done: !m.done } : m));
      return renderDrawer(await patch(i.id, { milestones: ms }));
    }
    if ((v = el("data-ms-rm")) != null) return renderDrawer(await patch(i.id, { milestones: i.milestones.filter((_, k) => k !== +v) }));
    if ((v = el("data-ms-up")) != null) {
      const ms = [...i.milestones];
      [ms[+v - 1], ms[+v]] = [ms[+v], ms[+v - 1]];
      return renderDrawer(await patch(i.id, { milestones: ms }));
    }
    if ((v = el("data-qn-rm")) != null) {
      const n = i.notes[+v];
      if (n.text.trim() && !confirm("Delete this note?")) return;
      return renderDrawer(await patch(i.id, { notes: i.notes.filter((_, k) => k !== +v) }));
    }
    if ((v = el("data-qn-color")) != null) {
      const cur = readQuickNotes(i) || i.notes;
      const k = +v, at = NOTE_COLORS.indexOf(cur[k].color);
      const notes = cur.map((n, j) => (j === k ? { ...n, color: NOTE_COLORS[(at + 1) % NOTE_COLORS.length] } : n));
      return renderDrawer(await patch(i.id, { notes }));
    }
    if ((v = el("data-unlink")) != null) return renderDrawer(await patch(i.id, { papers: i.papers.filter((_, k) => k !== +v) }));
    if ((v = el("data-link")) != null) {
      const p = JSON.parse(v);
      if (i.papers.some((x) => x.source === p.source && x.id === p.id)) return toast("Already linked");
      return renderDrawer(await patch(i.id, { papers: [...i.papers, p] }));
    }
    if ((v = el("data-open-paper")) != null) {
      const p = i.papers[+v];
      if (p.source === "library") { showSection("library"); return Sections.library.open(p.id); }
      return openRadarPaper(p.id);
    }
    if ((v = el("data-assist")) != null) return runAssist(i, v);
    switch (x) {
      case "close": return closeDrawer();
      case "pin": return renderDrawer(await patch(i.id, { pinned: !i.pinned }));
      case "write": case "preview":
        if (e.target.closest(".preview-box") && x === "write" && e.target.closest("a")) return;  // let links in the preview work
        I.preview = x === "preview";
        pref("ideas.preview", I.preview);
        renderDrawer(I.ideas.find((y) => y.id === i.id));
        if (!I.preview) $('#drawer [data-f="body"]')?.focus();
        return;
      case "assist-dismiss": assists.delete(i.id); $("#ideaAssist").innerHTML = ""; return;
      case "assist-copy": await navigator.clipboard.writeText(assists.get(i.id)?.markdown || ""); return toast("Copied");
      case "assist-append": {
        const a = assists.get(i.id);
        const current = $('#drawer [data-f="body"]')?.value ?? i.body;
        const body = `${current.trim()}\n\n---\n### ${ASSIST_TITLE[a.mode]} (${new Date().toLocaleDateString()})\n\n${a.markdown}\n`;
        assists.delete(i.id);
        toast("Added to your notes");
        return renderDrawer(await patch(i.id, { body }));
      }
      case "project": {
        const open = i.milestones.filter((m) => !m.done).length;
        const p = await api(`/api/ideas/${i.id}/project`, { method: "POST" });
        toast(`Project created${open ? ` with ${plural(open, "task")} from your milestones` : ""}`);
        showSection("projects");
        return Sections.projects.select(p.id);
      }
      case "goto-project": {
        const p = I.projects.find((y) => y.idea_id === i.id);
        showSection("projects");
        return Sections.projects.select(p.id);
      }
      case "delete":
        if (!confirm(`Delete “${i.title}”? This can't be undone.`)) return;
        await api(`/api/ideas/${i.id}`, { method: "DELETE" });
        closeDrawer();
        I.ideas = I.ideas.filter((y) => y.id !== i.id);
        return render();
    }
  }

  // ---------------------------------------------------------------- binding

  I.search = (q) => { I.q = q; render(); };
  I.onKey = (e) => { if (e.key === "n") { e.preventDefault(); $("#ideaInput").focus(); } };

  document.addEventListener("DOMContentLoaded", () => {
    $("#ideaCapture").onsubmit = async (e) => {
      e.preventDefault();
      const title = $("#ideaInput").value.trim();
      if (!title) return;
      const kind = $("#ideaKind").value;
      await api("/api/ideas", { method: "POST", body: { title, kind, body: TEMPLATES[kind] || "" } });
      $("#ideaInput").value = "";
      toast("Captured. Click it to develop");
      await I.show();
    };
    $("#ideaTabs").onclick = (e) => { const b = e.target.closest("[data-ikind]"); if (b) { I.kind = b.dataset.ikind; render(); } };
    $("#ideaStatus").onchange = (e) => { I.status = e.target.value; render(); };
    $("#ideaTags").onclick = (e) => { const b = e.target.closest("[data-itag]"); if (b) { I.tag = b.dataset.itag || null; render(); } };
    $("#ideaLayout").onclick = (e) => { const b = e.target.closest("[data-layout]"); if (b) { I.layout = pref("ideas.layout", b.dataset.layout); render(); } };

    const board = $("#ideaBoard");
    board.onclick = async (e) => {
      const card = e.target.closest(".idea-card");
      if (!card) return;
      const id = +card.dataset.iid;
      if (e.target.closest('[data-iact="pin"]')) return patch(id, { pinned: !I.ideas.find((x) => x.id === id).pinned });
      I.open(id);
    };
    // drag a card onto a board column to change its status
    board.addEventListener("dragstart", (e) => {
      const card = e.target.closest(".idea-card");
      if (!card) return;
      e.dataTransfer.setData("text/x-idea", card.dataset.iid);
      e.dataTransfer.effectAllowed = "move";
      card.classList.add("dragging");
    });
    board.addEventListener("dragend", (e) => e.target.closest(".idea-card")?.classList.remove("dragging"));
    board.addEventListener("dragover", (e) => {
      const col = e.target.closest("[data-drop-status]");
      if (!col || ![...e.dataTransfer.types].includes("text/x-idea")) return;
      e.preventDefault();
      $$(".board-col.over").forEach((c) => c !== col && c.classList.remove("over"));
      col.classList.add("over");
    });
    board.addEventListener("dragleave", (e) => { if (!e.currentTarget.contains(e.relatedTarget)) $$(".board-col.over").forEach((c) => c.classList.remove("over")); });
    board.addEventListener("drop", async (e) => {
      const col = e.target.closest("[data-drop-status]");
      const id = +e.dataTransfer.getData("text/x-idea");
      $$(".board-col.over").forEach((c) => c.classList.remove("over"));
      if (!col || !id) return;
      e.preventDefault();
      const i = I.ideas.find((x) => x.id === id);
      if (i && i.status !== col.dataset.dropStatus) await patch(id, { status: col.dataset.dropStatus });
    });

    const drawer = $("#drawer");
    drawer.addEventListener("click", drawerClick);
    drawer.addEventListener("input", (e) => {
      if (state.section !== "ideas" || I.openId === null) return;
      const f = e.target.dataset.f;
      if (f === "title" && e.target.value.trim()) saveField(I.openId, "title", e.target.value.trim());
      if (f === "body") saveField(I.openId, "body", e.target.value);
      if (e.target.matches("[data-qn], [data-x='qn-add'] textarea")) fitNote(e.target);
      if (e.target.dataset.qn != null) {
        const notes = readQuickNotes(I.ideas.find((x) => x.id === I.openId));
        if (notes) saveField(I.openId, "notes", notes);
      }
      if (e.target.dataset.x === "paper-q") searchPapers(e.target.value.trim());
    });
    drawer.addEventListener("change", async (e) => {
      if (state.section !== "ideas" || I.openId === null) return;
      const f = e.target.dataset.f;
      if (f === "kind" || f === "status") renderDrawer(await patch(I.openId, { [f]: e.target.value }));
    });
    drawer.addEventListener("keydown", async (e) => {
      if (state.section !== "ideas" || I.openId === null || e.key !== "Enter") return;
      const i = I.ideas.find((x) => x.id === I.openId);
      if (e.target.closest("[data-x='qn-add']") && !e.shiftKey) {
        e.preventDefault();
        const text = e.target.value.trim();
        if (!text) return;
        const notes = [...(readQuickNotes(i) || i.notes || []), { text, color: nextColor(i), at: new Date().toISOString() }];
        renderDrawer(await patch(i.id, { notes }));
        $("#drawer [data-x='qn-add'] textarea")?.focus();
        return;
      }
      if (e.target.dataset.x === "tag-input") {
        e.preventDefault();
        const t = e.target.value.trim();
        if (t && !i.tags.includes(t)) { renderDrawer(await patch(i.id, { tags: [...i.tags, t] })); $('#drawer [data-x="tag-input"]')?.focus(); }
      }
    });
    drawer.addEventListener("submit", async (e) => {
      if (state.section !== "ideas" || I.openId === null || e.target.dataset.x !== "ms-add") return;
      e.preventDefault();
      const title = $("input", e.target).value.trim();
      if (!title) return;
      const i = I.ideas.find((x) => x.id === I.openId);
      renderDrawer(await patch(i.id, { milestones: [...i.milestones, { title, done: false }] }));
      $('#drawer [data-x="ms-add"] input')?.focus();
    });
    drawer.addEventListener("drawerclose", () => { if (I.openId !== null) { I.openId = null; paperPool = null; render(); } });
  });
})();
