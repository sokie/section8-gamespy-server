// The overview page (index.html): totals, the top 10, servers online and the games.

import { $, apiUrl, getJSON, hours, num, pageUrl, render, setBackground, setTitle, showLeaderboard, showServers, start } from "./portal.js";

start(async ({ game, games }) => {
  setTitle(game, "Overview");
  const [summary, top, servers] = await Promise.all([
    getJSON(apiUrl(game, "/summary")),
    getJSON(apiUrl(game, "/leaderboard?sort=xp&limit=10")),
    getJSON(apiUrl(game, "/servers")),
  ]);

  setBackground($("banner"), game.banner);
  $("banner-title").textContent = game.name;
  if (game.logo) {
    $("banner-logo").src = game.logo;
    $("banner-logo").alt = game.name;
    $("banner-logo").hidden = false;
    $("banner-title").hidden = true;
  }

  const tiles = [
    ["Ranked players", num(summary.players)],
    ["Ranked matches", num(summary.matches)],
    ["Servers online", num(summary.servers_online)],
    ["Total kills", num(summary.total_kills)],
    ["Time in battle", hours(summary.total_seconds)],
  ];
  $("summary").replaceChildren(...tiles.map(([label, value]) => render("tpl-tile", { label, value })));

  showLeaderboard(game, top.rows);
  $("full-leaderboard").href = pageUrl(game, "/leaderboard");
  showServers(
    game,
    servers.filter((s) => s.online),
    { compact: true },
  );

  $("games").replaceChildren(
    ...games.map((g) => {
      const card = render("tpl-game-card", {
        link: { href: pageUrl(g) },
        name: g.name,
        players: `${num(g.players)} ranked player${g.players === 1 ? "" : "s"}`,
      });
      card.classList.toggle("on", g.key === game.key);
      if (g.banner) card.querySelector(".art").style.backgroundImage = `url(${JSON.stringify(g.banner)})`;
      return card;
    }),
  );
});
