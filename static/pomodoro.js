// Focus timer (pomodoro) in the Home sidebar: focus → short break, with a long break every 4th session. The state lives in
// localStorage as an end timestamp, so it keeps running across sections and reloads; the tab title shows it.
const Pomo = (() => {
  const PRESETS = { "25": [25, 5, 15], "50": [50, 10, 20], "15": [15, 3, 10] };
  const MODES = { focus: "Focus", short: "Short break", long: "Long break" };
  const KEY = "frontline:pomo";
  const baseTitle = document.title;
  let s = load(), tasks = [];

  function load() {
    try { return { mode: "focus", preset: "25", running: false, endAt: 0, left: null, taskId: null, round: 0, log: {}, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; }
    catch { return { mode: "focus", preset: "25", running: false, endAt: 0, left: null, taskId: null, round: 0, log: {} }; }
  }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch {} };
  const length = (mode = s.mode) => PRESETS[s.preset][{ focus: 0, short: 1, long: 2 }[mode]] * 60;
  const remaining = () => Math.max(0, s.running ? Math.round((s.endAt - Date.now()) / 1000) : s.left ?? length());
  const mmss = (sec) => `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
  const today = () => s.log[todayISO()] || { n: 0, min: 0 };

  function setMode(mode) { Object.assign(s, { mode, running: false, left: null }); save(); }
  function start() { Object.assign(s, { running: true, endAt: Date.now() + remaining() * 1000 }); save(); askNotify(); }
  function pause() { Object.assign(s, { left: remaining(), running: false }); save(); }

  function finish() {
    const wasFocus = s.mode === "focus";
    if (wasFocus) {
      const d = today();
      s.log = { [todayISO()]: { n: d.n + 1, min: d.min + length() / 60 } };  // only today's tally is kept
      s.round++;
    }
    setMode(wasFocus ? (s.round % 4 === 0 ? "long" : "short") : "focus");
    chime();
    const task = tasks.find((t) => t.id === s.taskId);
    const msg = wasFocus ? `Session done${task ? `: ${task.title}` : ""}. Take a ${s.mode === "long" ? "long" : "short"} break.` : "Break's over. Ready to focus?";
    toast(`${wasFocus ? "🍅" : "⏰"} ${msg}`, 6000);
    if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
      try { new Notification(wasFocus ? "Focus session complete 🍅" : "Break's over", { body: msg, tag: "frontline-pomo" }); } catch {}
    }
  }

  async function askNotify() {
    if ("Notification" in window && Notification.permission === "default") { try { await Notification.requestPermission(); } catch {} }
  }

  function chime() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      [660, 880, 1100].forEach((f, i) => {
        const o = ctx.createOscillator(), g = ctx.createGain(), t = ctx.currentTime + i * 0.18;
        o.frequency.value = f; o.type = "sine";
        g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.5);
        o.connect(g).connect(ctx.destination); o.start(t); o.stop(t + 0.55);
      });
      setTimeout(() => ctx.close(), 1500);
    } catch {}
  }

  // ---------------------------------------------------------------- rendering

  function html(openTasks) {
    tasks = openTasks;
    const left = remaining(), pct = 100 - (100 * left) / length();
    const d = today();
    const task = tasks.find((t) => t.id === s.taskId);
    return `
    <section class="pomo mode-${s.mode} ${s.running ? "running" : ""}" id="pomo">
      <div class="pomo-ring" style="--p:${pct}"><div><b id="pomoTime">${mmss(left)}</b><span>${MODES[s.mode]}</span></div></div>
      <div class="pomo-main">
        <div class="pomo-top">
          <div class="seg small" role="tablist">${Object.entries(MODES).map(([k, v]) =>
            `<button type="button" class="${k === s.mode ? "on" : ""}" data-pmode="${k}" title="${v}">${v.split(" ")[0]}</button>`).join("")}</div>
          <select data-ppreset aria-label="Session length" title="Focus / break length (minutes)">${Object.keys(PRESETS).map((k) =>
            `<option value="${k}" ${k === s.preset ? "selected" : ""}>${PRESETS[k][0]} / ${PRESETS[k][1]} min</option>`).join("")}</select>
        </div>
        <label class="pomo-task"><span class="q-label">Focusing on</span>
          <select data-ptask><option value="">Nothing in particular</option>${tasks.map((t) =>
            `<option value="${t.id}" ${t.id === s.taskId ? "selected" : ""}>${esc(t.title)}</option>`).join("")}</select></label>
        <div class="pomo-actions">
          <button type="button" class="primary-btn" data-pact="toggle">${s.running ? "❚❚ Pause" : left < length() ? "▶ Resume" : "▶ Start"}</button>
          <button type="button" class="ghost-btn" data-pact="reset" title="Restart this session">↺ Reset</button>
          <button type="button" class="ghost-btn" data-pact="skip" title="Skip to the next session">⏭ Skip</button>
          ${task && s.mode !== "focus" ? `<button type="button" class="ghost-btn" data-done="${task.id}" title="Mark “${esc(task.title)}” as done">✓ Mission done</button>` : ""}
        </div>
        <div class="pomo-today" title="Focus sessions completed today">
          <span class="q-label">Today</span>
          <span class="pomo-dots">${"🍅".repeat(Math.min(d.n, 12))}${d.n > 12 ? ` ×${d.n}` : ""}${d.n ? "" : `<span class="hint">No sessions yet</span>`}</span>
          ${d.n ? `<span class="meta">${d.min} min focused · next long break in ${4 - (s.round % 4)}</span>` : ""}
        </div>
      </div>
    </section>`;
  }

  // Updates the open panel and the tab title every second, wherever you are in the app
  function tick() {
    if (s.running && remaining() <= 0) { finish(); redraw(); }
    const left = remaining();
    document.title = s.running ? `${mmss(left)} · ${MODES[s.mode]} · ${baseTitle}` : baseTitle;
    const el = $("#pomoTime");
    if (el) { el.textContent = mmss(left); $("#pomo .pomo-ring").style.setProperty("--p", 100 - (100 * left) / length()); }
  }
  const redraw = () => { const el = $("#pomo"); if (el) el.outerHTML = html(tasks); };

  function onClick(e) {
    const m = e.target.closest("[data-pmode]");
    if (m) { setMode(m.dataset.pmode); return redraw(); }
    const act = e.target.closest("[data-pact]")?.dataset.pact;
    if (act === "toggle") s.running ? pause() : start();
    else if (act === "reset") setMode(s.mode);
    else if (act === "skip") { setMode(s.mode === "focus" ? "short" : "focus"); }
    else return;
    redraw();
  }
  function onChange(e) {
    if (e.target.matches("[data-ppreset]")) { s.preset = e.target.value; setMode(s.mode); redraw(); }
    if (e.target.matches("[data-ptask]")) { s.taskId = e.target.value ? +e.target.value : null; save(); }
  }

  setInterval(tick, 1000);
  document.addEventListener("DOMContentLoaded", tick);
  // another Frontline tab started/paused the timer
  addEventListener("storage", (e) => { if (e.key === KEY) { s = load(); redraw(); tick(); } });

  return { html, onClick, onChange, toggle: () => { s.running ? pause() : start(); redraw(); } };
})();
