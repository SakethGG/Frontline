// Bin: ideas, blog posts and projects that are completed or deleted wait here for 30 days, then the server deletes
// them for good. Shared by Ideas and Projects: Bin.complete() / Bin.remove() move an item in, Bin.open() shows it.
const Bin = (() => {
  const KIND = { idea: "💡 Idea", project: "🚀 Project idea", proposal: "📝 Proposal", roadmap: "🗺 Roadmap", blog: "✍️ Blog" };
  let data = { days: 30, ideas: [], projects: [] };

  const daysLeft = (iso) => Math.max(0, Math.ceil((new Date(iso) - Date.now()) / 864e5));
  const count = () => data.ideas.length + data.projects.length;

  async function refresh() {
    data = await api("/api/bin").catch(() => data);
    $$("[data-bin-count]").forEach((el) => (el.textContent = count() || ""));
    return data;
  }

  // reload whichever section is on screen, so restored / deleted items show up (or vanish) there
  const reloadSection = () => Sections[state.section]?.show?.();

  async function complete(type, id, title) {
    await api(`/api/${type}/${id}/complete`, { method: "POST" });
    await refresh();
    toast(`“${title}” completed: moved to the bin for ${data.days} days`);
  }

  // deleting never destroys anything straight away: it goes to the bin like a completed item
  async function remove(type, id, title) {
    await api(`/api/${type}/${id}`, { method: "DELETE" });
    await refresh();
    toast(`“${title}” moved to the bin. It's deleted for good in ${data.days} days`);
  }

  function row(type, r) {
    const left = daysLeft(r.deletes_at);
    const label = type === "projects" ? "🎯 Project" : KIND[r.kind] || "💡 Idea";
    const extra = type === "projects" && r.tasks ? ` · ${plural(r.tasks, "task")}` : "";
    return `
      <li class="bin-row">
        <span class="bin-main">
          <span class="pill">${label}</span>
          <b>${esc(type === "projects" ? r.name : r.title)}</b>
          <small>${r.bin_reason === "deleted" ? "Deleted" : "Completed"} ${esc(fmtDate(r.binned_at.slice(0, 10)))}${extra} · ${left ? `deleted for good in ${plural(left, "day")}` : "deleted for good today"}</small>
        </span>
        <span class="bin-actions">
          <button class="ghost-btn" data-bin-restore="${type}:${r.id}">↩ Restore</button>
          <button class="danger-btn" data-bin-delete="${type}:${r.id}">Delete forever</button>
        </span>
      </li>`;
  }

  function render() {
    const body = $("#binBody");
    body.innerHTML = count()
      ? `<p class="hint">Completed and deleted items stay here for ${data.days} days and are then deleted for good. Restore one to put it back where it was.</p>
         <ul class="bin-list">${data.projects.map((p) => row("projects", p)).join("")}${data.ideas.map((i) => row("ideas", i)).join("")}</ul>`
      : `<div class="empty"><h3>The bin is empty</h3><p>Completed or deleted ideas, blog posts and projects wait here for ${data.days} days before they're gone for good.</p></div>`;
    $("#binEmpty").hidden = !count();
  }

  async function open() {
    await refresh();
    render();
    $("#binModal").showModal();
  }

  document.addEventListener("DOMContentLoaded", () => {
    refresh();
    document.addEventListener("click", (e) => { if (e.target.closest("[data-bin-open]")) open(); });
    $("#binBody").addEventListener("click", async (e) => {
      const r = e.target.closest("[data-bin-restore]"), d = e.target.closest("[data-bin-delete]");
      if (!r && !d) return;
      const [type, id] = (r || d).getAttribute(r ? "data-bin-restore" : "data-bin-delete").split(":");
      const item = data[type].find((x) => x.id === +id);
      const name = item?.name || item?.title || "this";
      if (d) {
        const n = type === "projects" && item?.tasks ? ` and its ${plural(item.tasks, "task")}` : "";
        if (!confirm(`Delete “${name}”${n} for good? This can't be undone.`)) return;
        await api(`/api/${type}/${id}?forever=1`, { method: "DELETE" });
      } else {
        await api(`/api/${type}/${id}/restore`, { method: "POST" });
        toast(`Restored “${name}”`);
      }
      await refresh();
      render();
      reloadSection();
    });
    $("#binEmpty").onclick = async () => {
      if (!confirm(`Delete all ${plural(count(), "item")} in the bin for good? This can't be undone.`)) return;
      await api("/api/bin", { method: "DELETE" });
      await refresh();
      render();
    };
  });

  return { open, complete, remove, refresh, count };
})();
