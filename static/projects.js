// Projects: a to-do board (To do / In progress / Done) and a Future backlog per project, with an
// optional target venue whose deadline counts down in the project header.
(() => {
  const P = {
    placeholder: "Search tasks…",
    projects: [], tasks: [], conferences: [], ideas: [], q: "",
    sel: pref("proj.sel") ?? "all",  // "all" | "inbox" | project id
    view: "todo", showAllDone: false, editTask: null, editProject: null,
  };
  Sections.projects = P;
  const COLORS = ["#ff8b7b", "#fff27a", "#8fe3a4", "#7cc4ff", "#c9a7ff", "#ffb86b", "#5ee6d0", "#f78fb3"];
  const COLS = [["todo", "Missions"], ["doing", "In action"], ["done", "Completed"]];

  P.show = async () => {
    [P.projects, P.tasks, P.conferences, P.ideas] = await Promise.all([
      api("/api/projects"), api("/api/tasks"), api("/api/conferences").catch(() => []), api("/api/ideas").catch(() => []),
    ]);
    if (typeof P.sel === "number" && !P.projects.some((p) => p.id === P.sel)) P.sel = "all";
    render();
  };

  P.select = async (id) => {
    P.sel = pref("proj.sel", id);
    P.view = "todo";
    await P.show();
  };

  const project = () => (typeof P.sel === "number" ? P.projects.find((p) => p.id === P.sel) : null);
  const pmap = () => Object.fromEntries(P.projects.map((p) => [p.id, p]));
  const inScope = (t) => P.sel === "all" || (P.sel === "inbox" ? !t.project_id : t.project_id === P.sel);
  const matches = (t) => !P.q || `${t.title} ${t.notes}`.toLowerCase().includes(P.q.toLowerCase());
  const openCount = (xs) => xs.filter((t) => t.status === "todo" || t.status === "doing").length;
  const confTitle = (c) => `${c.name}${c.year && !String(c.name).includes(c.year) ? ` ${c.year}` : ""}`;

  async function reload() {
    [P.projects, P.tasks] = await Promise.all([api("/api/projects"), api("/api/tasks")]);
    render();
  }

  // ---------------------------------------------------------------- rendering

  function render() {
    $("#questHud").innerHTML = Quest.hud(P.tasks);
    renderSide();
    renderHead();
    renderNotes();
    $$("#projTabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.pview === P.view));
    const scoped = P.tasks.filter(inScope);
    $("#projCountTodo").textContent = openCount(scoped) || "";
    $("#projCountFuture").textContent = scoped.filter((t) => t.status === "future").length || "";
    const sel = $("#taskProj");
    sel.hidden = typeof P.sel === "number";
    const cur = project(), chip = $("#taskProjName");
    chip.hidden = !cur;
    if (cur) chip.innerHTML = `<span class="color-dot" style="background:${cur.color || "var(--ink)"}"></span>${esc(cur.name)}`;
    sel.innerHTML = `<option value="">Inbox</option>` + P.projects.filter((p) => p.status !== "done").map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    $("#taskTitle").placeholder = P.view === "future" ? "Add a future task: something for later… (Enter)" : "Add a mission… (Enter)";
    if (P.view === "todo") renderBoard(scoped.filter(matches));
    else renderFuture(scoped.filter(matches));
    renderStreak(scoped);
  }

  // ---------------------------------------------------------------- activity map
  // GitHub-style grid of the last year: a day turns red when at least one task was completed that day
  // (deeper red for more). Follows the current scope: All tasks, Inbox or one project.

  const localDay = (iso) => { const d = new Date(iso); return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
  const shiftDay = (day, n) => new Date(new Date(day + "T12:00:00Z").getTime() + n * 864e5).toISOString().slice(0, 10);
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function renderStreak(tasks) {
    const perDay = {};
    for (const t of tasks) if (t.status === "done" && t.done_at) { const d = localDay(t.done_at); perDay[d] = (perDay[d] || 0) + 1; }
    const today = todayISO();
    const dow = new Date(today + "T12:00:00Z").getUTCDay();          // 0 = Sunday
    const start = shiftDay(today, -(52 * 7 + dow));                  // Sunday 52 weeks back → 53 columns
    let cells = "", months = "", active = 0, total = 0, lastMonth = -1;
    for (let w = 0; w < 53; w++) {
      const first = shiftDay(start, w * 7), m = +first.slice(5, 7) - 1;
      if (m !== lastMonth) { if (w < 51) months += `<span style="grid-column:${w + 1}">${MONTHS[m]}</span>`; lastMonth = m; }
      for (let d = 0; d < 7; d++) {
        const day = shiftDay(start, w * 7 + d);
        if (day > today) { cells += `<span class="sm-cell future" style="grid-area:${d + 1}/${w + 1}"></span>`; continue; }
        const n = perDay[day] || 0;
        if (n) { active++; total += n; }
        const lvl = n === 0 ? 0 : n === 1 ? 1 : n <= 3 ? 2 : n <= 5 ? 3 : 4;
        const label = `${n ? plural(n, "task") + " done" : "No tasks done"} on ${new Date(day + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" })}`;
        cells += `<span class="sm-cell l${lvl}${day === today ? " today" : ""}" style="grid-area:${d + 1}/${w + 1}" title="${label}"></span>`;
      }
    }
    let streak = 0, d = perDay[today] ? today : shiftDay(today, -1);
    while (perDay[d]) { streak++; d = shiftDay(d, -1); }
    const el = $("#projStreak");
    el.innerHTML = `
      <div class="panel-head"><h2>Activity</h2>
        <span class="hint">${plural(total, "task")} done on ${plural(active, "day")} in the last year · current streak ${plural(streak, "day")}${perDay[today] ? "" : " · nothing done today yet"}</span></div>
      <div class="sm-scroll"><div class="sm-wrap">
        <div class="sm-days"><span></span><span>Mon</span><span></span><span>Wed</span><span></span><span>Fri</span><span></span></div>
        <div><div class="sm-months">${months}</div><div class="sm-grid">${cells}</div></div>
      </div></div>
      <div class="sm-legend">Less <span class="sm-cell l0"></span><span class="sm-cell l1"></span><span class="sm-cell l2"></span><span class="sm-cell l3"></span><span class="sm-cell l4"></span> More</div>`;
    const sc = el.querySelector(".sm-scroll");
    sc.scrollLeft = sc.scrollWidth;  // most recent weeks first on narrow screens
  }

  function renderSide() {
    const row = (key, label, n, color, extra = "") => `
      <button class="person-row coll ${P.sel === key ? "active" : ""}" data-psel="${key}">
        <span class="color-dot" style="background:${color || "var(--surface-2)"}"></span>
        <span class="person-main"><span class="person-name">${esc(label)}</span>${extra}</span>
        ${n ? `<span class="count">${n}</span>` : ""}
      </button>`;
    const active = P.projects.filter((p) => p.status === "active"), rest = P.projects.filter((p) => p.status !== "active");
    const proj = (p) => {
      const c = P.conferences.find((x) => x.id === p.conference_id);
      const nd = c?.deadlines.find((d) => new Date(d.at) > Date.now());
      return row(p.id, p.name, openCount(P.tasks.filter((t) => t.project_id === p.id)), p.color || "var(--ink)",
        nd ? `<span class="person-inst">🎯 ${esc(confTitle(c))} · ${countdown(nd.at).text}</span>` : p.status !== "active" ? `<span class="person-inst">${p.status}</span>` : "");
    };
    $("#projList").innerHTML =
      row("all", "All tasks", openCount(P.tasks), "linear-gradient(135deg,#ff8b7b,#7cc4ff)") +
      row("inbox", "Inbox", openCount(P.tasks.filter((t) => !t.project_id)), "transparent") +
      active.map(proj).join("") +
      (rest.length ? `<div class="side-sep">Paused &amp; done</div>` + rest.map(proj).join("") : "") +
      `<button class="person-row add" id="projNew">+ New project</button>`;
  }

  function renderHead() {
    const p = project();
    const head = $("#projHead");
    if (!p) {
      const open = openCount(P.tasks.filter(inScope));
      head.innerHTML = `<div><h1>${P.sel === "inbox" ? "Inbox" : "All tasks"}</h1>
        <p class="sub">${P.sel === "inbox" ? "Tasks that don't belong to a project yet." : `${plural(open, "open task")} across ${plural(P.projects.filter((x) => x.status === "active").length, "active project")}.`}</p></div>
        <div class="head-actions"><button class="primary-btn" id="projNewHead">+ New project</button></div>`;
      return;
    }
    const tasks = P.tasks.filter((t) => t.project_id === p.id && t.status !== "future");
    const done = tasks.filter((t) => t.status === "done").length;
    const c = P.conferences.find((x) => x.id === p.conference_id);
    const nd = c?.deadlines.find((d) => new Date(d.at) > Date.now());
    const idea = P.ideas.find((i) => i.id === p.idea_id);
    head.innerHTML = `
      <div class="proj-title">
        <h1><span class="color-dot big" style="background:${p.color || "var(--ink)"}"></span>${esc(p.name)} ${p.status !== "active" ? `<span class="pill">${p.status}</span>` : ""}</h1>
        ${p.description ? `<p class="sub">${esc(p.description)}</p>` : ""}
        <div class="proj-chips">
          ${c ? `<button class="chip static" data-goto="conferences" title="${nd ? esc(`${nd.label}: ${fmtWhen(nd.at)}`) : ""}">🎯 ${esc(confTitle(c))}${nd ? ` · ${esc(nd.label)} in <b class="countdown-inline ${countdown(nd.at).cls}">${countdown(nd.at).text}</b>` : " · no open deadlines"}</button>` : ""}
          ${idea ? `<button class="chip static" data-goto-idea="${idea.id}">💡 From idea: ${esc(idea.title)}</button>` : ""}
        </div>
        ${tasks.length ? `<div class="mini-progress wide" title="${done} of ${tasks.length} done"><span style="width:${(100 * done) / tasks.length}%"></span></div>
          <p class="hint">${done} of ${plural(tasks.length, "task")} done</p>` : ""}
      </div>
      <div class="head-actions"><button class="ghost-btn" id="projEdit">Edit project</button></div>`;
  }

  // ---------------------------------------------------------------- quick notes
  // Small sticky notes in the right-hand sidebar, one list per project ({text, color, at}). Hidden for All tasks / Inbox.

  const NOTE_COLORS = ["#fff27a", "#ff8b7b", "#8fe3a4", "#7cc4ff", "#c9a7ff", "#ffb86b"];
  const nextColor = (p) => NOTE_COLORS[(p.notes || []).length % NOTE_COLORS.length];
  const fitNote = (ta) => { ta.style.height = "auto"; ta.style.height = `${ta.scrollHeight}px`; };
  let notesFor = null, saveTimer = null;

  // the project's notes with any unsaved typing from the sidebar
  function readNotes(p) {
    const boxes = $$("#projNotes [data-qn]");
    return (p.notes || []).map((n, k) => {
      const box = boxes.find((b) => +b.dataset.qn === k);
      return box && box.value !== n.text ? { ...n, text: box.value } : n;
    });
  }

  async function saveNotes(p, notes, rerender = true) {
    clearTimeout(saveTimer);
    p.notes = notes;
    try {
      const updated = await api(`/api/projects/${p.id}`, { method: "PATCH", body: { notes } });
      p.notes = updated.notes;
    } catch (err) { toast(err.message); }
    if (rerender) renderNotes(true);
  }

  function renderNotes(force = false) {
    const p = project(), aside = $(".proj-notes");
    aside.hidden = !p || state.section !== "projects";
    if (!p) { notesFor = null; return; }
    // don't yank the textarea out from under someone typing when the board re-renders
    if (!force && notesFor === p.id && aside.contains(document.activeElement)) return;
    notesFor = p.id;
    const notes = p.notes || [];
    $("#projNotesCount").textContent = notes.length || "";
    $("#projNotes").innerHTML = `<div class="qnotes">${notes.map((n, k) => `
        <div class="qnote" style="--qn:${esc(n.color || NOTE_COLORS[k % NOTE_COLORS.length])}">
          <textarea data-qn="${k}" rows="2" aria-label="Note">${esc(n.text)}</textarea>
          <div class="qn-foot">
            <span>${n.at ? timeAgo(n.at) : ""}</span>
            <span class="spacer"></span>
            <button class="qn-color" data-qn-color="${k}" title="Change colour" aria-label="Change colour"></button>
            <button class="icon-btn tiny" data-qn-del="${k}" title="Delete note" aria-label="Delete note">✕</button>
          </div>
        </div>`).join("")}
        <form class="qnote add" data-qn-add style="--qn:${nextColor(p)}">
          <textarea rows="2" placeholder="+ Jot a note (Enter to add, Shift+Enter for a new line)" aria-label="New note"></textarea>
        </form>
      </div>`;
    $$("#projNotes textarea").forEach(fitNote);
  }

  function taskCard(t, showProject) {
    const di = dueInfo(t.due);
    const p = showProject && t.project_id && pmap()[t.project_id];
    return `
    <article class="task-card prio-${t.priority} ${t.status}" data-tid="${t.id}" draggable="true">
      <input type="checkbox" data-tact="toggle" ${t.status === "done" ? "checked" : ""} aria-label="Done">
      <div class="tc-main">
        <div class="tc-title">${t.priority === 2 ? `<span class="prio" title="High priority">‼</span> ` : ""}${esc(t.title)}</div>
        ${t.notes ? `<div class="tc-notes">${esc(t.notes.slice(0, 140))}</div>` : ""}
        <div class="tc-meta">
          ${Quest.chip(t)}
          ${di && t.status !== "done" ? `<span class="due ${di.cls}">${di.text}</span>` : ""}
          ${p ? `<span class="tag" style="border-left:6px solid ${p.color || "var(--ink)"}">${esc(p.name)}</span>` : ""}
          ${t.status === "done" && t.done_at ? `<span class="meta">done ${timeAgo(t.done_at)}</span>` : ""}
        </div>
      </div>
      <div class="tc-actions">
        ${t.status === "todo" ? `<button class="icon-btn tiny" data-tact="start" title="Start (move to In progress)">▶</button>` : ""}
        ${t.status !== "done" ? `<button class="icon-btn tiny" data-tact="later" title="Move to Future">⏭</button>` : ""}
      </div>
    </article>`;
  }

  function renderBoard(xs) {
    const showProject = P.sel === "all";
    $("#projBody").className = "board tasks";
    $("#projBody").innerHTML = COLS.map(([k, label]) => {
      let col = xs.filter((t) => t.status === k);
      if (k === "done") col.sort((a, b) => (b.done_at || "").localeCompare(a.done_at || ""));
      const hidden = k === "done" && !P.showAllDone ? Math.max(0, col.length - 8) : 0;
      if (hidden) col = col.slice(0, 8);
      return `<section class="board-col" data-drop-status="${k}">
        <h3>${label} <span class="badge">${xs.filter((t) => t.status === k).length || ""}</span></h3>
        <div class="board-cards">${col.map((t) => taskCard(t, showProject)).join("") ||
          `<p class="hint col-empty">${k === "todo" ? "No missions queued. Add one above." : k === "doing" ? "Drag a mission here when you start it." : "Completed missions land here. Go earn some XP!"}</p>`}
          ${hidden ? `<button class="text-btn" data-show-done>Show ${hidden} more</button>` : ""}</div>
      </section>`;
    }).join("");
  }

  function renderFuture(xs) {
    const fut = xs.filter((t) => t.status === "future");
    const groups = P.sel === "all"
      ? [[null, "Inbox"], ...P.projects.map((p) => [p.id, p.name])].map(([id, name]) => [name, fut.filter((t) => (t.project_id || null) === id), id && pmap()[id]?.color])
        .filter(([, ts]) => ts.length)
      : [[null, fut, null]];
    $("#projBody").className = "future";
    $("#projBody").innerHTML = fut.length ? groups.map(([name, ts, color]) => `
      <section class="panel future-group">
        ${name ? `<div class="panel-head"><h2><span class="color-dot" style="background:${color || "var(--surface-2)"}"></span>${esc(name)}</h2><span class="badge">${ts.length}</span></div>` : ""}
        <ul class="future-list">${ts.map((t) => {
          const di = dueInfo(t.due);
          return `<li data-tid="${t.id}">
            <div class="tc-main"><div class="tc-title">${t.priority === 2 ? `<span class="prio">‼</span> ` : ""}${esc(t.title)}</div>
              ${t.notes ? `<div class="tc-notes">${esc(t.notes.slice(0, 200))}</div>` : ""}</div>
            ${di ? `<span class="due ${di.cls}">${di.text}</span>` : ""}
            <button class="ghost-btn" data-tact="now" title="Move to the To-do board">→ To-do</button>
          </li>`;
        }).join("")}</ul>
      </section>`).join("")
      : `<div class="empty"><h3>No future tasks</h3><p>Park things you'll do later here (follow-up experiments, "someday" reading, next paper's ideas) so the To-do board stays focused.</p></div>`;
  }

  // ---------------------------------------------------------------- tasks

  // `at` = where to burst the confetti if this completes a mission
  async function patchTask(id, body, at) {
    const before = Quest.stats(P.tasks);
    await api(`/api/tasks/${id}`, { method: "PATCH", body });
    await reload();
    Quest.celebrate(before, Quest.stats(P.tasks), at);
  }

  // Due dates are optional: empty fields read "No date", and a small × inside the field clears a set date.
  function addDateClear(input) {
    const wrap = document.createElement("span");
    wrap.className = "date-wrap no-date";
    input.replaceWith(wrap);
    const x = document.createElement("button");
    x.type = "button";
    x.className = "date-clear";
    x.textContent = "×";
    x.title = x.ariaLabel = "No date";
    x.onclick = () => { input.value = ""; syncDate(input); };
    wrap.append(input, x);
    input.addEventListener("input", () => syncDate(input));
    input.addEventListener("change", () => syncDate(input));
    syncDate(input);
  }
  function syncDate(input) {
    const wrap = input.parentElement;
    if (!wrap?.classList.contains("date-wrap")) return;
    // Pin the quick-add box to its plain width before the × takes room, so it never grows.
    if (input.value && !wrap.style.width && !input.closest(".form") && input.offsetWidth) wrap.style.width = `${input.offsetWidth}px`;
    wrap.classList.toggle("no-date", !input.value);
  }

  function openTask(t) {
    P.editTask = t.id;
    const f = $("#taskForm");
    f.title.value = t.title;
    f.notes.value = t.notes;
    f.status.value = t.status;
    f.priority.value = t.priority;
    f.due.value = t.due;
    syncDate(f.due);
    f.project_id.innerHTML = `<option value="">Inbox (no project)</option>` + P.projects.map((p) => `<option value="${p.id}" ${p.id === t.project_id ? "selected" : ""}>${esc(p.name)}</option>`).join("");
    $("#taskModal").showModal();
  }

  async function saveTask(e) {
    e.preventDefault();
    const f = e.target;
    const pid = f.project_id.value ? +f.project_id.value : null;
    await patchTask(P.editTask, {
      title: f.title.value.trim(), notes: f.notes.value, status: f.status.value, priority: +f.priority.value, due: f.due.value,
      ...(pid ? { project_id: pid } : { clear_project: true }),
    });
    $("#taskModal").close();
  }

  // ---------------------------------------------------------------- projects

  function openProject(p) {
    P.editProject = p?.id ?? null;
    const f = $("#projectForm");
    f.reset();
    $("#pmTitle").textContent = p ? `Edit “${p.name}”` : "New project";
    $("#pmDelete").hidden = !p;
    f.name.value = p?.name || "";
    f.description.value = p?.description || "";
    f.status.value = p?.status || "active";
    f.conference_id.innerHTML = `<option value="">(none)</option>` + P.conferences.map((c) => {
      const nd = c.deadlines.find((d) => new Date(d.at) > Date.now());
      return `<option value="${c.id}" ${c.id === p?.conference_id ? "selected" : ""}>${esc(confTitle(c))}${nd ? ` · ${esc(nd.label)} in ${countdown(nd.at).text}` : ""}</option>`;
    }).join("") + `<option value="__add">+ Track a venue first…</option>`;
    const color = p?.color || COLORS[P.projects.length % COLORS.length];
    $("#pmColors").innerHTML = COLORS.map((c) => `<label class="swatch" style="background:${c}"><input type="radio" name="color" value="${c}" ${c === color ? "checked" : ""} aria-label="Colour ${c}"></label>`).join("");
    $("#projectModal").showModal();
    f.name.focus();
  }

  async function saveProject(e) {
    e.preventDefault();
    const f = e.target;
    const cid = f.conference_id.value && f.conference_id.value !== "__add" ? +f.conference_id.value : null;
    const body = {
      name: f.name.value.trim(), description: f.description.value.trim(), status: f.status.value,
      color: f.querySelector("input[name=color]:checked")?.value || "", ...(cid ? { conference_id: cid } : { clear_conference: true }),
    };
    try {
      const p = P.editProject
        ? await api(`/api/projects/${P.editProject}`, { method: "PATCH", body })
        : await api("/api/projects", { method: "POST", body });
      $("#projectModal").close();
      P.sel = pref("proj.sel", p.id);
      await reload();
    } catch (err) { toast(err.message); }
  }

  async function deleteProject() {
    const p = P.projects.find((x) => x.id === P.editProject);
    const n = P.tasks.filter((t) => t.project_id === p.id).length;
    if (!confirm(`Delete “${p.name}”${n ? ` and its ${plural(n, "task")}` : ""}?`)) return;
    await api(`/api/projects/${p.id}`, { method: "DELETE" });
    $("#projectModal").close();
    P.sel = pref("proj.sel", "all");
    await reload();
  }

  // ---------------------------------------------------------------- binding

  P.search = (q) => { P.q = q; render(); };
  P.onKey = (e) => { if (e.key === "n") { e.preventDefault(); $("#taskTitle").focus(); } };

  document.addEventListener("DOMContentLoaded", () => {
    $("#projList").onclick = (e) => {
      if (e.target.closest("#projNew")) return openProject(null);
      const b = e.target.closest("[data-psel]");
      if (!b) return;
      const v = b.dataset.psel;
      P.sel = pref("proj.sel", v === "all" || v === "inbox" ? v : +v);
      P.showAllDone = false;
      render();
    };
    const notesEl = $("#projNotes");
    notesEl.addEventListener("input", (e) => {
      if (!e.target.matches("textarea")) return;
      fitNote(e.target);
      const p = project();
      if (!p || e.target.dataset.qn == null) return;
      clearTimeout(saveTimer);
      const notes = readNotes(p);
      saveTimer = setTimeout(() => saveNotes(p, notes, false), 500);
    });
    notesEl.addEventListener("keydown", async (e) => {
      const p = project();
      if (!p || e.key !== "Enter" || e.shiftKey || !e.target.closest("[data-qn-add]")) return;
      e.preventDefault();
      const text = e.target.value.trim();
      if (!text) return;
      await saveNotes(p, [...readNotes(p), { text, color: nextColor(p), at: new Date().toISOString() }]);
      $("#projNotes [data-qn-add] textarea")?.focus();
    });
    notesEl.addEventListener("submit", (e) => e.preventDefault());
    notesEl.addEventListener("click", async (e) => {
      const p = project();
      if (!p) return;
      const del = e.target.closest("[data-qn-del]"), col = e.target.closest("[data-qn-color]");
      if (del) {
        const notes = readNotes(p), k = +del.dataset.qnDel;
        if (notes[k].text.trim() && !confirm("Delete this note?")) return;
        return saveNotes(p, notes.filter((_, j) => j !== k));
      }
      if (col) {
        const notes = readNotes(p), k = +col.dataset.qnColor, at = NOTE_COLORS.indexOf(notes[k].color);
        return saveNotes(p, notes.map((n, j) => (j === k ? { ...n, color: NOTE_COLORS[(at + 1) % NOTE_COLORS.length] } : n)));
      }
    });

    $("#projHead").onclick = (e) => {
      if (e.target.closest("#projEdit")) return openProject(project());
      if (e.target.closest("#projNewHead")) return openProject(null);
      if (e.target.closest("[data-goto]")) return showSection("deadlines");
      const idea = e.target.closest("[data-goto-idea]");
      if (idea) { showSection("ideas"); Sections.ideas.open(+idea.dataset.gotoIdea); }
    };
    $("#questHud").onclick = (e) => {
      const b = e.target.closest("[data-goal]");
      if (b) { Quest.adjustGoal(+b.dataset.goal); render(); }
    };
    $("#projTabs").onclick = (e) => { const b = e.target.closest("[data-pview]"); if (b) { P.view = b.dataset.pview; render(); } };
    $("#taskAdd").onsubmit = async (e) => {
      e.preventDefault();
      const title = $("#taskTitle").value.trim();
      if (!title) return;
      const pid = typeof P.sel === "number" ? P.sel : P.sel === "inbox" ? null : ($("#taskProj").value ? +$("#taskProj").value : null);
      try {
        await api("/api/tasks", { method: "POST", body: {
          title, due: $("#taskDue").value, priority: +$("#taskPrio").value,
          status: P.view === "future" ? "future" : "todo", ...(pid ? { project_id: pid } : {}),
        } });
      } catch (err) { return toast(err.message); }
      $("#taskTitle").value = "";
      $("#taskDue").value = "";
      syncDate($("#taskDue"));
      $("#taskPrio").value = "1";
      await reload();
      $("#taskTitle").focus();
    };

    const body = $("#projBody");
    body.onclick = async (e) => {
      if (e.target.closest("[data-show-done]")) { P.showAllDone = true; return render(); }
      const el = e.target.closest("[data-tid]");
      if (!el) return;
      const t = P.tasks.find((x) => x.id === +el.dataset.tid);
      const act = e.target.closest("[data-tact]")?.dataset.tact;
      if (act === "toggle") return patchTask(t.id, { status: t.status === "done" ? "todo" : "done" }, Quest.spot(e.target));
      if (act === "start") return patchTask(t.id, { status: "doing" });
      if (act === "later") { toast("Moved to Future"); return patchTask(t.id, { status: "future" }); }
      if (act === "now") { toast("Moved to To-do"); return patchTask(t.id, { status: "todo" }); }
      openTask(t);
    };
    body.addEventListener("dragstart", (e) => {
      const card = e.target.closest(".task-card");
      if (!card) return;
      e.dataTransfer.setData("text/x-task", card.dataset.tid);
      e.dataTransfer.effectAllowed = "move";
      card.classList.add("dragging");
    });
    body.addEventListener("dragend", (e) => e.target.closest(".task-card")?.classList.remove("dragging"));
    body.addEventListener("dragover", (e) => {
      const col = e.target.closest("[data-drop-status]");
      if (!col || ![...e.dataTransfer.types].includes("text/x-task")) return;
      e.preventDefault();
      $$("#projBody .board-col.over").forEach((c) => c !== col && c.classList.remove("over"));
      col.classList.add("over");
    });
    body.addEventListener("drop", async (e) => {
      const col = e.target.closest("[data-drop-status]");
      const id = +e.dataTransfer.getData("text/x-task");
      $$("#projBody .board-col.over").forEach((c) => c.classList.remove("over"));
      if (!col || !id) return;
      e.preventDefault();
      const t = P.tasks.find((x) => x.id === id);
      if (t && t.status !== col.dataset.dropStatus) await patchTask(id, { status: col.dataset.dropStatus }, { x: e.clientX, y: e.clientY });
    });

    $("#taskForm").onsubmit = saveTask;
    addDateClear($("#taskDue"));
    addDateClear($("#taskForm").due);
    $("#tkDelete").onclick = async () => {
      await api(`/api/tasks/${P.editTask}`, { method: "DELETE" });
      $("#taskModal").close();
      await reload();
    };
    $("#projectForm").onsubmit = saveProject;
    $("#projectForm").conference_id.onchange = (e) => {
      if (e.target.value !== "__add") return;
      $("#projectModal").close();
      showSection("deadlines");
      toast("Track the venue you're aiming for, then pick it as the project's target");
    };
    $("#pmDelete").onclick = deleteProject;
  });
})();
