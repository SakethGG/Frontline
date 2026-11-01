// Deadlines: personal deadlines and tracked venues' deadlines on one live timeline, plus conferences &
// workshops (tracked venues, catalog suggestions for the user's focus areas, a manual editor).
(() => {
  const C = {
    placeholder: "Search deadlines and venues…",
    tracked: [], mine: [], projects: [], catalog: null, catalogTags: [], focus: [], q: "",
    showPast: false, filter: "all", catTag: null, editId: null, dlEditId: null,
  };
  Sections.deadlines = C;
  const KIND = { conference: "Conference", workshop: "Workshop", journal: "Journal", other: "Venue" };
  const TZ_ALIASES = { PST: "America/Los_Angeles", PDT: "America/Los_Angeles", PT: "America/Los_Angeles", EST: "America/New_York", EDT: "America/New_York", ET: "America/New_York", CST: "America/Chicago", CET: "Europe/Paris", CEST: "Europe/Paris", BST: "Europe/London", JST: "Asia/Tokyo", KST: "Asia/Seoul", IST: "Asia/Kolkata" };
  const LOCAL_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const TZ_CHOICES = [["AoE", "AoE (UTC−12, anywhere on Earth)"], ["UTC", "UTC"], [LOCAL_TZ, `${LOCAL_TZ} (your time)`],
    ["America/Los_Angeles", "Pacific time"], ["America/New_York", "Eastern time"], ["Europe/London", "UK time"],
    ["Europe/Paris", "Central European time"], ["Asia/Shanghai", "China time"], ["Asia/Tokyo", "Japan time"]];

  // ---------------------------------------------------------------- time zones

  function fixedOffset(tz) {  // minutes east of UTC for AoE / UTC±H(:MM), else null
    const t = String(tz || "").trim();
    if (!t || /^aoe$/i.test(t)) return -720;
    const m = t.match(/^(?:UTC|GMT)\s*(?:([+-])\s*(\d{1,2})(?::?(\d{2}))?)?$/i);
    if (!m) return null;
    if (!m[1]) return 0;
    const mins = +m[2] * 60 + +(m[3] || 0);
    return m[1] === "+" ? mins : -mins;
  }
  // a UTC instant as wall-clock time in the deadline's own zone, for the editor ("YYYY-MM-DDTHH:MM")
  function inZone(iso, tz) {
    const off = fixedOffset(tz);
    if (off !== null) return new Date(new Date(iso).getTime() + off * 60000).toISOString().slice(0, 16);
    try {
      const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: TZ_ALIASES[tz.toUpperCase()] || tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
        .formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
      return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
    } catch { return iso.slice(0, 16); }
  }

  // ---------------------------------------------------------------- loading

  C.show = async () => {
    [C.tracked, C.mine, C.focus, C.projects] = await Promise.all([api("/api/conferences"), api("/api/deadlines"),
      api("/api/conferences/focus"), api("/api/projects").catch(() => [])]);
    render();
    loadCatalog().then(renderSuggestions).catch((e) => {
      $("#confSuggest").innerHTML = `<p class="hint">Couldn't load the conference catalog (${esc(e.message)}). You can still add venues manually.</p>`;
    });
  };

  async function loadCatalog(refresh = false) {
    if (C.catalog && !refresh) return C.catalog;
    if (!C.catalog) $("#confSuggest").innerHTML = `<p class="loading">Loading the conference catalog…</p>`;
    const r = await api(`/api/conferences/catalog${refresh ? "?refresh=true" : ""}`);
    C.catalog = r.items;
    C.catalogTags = r.tags;
    C.fetchedAt = r.fetched_at;
    return C.catalog;
  }

  const nextDeadline = (c) => c.deadlines.find((d) => new Date(d.at) > Date.now());
  const matches = (c) => !C.q || [c.name, c.full_name, c.location, c.parent, c.notes, (c.tags || []).join(" "), String(c.year || "")]
    .join(" ").toLowerCase().includes(C.q.toLowerCase());
  const matchesMine = (d) => !C.q || `${d.title} ${d.notes}`.toLowerCase().includes(C.q.toLowerCase());
  const title = (c) => `${c.name}${c.year && !String(c.name).includes(c.year) ? ` ${c.year}` : ""}`;
  function dateRange(c) {
    if (c.date_text) return c.date_text;
    if (!c.start_date) return "";
    const f = (d) => new Date(d + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    return c.end_date && c.end_date !== c.start_date ? `${f(c.start_date)} – ${f(c.end_date)}` : f(c.start_date);
  }

  // ---------------------------------------------------------------- rendering

  function render() {
    renderFocus();
    renderTimeline();
    renderTracked();
    if (C.catalog) renderSuggestions();
  }

  function renderFocus() {
    $("#confFocus").innerHTML = `<span class="row-label">Focus</span>` +
      (C.focus.length ? C.focus.map((t) => `<span class="chip static">${esc(t.replace(/-/g, " "))}</span>`).join("") : `<span class="hint inline">Pick the areas you submit to and get matching venues suggested.</span>`) +
      `<button class="chip ghost" id="confEditFocus">${C.focus.length ? "✎ Edit" : "+ Choose focus areas"}</button>`;
  }

  function renderTimeline() {
    const now = Date.now(), cutoff = now - 60 * 864e5;
    const venueRows = C.filter === "personal" ? [] : C.tracked.filter(matches).flatMap((c) => c.deadlines.map((d) => ({ ...d, c })))
      .filter((d) => (C.showPast ? new Date(d.at) > cutoff : new Date(d.at) > now));
    // personal: open ones stay until ticked off (overdue shows as "passed"); done ones only with "Show past"
    const mineRows = C.filter === "venues" ? [] : C.mine.filter(matchesMine)
      .filter((d) => (C.showPast ? new Date(d.at) > cutoff || !d.done : !d.done && new Date(d.at) > cutoff));
    const rows = [...venueRows, ...mineRows].sort((a, b) => new Date(a.at) - new Date(b.at));
    $("#confTimeline").innerHTML = rows.map((d) => (d.c ? venueRow(d) : personalRow(d))).join("") ||
      `<li class="hint">${C.tracked.length || C.mine.length ? "Nothing coming up." : "Add a deadline of your own, or track a venue to see its deadlines counting down here."}</li>`;
  }

  function venueRow(d) {
    const cd = countdown(d.at);
    return `<li class="${cd.cls}">
      <span class="countdown ${cd.cls}" title="${esc(new Date(d.at).toString())}">${cd.text}</span>
      <span class="tl-main"><b>${esc(title(d.c))}</b>${d.c.kind !== "conference" ? ` <span class="kind-badge">${KIND[d.c.kind]}</span>` : ""} · ${esc(d.label)}
        <small>${fmtWhen(d.at)} your time${d.tz && d.tz !== LOCAL_TZ ? ` · ${esc(inZone(d.at, d.tz).replace("T", " "))} ${esc(d.tz)}` : ""}</small></span>
      ${d.c.url ? `<a class="act" href="${esc(d.c.url)}" target="_blank" rel="noopener" title="Website">↗</a>` : ""}
    </li>`;
  }

  function personalRow(d) {
    const cd = d.done ? { text: "done", cls: "past" } : countdown(d.at);
    const p = C.projects.find((x) => x.id === d.project_id);
    return `<li class="personal ${cd.cls} ${d.done ? "done" : ""}" data-dl="${d.id}" ${p?.color ? `style="border-left-color:${esc(p.color)}"` : ""}>
      <input type="checkbox" class="tl-check" data-dl-done ${d.done ? "checked" : ""} aria-label="Mark done">
      <span class="countdown ${cd.cls}">${cd.text}</span>
      <span class="tl-main" title="Edit"><b>${esc(d.title)}</b>${p ? ` · ${esc(p.name)}` : ""}
        <small>${d.all_day ? fmtDay(d.at) : `${fmtWhen(d.at)}`}${d.notes ? ` · ${esc(d.notes.split("\n")[0])}` : ""}</small></span>
    </li>`;
  }

  function deadlineList(c) {
    return c.deadlines.length ? `<ul class="cc-deadlines">${c.deadlines.map((d) => {
      const past = new Date(d.at) < Date.now();
      return `<li class="${past ? "past" : ""}"><span>${esc(d.label)}</span><span>${new Date(d.at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}</span></li>`;
    }).join("")}</ul>` : `<p class="hint">No deadlines announced yet.</p>`;
  }

  function cardHTML(c, tracked) {
    const nd = nextDeadline(c);
    const cd = nd && countdown(nd.at);
    const projs = tracked ? C.projects.filter((p) => p.conference_id === c.id) : [];
    const meta = [c.location && `📍 ${esc(c.location)}`, dateRange(c) && `🗓 ${esc(dateRange(c))}`, c.parent && `at ${esc(c.parent)}`].filter(Boolean).join(" · ");
    return `
    <article class="conf-card ${tracked ? "" : "suggest"}" data-cid="${tracked ? c.id : ""}" data-key="${esc(c.source_key || "")}">
      <div class="cc-head">
        <h3>${esc(title(c))}</h3>
        ${c.kind !== "conference" ? `<span class="kind-badge">${KIND[c.kind]}</span>` : ""}
        <span class="spacer"></span>
        ${cd ? `<span class="countdown ${cd.cls}" title="${esc(nd.label)}">${cd.text}</span>` : c.upcoming === false ? `<span class="pill">past</span>` : ""}
      </div>
      ${c.full_name ? `<div class="cc-full">${esc(c.full_name)}</div>` : ""}
      ${meta ? `<div class="cc-meta">${meta}</div>` : ""}
      ${nd ? `<div class="cc-next">Next: <b>${esc(nd.label)}</b>, ${fmtWhen(nd.at)}</div>` : ""}
      ${tracked ? deadlineList(c) : ""}
      ${projs.length ? `<div class="cc-projects">${projs.map((p) => `<button class="tag link" data-proj="${p.id}">🎯 ${esc(p.name)}</button>`).join("")}</div>` : ""}
      ${tracked && c.notes ? `<p class="cc-notes">${esc(c.notes)}</p>` : ""}
      ${c.rankings ? `<div class="cc-rank">${esc(c.rankings)}</div>` : ""}
      <div class="card-foot">
        ${c.url ? `<a class="act" href="${esc(c.url)}" target="_blank" rel="noopener">Website ↗</a>` : ""}
        ${tracked
          ? `<button class="act" data-cact="edit">Edit</button>${c.custom ? `<span class="hint inline" title="You edited the dates, so catalog updates leave this venue alone">edited</span>` : ""}<button class="act open" data-cact="untrack">Untrack</button>`
          : `<button class="act open" data-cact="track">+ Track</button>`}
      </div>
    </article>`;
  }

  function renderTracked() {
    const xs = C.tracked.filter(matches).sort((a, b) => (nextDeadline(a)?.at || "9" + (a.start_date || "")).localeCompare(nextDeadline(b)?.at || "9" + (b.start_date || "")));
    $("#confTracked").innerHTML = xs.map((c) => cardHTML(c, true)).join("") ||
      `<div class="empty small"><h3>No venues yet</h3><p>Track conferences from the catalog, or add a workshop by hand.</p>
       <button class="primary-btn" data-cact="browse">Browse catalog</button></div>`;
  }

  function suggestions() {
    const focus = new Set(C.focus);
    return (C.catalog || []).filter((c) => c.upcoming && !c.tracked_id && nextDeadline(c) && matches(c) &&
      (!focus.size || c.tags.some((t) => focus.has(t))));
  }

  function renderSuggestions() {
    const xs = suggestions().slice(0, 6);
    $("#confSuggestTitle").textContent = C.focus.length ? "Suggested for your focus" : "Open deadlines";
    $("#confSuggest").innerHTML = xs.map((c) => cardHTML(c, false)).join("") ||
      `<p class="hint">${C.focus.length ? "No open deadlines in your focus areas right now." : "Nothing open right now."} <button class="text-btn" data-cact="browse">Browse the whole catalog</button></p>`;
  }

  // ---------------------------------------------------------------- catalog modal

  async function openCatalog() {
    $("#catalogModal").showModal();
    $("#catalogQ").value = "";
    C.catTag = null;
    $("#catalogList").innerHTML = `<li class="loading">Loading the catalog…</li>`;
    try { await loadCatalog(); } catch (e) { $("#catalogList").innerHTML = `<li class="hint">${esc(e.message)}</li>`; return; }
    renderCatalogHint();
    renderCatalog();
    $("#catalogQ").focus();
  }

  function renderCatalogHint() {
    $("#catalogHint").innerHTML = `Community-maintained deadlines from <a href="https://github.com/huggingface/ai-deadlines" target="_blank" rel="noopener">huggingface/ai-deadlines</a>${C.fetchedAt ? `, updated ${timeAgo(C.fetchedAt)}` : ""} <button class="text-btn" id="catalogRefresh">refresh</button>. Track a past edition to get the next one automatically when it's announced. Missing a venue? Use <b>+ Add manually</b>.`;
  }

  function renderCatalog() {
    const tags = [...new Set([...C.focus, ...C.catalogTags])];
    $("#catalogTags").innerHTML = `<button class="chip ${!C.catTag ? "active" : ""}" data-ctag="">All</button>` +
      tags.map((t) => `<button class="chip ${C.catTag === t ? "active" : ""} ${C.focus.includes(t) ? "focus" : ""}" data-ctag="${esc(t)}">${esc(t.replace(/-/g, " "))}</button>`).join("");
    const q = $("#catalogQ").value.trim().toLowerCase();
    const xs = C.catalog.filter((c) => (!C.catTag || c.tags.includes(C.catTag)) &&
      (!q || [c.name, c.full_name, c.location, c.tags.join(" "), String(c.year)].join(" ").toLowerCase().includes(q)));
    $("#catalogList").innerHTML = xs.map((c) => {
      const nd = nextDeadline(c);
      const cd = nd && countdown(nd.at);
      return `<li class="candidate">
        <div class="c-main">
          <div class="c-name">${esc(title(c))} ${cd ? `<span class="countdown ${cd.cls}">${cd.text}</span>` : c.upcoming ? "" : `<span class="pill">past edition</span>`}</div>
          <div class="c-sub">${esc(c.full_name)}${c.location ? ` · ${esc(c.location)}` : ""}${dateRange(c) ? ` · ${esc(dateRange(c))}` : ""}</div>
          ${nd ? `<div class="c-areas">Next: <b>${esc(nd.label)}</b>, ${fmtWhen(nd.at)}</div>` : ""}
          <div class="c-areas">${c.tags.map((t) => esc(t.replace(/-/g, " "))).join(" · ")}</div>
        </div>
        ${c.tracked_id ? `<button class="ghost-btn" disabled>✓ Tracking</button>` : `<button class="primary-btn" data-track="${esc(c.source_key)}">Track</button>`}
      </li>`;
    }).join("") || `<li class="hint">Nothing matches. Add it with <b>+ Add manually</b>.</li>`;
  }

  async function track(key, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "Tracking…"; }
    try {
      const c = await api("/api/conferences", { method: "POST", body: { source_key: key } });
      const item = C.catalog?.find((x) => x.source_key === key);
      if (item) item.tracked_id = c.id;
      toast(`Tracking ${title(c)}${nextDeadline(c) ? `: next deadline in ${countdown(nextDeadline(c).at).text}` : ""}`);
      C.tracked = await api("/api/conferences");
      render();
      if ($("#catalogModal").open) renderCatalog();
    } catch (e) { toast(e.message); if (btn) { btn.disabled = false; btn.textContent = "Track"; } }
  }

  // ---------------------------------------------------------------- focus modal

  async function openFocus() {
    $("#focusModal").showModal();
    $("#focusPick").innerHTML = `<p class="loading">Loading areas…</p>`;
    try { await loadCatalog(); } catch {}
    const tags = [...new Set([...C.focus, ...C.catalogTags])];
    $("#focusPick").innerHTML = tags.map((t) => `<label class="chip pick"><input type="checkbox" value="${esc(t)}" ${C.focus.includes(t) ? "checked" : ""}> ${esc(t.replace(/-/g, " "))}</label>`).join("") ||
      `<p class="hint">Couldn't load the catalog's areas.</p>`;
  }

  async function saveFocus() {
    C.focus = await api("/api/conferences/focus", { method: "PUT", body: $$("#focusPick input:checked").map((i) => i.value) });
    $("#focusModal").close();
    render();
  }

  // ---------------------------------------------------------------- editor

  function deadlineRow(d = {}) {
    const tz = d.tz || "AoE";
    const opts = TZ_CHOICES.some(([v]) => v === tz) ? TZ_CHOICES : [[tz, tz], ...TZ_CHOICES];
    return `<div class="dl-row">
      <input class="dl-label" placeholder="e.g. Paper submission" value="${esc(d.label || "")}" aria-label="Deadline name">
      <input class="dl-date" type="datetime-local" value="${d.at ? inZone(d.at, tz) : ""}" aria-label="Date and time">
      <select class="dl-tz" aria-label="Time zone">${opts.map(([v, l]) => `<option value="${esc(v)}" ${v === tz ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>
      <button type="button" class="icon-btn" data-rm-dl aria-label="Remove deadline">✕</button>
    </div>`;
  }

  function openEditor(c) {
    const f = $("#confForm");
    f.reset();
    C.editId = c?.id ?? null;
    $("#cmTitle").textContent = c ? `Edit ${title(c)}` : "Add a conference or workshop";
    $("#cmDelete").hidden = !c;
    for (const k of ["kind", "name", "full_name", "year", "parent", "url", "location", "start_date", "end_date", "notes"]) f[k].value = c?.[k] ?? (k === "kind" ? "conference" : "");
    f.tags.value = (c?.tags || C.focus).join(", ");
    $("#cmDeadlines").innerHTML = (c?.deadlines.length ? c.deadlines : [{}]).map(deadlineRow).join("");
    toggleParent();
    $("#confModal").showModal();
  }

  const toggleParent = () => { $('#confForm [data-only="workshop"]').hidden = $("#confForm").kind.value !== "workshop"; };

  async function saveEditor(e) {
    e.preventDefault();
    const f = e.target;
    const deadlines = $$("#cmDeadlines .dl-row").map((row) => ({
      label: $(".dl-label", row).value.trim() || "Deadline", date: $(".dl-date", row).value.replace("T", " "), tz: $(".dl-tz", row).value,
    })).filter((d) => d.date);
    const body = {
      kind: f.kind.value, name: f.name.value.trim(), full_name: f.full_name.value.trim(), year: f.year.value ? +f.year.value : null,
      parent: f.kind.value === "workshop" ? f.parent.value.trim() : "", url: f.url.value.trim(), location: f.location.value.trim(),
      start_date: f.start_date.value, end_date: f.end_date.value, notes: f.notes.value.trim(),
      tags: f.tags.value.split(",").map((s) => s.trim()).filter(Boolean),
      deadlines: deadlines.map((d) => ({ ...d, type: /abstract/i.test(d.label) ? "abstract" : /camera/i.test(d.label) ? "camera_ready" : /notif/i.test(d.label) ? "notification" : /rebuttal/i.test(d.label) ? "rebuttal" : "deadline" })),
    };
    try {
      await api(C.editId ? `/api/conferences/${C.editId}` : "/api/conferences", { method: C.editId ? "PUT" : "POST", body });
      $("#confModal").close();
      toast(C.editId ? "Saved" : `Tracking ${body.name}`);
      C.tracked = await api("/api/conferences");
      render();
    } catch (err) { toast(err.message); }
  }

  async function untrack(c) {
    if (!confirm(`Stop tracking ${title(c)}?${C.projects.some((p) => p.conference_id === c.id) ? " Projects targeting it will lose their target venue." : ""}`)) return;
    await api(`/api/conferences/${c.id}`, { method: "DELETE" });
    const item = C.catalog?.find((x) => x.tracked_id === c.id);
    if (item) item.tracked_id = null;
    $("#confModal").close();
    C.tracked = C.tracked.filter((x) => x.id !== c.id);
    render();
  }

  // ---------------------------------------------------------------- personal deadlines

  const pad = (n) => String(n).padStart(2, "0");
  const localDate = (dt) => `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;

  function openDeadline(d) {
    const f = $("#dlForm");
    f.reset();
    C.dlEditId = d?.id ?? null;
    $("#dlTitle").textContent = d ? "Edit deadline" : "New deadline";
    $("#dlDelete").hidden = !d;
    f.project_id.innerHTML = `<option value="">(none)</option>` +
      C.projects.filter((p) => p.status !== "done" || p.id === d?.project_id).map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    if (d) {
      const at = new Date(d.at);
      f.title.value = d.title;
      f.date.value = localDate(at);
      f.time.value = d.all_day ? "" : `${pad(at.getHours())}:${pad(at.getMinutes())}`;
      f.project_id.value = d.project_id || "";
      f.notes.value = d.notes;
      f.done.checked = d.done;
    }
    $("#dlModal").showModal();
    f.title.focus();
  }

  async function saveDeadline(e) {
    e.preventDefault();
    const f = e.target;
    const at = new Date(`${f.date.value}T${f.time.value || "23:59"}`);  // no time: end of that day, your time
    if (isNaN(at)) return toast("Pick a date");
    const pid = +f.project_id.value || null;
    const body = { title: f.title.value.trim(), at: at.toISOString(), all_day: !f.time.value, notes: f.notes.value.trim(),
      done: f.done.checked, ...(pid ? { project_id: pid } : { clear_project: true }) };
    try {
      await api(C.dlEditId ? `/api/deadlines/${C.dlEditId}` : "/api/deadlines", { method: C.dlEditId ? "PATCH" : "POST", body });
      $("#dlModal").close();
      toast(C.dlEditId ? "Saved" : `Added: ${countdown(body.at).text} to go`);
      await reloadMine();
    } catch (err) { toast(err.message); }
  }

  async function reloadMine() {
    C.mine = await api("/api/deadlines");
    renderTimeline();
  }

  // ---------------------------------------------------------------- binding

  C.search = (q) => { C.q = q; render(); };
  C.onKey = (e) => { if (e.key === "n") { e.preventDefault(); openDeadline(null); } };

  document.addEventListener("DOMContentLoaded", () => {
    const page = $('main[data-section="deadlines"]');
    page.addEventListener("click", async (e) => {
      const row = e.target.closest("[data-dl]");
      if (row) {
        const d = C.mine.find((x) => x.id === +row.dataset.dl);
        if (!e.target.closest("[data-dl-done]")) return openDeadline(d);
        await api(`/api/deadlines/${d.id}`, { method: "PATCH", body: { done: e.target.checked } }).catch((err) => toast(err.message));
        if (e.target.checked) toast(`Done: ${d.title}`);
        return reloadMine();
      }
      const f = e.target.closest("[data-dlf]");
      if (f) {
        C.filter = f.dataset.dlf;
        $$("#dlFilter button").forEach((b) => b.classList.toggle("on", b === f));
        return renderTimeline();
      }
      if (e.target.closest("#confEditFocus")) return openFocus();
      const proj = e.target.closest("[data-proj]");
      if (proj) { showSection("projects"); return Sections.projects.select(+proj.dataset.proj); }
      const act = e.target.closest("[data-cact]")?.dataset.cact;
      if (!act) return;
      if (act === "browse") return openCatalog();
      const card = e.target.closest(".conf-card");
      if (act === "track") return track(card.dataset.key, e.target);
      const c = C.tracked.find((x) => x.id === +card.dataset.cid);
      if (act === "edit") return openEditor(c);
      if (act === "untrack") return untrack(c);
    });
    $("#confBrowse").onclick = openCatalog;
    $("#confAdd").onclick = () => openEditor(null);
    $("#dlAdd").onclick = () => openDeadline(null);
    $("#dlForm").onsubmit = saveDeadline;
    $("#dlDelete").onclick = async () => {
      const d = C.mine.find((x) => x.id === C.dlEditId);
      if (!d || !confirm(`Delete “${d.title}”?`)) return;
      await api(`/api/deadlines/${d.id}`, { method: "DELETE" });
      $("#dlModal").close();
      await reloadMine();
    };
    $("#confPast").onchange = (e) => { C.showPast = e.target.checked; renderTimeline(); };
    $("#catalogQ").oninput = debounce(renderCatalog, 150);
    $("#catalogModal").addEventListener("click", async (e) => {
      const t = e.target.closest("[data-track]");
      if (t) return track(t.dataset.track, t);
      const tag = e.target.closest("[data-ctag]");
      if (tag) { C.catTag = tag.dataset.ctag || null; return renderCatalog(); }
      if (e.target.id === "catalogRefresh") {
        e.target.textContent = "refreshing…";
        try { await loadCatalog(true); C.tracked = await api("/api/conferences"); render(); renderCatalog(); toast("Catalog updated"); }
        catch (err) { toast(err.message); }
        renderCatalogHint();
      }
    });
    $("#focusSave").onclick = saveFocus;
    $("#confForm").onsubmit = saveEditor;
    $("#confForm").kind.onchange = toggleParent;
    $("#cmAddDeadline").onclick = () => $("#cmDeadlines").insertAdjacentHTML("beforeend", deadlineRow());
    $("#cmDeadlines").onclick = (e) => { if (e.target.closest("[data-rm-dl]")) e.target.closest(".dl-row").remove(); };
    $("#cmDelete").onclick = () => untrack(C.tracked.find((x) => x.id === C.editId));
    // keep countdowns ticking
    setInterval(() => { if (state.section === "deadlines" && !document.hidden) { renderTimeline(); renderTracked(); } }, 60 * 1000);
  });
})();
