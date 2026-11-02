"""Stats portal: leaderboard, careers, servers and a read-only JSON API for both games.

Prejudice's pages are at the root and Section 8's are under /s8. See docs/stats_portal.md.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import log
from .portal_assets import Assets, emblem, insignia
from .portal_data import SORTS, PortalData, games_for, set_display_name

PAGE_SIZE = 50
GEAR_LABELS = {
    "TimePlayed": "Time",
    "Kills": "Kills",
    "Destruction": "Destr.",
    "KillsIAL": "Best kills",
    "DamageIAL": "Best dmg",
    "DestructionIAL": "Best destr.",
}
# The pages use no script. Inline styles carry the CSS and the drawn icons.
CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; form-action 'self'"


def to_int(text, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(text)))
    except (TypeError, ValueError):
        return default


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def num(v) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return esc(v)


def hours(seconds) -> str:
    s = int(seconds or 0)
    h, m = divmod(s // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s % 60:02d}s"


def ago(seconds) -> str:
    if seconds is None:
        return "never"
    s = int(seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit} ago"
    return f"{s}s ago"


def day(iso) -> str:
    return esc((iso or "")[:10]) if iso else "&mdash;"


CSS = """
:root{--bg:#0b0f14;--panel:#121922;--panel2:#18212c;--line:#243142;--text:#dbe4ee;--muted:#8a9bb0;
--accent:#f5b229;--accent2:#4fb3e8;--good:#5fd38d;--bad:#ef6b6b;--radius:6px;
--font:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 var(--font)}
a{color:var(--accent2);text-decoration:none}a:hover{text-decoration:underline}
header{background:linear-gradient(180deg,#141d28,#0e141b);border-bottom:2px solid var(--accent)}
.bar{max-width:1180px;margin:0 auto;padding:12px 16px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
.brand{font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:var(--text);font-size:17px}
.brand b{color:var(--accent)}nav{display:flex;gap:14px;flex-wrap:wrap}
nav a{color:var(--muted);text-transform:uppercase;letter-spacing:.08em;font-size:13px;font-weight:600}
nav a.on,nav a:hover{color:var(--text);text-decoration:none}
form.search{margin-left:auto;display:flex;gap:6px}
input,button,select{font:inherit;color:var(--text);background:var(--panel2);border:1px solid var(--line);
border-radius:var(--radius);padding:6px 10px}button{cursor:pointer;background:var(--accent);color:#111;border:0;font-weight:700}
main{max-width:1180px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:26px;margin:4px 0 2px;letter-spacing:.02em}h2{font-size:14px;text-transform:uppercase;
letter-spacing:.12em;color:var(--accent);margin:28px 0 10px;font-weight:700}
.sub{color:var(--muted);margin:0 0 8px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px}
.tile .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}
.tile .v{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);background:var(--panel2)}
td.n,th.n{text-align:right}tr:last-child td{border-bottom:0}tbody tr:hover{background:#16202b}
th a{color:var(--muted)}th a.on{color:var(--accent)}
.pos{color:var(--muted);width:44px}.top1 .pos{color:#ffd54a}.top2 .pos{color:#cfd8dc}.top3 .pos{color:#d4915a}
.top1 td{background:linear-gradient(90deg,rgba(255,213,74,.14),transparent)}
.top2 td{background:linear-gradient(90deg,rgba(207,216,220,.12),transparent)}
.top3 td{background:linear-gradient(90deg,rgba(212,145,90,.12),transparent)}
.rank{color:var(--muted);font-size:13px}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:700}
.on-pill{background:rgba(95,211,141,.15);color:var(--good)}.off-pill{background:rgba(138,155,176,.15);color:var(--muted)}
.hero{display:flex;gap:20px;align-items:center;flex-wrap:wrap;background:var(--panel);border:1px solid var(--line);
border-left:4px solid var(--accent);border-radius:var(--radius);padding:16px 18px}
.lvl{font-size:40px;font-weight:800;color:var(--accent);line-height:1;min-width:64px;text-align:center}
.lvl small{display:block;font-size:11px;color:var(--muted);letter-spacing:.1em;font-weight:600}
.prog{height:8px;background:var(--panel2);border-radius:99px;overflow:hidden;margin-top:8px;max-width:420px}
.prog i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),#ffd27a)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px}
.award{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:9px 11px}
.award .t{font-weight:700}.award .d{color:var(--muted);font-size:12.5px;margin-top:2px}
.award.no .t{color:var(--muted)}.award .c{float:right;color:var(--accent);font-weight:800}
.cat{color:var(--accent2);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:760px){.two{grid-template-columns:1fr}
form.search{margin-left:0;width:100%}form.search input{flex:1}
.grid{grid-template-columns:1fr 1fr}.award .d{font-size:12px}}
.muted{color:var(--muted)}.empty{padding:16px;color:var(--muted)}
.pager{display:flex;gap:10px;justify-content:flex-end;margin-top:10px}
code{background:var(--panel2);padding:1px 6px;border-radius:4px}
.ico{flex:none;display:block;object-fit:contain}.ico.gear{width:auto;max-width:64px;height:28px}
.who{display:flex;gap:10px;align-items:center}
.award{display:flex;gap:10px;align-items:flex-start}.award .body{flex:1;min-width:0}
.award.no .ico{filter:grayscale(1);opacity:.4}
.banner{background:var(--panel) center/cover no-repeat;border:1px solid var(--line);border-radius:var(--radius);
padding:26px 22px;margin-bottom:14px;min-height:120px;display:flex;flex-direction:column;justify-content:center}
.banner .logo{width:auto;height:auto;max-width:min(420px,100%);max-height:110px;margin-bottom:6px}
.hero{background-size:cover;background-position:center}.insig{flex:none}
.games{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}
.game{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;padding-bottom:10px}
.game.on{border-color:var(--accent)}.game .gname{font-weight:700;padding:8px 12px 0}.game .muted{padding:0 12px}
.game .art{height:120px;background:center/cover no-repeat}.game .art.none{background:linear-gradient(135deg,#1b2633,#0e141b)}
.switch{display:flex;gap:4px;background:var(--panel2);border:1px solid var(--line);border-radius:99px;padding:3px}
.switch a{color:var(--muted);font-size:12.5px;font-weight:700;padding:3px 11px;border-radius:99px;text-decoration:none}
.switch a.on{background:var(--accent);color:#111}
.rk-pill{background:rgba(245,178,41,.15);color:var(--accent)}a.game{color:var(--text);text-decoration:none;display:block}
a.game:hover{border-color:var(--accent2)}.also{margin-top:6px}.also a{margin-right:12px;font-weight:600}
footer{max-width:1180px;margin:0 auto;padding:0 16px 30px;color:var(--muted);font-size:12.5px}
"""


class Portal:
    def __init__(self, db_path: str, games, assets_dir: str | None = None, names=None):
        self.games = games
        self.data = {g.key: PortalData(db_path, names, g) for g in self.games}
        self.by_prefix = {g.prefix.strip("/"): g for g in self.games}
        self.assets = Assets(assets_dir or "portal_assets")

    def other_games(self, g):
        return [x for x in self.games if x.key != g.key]

    @staticmethod
    def u(g, path: str = "/") -> str:
        """Gives the URL of path inside the pages of game g."""
        return (g.prefix + path) if path != "/" else (g.prefix or "/")

    @staticmethod
    def img(url, alt, size=44, cls="ico") -> str:
        dims = f' width="{size}" height="{size}"' if size else ""
        return f'<img class="{cls}" src="{esc(url)}" alt="{esc(alt)}"{dims} loading="lazy">'

    def rank_icon(self, g, level, rank, size=44) -> str:
        url = self.assets.rank(level, g.key)
        return self.img(url, rank, size) if url else insignia(level, size)

    def banner_style(self, key="prejudice", fade=".55") -> str:
        url = self.assets.game(key)
        if not url:
            return ""
        return (
            ' style="background-image:linear-gradient(90deg,rgba(11,15,20,.96) 25%,rgba(11,15,20,'
            + fade
            + f")),url('{esc(url)}')\""
        )

    def page(self, g, title: str, body: str, active: str = "") -> str:
        def link(href, label, on):
            return (
                f'<a href="{href}" class="on" aria-current="page">{label}</a>'
                if on
                else f'<a href="{href}">{label}</a>'
            )

        switch = "".join(link(self.u(x), esc(x.short), x.key == g.key) for x in self.games)
        nav = "".join(
            link(href, label, key == active)
            for href, label, key in (
                (self.u(g), "Overview", "home"),
                (self.u(g, "/leaderboard"), "Leaderboard", "lb"),
                (self.u(g, "/servers"), "Servers", "srv"),
                ("/api", "API", "api"),
            )
        )
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark">
<title>{esc(title)} &middot; {esc(g.name)} Stats</title><style>{CSS}</style></head><body>
<header><div class="bar"><a class="brand" href="/">Section <b>8</b> Stats</a><div class="switch">{switch}</div>
<nav>{nav}</nav>
<form class="search" action="{self.u(g, "/players")}" method="get"><input name="q" placeholder="Find a player" aria-label="Find a player">
<button>Search</button></form></div></header><main>{body}</main>
<footer>{esc(g.name)} &middot; ranked stats from this server's ATLAS reports &middot; fan-run preservation project</footer>
</body></html>"""

    def player_link(self, g, p) -> str:
        return f'<a href="{self.u(g, "/players/" + str(p["profileid"]))}">{esc(p["name"])}</a>'

    def leaderboard_table(self, g, rows, sort="xp", sortable=False) -> str:
        if not rows:
            return '<div class="panel empty">No ranked players yet. Finish a ranked match and they appear here.</div>'

        def th(key, label, cls="n"):
            if not sortable:
                return f'<th class="{cls}">{label}</th>'
            href = self.u(g, f"/leaderboard?sort={key}")
            if key == sort:
                return f'<th class="{cls}" aria-sort="descending"><a class="on" href="{href}">{label}</a></th>'
            return f'<th class="{cls}"><a href="{href}">{label}</a></th>'

        body = "".join(
            f'<tr class="top{p["position"]}"><td class="pos">#{p["position"]}</td>'
            f'<td><div class="who">{self.rank_icon(g, p["level"], p["rank"], 30)}<div>{self.player_link(g, p)}'
            f'<div class="rank">{esc(p["rank"])}</div></div></div></td>'
            f'<td class="n">{p["level"]}</td><td class="n">{num(p["xp"])}</td><td class="n">{num(p["kills"])}</td>'
            f'<td class="n">{num(p["deaths"])}</td><td class="n">{p["kd"]:.2f}</td>'
            f'<td class="n">{hours(p["time_played"])}</td><td class="n">{num(p["matches"])}</td></tr>'
            for p in rows
        )
        return (
            '<div class="panel scroll"><table><thead><tr><th>#</th><th>Player</th><th class="n">Lvl</th>'
            f'{th("xp", "XP")}{th("kills", "Kills")}<th class="n">Deaths</th>{th("kd", "K/D")}'
            f"{th('time', 'Time')}{th('matches', 'Matches')}</tr></thead><tbody>{body}</tbody></table></div>"
        )

    def servers_table(self, g, servers, compact=False) -> str:
        if not servers:
            return '<div class="panel empty">No dedicated server has published its status yet.</div>'
        rows = []
        for s in servers:
            pill = (
                '<span class="pill on-pill">ONLINE</span>'
                if s["online"]
                else '<span class="pill off-pill">OFFLINE</span>'
            )
            if s.get("ranked"):
                pill += ' <span class="pill rk-pill">RANKED</span>'
            extra = ""
            if not compact:
                extra = (
                    "<td><span class=muted>"
                    + esc(", ".join(f"{k}={v}" for k, v in s["fields"].items()))
                    + "</span></td>"
                )
            rows.append(
                f'<tr><td>{pill}</td><td><a href="{self.u(g, "/players/" + str(s["owner"]))}">{esc(s["host"])}</a></td>'
                f'<td>{esc(s["mode"] or "—")}</td><td class="n">{esc(s["max_humans"] or "—")}</td>'
                f'<td class="n">{ago(s["age_seconds"])}</td>{extra}</tr>'
            )
        head = (
            '<th>Status</th><th>Host account</th><th>Mode</th><th class="n">Max humans</th><th class="n">Updated</th>'
        )
        if not compact:
            head += "<th>Published status</th>"
        return f'<div class="panel scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'

    def home(self, g) -> str:
        data = self.data[g.key]
        s = data.summary()
        top = data.leaderboard("xp", 0, 10)["rows"]
        servers = [x for x in data.servers() if x["online"]]
        tiles = "".join(
            f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div></div>'
            for k, v in (
                ("Ranked players", num(s["players"])),
                ("Ranked matches", num(s["matches"])),
                ("Servers online", num(s["servers_online"])),
                ("Total kills", num(s["total_kills"])),
                ("Time in battle", hours(s["total_seconds"])),
            )
        )
        logo = self.assets.game_logo(g.key)
        heading = self.img(logo, g.name, 0, "logo") if logo else f"<h1>{esc(g.name)}</h1>"
        games = ""
        for x in self.games:
            art = self.assets.game(x.key)
            pic = (
                f'<div class="art" style="background-image:url(\'{esc(art)}\')"></div>'
                if art
                else '<div class="art none"></div>'
            )
            n = self.data[x.key].summary()["players"]
            games += (
                f'<a class="game{" on" if x.key == g.key else ""}" href="{self.u(x)}">{pic}<div class="gname">{esc(x.name)}</div>'
                f'<div class="muted">{num(n)} ranked player{"s" if n != 1 else ""}</div></a>'
            )
        body = (
            f'<div class="banner"{self.banner_style(g.key)}>{heading}<p class="sub">Ranked careers recorded by this server.</p></div>'
            f'<div class="tiles">{tiles}</div><h2>Top players</h2>{self.leaderboard_table(g, top)}'
            f'<p><a href="{self.u(g, "/leaderboard")}">Full leaderboard &rarr;</a></p>'
            f"<h2>Servers online</h2>{self.servers_table(g, servers, compact=True)}"
            f'<h2>Games</h2><div class="games">{games}</div>'
        )
        return self.page(g, "Overview", body, "home")

    def leaderboard(self, g, q) -> str:
        data = self.data[g.key]
        last = max(1, (len(data.players()) - 1) // PAGE_SIZE + 1)
        page = to_int(q.get("page"), 1, 1, last)
        lb = data.leaderboard(q.get("sort", "xp"), (page - 1) * PAGE_SIZE, PAGE_SIZE)
        pager = ""
        if lb["total"] > PAGE_SIZE:
            base = self.u(g, f"/leaderboard?sort={lb['sort']}")
            prev_link = f'<a href="{base}&page={page - 1}">&larr; Prev</a>' if page > 1 else ""
            next_link = f'<a href="{base}&page={page + 1}">Next &rarr;</a>' if page < last else ""
            pager = f'<div class="pager">{prev_link}<span class="muted">Page {page} of {last}</span>{next_link}</div>'
        body = (
            f'<h1>Leaderboard</h1><p class="sub">{esc(g.name)} &middot; {num(lb["total"])} ranked players, sorted by '
            f"{esc(SORTS[lb['sort']][0])}. Click a column to re-sort.</p>"
            f"{self.leaderboard_table(g, lb['rows'], lb['sort'], sortable=True)}{pager}"
        )
        return self.page(g, "Leaderboard", body, "lb")

    def search(self, g, q) -> str:
        query = q.get("q", "")
        found = self.data[g.key].search(query)
        if found:
            items = "".join(
                f'<tr><td>{self.player_link(g, p)}</td><td class="rank">{esc(p["rank"])}</td><td class="n">{p["level"]}</td>'
                f'<td class="n">{num(p["xp"])}</td></tr>'
                for p in found
            )
            table = (
                '<div class="panel scroll"><table><thead><tr><th>Player</th><th>Rank</th><th class="n">Lvl</th>'
                f'<th class="n">XP</th></tr></thead><tbody>{items}</tbody></table></div>'
            )
        else:
            table = f'<div class="panel empty">No {esc(g.name)} player matches that name.</div>'
        return self.page(
            g,
            "Search",
            f'<h1>Search</h1><p class="sub">{esc(g.name)} &middot; results for &ldquo;{esc(query)}&rdquo;</p>{table}',
        )

    def player(self, g, pid: int) -> str | None:
        p = self.data[g.key].player(pid)
        if p is None:
            return None
        nxt = p["level_next"]
        if nxt:
            pct = 100 * (p["xp"] - p["level_floor"]) / max(1, nxt - p["level_floor"])
            progress = (
                f'<div class="prog"><i style="width:{max(0, min(100, pct)):.1f}%"></i></div>'
                f'<div class="muted">{num(p["xp"])} / {num(nxt)} XP to level {p["level"] + 1}</div>'
            )
        else:
            progress = f'<div class="muted">{num(p["xp"])} XP &middot; maximum level</div>'
        clan = f' <span class="muted">[{esc(p["clan_tag"])}]</span>' if p["clan_tag"] else ""
        nick = (
            f'<div class="muted">GameSpy nick <code>{esc(p["uniquenick"])}</code></div>'
            if p["uniquenick"] != p["name"]
            else ""
        )
        also = [
            f'<a href="{self.u(x, "/players/" + str(pid))}">{esc(x.name)} career &rarr;</a>'
            for x in self.other_games(g)
            if self.data[x.key].has_career(pid)
        ]
        also_html = f'<div class="also">{" ".join(also)}</div>' if also else ""
        hero = (
            f'<div class="hero"{self.banner_style(g.key, fade=".35")}><div class="insig">{self.rank_icon(g, p["level"], p["rank"], 72)}</div>'
            f'<div class="lvl">{p["level"]}<small>LEVEL</small></div><div>'
            f'<div class="cat">{esc(g.name)}</div><h1>{esc(p["name"])}{clan}</h1><div class="cat">{esc(p["rank"])}</div>'
            f"{progress}{nick}{also_html}</div></div>"
        )
        if not p["has_stats"]:
            body = hero + f'<div class="panel empty" style="margin-top:14px">No ranked {esc(g.name)} career yet.</div>'
            return self.page(g, p["name"], body)
        tiles = [
            ("Kills", num(p["kills"])),
            ("Deaths", num(p["deaths"])),
            ("K/D", f"{p['kd']:.2f}"),
            ("Time played", hours(p["time_played"])),
            ("Matches", num(p["matches"])),
        ]
        tiles += [
            (c["label"], num(c["value"])) for c in p["career"] if c["column"] not in ("Ranked_Kills", "Ranked_Deaths")
        ]
        tiles_html = "".join(
            f'<div class="tile"><div class="k">{esc(k)}</div><div class="v">{v}</div></div>' for k, v in tiles
        )

        records = "".join(
            f'<tr><td>{esc(r["label"].replace(" in a life", ""))}</td><td class="n">{num(r["value"])}</td><td class="n">{day(r["date"])}</td></tr>'
            for r in p["records"]
        )
        records_html = (
            '<h2>Best in a single life</h2><div class="panel scroll"><table><thead><tr><th>Record</th>'
            f'<th class="n">Best</th><th class="n">Set</th></tr></thead><tbody>{records}</tbody></table></div>'
        )
        if p["modes"]:
            modes = "".join(
                f'<tr><td>{esc(m["name"])}</td><td class="n">{hours(m["seconds"])}</td></tr>' for m in p["modes"]
            )
            two = (
                f'<div class="two"><div>{records_html}</div>'
                '<div><h2>Time per mode</h2><div class="panel scroll"><table><thead><tr><th>Mode</th><th class="n">Time</th></tr>'
                f"</thead><tbody>{modes}</tbody></table></div></div></div>"
            )
        else:
            two = records_html

        def award_grid(items, feats=False):
            cards = []
            for a in items:
                url = (
                    self.assets.feat(a["id"], a["name"], g.key)
                    if feats
                    else self.assets.badge(a["id"], a["name"], g.key)
                )
                icon = (
                    self.img(url, a["name"]) if url else emblem("feat" if feats else "badge", a["name"], a["category"])
                )
                extra = ""
                if feats and a["count"]:
                    extra = f' <span class="muted">({a["tiers"][0]}&times;1 / {a["tiers"][1]}&times;2 / {a["tiers"][2]}&times;3)</span>'
                when = f" &middot; {day(a['date'])}" if a["date"] else ""
                cards.append(
                    f'<div class="award {"" if a["count"] else "no"}">{icon}<div class="body"><span class="c">{"&times;" + num(a["count"]) if a["count"] else ""}</span>'
                    f'<div class="cat">{esc(a["category"])}</div><div class="t">{esc(a["name"])}</div>'
                    f'<div class="d">{esc(a["description"])}{extra}{when}</div></div></div>'
                )
            return f'<div class="grid">{"".join(cards)}</div>'

        earned_b = sum(1 for b in p["badges"] if b["count"])
        earned_f = sum(1 for f in p["feats"] if f["count"])
        if p["gear"]:
            cols = p["gear_stats"]
            mods = any(x["mod"] for x in p["gear"])

            def cell(x, k):
                return f'<td class="n">{hours(x[k]) if k == "TimePlayed" else num(x[k])}</td>'

            gear_rows = "".join(
                f'<tr><td><div class="who">{self.gear_icon(g, x)}{esc(x["weapon"])}</div></td>'
                + (f'<td class="muted">{esc(x["mod"])}</td>' if mods else "")
                + "".join(cell(x, k) for k in cols)
                + "</tr>"
                for x in p["gear"]
            )
            head = "<th>Weapon / gear</th>" + ("<th>Mod</th>" if mods else "")
            head += "".join(f'<th class="n">{GEAR_LABELS[k]}</th>' for k in cols)
            gear = f'<div class="panel scroll"><table><thead><tr>{head}</tr></thead><tbody>{gear_rows}</tbody></table></div>'
        else:
            gear = '<div class="panel empty">No gear used in ranked play yet.</div>'
        if p["history"]:
            hist_rows = "".join(
                f"<tr><td>{esc((h['date'] or '').replace('T', ' ').rstrip('Z'))}</td><td>{esc(h['mode'] or '—')}</td>"
                f'<td class="n">{num(h["players"])}</td><td class="n">+{num(h["xp_gained"] or 0)}</td>'
                f'<td class="n">{esc(h["level_after"] if h["level_after"] is not None else "—")}</td></tr>'
                for h in p["history"]
            )
            history = (
                '<div class="panel scroll"><table><thead><tr><th>Date (UTC)</th><th>Mode</th><th class="n">Players</th>'
                f'<th class="n">XP</th><th class="n">Level after</th></tr></thead><tbody>{hist_rows}</tbody></table></div>'
            )
        else:
            history = '<div class="panel empty">No ranked matches recorded yet.</div>'

        unlocks = self.unlocks_section(p) if g.linked_level else ""
        body = (
            f'{hero}<h2>Career</h2><div class="tiles">{tiles_html}</div>{two}'
            f"<h2>Badges &middot; {earned_b} of {len(p['badges'])}</h2>{award_grid(p['badges'])}"
            f"<h2>Feats &middot; {earned_f} of {len(p['feats'])}</h2>{award_grid(p['feats'], feats=True)}"
            f"{unlocks}<h2>Weapons &amp; gear</h2>{gear}<h2>Recent matches</h2>{history}"
        )
        return self.page(g, p["name"], body)

    def gear_icon(self, g, x) -> str:
        url = self.assets.gear(x["id"], x["weapon"], x["mod"], g.key)
        return self.img(url, x["weapon"], 36, "ico gear") if url else ""

    def unlocks_section(self, p) -> str:
        """Draws the unlocks from unlocks.json. Each one is lit once the player reaches its level."""
        items = self.assets.unlocks()
        if not items:
            return ""
        cards, got = [], 0
        for item in items:
            if item.get("section8_level") is not None:
                need, have, what = int(item["section8_level"]), p.get("section8_level") or 0, "Section 8 level"
            else:
                need, have, what = int(item.get("level", 1)), p["level"], "Level"
            ok = have >= need
            got += ok
            url = self.assets.unlock(item)
            icon = self.img(url, item["name"]) if url else emblem("unlock", item["name"], item.get("category", ""))
            cards.append(
                f'<div class="award {"" if ok else "no"}">{icon}<div class="body">'
                f'<div class="cat">{esc(item.get("category", "Unlock"))}</div><div class="t">{esc(item["name"])}</div>'
                f'<div class="d">{what} {need}{(" &middot; " + esc(item["description"])) if item.get("description") else ""}</div></div></div>'
            )
        return f'<h2>Unlocks &middot; {got} of {len(items)}</h2><div class="grid">{"".join(cards)}</div>'

    def servers(self, g) -> str:
        body = (
            f'<h1>Servers</h1><p class="sub">{esc(g.name)} dedicated servers that published their status here. A server '
            "that has not updated in 5 minutes is shown offline.</p>"
            + self.servers_table(g, self.data[g.key].servers())
        )
        return self.page(g, "Servers", body, "srv")

    def api_index(self) -> str:
        rows = ""
        for g in self.games:
            base = "/api" + g.prefix
            eps = (
                (f"{base}/summary", "Totals: players, matches, servers online, kills, time."),
                (f"{base}/leaderboard?sort=xp&offset=0&limit=50", "Ranked list. sort = " + " | ".join(SORTS)),
                (f"{base}/players?q=name", "Search players by name or profileid."),
                (
                    f"{base}/players/&lt;profileid&gt;",
                    "Full career: records, modes, badges, feats, gear, recent matches.",
                ),
                (f"{base}/servers", "Dedicated servers with their published status and freshness."),
            )
            rows += f'<tr><th colspan="2">{esc(g.name)}</th></tr>'
            rows += "".join(
                f'<tr><td><a href="{e.split("?")[0] if "&lt;" in e else e}"><code>{e}</code></a></td><td>{esc(d)}</td></tr>'
                for e, d in eps
            )
        body = (
            '<h1>JSON API</h1><p class="sub">Read-only, no authentication. All responses are JSON.</p>'
            f'<div class="panel scroll"><table><tbody>{rows}</tbody></table></div>'
        )
        return self.page(self.games[0], "API", body, "api")

    def handle(self, raw_path: str):
        """Routes one GET request. Returns (status, content_type, body)."""
        url = urlparse(raw_path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        path = url.path.rstrip("/") or "/"
        parts = [x for x in path.split("/") if x]
        g = self.games[0]
        try:
            if len(parts) >= 2 and parts[0] == "assets":
                found = self.assets.read(unquote("/".join(parts[1:])))
                if found:
                    return 200, found[1], found[0]
                return 404, "text/plain", "no such image"
            if parts and parts[0] == "api":
                rest = parts[1:]
                if rest and rest[0] in self.by_prefix and rest[0]:
                    return self.handle_api(self.by_prefix[rest[0]], rest[1:], q)
                return self.handle_api(g, rest, q)
            if parts and parts[0] in self.by_prefix and parts[0]:
                g, parts = self.by_prefix[parts[0]], parts[1:]
            if not parts:
                return 200, "text/html", self.home(g)
            if parts == ["leaderboard"]:
                return 200, "text/html", self.leaderboard(g, q)
            if parts == ["servers"]:
                return 200, "text/html", self.servers(g)
            if parts == ["players"]:
                return 200, "text/html", self.search(g, q)
            if len(parts) == 2 and parts[0] == "players" and parts[1].isdigit():
                html_page = self.player(g, int(parts[1]))
                if html_page is not None:
                    return 200, "text/html", html_page
        except Exception as e:
            log.log(f"    [portal] error on {raw_path}: {e!r}")
            if parts and parts[0] == "api":
                return 500, "application/json", json.dumps({"error": "server error"})
            return (
                500,
                "text/html",
                self.page(g, "Error", '<h1>Something went wrong</h1><p class="sub">See the server log.</p>'),
            )
        return (
            404,
            "text/html",
            self.page(g, "Not found", '<h1>Not found</h1><p class="sub">No such page or player.</p>'),
        )

    def handle_api(self, g, parts, q):
        data = self.data[g.key]

        def ok(obj):
            return 200, "application/json", json.dumps(obj, indent=2)

        if not parts:
            return 200, "text/html", self.api_index()
        if parts == ["summary"]:
            return ok(data.summary())
        if parts == ["leaderboard"]:
            offset, limit = to_int(q.get("offset"), 0, 0, 10**6), to_int(q.get("limit"), 50, 0, 500)
            return ok(data.leaderboard(q.get("sort", "xp"), offset, limit))
        if parts == ["players"]:
            return ok(data.search(q.get("q", "")))
        if len(parts) == 2 and parts[0] == "players" and parts[1].isdigit():
            p = data.player(int(parts[1]))
            return ok(p) if p is not None else (404, "application/json", json.dumps({"error": "no such player"}))
        if parts == ["servers"]:
            return ok(data.servers())
        return 404, "application/json", json.dumps({"error": "unknown endpoint"})


class _Handler(BaseHTTPRequestHandler):
    portal: Portal
    # An idle connection is closed after this many seconds, so it does not hold a thread.
    timeout = 15

    def do_GET(self):  # noqa: N802
        status, ctype, body = self.portal.handle(self.path)
        image = isinstance(body, bytes)
        data = body if image else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype if image else f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=300" if image else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        """Keeps page hits out of the backend log."""


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # HTTPServer sets SO_REUSEADDR. On Windows that lets the bind take a port another process listens on.
    allow_reuse_address = sys.platform != "win32"


def start(db_path: str, bind: str, port: int, games, assets_dir: str | None = None, names=None):
    """Starts the portal on a daemon thread. Returns the HTTP server, or None if the port is taken."""
    handler = type("PortalHandler", (_Handler,), {"portal": Portal(db_path, games, assets_dir, names)})
    try:
        httpd = _Server((bind, port), handler)
    except OSError as e:
        log.log(f"(skip stats portal on {port}: {e})")
        return None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    shown = "localhost" if bind in ("0.0.0.0", "127.0.0.1") else bind
    log.log(f"stats portal on http://{shown}:{port}/")
    return httpd


def _cli(argv) -> int:
    from .config import Config
    from .titles import Titles

    ap = argparse.ArgumentParser(prog="python -m server.portal", description="Stats portal tools.")
    ap.add_argument("--config", default="config.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("name", help="set or clear a player's display name; safe while the server runs")
    p.add_argument("who", help="GameSpy uniquenick or profileid")
    p.add_argument("display_name", nargs="?", default="", help="leave it out to clear the name")
    sub.add_parser("serve", help="run the portal alone and read-only, beside any server build")
    sub.add_parser("assets", help="list the images the portal looks for")
    args = ap.parse_args(argv[1:])
    config = Config.load(args.config)

    if args.cmd == "name":
        try:
            pid = set_display_name(config.db_path, args.who, args.display_name or None)
        except FileNotFoundError:
            print(f"no database at {config.db_path}; set db_path in {args.config}")
            return 1
        if pid is None:
            print(f"no player {args.who!r}")
            return 1
        print(f"{pid}: {'shown as ' + repr(args.display_name) if args.display_name else 'display name cleared'}")
        return 0
    if args.cmd == "serve":
        log.configure(config.log_path + ".portal", truncate=False)
        if not os.path.exists(config.db_path):
            print(f"no database at {config.db_path}; set db_path in {args.config}")
            return 1
    games = games_for(Titles(config.titles, config.default_unknown_title))
    if args.cmd == "serve":
        if not start(
            config.db_path,
            config.bind_address,
            config.portal_port,
            games,
            config.portal_assets_path,
            config.portal_names,
        ):
            return 1
        print(f"portal on port {config.portal_port}; Ctrl+C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0
    assets = Assets(config.portal_assets_path)
    found = 0
    print(f"images in {assets.root}:")
    for what, url in assets.checklist(games):
        found += bool(url)
        print(f"  {'found  ' + url[len('/assets/') :] if url else 'missing'}   {what}")
    print(f"{found} found; anything missing is drawn")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
