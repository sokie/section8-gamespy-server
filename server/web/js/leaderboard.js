// The leaderboard page: every ranked player, 50 to a page, sorted by the column in ?sort=.

import { $, apiUrl, getJSON, num, pageUrl, setTitle, showLeaderboard, start } from "./portal.js";

const PAGE_SIZE = 50;

start(async ({ game }) => {
  setTitle(game, "Leaderboard");
  const params = new URLSearchParams(location.search);
  const sort = params.get("sort") || "xp";
  const read = (page) =>
    getJSON(apiUrl(game, `/leaderboard?sort=${encodeURIComponent(sort)}&offset=${(page - 1) * PAGE_SIZE}&limit=${PAGE_SIZE}`));

  let page = Math.max(1, Number.parseInt(params.get("page"), 10) || 1);
  let board = await read(page);
  const last = Math.max(1, Math.ceil(board.total / PAGE_SIZE));
  if (page > last) {
    page = last;
    board = await read(page);
  }

  $("leaderboard-summary").textContent =
    `${game.name} · ${num(board.total)} ranked players, sorted by ${board.sort_label}. Click a column to re-sort.`;
  showLeaderboard(game, board.rows, board.sort);

  if (last > 1) {
    const at = (n) => pageUrl(game, `/leaderboard?sort=${board.sort}&page=${n}`);
    $("pager-page").textContent = `Page ${page} of ${last}`;
    $("pager-prev").href = at(page - 1);
    $("pager-prev").hidden = page <= 1;
    $("pager-next").href = at(page + 1);
    $("pager-next").hidden = page >= last;
    $("pager").hidden = false;
  }
});
