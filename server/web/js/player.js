// The player page (/players/<profileid>): level, career, records, awards, unlocks, gear and recent matches.

import { $, apiUrl, day, getJSON, hours, num, ratio, render, setBackground, setTitle, start } from "./portal.js";

const GEAR_COLUMNS = {
  TimePlayed: "Time",
  Kills: "Kills",
  Destruction: "Destr.",
  KillsIAL: "Best kills",
  DamageIAL: "Best dmg",
  DestructionIAL: "Best destr.",
};

start(async ({ game }) => {
  const id = location.pathname.match(/\/players\/(\d+)$/)?.[1];
  const p = id ? await getJSON(apiUrl(game, `/players/${id}`)) : null;
  if (!p) {
    setTitle(game, "Not found");
    $("player-missing").hidden = false;
    return;
  }
  setTitle(game, p.name);
  showHero(game, p);
  $("player").hidden = false;
  if (!p.has_stats) {
    $("no-career").textContent = `No ranked ${game.name} career yet.`;
    $("no-career").hidden = false;
    return;
  }
  showCareer(p);
  showAwards("badges", "Badges", p.badges, (b) => (b.date ? ` · ${day(b.date)}` : ""));
  showAwards("feats", "Feats", p.feats, (f) => {
    const tiers = f.count ? ` (${f.tiers[0]}×1 / ${f.tiers[1]}×2 / ${f.tiers[2]}×3)` : "";
    return tiers + (f.date ? ` · ${day(f.date)}` : "");
  });
  showUnlocks(p.unlocks);
  showGear(p);
  showHistory(p.history);
  $("career").hidden = false;
});

function showHero(game, p) {
  setBackground($("hero"), game.banner, 0.35);
  $("hero-icon").src = p.icon;
  $("hero-icon").alt = p.rank;
  $("hero-level").textContent = p.level;
  $("hero-game").textContent = game.name;
  $("hero-name").textContent = p.name;
  $("hero-rank").textContent = p.rank;
  if (p.clan_tag) {
    $("hero-clan").textContent = `[${p.clan_tag}]`;
    $("hero-clan").hidden = false;
  }
  if (p.level_next) {
    const pct = (100 * (p.xp - p.level_floor)) / Math.max(1, p.level_next - p.level_floor);
    $("hero-bar").style.width = `${Math.max(0, Math.min(100, pct)).toFixed(1)}%`;
    $("hero-xp").textContent = `${num(p.xp)} / ${num(p.level_next)} XP to level ${p.level + 1}`;
  } else {
    $("hero-progress").hidden = true;
    $("hero-xp").textContent = `${num(p.xp)} XP · maximum level`;
  }
  if (p.uniquenick && p.uniquenick !== p.name) {
    $("hero-nick-value").textContent = p.uniquenick;
    $("hero-nick").hidden = false;
  }
  $("hero-also").replaceChildren(
    ...p.other_careers.map((c) => render("tpl-other-career", { link: { text: `${c.name} career →`, href: c.url } })),
  );
}

function showCareer(p) {
  const tiles = [
    ["Kills", num(p.kills)],
    ["Deaths", num(p.deaths)],
    ["K/D", ratio(p.kd)],
    ["Time played", hours(p.time_played)],
    ["Matches", num(p.matches)],
    ...p.career.filter((c) => !["Ranked_Kills", "Ranked_Deaths"].includes(c.column)).map((c) => [c.label, num(c.value)]),
  ];
  $("career-tiles").replaceChildren(...tiles.map(([label, value]) => render("tpl-tile", { label, value })));

  $("records").replaceChildren(
    ...p.records.map((r) =>
      render("tpl-record-row", { label: r.label.replace(/ in a life$/, ""), value: num(r.value), date: day(r.date) }),
    ),
  );
  if (p.modes.length) {
    $("modes").replaceChildren(...p.modes.map((m) => render("tpl-mode-row", { name: m.name, time: hours(m.seconds) })));
  } else {
    $("modes-box").hidden = true;
    $("records-and-modes").classList.remove("two");
  }
}

/** Fills a grid of badges or feats. Earned ones are in full colour with their count. */
function showAwards(id, title, awards, extra) {
  const earned = awards.filter((a) => a.count).length;
  $(`${id}-title`).textContent = `${title} · ${earned} of ${awards.length}`;
  $(id).replaceChildren(
    ...awards.map((a) => {
      const card = render("tpl-award", {
        icon: { src: a.icon, alt: a.name },
        count: a.count ? `×${num(a.count)}` : "",
        category: a.category,
        name: a.name,
        detail: a.description + extra(a),
      });
      card.classList.toggle("locked", !a.count);
      return card;
    }),
  );
}

function showUnlocks(unlocks) {
  if (!unlocks.length) return;
  const got = unlocks.filter((u) => u.unlocked).length;
  $("unlocks-title").textContent = `Unlocks · ${got} of ${unlocks.length}`;
  $("unlocks").replaceChildren(
    ...unlocks.map((u) => {
      const card = render("tpl-award", {
        icon: { src: u.icon, alt: u.name },
        count: "",
        category: u.category,
        name: u.name,
        detail: u.requirement + (u.description ? ` · ${u.description}` : ""),
      });
      card.classList.toggle("locked", !u.unlocked);
      return card;
    }),
  );
  $("unlocks-box").hidden = false;
}

function showGear(p) {
  if (!p.gear.length) {
    $("gear").replaceWith(render("tpl-empty", { text: "No gear used in ranked play yet." }));
    return;
  }
  const withMods = p.gear.some((g) => g.mod);
  const head = $("gear-head");
  if (withMods) head.append(cell("th", "Mod"));
  for (const stat of p.gear_stats) head.append(cell("th", GEAR_COLUMNS[stat] ?? stat, "n"));

  $("gear-rows").replaceChildren(
    ...p.gear.map((g) => {
      const row = render("tpl-gear-row", { icon: { src: g.icon, alt: g.weapon }, weapon: g.weapon });
      if (withMods) row.append(cell("td", g.mod, "muted"));
      for (const stat of p.gear_stats) row.append(cell("td", stat === "TimePlayed" ? hours(g[stat]) : num(g[stat]), "n"));
      return row;
    }),
  );
}

function showHistory(history) {
  if (!history.length) {
    $("history").replaceWith(render("tpl-empty", { text: "No ranked matches recorded yet." }));
    return;
  }
  $("history-rows").replaceChildren(
    ...history.map((h) =>
      render("tpl-history-row", {
        date: (h.date || "").replace("T", " ").replace(/Z$/, ""),
        mode: h.mode || "—",
        players: num(h.players),
        xp: `+${num(h.xp_gained || 0)}`,
        level: h.level_after ?? "—",
      }),
    ),
  );
}

/** A plain table cell holding text. */
function cell(tag, text, className = "") {
  const el = document.createElement(tag);
  el.textContent = text;
  if (className) el.className = className;
  return el;
}
