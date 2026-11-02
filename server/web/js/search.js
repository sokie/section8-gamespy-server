// The search page: players whose name, GameSpy nick or profileid matches ?q=.

import { $, apiUrl, getJSON, num, playerUrl, render, setTitle, showEmpty, start } from "./portal.js";

start(async ({ game }) => {
  setTitle(game, "Search");
  const query = new URLSearchParams(location.search).get("q") || "";
  document.querySelector("#site-search input").value = query;
  $("search-summary").textContent = `${game.name} · results for “${query}”`;

  const found = query.trim() ? await getJSON(apiUrl(game, `/players?q=${encodeURIComponent(query)}`)) : [];
  if (!found.length) {
    showEmpty($("results"), `No ${game.name} player matches that name.`);
    return;
  }
  $("results-rows").replaceChildren(
    ...found.map((p) =>
      render("tpl-result-row", {
        icon: { src: p.icon, alt: p.rank },
        name: { text: p.name, href: playerUrl(game, p.profileid) },
        rank: p.rank,
        level: p.level,
        xp: num(p.xp),
      }),
    ),
  );
});
