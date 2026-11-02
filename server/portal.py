"""Stats portal for both games: the static pages in server/web and the read-only JSON API they read.

The pages are plain HTML, CSS and JavaScript. Each page loads its data from the JSON API in the browser,
so this module only routes requests and builds JSON. docs/stats_portal.md describes the pages and the API.

    /, /leaderboard, /servers, /players?q=, /players/<id>    Prejudice pages
    /s8/...                                                  the same pages for Section 8 (2009)
    /api/games, /api/<endpoint>, /api/s8/<endpoint>          JSON
    /static/<file>                                           CSS and JavaScript from server/web
    /assets/<path>                                           images from portal_assets/
    /drawn/<kind>.svg                                        stand-in icons for images that are missing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import log
from .portal_assets import Assets, drawn_url, emblem, insignia
from .portal_data import PortalData, games_for, set_display_name

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# The HTML file of each page, by its path without the game prefix.
PAGES = {
    (): "index.html",
    ("leaderboard",): "leaderboard.html",
    ("servers",): "servers.html",
    ("players",): "search.html",
}
STATIC_TYPES = {".css": "text/css", ".js": "text/javascript"}
# A page pulls in a shared piece of markup from server/web/partials with this line.
INCLUDE = re.compile(r'<!--#include file="([\w.-]+)" -->')
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
LEADERBOARD_MAX = 500


@dataclass
class Response:
    status: int
    content_type: str
    body: bytes
    cache: str = "no-cache"


def to_int(text, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(text)))
    except (TypeError, ValueError):
        return default


def json_response(obj, status: int = 200) -> Response:
    return Response(status, "application/json", json.dumps(obj, indent=2).encode("utf-8"))


def read_page(name: str, web_dir: str = WEB_DIR) -> str:
    """Gives a page from server/web with its shared parts filled in."""
    with open(os.path.join(web_dir, name), encoding="utf-8") as f:
        text = f.read()
    return INCLUDE.sub(lambda m: read_page(m.group(1), os.path.join(WEB_DIR, "partials")), text)


def page_response(name: str, status: int = 200) -> Response:
    return Response(status, "text/html", read_page(name).encode("utf-8"))


def static_response(rel: str) -> Response | None:
    """Gives a CSS or JavaScript file from server/web, or None. Never reads outside the folder."""
    root = os.path.realpath(WEB_DIR)
    full = os.path.realpath(os.path.join(root, rel))
    ctype = STATIC_TYPES.get(os.path.splitext(full)[1].lower())
    if not ctype or not full.startswith(root + os.sep) or not os.path.isfile(full):
        return None
    with open(full, "rb") as f:
        return Response(200, ctype, f.read())


class Portal:
    def __init__(self, db_path: str, games, assets_dir: str | None = None, names=None):
        self.games = games
        self.data = {g.key: PortalData(db_path, names, g) for g in self.games}
        self.by_prefix = {g.prefix.strip("/"): g for g in self.games if g.prefix}
        self.default = next((g for g in self.games if not g.prefix), self.games[0])
        self.assets = Assets(assets_dir or "portal_assets")

    # JSON API

    def games_json(self) -> list[dict]:
        return [
            {
                "key": g.key,
                "name": g.name,
                "short": g.short,
                "prefix": g.prefix,
                "banner": self.assets.game(g.key),
                "logo": self.assets.game_logo(g.key),
                "players": len(self.data[g.key].players()),
            }
            for g in self.games
        ]

    def rank_icon(self, g, level: int) -> str:
        return self.assets.rank(level, g.key) or f"/drawn/rank.svg?level={int(level)}"

    def with_icons(self, g, players: list[dict]) -> list[dict]:
        return [{**p, "icon": self.rank_icon(g, p["level"])} for p in players]

    def player_json(self, g, pid: int) -> dict | None:
        p = self.data[g.key].player(pid)
        if p is None:
            return None
        p["icon"] = self.rank_icon(g, p["level"])
        for b in p["badges"]:
            b["icon"] = self.assets.badge(b["id"], b["name"], g.key) or drawn_url("badge", b["name"], b["category"])
        for f in p["feats"]:
            f["icon"] = self.assets.feat(f["id"], f["name"], g.key) or drawn_url("feat", f["name"], f["category"])
        for x in p["gear"]:
            x["icon"] = self.assets.gear(x["id"], x["weapon"], x["mod"], g.key)
        p["unlocks"] = self.unlocks_json(p) if g.linked_level else []
        p["other_careers"] = [
            {"game": x.key, "name": x.name, "url": f"{x.prefix}/players/{pid}"}
            for x in self.games
            if x.key != g.key and self.data[x.key].has_career(pid)
        ]
        return p

    def unlocks_json(self, p: dict) -> list[dict]:
        """The unlocks from portal_assets/unlocks.json, each marked unlocked once the player has its level."""
        out = []
        for item in self.assets.unlocks():
            if item.get("section8_level") is not None:
                need, have, what = item["section8_level"], p.get("section8_level") or 0, "Section 8 level"
            else:
                need, have, what = item.get("level", 1), p["level"], "Level"
            category = item.get("category", "Unlock")
            out.append(
                {
                    "name": item["name"],
                    "category": category,
                    "description": item.get("description", ""),
                    "requirement": f"{what} {need}",
                    "unlocked": have >= need,
                    "icon": self.assets.unlock(item) or drawn_url("unlock", item["name"], category),
                }
            )
        return out

    def api(self, g, parts: list[str], q: dict) -> Response:
        data = self.data[g.key]
        if parts == ["summary"]:
            return json_response(data.summary())
        if parts == ["leaderboard"]:
            offset = to_int(q.get("offset"), 0, 0, 10**6)
            limit = to_int(q.get("limit"), 50, 0, LEADERBOARD_MAX)
            lb = data.leaderboard(q.get("sort", "xp"), offset, limit)
            return json_response({**lb, "rows": self.with_icons(g, lb["rows"])})
        if parts == ["players"]:
            return json_response(self.with_icons(g, data.search(q.get("q", ""))))
        if len(parts) == 2 and parts[0] == "players" and parts[1].isdigit():
            p = self.player_json(g, int(parts[1]))
            return json_response(p) if p is not None else json_response({"error": "no such player"}, 404)
        if parts == ["servers"]:
            return json_response(data.servers())
        return json_response({"error": "unknown endpoint"}, 404)

    # Routing

    def handle(self, raw_path: str) -> Response:
        """Answers one GET request."""
        url = urlparse(raw_path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        parts = [x for x in url.path.split("/") if x]
        try:
            return self.route(parts, q)
        except Exception as e:
            log.log(f"    [portal] error on {raw_path}: {e!r}")
            if parts and parts[0] == "api":
                return json_response({"error": "server error"}, 500)
            return Response(500, "text/plain", b"Server error. The server log has the details.")

    def route(self, parts: list[str], q: dict) -> Response:
        head, rest = (parts[0], parts[1:]) if parts else ("", [])
        if head == "static":
            return static_response(unquote("/".join(rest))) or Response(404, "text/plain", b"no such file")
        if head == "assets":
            found = self.assets.read(unquote("/".join(rest))) if rest else None
            if not found:
                return Response(404, "text/plain", b"no such image")
            return Response(200, found[1], found[0], "max-age=300")
        if head == "drawn" and len(rest) == 1:
            return self.drawn(rest[0], q)
        if head == "api":
            if not rest or (len(rest) == 1 and rest[0] in self.by_prefix):
                return page_response("api.html")
            if rest == ["games"]:
                return json_response(self.games_json())
            g = self.by_prefix.get(rest[0])
            return self.api(g, rest[1:], q) if g else self.api(self.default, rest, q)

        g = self.by_prefix.get(head)
        page = tuple(rest) if g else tuple(parts)
        g = g or self.default
        if page in PAGES:
            return page_response(PAGES[page])
        is_player = len(page) == 2 and page[0] == "players" and page[1].isdigit()
        if is_player and self.data[g.key].exists(int(page[1])):
            return page_response("player.html")
        return page_response("not-found.html", 404)

    @staticmethod
    def drawn(name: str, q: dict) -> Response:
        """A stand-in icon: rank.svg?level=, or badge.svg, feat.svg and unlock.svg with ?text= and ?tone=."""
        kind = name.removesuffix(".svg")
        if kind == "rank":
            svg = insignia(to_int(q.get("level"), 1, 0, 1000))
        elif kind in ("badge", "feat", "unlock"):
            svg = emblem(kind, q.get("text", ""), q.get("tone", kind))
        else:
            return Response(404, "text/plain", b"no such icon")
        return Response(200, "image/svg+xml", svg.encode("utf-8"), "max-age=86400")


class _Handler(BaseHTTPRequestHandler):
    portal: Portal
    # An idle connection is closed after this many seconds, so it does not hold a thread.
    timeout = 15

    def do_GET(self):  # noqa: N802
        r = self.portal.handle(self.path)
        text = r.content_type.startswith("text/") or r.content_type == "application/json"
        self.send_response(r.status)
        self.send_header("Content-Type", f"{r.content_type}; charset=utf-8" if text else r.content_type)
        self.send_header("Content-Length", str(len(r.body)))
        self.send_header("Cache-Control", r.cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(r.body)

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
