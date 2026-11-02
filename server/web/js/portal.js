// Shared by every portal page: the open game, the header, API calls, formatting, and filling templates.
//
// A page is an HTML file in server/web with <template> elements for its repeated parts. Its script fills
// them from the JSON API with render(). Values always go into the page as text, never as markup.

/** Shorthand for document.getElementById. */
export const $ = (id) => document.getElementById(id);

/** Fetches JSON from the API. Gives null for a 404 and throws for any other error. */
export async function getJSON(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`${path} answered HTTP ${response.status}`);
  return response.json();
}

/** The URL of a page of `game`, such as "/leaderboard". Section 8 (2009) pages start with /s8. */
export const pageUrl = (game, path = "/") => (path === "/" ? game.prefix || "/" : game.prefix + path);
export const apiUrl = (game, path) => `/api${game.prefix}${path}`;
export const playerUrl = (game, id) => pageUrl(game, `/players/${id}`);

/**
 * Runs a page: works out the open game from the URL, sets up the header, then calls
 * main({ game, games }). An error on the way is shown at the top of the page.
 */
export function start(main) {
  getJSON("/api/games")
    .then(async (games) => {
      const prefix = "/" + (location.pathname.split("/")[1] || "");
      const game = games.find((g) => g.prefix === prefix) ?? games.find((g) => !g.prefix) ?? games[0];
      setUpHeader(game, games);
      await main({ game, games });
    })
    .catch((error) => {
      console.error(error);
      $("page-error").textContent = `Could not load the stats: ${error.message}. The server log has the details.`;
      $("page-error").hidden = false;
    });
}

export function setTitle(game, title) {
  document.title = `${title} · ${game.name} Stats`;
}

function setUpHeader(game, games) {
  const page = document.body.dataset.page;
  for (const link of document.querySelectorAll(".pages a")) {
    if (link.dataset.path) link.href = pageUrl(game, link.dataset.path);
    link.classList.toggle("on", link.dataset.page === page);
  }
  // The game switch keeps you on the same kind of page, except for one player's career.
  const samePage = location.pathname.slice(game.prefix.length) || "/";
  const switchTo = (g) => pageUrl(g, page === "player" || page === "not-found" ? "/" : samePage) + location.search;
  $("game-switch").replaceChildren(
    ...games.map((g) => {
      const link = render("tpl-game-link", { link: { text: g.short, href: switchTo(g) } });
      if (g.key === game.key) {
        link.classList.add("on");
        link.setAttribute("aria-current", "page");
      }
      return link;
    }),
  );
  $("site-search").action = pageUrl(game, "/players");
  $("footer-game").textContent = game.name;
}

/**
 * Clones <template id=templateId> and fills its [data-field] elements from `values`.
 * A plain value becomes the element's text. An object sets several things at once:
 *   { text, href, src, alt, hidden, className }
 * An <img> whose src is empty is removed, so a missing icon leaves no gap.
 */
export function render(templateId, values = {}) {
  const node = $(templateId).content.firstElementChild.cloneNode(true);
  const fields = node.matches("[data-field]") ? [node, ...node.querySelectorAll("[data-field]")] : node.querySelectorAll("[data-field]");
  for (const el of fields) {
    const key = el.dataset.field;
    if (key in values) fillField(el, values[key]);
  }
  return node;
}

function fillField(el, value) {
  if (value === null || typeof value !== "object") {
    el.textContent = value ?? "";
    return;
  }
  if ("text" in value) el.textContent = value.text ?? "";
  if ("href" in value) el.href = value.href;
  if ("src" in value) {
    if (!value.src) {
      el.remove();
      return;
    }
    el.src = value.src;
    el.alt = value.alt ?? "";
  }
  if ("hidden" in value) el.hidden = Boolean(value.hidden);
  if (value.className) el.classList.add(...value.className.split(" ").filter(Boolean));
}

/** Replaces a table's panel with a message, for a list that has nothing in it. */
export function showEmpty(panel, text) {
  panel.replaceWith(render("tpl-empty", { text }));
}

/** Sets an element's background to an image, darkened on the left so text stays readable. */
export function setBackground(el, src, fade = 0.55) {
  if (!src) return;
  el.style.backgroundImage = `linear-gradient(90deg, rgba(11,15,20,.96) 25%, rgba(11,15,20,${fade})), url(${JSON.stringify(src)})`;
}

// Formatting

export function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.trunc(n).toLocaleString("en-US") : String(value ?? "");
}

export const ratio = (value) => Number(value || 0).toFixed(2);

/** 3725 seconds -> "1h 02m"; under an hour -> "12m 05s". */
export function hours(seconds) {
  const s = Math.trunc(Number(seconds) || 0);
  const m = Math.trunc(s / 60);
  const h = Math.trunc(m / 60);
  return h ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${m}m ${String(s % 60).padStart(2, "0")}s`;
}

export function ago(seconds) {
  if (seconds === null || seconds === undefined) return "never";
  const s = Math.trunc(seconds);
  for (const [unit, size] of [["d", 86400], ["h", 3600], ["m", 60]]) {
    if (s >= size) return `${Math.trunc(s / size)}${unit} ago`;
  }
  return `${s}s ago`;
}

/** "2026-11-02T12:44:00Z" -> "2026-11-02"; nothing -> "—". */
export const day = (iso) => (iso ? iso.slice(0, 10) : "—");

// Tables used on more than one page

/** Fills the ranked players table from leaderboard-table.html. `sort` marks the sorted column. */
export function showLeaderboard(game, rows, sort = null) {
  const panel = $("leaderboard");
  for (const link of panel.querySelectorAll("a[data-sort]")) {
    link.href = pageUrl(game, `/leaderboard?sort=${link.dataset.sort}`);
    if (link.dataset.sort === sort) {
      link.classList.add("on");
      link.closest("th").setAttribute("aria-sort", "descending");
    }
  }
  if (!rows.length) {
    showEmpty(panel, "No ranked players yet. Finish a ranked match and they appear here.");
    return;
  }
  $("leaderboard-rows").replaceChildren(
    ...rows.map((p) => {
      const row = render("tpl-leaderboard-row", {
        position: `#${p.position}`,
        icon: { src: p.icon, alt: p.rank },
        name: { text: p.name, href: playerUrl(game, p.profileid) },
        rank: p.rank,
        level: p.level,
        xp: num(p.xp),
        kills: num(p.kills),
        deaths: num(p.deaths),
        kd: ratio(p.kd),
        time: hours(p.time_played),
        matches: num(p.matches),
      });
      if (p.position <= 3) row.classList.add(`top${p.position}`);
      return row;
    }),
  );
}

/** Fills the servers table from servers-table.html. `compact` leaves out the published status column. */
export function showServers(game, servers, { compact = false } = {}) {
  const panel = $("servers");
  panel.classList.toggle("compact", compact);
  if (!servers.length) {
    showEmpty(panel, "No dedicated server has published its status yet.");
    return;
  }
  $("servers-rows").replaceChildren(
    ...servers.map((s) =>
      render("tpl-server-row", {
        status: { text: s.online ? "ONLINE" : "OFFLINE", className: s.online ? "on" : "off" },
        ranked: { hidden: !s.ranked },
        host: { text: s.host, href: playerUrl(game, s.owner) },
        mode: s.mode || "—",
        max_humans: s.max_humans || "—",
        updated: ago(s.age_seconds),
        fields: Object.entries(s.fields).map(([k, v]) => `${k}=${v}`).join(", "),
      }),
    ),
  );
}
