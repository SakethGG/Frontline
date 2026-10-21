// Library: the PDFs in the user's chosen folder (e.g. inside Google Drive), tagged by interest.
// Collections are sub-folders, browsed like a file manager: a folder shows its sub-folders and the
// PDFs directly inside it; searching or filtering looks through everything below it.
// Everything is filtered client-side; libraries are small enough.
(() => {
  const L = {
    placeholder: "Search your library…",
    meta: null, items: [], q: "",
    view: pref("lib.view") === "starred" ? "starred" : "all", sort: pref("lib.sort") || "relevance",
    collection: null,  // null = the library folder itself, "__missing" = files that vanished
    interest: null, openId: null, polling: null, lastScan: 0,
  };
  const chats = new Map(), dives = new Map();
  Sections.library = L;

  // ---------------------------------------------------------------- loading

  L.show = async () => {
    await loadMeta();
    if (!L.meta.root_exists) return;
    await loadItems();
    // pick up PDFs dropped into the folder (e.g. synced from another device) since the last look
    if (!L.meta.scan.running && Date.now() - L.lastScan > 5 * 60 * 1000) scan(false, true);
    else if (L.meta.scan.running) poll();
  };

  async function loadMeta() {
    L.meta = await api("/api/library/meta");
    const m = L.meta;
    $("#libPath").innerHTML = m.root
      ? `📁 <span title="${esc(m.root)}">${esc(m.root)}</span> <button class="text-btn" id="libChangeDir">change</button>`
      : "Your papers, in a folder you choose.";
    $("#libSetup").hidden = m.root_exists;
    $("#libMain").hidden = !m.root_exists;
    $("#libActions").hidden = !m.root_exists;
    $$('.layout > aside[data-for="library"]').forEach((a) => (a.hidden = !m.root_exists || state.section !== "library"));
    if (!m.root_exists) renderSetup();
  }

  async function loadItems() {
    L.items = await api("/api/library");
    render();
  }

  function renderSetup() {
    const m = L.meta;
    $("#libSetup").innerHTML = `
      <h2>Where should your papers live?</h2>
      ${m.root ? `<p class="warn-box">Can't find <b>${esc(m.root)}</b>. If it's in Google Drive, check that Drive for desktop is running, or pick another folder.</p>` : ""}
      <p>Pick a folder for your PDFs. A folder inside <b>Google Drive</b> (or Dropbox / OneDrive) keeps the library synced across your devices.
      Frontline keeps plain PDF files there: drop files in from anywhere and they show up here, and <b>sub-folders become collections</b>.</p>
      ${m.suggestions.length ? `<div class="suggestions">${m.suggestions.map((s) => `
        <button class="ghost-btn" data-use-dir="${esc(s)}">Use <b>${esc(s)}</b></button>`).join("")}</div>` : ""}
      <form class="follow-search" id="libDirForm">
        <input id="libDirInput" value="${esc(m.root || "")}" placeholder="Full path, e.g. ${navigator.platform.startsWith("Win") ? "G:\\My Drive\\Research Papers" : "~/Library/CloudStorage/GoogleDrive-you@gmail.com/My Drive/Research Papers"}" aria-label="Library folder">
        <button class="primary-btn" type="submit">Use this folder</button>
      </form>
      <p class="hint">The folder is created if it doesn't exist. No Google Drive folder yet? Install <a href="https://www.google.com/drive/download/" target="_blank" rel="noopener">Google Drive for desktop</a> and your Drive shows up as a folder on this computer.</p>`;
  }

  async function useDir(dir) {
    try {
      await api("/api/library/settings", { method: "PUT", body: { library_dir: dir, create: true } });
      toast("Library folder set, scanning for PDFs…");
      L.lastScan = Date.now();
      await L.show();
      poll();
    } catch (e) { toast(e.message, 5000); }
  }

  async function scan(retag = false, quiet = false) {
    L.lastScan = Date.now();
    try {
      await api("/api/library/scan", { method: "POST", body: { retag } });
      poll(quiet);
    } catch (e) { if (!quiet) toast(e.message); }
  }

  function poll(quiet = false) {
    clearInterval(L.polling);
    const tick = async () => {
      let st;
      try { st = await api("/api/library/scan"); } catch { return; }
      renderScan(st);
      if (!st.running) {
        clearInterval(L.polling);
        L.polling = null;
        if (st.added || !quiet) toast(st.message);
        if (state.section === "library") loadItems();
      } else if (st.done && state.section === "library" && st.done % 3 === 0) loadItems();
    };
    L.polling = setInterval(tick, 1500);
    tick();
  }

  function renderScan(st) {
    $("#libRescan").disabled = !!st?.running;
    $("#libRescan").textContent = st?.running ? "Scanning…" : "⟳ Rescan";
    L.scanText = st?.running ? (st.total ? `${st.message.replace("…", "")} ${st.done} of ${st.total}…` : st.message) : "";
    renderStatusLine();
  }

  // ---------------------------------------------------------------- filtering

  const inCollection = (r) => L.collection === null || r.collection === L.collection || r.collection.startsWith(L.collection + "/");
  const parentOf = (c) => c.includes("/") ? c.slice(0, c.lastIndexOf("/")) : "";
  // plain folder browsing: no search, interest or starred filter narrowing things down
  const browsing = () => L.collection !== "__missing" && !L.q && !L.interest && L.view === "all";

  function haystack(r) {
    return (r._h ??= [r.title, r.authors.join(" "), r.abstract, r.tldr, r.notes, r.tags.join(" "), r.venue, r.filename, r.year]
      .join(" ").toLowerCase());
  }

  function scoped() {  // collection (whole subtree) + interest + search, before the starred tab
    const q = L.q.toLowerCase();
    return L.items.filter((r) => (L.collection === "__missing" ? r.missing : !r.missing && inCollection(r))
      && (!L.interest || r.tags.includes(L.interest)) && (!q || haystack(r).includes(q)));
  }

  function subfolders() {
    if (!browsing()) return [];
    const here = L.collection || "";
    return L.meta.collections.filter((c) => parentOf(c) === here).sort((a, b) => a.localeCompare(b));
  }

  const SORTS = {
    relevance: (a, b) => b.relevance - a.relevance || b.added_at.localeCompare(a.added_at),
    added: (a, b) => b.added_at.localeCompare(a.added_at),
    year: (a, b) => (b.year || "").localeCompare(a.year || "") || b.added_at.localeCompare(a.added_at),
    title: (a, b) => a.title.localeCompare(b.title),
  };

  function visible() {
    const here = L.collection || "";
    const xs = browsing() ? scoped().filter((r) => r.collection === here) : scoped().filter((r) => L.view === "all" || r.starred);
    return xs.sort(SORTS[L.sort] || SORTS.relevance);
  }

  // ---------------------------------------------------------------- rendering

  function render() {
    renderSide();
    renderInterests();
    const sc = scoped();
    const count = { all: sc.length, starred: sc.filter((r) => r.starred).length };
    $$("#libTabs .tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.lview === L.view);
      $("[data-count]", t).textContent = count[t.dataset.lview] || "";
    });
    $("#libSort").value = L.sort;
    const xs = visible(), dirs = subfolders();
    $("#libList").innerHTML = dirs.map(folderHTML).join("") + xs.map(rowHTML).join("");
    renderStatusLine(xs.length);
    const empty = $("#libEmpty");
    empty.hidden = xs.length + dirs.length > 0;
    if (!xs.length && !dirs.length) {
      empty.innerHTML = !L.items.some((r) => !r.missing) && L.collection !== "__missing"
        ? `<h3>Your library is empty</h3><p>Drop PDFs here, use <b>+ Add PDFs</b>, put files into <b>${esc(L.meta.root)}</b>, or press <b>⤓ Save to library</b> on any Radar paper.</p>`
        : browsing() ? `<h3>This folder is empty</h3><p>Drop PDFs here or use <b>+ Add PDFs</b> to put papers in <b>${esc(L.collection)}</b>.</p>`
        : `<h3>Nothing here</h3><p>No papers match these filters.</p><button class="primary-btn" data-lib-clear>Clear filters</button>`;
    }
  }

  function renderStatusLine(n) {
    if (n === undefined) n = visible().length;
    const bits = [];
    if (L.interest) bits.push(`tagged “${esc(L.interest)}” <button class="text-btn" data-lib-unset="interest">clear</button>`);
    if (L.q) bits.push(`matching “${esc(L.q)}” <button class="text-btn" data-lib-unset="q">clear</button>`);
    $("#libStatus").innerHTML = crumbsHTML() + (L.items.length ? `${plural(n, "paper")}${bits.length ? " · " + bits.join(" · ") : ""}` : "") +
      (L.scanText ? `<span class="scan-line"><span class="loading">${esc(L.scanText)}</span></span>` : "");
  }

  function crumbsHTML() {  // Library › Parent › Child, each part opens that folder
    if (L.collection === null || L.collection === "__missing") return "";
    const parts = L.collection.split("/");
    return `<span class="crumbs"><button class="text-btn" data-coll="\u0000">Library</button>${parts.map((p, i) => i === parts.length - 1
      ? ` › <span>${esc(p)}</span>` : ` › <button class="text-btn" data-coll="${esc(parts.slice(0, i + 1).join("/"))}">${esc(p)}</button>`).join("")}</span>`;
  }

  function renderSide() {
    const live = L.items.filter((r) => !r.missing);
    const n = (c) => live.filter((r) => r.collection === c || r.collection.startsWith(c + "/")).length;
    const missing = L.items.length - live.length;
    const row = (key, label, count, depth = 0) => `
      <button class="person-row coll ${L.collection === key ? "active" : ""}" data-coll="${esc(key ?? "\u0000")}" style="--depth:${depth}">
        <span class="coll-icon" aria-hidden="true">${key === null ? "▤" : key === "__missing" ? "⚠" : key === "" ? "◌" : "📁"}</span>
        <span class="person-main"><span class="person-name">${esc(label)}</span></span>
        ${count ? `<span class="count">${count}</span>` : ""}
      </button>`;
    $("#libCollections").innerHTML =
      row(null, "Library", live.length) +
      L.meta.collections.map((c) => row(c, c.split("/").pop(), n(c), c.split("/").length - 1)).join("") +
      (missing ? row("__missing", "Missing files", missing) : "") +
      `<button class="person-row add" id="libNewColl">+ New collection</button>`;
  }

  function renderInterests() {
    const live = L.items.filter((r) => !r.missing && inCollection(r));
    const ints = L.meta.interests;
    $("#libInterests").innerHTML = `<span class="row-label">Interests</span>` +
      ints.map((i) => {
        const c = live.filter((r) => r.tags.includes(i)).length;
        return `<button class="chip ${L.interest === i ? "active" : ""}" data-interest="${esc(i)}">${esc(i)}${c ? ` <span class="n">${c}</span>` : ""}</button>`;
      }).join("") +
      `<button class="chip ghost" id="libEditInterests">${ints.length ? "✎ Edit" : "+ Add your interests"}</button>`;
  }

  function shortAuthors(a) {
    return a.length > 3 ? `${a.slice(0, 2).join(", ")} … ${a[a.length - 1]}` : a.join(", ");
  }

  function folderHTML(c) {
    const n = L.items.filter((r) => !r.missing && (r.collection === c || r.collection.startsWith(c + "/"))).length;
    return `
    <article class="lib-row folder" data-coll="${esc(c)}" title="Open folder">
      <span class="coll-icon" aria-hidden="true">📁</span>
      <div class="lr-main"><h3>${esc(c.split("/").pop())}</h3></div>
      <div class="lr-side"><span class="lr-meta">${plural(n, "paper")}</span></div>
    </article>`;
  }

  function rowHTML(r) {
    const meta = [shortAuthors(r.authors), r.year, r.venue].filter(Boolean).map(esc).join(" · ");
    return `
    <article class="lib-row ${r.id === L.openId ? "selected" : ""}" data-id="${r.id}" title="${r.missing ? "" : "Open PDF"}">
      <button class="star-btn ${r.starred ? "on" : ""}" data-act="star" title="${r.starred ? "Unstar" : "Star"}" aria-label="Star">${r.starred ? "★" : "☆"}</button>
      <div class="lr-main">
        <h3>${esc(r.title)}</h3>
        <div class="lr-meta">${meta || esc(r.filename)}${r.collection && r.collection !== (L.collection || "") ? ` · <span class="coll-tag">📁 ${esc(r.collection)}</span>` : ""}</div>
        ${r.tldr ? `<p class="lr-tldr">${esc(r.tldr)}</p>` : ""}
        ${r.tags.length ? `<div class="tags">${r.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
      </div>
      <div class="lr-side">
        ${!r.tagged_by ? `<span class="score lo" title="Not tagged yet">…</span>` : r.relevance || r.tagged_by === "ai" ? `<span class="score ${scoreClass(r.relevance)}" title="Match with your interests">${r.relevance}/10</span>` : ""}
        ${r.missing ? `<span class="pill missing" title="The file is no longer in the folder">missing</span>` : ""}
        <button class="act" data-act="info" title="Notes, abstract, deep dive and Q&A">Details</button>
      </div>
    </article>`;
  }

  // ---------------------------------------------------------------- actions

  async function openFile(r, folder = false) {  // in the system PDF app / file manager, like double-clicking it
    try { await api(`/api/library/${r.id}/reveal`, { method: "POST", body: { folder } }); } catch (err) { toast(err.message); }
  }

  async function patch(r, body) {
    const notes = L.openId === r.id ? $("#libNotes")?.value : undefined;  // don't lose unsaved typing on re-render
    if (notes !== undefined && notes !== r.notes && !("notes" in body)) body = { ...body, notes };
    const updated = await api(`/api/library/${r.id}`, { method: "PATCH", body });
    const i = L.items.findIndex((x) => x.id === r.id);
    if (i >= 0) L.items[i] = updated;
    render();
    if (L.openId === r.id) renderDrawer(updated);
    return updated;
  }

  async function upload(files) {
    const pdfs = [...files].filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    if (!pdfs.length) return toast("Only PDF files can be added");
    const coll = L.collection && L.collection !== "__missing" ? L.collection : "";
    let ok = 0;
    for (const [i, f] of pdfs.entries()) {
      L.scanText = `Adding ${f.name} (${i + 1} of ${pdfs.length})…`;
      renderStatusLine();
      try {
        const res = await fetch(`/api/library/upload?${new URLSearchParams({ name: f.name, collection: coll })}`, { method: "POST", body: f });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
        ok++;
      } catch (e) { toast(`${f.name}: ${e.message}`, 5000); }
    }
    L.scanText = "";
    if (ok) toast(`Added ${plural(ok, "paper")}${coll ? ` to ${coll}` : ""}`);
    await loadItems();
  }

  async function newCollection() {
    const name = prompt("New collection (a sub-folder in your library). Use / for nesting, e.g. Parent/Child:");
    if (!name?.trim()) return;
    try {
      const { collections } = await api("/api/library/collections", { method: "POST", body: { name } });
      L.meta.collections = collections;
      L.collection = collections.find((c) => c.toLowerCase() === name.trim().replace(/\\/g, "/").replace(/\s*\/\s*/g, "/").toLowerCase()) ?? L.collection;
      render();
    } catch (e) { toast(e.message); }
  }

  function openInterests() {
    const f = $("#interestsForm");
    f.interests.value = L.meta.interests.join("\n");
    f.retag.checked = true;
    const auto = L.items.filter((r) => r.tagged_by !== "manual").length;
    $("#retagHint").textContent = auto ? `(${plural(auto, "paper")}${L.meta.ai ? ", one short AI call each" : ""}; papers you tagged by hand are left alone)` : "";
    $("#interestsModal").showModal();
  }

  async function saveInterests(e, reset = false) {
    e?.preventDefault();
    const f = $("#interestsForm");
    try {
      L.meta = await api("/api/library/settings", { method: "PUT", body: reset ? { reset_interests: true } : { interests: lines(f.interests.value) } });
      $("#interestsModal").close();
      if (L.interest && !L.meta.interests.includes(L.interest)) L.interest = null;
      render();
      if (f.retag.checked) scan(true);
    } catch (err) { toast(err.message); }
  }

  // ---------------------------------------------------------------- drawer

  L.open = async (id) => {
    if (!L.items.length) await loadItems();
    const r = L.items.find((x) => x.id === id);
    if (!r) return;
    L.openId = id;
    L.tab = "notes";
    renderDrawer(r);
    $$(".lib-row.selected").forEach((x) => x.classList.remove("selected"));
    $(`.lib-row[data-id="${id}"]`)?.classList.add("selected");
  };

  function renderDrawer(r) {
    const tabs = [["notes", "Notes"], ["abstract", "Abstract"], ["dive", "Deep dive"], ["ask", "Ask"]];
    const colls = ["", ...L.meta.collections];
    openDrawer(`
      <div class="reader-nav"><span class="drawer-kicker">Library paper</span></div>
      <div class="reader-actions">
        <button class="ghost-btn ${r.starred ? "on" : ""}" data-d="star">${r.starred ? "★ Starred" : "☆ Star"}</button>
        <button class="icon-btn" data-d="close" title="Close (Esc)" aria-label="Close">✕</button>
      </div>`, `
      <div class="reader-head">
        <div class="reader-meta">
          ${r.relevance || r.tagged_by === "ai" ? `<span class="score ${scoreClass(r.relevance)}" title="Match with your interests">${r.relevance}/10</span>` : ""}
          ${r.year ? `<span>${esc(r.year)}</span>` : ""}${r.venue ? `<span>·</span><span>${esc(r.venue)}</span>` : ""}
          <span>·</span><span title="${esc(r.rel_path)}">${esc(r.filename)}</span>
        </div>
        <h2>${esc(r.title)}</h2>
        <div class="reader-authors">${esc(r.authors.join(", ")) || "<i>Authors unknown</i>"}</div>
        <div class="reader-links">
          ${r.missing ? `<span class="warn-box">This PDF is no longer in your library folder.</span>` : `
          <a class="primary-btn" href="/api/library/${r.id}/file" target="_blank" rel="noopener">Open PDF ↗</a>
          <button class="ghost-btn" data-d="open-app" title="Open in your PDF app">Open in app</button>
          <button class="ghost-btn" data-d="reveal">Show in folder</button>`}
          ${r.url ? `<a class="ghost-btn" href="${esc(r.url)}" target="_blank" rel="noopener">Paper page ↗</a>` : ""}
          <button class="ghost-btn" data-d="cite">Copy citation</button>
          <button class="ghost-btn" data-d="idea" title="Start an idea that links this paper">💡 New idea</button>
        </div>
      </div>
      <div class="lib-controls">
        <label class="inline">Collection
          <select data-d="collection" ${r.missing ? "disabled" : ""}>${colls.map((c) => `<option value="${esc(c)}" ${c === r.collection ? "selected" : ""}>${c ? esc(c) : "(unfiled)"}</option>`).join("")}</select>
        </label>
      </div>
      <div class="tag-editor" data-d="tags">
        ${r.tags.map((t) => `<span class="tag removable">${esc(t)}<button data-untag="${esc(t)}" aria-label="Remove tag ${esc(t)}">×</button></span>`).join("")}
        <input list="libInterestList" placeholder="+ tag" aria-label="Add a tag" data-d="tag-input">
        <datalist id="libInterestList">${L.meta.interests.filter((i) => !r.tags.includes(i)).map((i) => `<option value="${esc(i)}">`).join("")}</datalist>
        <button class="text-btn" data-d="retag" title="${L.meta.ai ? "Let the AI re-read and re-tag this paper" : "Re-tag from keywords"}">↻ auto-tag</button>
      </div>
      ${r.tldr ? `<p class="tldr-big">${esc(r.tldr)}</p>` : ""}
      <div class="rtabs" role="tablist">${tabs.map(([k, v]) => `<button class="rtab ${L.tab === k ? "active" : ""}" data-tab="${k}">${v}</button>`).join("")}</div>
      <section class="rpanel" id="libTab"></section>
      <details class="sec danger-zone"><summary>More</summary>
        <p class="hint">Removing moves the PDF to the hidden <code>.frontline-trash</code> folder inside your library, so you can get it back.</p>
        <button class="danger-btn" data-d="remove">Remove from library</button>
      </details>`, { ask: L.tab === "ask" });
    renderTab(r);
  }

  function renderTab(r) {
    const el = $("#libTab");
    if (!el) return;
    $("#drawerAsk").hidden = L.tab !== "ask";
    if (L.tab === "notes") {
      el.innerHTML = `<textarea class="notes" id="libNotes" placeholder="Your notes on this paper: key takeaways, quotes, how it relates to your work… (saved automatically)">${esc(r.notes)}</textarea>`;
    } else if (L.tab === "abstract") {
      el.innerHTML = r.abstract ? `<p class="abstract">${esc(r.abstract)}</p>` : `<p class="hint">No abstract found for this PDF.</p>`;
    } else if (L.tab === "dive") {
      if (dives.has(r.id)) el.innerHTML = `<div class="md">${md(dives.get(r.id))}</div><button class="text-btn" data-d="regen">Regenerate</button>`;
      else if (r.has_deep_dive) loadDive(r, false);
      else el.innerHTML = `<p class="hint">${esc(state.status?.provider || "The AI")} reads the full PDF and writes a dense breakdown: method, setup, numbers, limitations, ideas worth stealing.</p>
        <button class="primary-btn" data-d="dive">Write the deep dive</button>`;
    } else if (L.tab === "ask") {
      renderChat(r);
      setTimeout(() => $("#drawerAskInput").focus(), 50);
    }
  }

  async function loadDive(r, refresh) {
    $("#libTab").innerHTML = `<div class="loading">${esc(state.status?.provider || "The AI")} is reading the full paper. This can take a minute…</div>`;
    try {
      const { markdown } = await api(`/api/library/${r.id}/deep-dive${refresh ? "?refresh=true" : ""}`, { method: "POST" });
      dives.set(r.id, markdown);
      if (L.openId === r.id && L.tab === "dive") renderTab(r);
    } catch (e) { $("#libTab").innerHTML = `<p>Couldn't write the deep dive: ${esc(e.message)}</p>`; }
  }

  function renderChat(r, pending) {
    const hist = chats.get(r.id) || [];
    $("#libTab").innerHTML = `<div class="chat">${!hist.length && !pending
      ? `<p class="hint">Ask anything about this paper; the AI reads your local PDF.</p>
         <div class="suggest">${["Summarize the key idea in plain words", "What are the main limitations?", "How could I apply this to my work?", "What should I read next?"]
           .map((q) => `<button class="chip" data-ask="${esc(q)}">${esc(q)}</button>`).join("")}</div>` : ""}
      ${hist.map((t) => `<div class="msg q">${esc(t.q)}</div><div class="msg a md">${md(t.a)}</div>`).join("")}
      ${pending ? `<div class="msg q">${esc(pending)}</div><div class="msg a"><span class="loading">Thinking…</span></div>` : ""}</div>`;
    $("#drawerBody").scrollTop = 1e6;
  }

  async function ask(q) {
    const r = L.items.find((x) => x.id === L.openId);
    if (!q || !r) return;
    $("#drawerAskInput").value = "";
    renderChat(r, q);
    try {
      const hist = chats.get(r.id) || [];
      const { answer } = await api(`/api/library/${r.id}/ask`, { method: "POST", body: { question: q, history: hist } });
      chats.set(r.id, [...hist, { q, a: answer }]);
    } catch (e) { toast(e.message); }
    if (L.openId === r.id && L.tab === "ask") renderChat(r);
  }

  const saveNotes = debounce(async (id, notes) => {
    const r = L.items.find((x) => x.id === id);
    if (!r || r.notes === notes) return;
    const updated = await api(`/api/library/${id}`, { method: "PATCH", body: { notes } });
    const i = L.items.findIndex((x) => x.id === id);
    if (i >= 0) L.items[i] = updated;
  }, 600);

  async function drawerClick(e) {
    if (state.section !== "library" || L.openId === null) return;
    const r = L.items.find((x) => x.id === L.openId);
    if (!r) return;
    const d = e.target.closest("[data-d]")?.dataset.d;
    const tab = e.target.closest("[data-tab]")?.dataset.tab;
    if (tab) {
      L.tab = tab;
      $$("#drawerBody .rtab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
      return renderTab(r);
    }
    const untag = e.target.closest("[data-untag]")?.dataset.untag;
    if (untag) return patch(r, { tags: r.tags.filter((t) => t !== untag) });
    const q = e.target.closest("[data-ask]")?.dataset.ask;
    if (q) return ask(q);
    switch (d) {
      case "close": return closeDrawer();
      case "star": return patch(r, { starred: !r.starred });
      case "open-app": case "reveal": return openFile(r, d === "reveal");
      case "cite":
        await navigator.clipboard.writeText(bibtex({ ...r, published: r.year, url: r.url || "" }));
        return toast("BibTeX copied");
      case "idea": return Sections.ideas.create({ title: `Idea from: ${r.title}`, papers: [{ source: "library", id: r.id, title: r.title }] });
      case "retag": {
        toast(L.meta.ai ? "Re-reading the paper…" : "Re-tagging…");
        const updated = await api(`/api/library/${r.id}/retag`, { method: "POST" });
        L.items[L.items.findIndex((x) => x.id === r.id)] = updated;
        render(); renderDrawer(updated);
        return;
      }
      case "dive": return loadDive(r, false);
      case "regen": return loadDive(r, true);
      case "remove":
        if (!confirm(`Remove “${r.title}” from your library? The PDF moves to the .frontline-trash folder.`)) return;
        await api(`/api/library/${r.id}`, { method: "DELETE" });
        closeDrawer();
        toast("Removed. The PDF is in .frontline-trash inside your library folder");
        return loadItems();
    }
  }

  // ---------------------------------------------------------------- binding

  L.search = (q) => { L.q = q; render(); };
  L.onKey = (e) => { if (e.key === "n") { e.preventDefault(); $("#libFiles").click(); } };

  document.addEventListener("DOMContentLoaded", () => {
    const page = $('main[data-section="library"]');
    page.addEventListener("click", async (e) => {
      const dir = e.target.closest("[data-use-dir]")?.dataset.useDir;
      if (dir) return useDir(dir);
      if (e.target.id === "libChangeDir") {
        const d = prompt("Library folder (full path). Existing papers are matched by content if you moved the folder:", L.meta.root);
        if (d && d !== L.meta.root) useDir(d);
        return;
      }
      if (e.target.closest("#libEditInterests")) return openInterests();
      const intr = e.target.closest("[data-interest]")?.dataset.interest;
      if (intr !== undefined) { L.interest = L.interest === intr ? null : intr; return render(); }
      const unset = e.target.closest("[data-lib-unset]")?.dataset.libUnset;
      if (unset === "interest") { L.interest = null; return render(); }
      if (unset === "q") { L.q = ""; $("#search").value = ""; return render(); }
      if (e.target.closest("[data-lib-clear]")) { Object.assign(L, { q: "", interest: null, collection: null, view: "all" }); $("#search").value = ""; return render(); }
      const tab = e.target.closest("[data-lview]")?.dataset.lview;
      if (tab) { L.view = pref("lib.view", tab); return render(); }
      const coll = e.target.closest("[data-coll]")?.dataset.coll;
      if (coll !== undefined) { L.collection = coll === "\u0000" ? null : coll; return render(); }
      const row = e.target.closest(".lib-row");
      if (!row) return;
      const r = L.items.find((x) => x.id === +row.dataset.id);
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act === "star") return patch(r, { starred: !r.starred });
      if (act === "info" || r.missing) return L.open(r.id);
      openFile(r);
    });
    page.addEventListener("submit", (e) => {
      if (e.target.id !== "libDirForm") return;
      e.preventDefault();
      const d = $("#libDirInput").value.trim();
      if (d) useDir(d);
    });
    $("#libCollections").onclick = (e) => {
      if (e.target.closest("#libNewColl")) return newCollection();
      const b = e.target.closest("[data-coll]");
      if (!b) return;
      L.collection = b.dataset.coll === "\u0000" ? null : b.dataset.coll;
      render();
    };
    $("#libSort").onchange = (e) => { L.sort = pref("lib.sort", e.target.value); render(); };
    $("#libRescan").onclick = () => scan(false);
    $("#libFiles").onchange = (e) => { upload(e.target.files); e.target.value = ""; };
    $("#interestsForm").onsubmit = (e) => saveInterests(e);
    $("#interestsReset").onclick = () => saveInterests(null, true);

    // drag & drop PDFs anywhere on the library page
    let depth = 0;
    const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
    page.addEventListener("dragenter", (e) => { if (hasFiles(e) && L.meta?.root_exists) { depth++; $("#libDrop").hidden = false; } });
    page.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; $("#libDrop").hidden = true; } });
    page.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
    page.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      $("#libDrop").hidden = true;
      upload(e.dataTransfer.files);
    });

    // drawer interactions (the drawer is shared with Ideas; only act while a library paper is open)
    $("#drawer").addEventListener("click", drawerClick);
    $("#drawer").addEventListener("input", (e) => { if (e.target.id === "libNotes" && L.openId !== null) saveNotes(L.openId, e.target.value); });
    $("#drawer").addEventListener("change", (e) => {
      if (L.openId === null || state.section !== "library") return;
      const r = L.items.find((x) => x.id === L.openId);
      if (e.target.dataset.d === "collection") patch(r, { collection: e.target.value }).then((u) => toast(`Moved to ${u.collection || "the library root"}`)).catch((err) => toast(err.message));
    });
    $("#drawer").addEventListener("keydown", (e) => {
      if (e.target.dataset.d !== "tag-input" || e.key !== "Enter" || L.openId === null) return;
      e.preventDefault();
      const r = L.items.find((x) => x.id === L.openId);
      const t = e.target.value.trim();
      if (t && !r.tags.includes(t)) patch(r, { tags: [...r.tags, t] }).then(() => $('#drawer [data-d="tag-input"]')?.focus());
    });
    $("#drawerAsk").addEventListener("submit", (e) => {
      if (state.section !== "library" || L.openId === null) return;
      e.preventDefault();
      ask($("#drawerAskInput").value.trim());
    });
    $("#drawer").addEventListener("drawerclose", () => {
      if (L.openId === null) return;
      L.openId = null;
      $$(".lib-row.selected").forEach((x) => x.classList.remove("selected"));
    });
  });
})();
