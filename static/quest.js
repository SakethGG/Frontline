// Quest: turns the to-do list into missions. XP, levels, a daily goal, a streak and badges are all
// derived from the tasks themselves (status, priority, due, done_at), so there's nothing extra to store.
const Quest = (() => {
  const XP = { 0: 10, 1: 20, 2: 40 };     // by priority: low / normal / high
  const ON_TIME = 10;                      // finished on or before its due date
  const GOAL_BONUS = 30;                   // per day the daily goal was met
  // One military rank per level ([name, abbreviation]); past the last one you stay Commander.
  const RANKS = [
    ["Private", "PVT"], ["Private First Class", "PFC"], ["Lance Corporal", "LCPL"], ["Corporal", "CPL"],
    ["Sergeant", "SGT"], ["Staff Sergeant", "SSGT"], ["Gunnery Sergeant", "GYSGT"], ["Master Sergeant", "MSGT"],
    ["First Sergeant", "1STSGT"], ["Sergeant Major", "SGTMAJ"], ["Second Lieutenant", "2NDLT"], ["First Lieutenant", "1STLT"],
    ["Captain", "CAPT"], ["Major", "MAJ"], ["Lieutenant Colonel", "LTCOL"], ["Colonel", "COL"],
    ["Brigadier General", "BGEN"], ["Major General", "MAJGEN"], ["Lieutenant General", "LTGEN"], ["General", "GEN"],
    ["Commander", "CDR"],
  ];
  const DIFFICULTY = { 0: "Easy", 1: "Standard", 2: "Boss" };
  // [key, icon, name, description, stat it tracks, target, medal tier]
  const BADGES = [
    ["first", "🎖️", "First Deployment", "Complete your first mission.", "done", 1, "bronze"],
    ["ten", "🪖", "Fireteam", "Complete 10 missions.", "done", 10, "bronze"],
    ["fifty", "🏅", "Platoon Leader", "Complete 50 missions.", "done", 50, "silver"],
    ["century", "🦅", "Centurion", "Complete 100 missions.", "done", 100, "gold"],
    ["boss", "🎯", "HVT Hunter", "Take down 5 high-priority (boss) missions.", "bosses", 5, "gold"],
    ["punctual", "⏱️", "Zero Hour", "Finish 10 missions on or before their due date.", "onTime", 10, "silver"],
    ["blitz", "⚡", "Blitzkrieg", "Complete 5 missions in a single day.", "bestDay", 5, "silver"],
    ["streak3", "🔥", "Tour of Duty", "Complete a mission 3 days in a row.", "bestStreak", 3, "bronze"],
    ["streak7", "🛡️", "Iron Discipline", "Complete a mission 7 days in a row.", "bestStreak", 7, "silver"],
    ["streak30", "⭐", "Legend", "Complete a mission 30 days in a row.", "bestStreak", 30, "gold"],
  ].map(([key, icon, name, how, stat, target, tier]) => ({ key, icon, name, how, stat, target, tier }));
  const TIER = { bronze: "Bronze medal", silver: "Silver medal", gold: "Gold medal" };

  const localDay = (iso) => { const d = new Date(iso); return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
  const shiftDay = (day, n) => new Date(new Date(day + "T12:00:00Z").getTime() + n * 864e5).toISOString().slice(0, 10);
  const goal = () => pref("quest.goal") || 3;

  // XP a single mission is worth (the on-time bonus counts if it isn't overdue yet)
  function reward(t) {
    const day = t.status === "done" && t.done_at ? localDay(t.done_at) : todayISO();
    return (XP[t.priority] ?? XP[1]) + (t.due && day <= t.due ? ON_TIME : 0);
  }

  // XP needed to go from level L to L+1 grows by 50 each level: 100, 150, 200…
  const levelCost = (L) => 100 + 50 * (L - 1);

  function stats(tasks) {
    const done = tasks.filter((t) => t.status === "done" && t.done_at);
    const perDay = {};
    let xp = 0, bosses = 0, onTime = 0;
    for (const t of done) {
      const day = localDay(t.done_at);
      perDay[day] = (perDay[day] || 0) + 1;
      xp += reward(t);
      if (t.priority === 2) bosses++;
      if (t.due && day <= t.due) onTime++;
    }
    const g = goal();
    xp += Object.values(perDay).filter((n) => n >= g).length * GOAL_BONUS;

    // streak: consecutive days with at least one completion, ending today (or yesterday, still alive)
    const today = todayISO();
    let streak = 0, d = perDay[today] ? today : shiftDay(today, -1);
    while (perDay[d]) { streak++; d = shiftDay(d, -1); }
    let bestStreak = 0, run = 0, prev = null;
    for (const day of Object.keys(perDay).sort()) {
      run = prev && shiftDay(prev, 1) === day ? run + 1 : 1;
      bestStreak = Math.max(bestStreak, run);
      prev = day;
    }

    let level = 1, rest = xp;
    while (rest >= levelCost(level)) { rest -= levelCost(level); level++; }
    const s = {
      xp, level, into: rest, need: levelCost(level), rank: RANKS[Math.min(RANKS.length - 1, level - 1)][0], rankAbbr: RANKS[Math.min(RANKS.length - 1, level - 1)][1],
      done: done.length, bosses, onTime, today: perDay[today] || 0, goal: g, streak, bestStreak,
      bestDay: Math.max(0, ...Object.values(perDay)), litToday: !!perDay[today],
    };
    s.badges = BADGES.filter((b) => s[b.stat] >= b.target).map((b) => b.key);
    return s;
  }

  // ---------------------------------------------------------------- rendering

  function hud(tasks, { compact = false } = {}) {
    const s = stats(tasks);
    const pct = Math.round((100 * s.into) / s.need);
    const gp = Math.min(100, Math.round((100 * s.today) / s.goal));
    const goalDone = s.today >= s.goal;
    const open = tasks.filter((t) => t.status === "todo" || t.status === "doing");
    const bounty = open.reduce((n, t) => n + reward(t), 0);
    const level = `
      <div class="q-level" title="${s.xp} XP total">
        <span class="q-lvl">LV<b>${s.level}</b></span>
        <div class="q-xp">
          <div class="q-rank"><span><span class="q-abbr">${esc(s.rankAbbr)}</span><span class="q-rank-name">${esc(s.rank)}</span></span> <small>${s.into} / ${s.need} XP</small></div>
          <div class="q-bar"><span style="width:${pct}%"></span></div>
        </div>
      </div>`;
    const daily = `
      <div class="q-stat q-goal ${goalDone ? "met" : ""}" title="Complete ${plural(s.goal, "mission")} today for +${GOAL_BONUS} XP">
        <span class="q-ring" style="--p:${gp}"><b>${s.today}/${s.goal}</b></span>
        <span><span class="q-label">Daily goal</span>${goalDone ? "Cleared! ✓" : `${s.goal - s.today} to go`}
          ${compact ? "" : `<span class="q-adj"><button type="button" data-goal="-1" aria-label="Lower daily goal">−</button><button type="button" data-goal="1" aria-label="Raise daily goal">+</button></span>`}</span>
      </div>`;
    const streak = `
      <div class="q-stat q-streak ${s.litToday ? "lit" : s.streak ? "warn" : ""}" title="${s.streak && !s.litToday ? "Complete a mission today to keep your streak" : `Best: ${plural(s.bestStreak, "day")}`}">
        <span class="q-flame">🔥</span><span><span class="q-label">Streak</span>${plural(s.streak, "day")}${s.streak && !s.litToday ? " · at risk!" : ""}</span>
      </div>`;
    if (compact) return `<div class="quest-hud compact">${level}${daily}${streak}</div>`;
    return `<div class="quest-hud">
      ${level}${daily}${streak}
      <div class="q-stat" title="XP waiting in your open missions"><span class="q-flame">💰</span><span><span class="q-label">Bounty</span>${bounty} XP up for grabs</span></div>
      <div class="q-badges" aria-label="Badges">${BADGES.map((b, i) => badge(b, s, i >= BADGES.length / 2)).join("")}</div>
    </div>`;
  }

  // A medal on a ribbon; hovering (or focusing) it shows a card with what it's for and your progress.
  function badge(b, s, alignRight) {
    const got = s.badges.includes(b.key), have = Math.min(s[b.stat], b.target);
    return `<span class="q-badge ${b.tier} ${got ? "got" : ""}" tabindex="0" aria-label="${esc(b.name)}: ${esc(b.how)} ${got ? "Earned" : `${have} of ${b.target}`}">
      <span class="q-ribbon"></span><span class="q-medal">${b.icon}</span>
      <span class="q-tip ${alignRight ? "right" : ""}" role="tooltip">
        <span class="q-tip-head"><span class="q-tip-icon">${b.icon}</span><span><b>${esc(b.name)}</b><small class="q-tier">${TIER[b.tier]}</small></span></span>
        <span class="q-tip-how">${esc(b.how)}</span>
        ${got ? `<span class="q-tip-done">✓ Earned</span>`
          : `<span class="q-tip-bar"><i style="width:${Math.round((100 * have) / b.target)}%"></i></span><span class="q-tip-prog">🔒 ${have} / ${b.target}</span>`}
      </span>
    </span>`;
  }

  // Small "+20 XP · Boss" chip for a mission card
  function chip(t) {
    if (t.status === "done") return `<span class="q-chip got">+${reward(t)} XP</span>`;
    return `<span class="q-chip d${t.priority}" title="${DIFFICULTY[t.priority] || ""} mission${t.due ? ` · +${ON_TIME} XP if done by its due date` : ""}">${t.priority === 2 ? "🐉 " : ""}${reward(t)} XP</span>`;
  }

  function adjustGoal(delta) { pref("quest.goal", Math.max(1, Math.min(20, goal() + delta))); }

  // ---------------------------------------------------------------- celebration

  const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  function burst(x, y, n = 28) {
    if (calm()) return;
    const colors = ["#ff8b7b", "#fff27a", "#8fe3a4", "#7cc4ff", "#c9a7ff", "#ffb86b"];
    for (let i = 0; i < n; i++) {
      const p = document.createElement("span");
      p.className = "q-confetti";
      const a = Math.random() * Math.PI * 2, r = 60 + Math.random() * 110;
      p.style.cssText = `left:${x}px;top:${y}px;background:${colors[i % colors.length]};--dx:${Math.cos(a) * r}px;--dy:${Math.sin(a) * r - 40}px;--rot:${Math.random() * 720 - 360}deg`;
      document.body.appendChild(p);
      setTimeout(() => p.remove(), 900);
    }
  }

  function float(x, y, text) {
    const f = document.createElement("div");
    f.className = "q-float";
    f.textContent = text;
    f.style.left = `${x}px`; f.style.top = `${y}px`;
    document.body.appendChild(f);
    setTimeout(() => f.remove(), 1300);
  }

  function banner(title, sub) {
    $(".q-banner")?.remove();
    const b = document.createElement("div");
    b.className = "q-banner";
    b.innerHTML = `<b>${esc(title)}</b><span>${esc(sub)}</span>`;
    document.body.appendChild(b);
    burst(innerWidth / 2, innerHeight / 3, 60);
    setTimeout(() => b.remove(), 2600);
  }

  // Compare stats before/after a change and celebrate what was gained. `at` = {x, y} in viewport px.
  function celebrate(before, after, at) {
    const gained = after.xp - before.xp;
    if (gained <= 0) return;
    const x = at?.x ?? innerWidth / 2, y = at?.y ?? innerHeight / 2;
    burst(x, y);
    float(x, y, `+${gained} XP`);
    const news = BADGES.filter((b) => after.badges.includes(b.key) && !before.badges.includes(b.key));
    if (after.level > before.level) banner(after.rank !== before.rank ? "Promoted!" : `Level ${after.level}!`, after.rank !== before.rank ? `${after.rank} (${after.rankAbbr}) · Level ${after.level}` : `${after.rank} · keep going`);
    else if (news.length) banner(`${news[0].icon} ${news[0].name}`, `${TIER[news[0].tier]} earned`);
    else if (after.today >= after.goal && before.today < before.goal) banner("Daily goal cleared!", `+${GOAL_BONUS} XP bonus`);
    else if (after.streak > before.streak && after.streak > 1) toast(`🔥 ${after.streak}-day streak!`);
    else toast(["Mission complete ✓", "Nice work ✓", "Target down ✓", "On to the next one ✓"][Math.floor(Math.random() * 4)]);
  }

  // Where an element is on screen, for anchoring the burst before it gets re-rendered
  const spot = (el) => { const r = el?.getBoundingClientRect(); return r ? { x: r.left + Math.min(r.width / 2, 40), y: r.top + r.height / 2 } : null; };

  return { stats, hud, chip, reward, celebrate, spot, adjustGoal };
})();
