// Home: today at a glance. Deadlines, tasks, new papers, recent library additions and ideas.
(() => {
  const H = { placeholder: "Search papers…" };
  Sections.home = H;

  H.show = async () => {
    const hour = new Date().getHours();
    $("#homeHello").textContent = hour < 5 ? "Working late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    $("#homeDate").textContent = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
    const get = (url, fallback) => api(url).catch(() => fallback);
    const [confs, tasks, projects, papers, lib, ideas] = await Promise.all([
      get("/api/conferences", []), get("/api/tasks", []), get("/api/projects", []),
      get("/api/papers?unread=true&min_relevance=5&sort=relevance&limit=4", { items: [], total: 0 }),
      get("/api/library", []), get("/api/ideas", []),
    ]);
    if (state.section !== "home") return;
    const pname = Object.fromEntries(projects.map((p) => [p.id, p]));
    const now = new Date().toISOString();

    const deadlines = confs.flatMap((c) => c.deadlines.filter((d) => d.at >= now).map((d) => ({ ...d, c })))
      .sort((a, b) => a.at.localeCompare(b.at)).slice(0, 5);
    const open = tasks.filter((t) => t.status === "todo" || t.status === "doing");
    const focus = open.filter((t) => t.status === "doing" || (t.due && t.due <= new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10)) || t.priority === 2)
      .sort((a, b) => (a.due || "9999").localeCompare(b.due || "9999") || b.priority - a.priority).slice(0, 7);
    const recent = lib.filter((r) => !r.missing).sort((a, b) => b.added_at.localeCompare(a.added_at)).slice(0, 4);
    const recentIdeas = ideas.slice(0, 4);

    const panel = (title, link, body, extra = "") => `
      <section class="panel home-panel">
        <div class="panel-head"><h2>${title}</h2>${link ? `<a href="#${link[0]}" data-go="${link[0]}">${link[1]} ›</a>` : ""}</div>
        ${body}${extra}
      </section>`;

    $("#homeGrid").innerHTML = [
      panel("Next deadlines", ["conferences", "All venues"], deadlines.length
        ? `<ol class="timeline compact">${deadlines.map((d) => {
            const cd = countdown(d.at);
            return `<li><span class="countdown ${cd.cls}">${cd.text}</span>
              <span class="tl-main"><b>${esc(d.c.name)} ${d.c.year || ""}</b> · ${esc(d.label)}<small>${fmtWhen(d.at)}</small></span></li>`;
          }).join("")}</ol>`
        : `<p class="hint">No upcoming deadlines. <button class="text-btn" data-go="conferences">Track a conference</button> to see its countdown here.</p>`),

      panel("Focus this week", ["projects", "All tasks"], (focus.length
        ? `<ul class="task-mini">${focus.map((t) => {
            const di = dueInfo(t.due);
            return `<li><input type="checkbox" data-done="${t.id}" aria-label="Mark done">
              <span class="tm-title">${t.status === "doing" ? `<span class="pill doing">doing</span> ` : ""}${esc(t.title)}
              ${t.project_id && pname[t.project_id] ? `<small>${esc(pname[t.project_id].name)}</small>` : ""}</span>
              ${di ? `<span class="due ${di.cls}">${di.text}</span>` : ""}</li>`;
          }).join("")}</ul>`
        : `<p class="hint">${open.length ? `Nothing urgent. ${plural(open.length, "open task")} in your projects.` : "No open tasks."}</p>`) +
        `<form class="mini-add" data-add="task"><input placeholder="Quick task… (Enter)" aria-label="Quick task"></form>`),

      panel("New on the radar", ["radar", papers.total ? `${papers.total} new` : "Radar"], papers.items.length
        ? `<ul class="paper-mini">${papers.items.map((p) => `
            <li data-radar="${p.id}"><span class="score ${scoreClass(p.relevance)}">${p.relevance}</span>
              <span><b>${esc(p.title)}</b><small>${esc(p.summary?.tldr || "")}</small></span></li>`).join("")}</ul>`
        : `<p class="hint">You're caught up. <button class="text-btn" data-sweep>⟳ Sweep now</button> to look for new papers.</p>`),

      panel("Recently added", ["library", "Library"], recent.length
        ? `<ul class="paper-mini">${recent.map((r) => `
            <li data-lib="${r.id}"><span><b>${esc(r.title)}</b><small>${esc([r.authors[0], r.year, r.collection && `📁 ${r.collection}`].filter(Boolean).join(" · "))}</small></span></li>`).join("")}</ul>`
        : `<p class="hint">Your library is empty. Save papers from the Radar or drop PDFs into your library folder.</p>`),

      panel("Ideas", ["ideas", "Ideation center"], (recentIdeas.length
        ? `<ul class="paper-mini">${recentIdeas.map((i) => `
            <li data-idea="${i.id}"><span class="pill kind-${i.kind}">${i.kind}</span>
              <span><b>${esc(i.title)}</b><small>${esc((i.body || "").replace(/[#*_>`-]/g, "").slice(0, 110))}</small></span></li>`).join("")}</ul>`
        : `<p class="hint">Your ideas, proposals and roadmaps live here.</p>`) +
        `<form class="mini-add" data-add="idea"><input placeholder="Capture an idea… (Enter)" aria-label="Quick idea"></form>`),
    ].join("");
  };

  H.onKey = (e) => { if (e.key === "n") { e.preventDefault(); $('#homeGrid [data-add="task"] input')?.focus(); } };

  document.addEventListener("DOMContentLoaded", () => {
    const grid = $("#homeGrid");
    grid.onclick = async (e) => {
      const go = e.target.closest("[data-go]");
      if (go) { e.preventDefault(); return showSection(go.dataset.go); }
      if (e.target.closest("[data-sweep]")) return startSweep();
      const done = e.target.closest("[data-done]");
      if (done) {
        await api(`/api/tasks/${done.dataset.done}`, { method: "PATCH", body: { status: "done" } });
        toast("Done ✓");
        refreshNavBadges();
        return H.show();
      }
      const r = e.target.closest("[data-radar]");
      if (r) return openRadarPaper(+r.dataset.radar);
      const l = e.target.closest("[data-lib]");
      if (l) { showSection("library"); return Sections.library.open(+l.dataset.lib); }
      const i = e.target.closest("[data-idea]");
      if (i) { showSection("ideas"); return Sections.ideas.open(+i.dataset.idea); }
    };
    grid.onsubmit = async (e) => {
      e.preventDefault();
      const input = e.target.querySelector("input");
      const title = input.value.trim();
      if (!title) return;
      if (e.target.dataset.add === "task") {
        await api("/api/tasks", { method: "POST", body: { title, status: "todo" } });
        toast("Task added to your inbox");
      } else {
        await api("/api/ideas", { method: "POST", body: { title } });
        toast("Idea captured");
      }
      input.value = "";
      await H.show();
      $(`#homeGrid [data-add="${e.target.dataset.add}"] input`)?.focus();
    };
  });
})();
