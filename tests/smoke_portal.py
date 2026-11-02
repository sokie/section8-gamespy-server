"""Seeds a database from ATLAS report fixtures and checks the portal's JSON API, pages and files over HTTP.

The pages are static files that fill themselves from the API in the browser. Without a browser, this checks
that each page and script is served, that every element id a script uses exists in its page, and that no
script writes markup.

python -m tests.smoke_portal [--keep]     --keep leaves the portal running on port 18080
"""

import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib

from server import log, portal
from server.competition import CompetitionService
from server.persistence import Store
from server.portal_data import PortalData, games_for, set_display_name
from server.titles import Titles

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
WEB = portal.WEB_DIR
failures = 0


def check(name, cond):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        failures += 1


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def with_minted_ccid(blob, pids):
    out = bytearray(blob)
    for i, pid in enumerate(pids):
        out[0x44 + 20 * i : 0x44 + 20 * i + 16] = bytes.fromhex(f"{pid:08X}BEEFCAFE") + bytes(8)
    return bytes(out)


def seed(path):
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass
    st = Store(path)
    for nick in ("T8iPHostNick", "T8iPFriendNick", "DediBox"):
        st.get_or_create_profile(nick)
    comp = CompetitionService(st, report_dir=tempfile.gettempdir())
    comp.ingest_report(with_minted_ccid(fixture("report_1p_handles.bin"), [10000001]), csid="p1")
    comp.ingest_report(fixture("report_2p_ccid.bin"), csid="p2")
    comp.ingest_report(fixture("report_1p_ccid_badges.bin"), csid="p3")

    conn = sqlite3.connect(path)  # What the stats migration leaves for a carried-over career.
    cur = conn.execute(
        "INSERT INTO matches(report_sha256, received_at, csid, player_count) VALUES('migrated-career-xp:10000002', '2026-01-01T00:00:00Z', 'migration', 1)"
    )
    conn.execute(
        "INSERT INTO match_players(match_id, slot, profileid, xp_gained, level_after) VALUES(?,0,10000002,0,1)",
        (cur.lastrowid,),
    )
    conn.commit()
    conn.close()
    # A Section 8 report key names the same column as in Prejudice, so a Prejudice fixture stands in.
    # One changed checksum byte keeps it from being a resend of the Prejudice match above.
    s8_report = bytearray(fixture("report_1p_ccid_badges.bin"))
    s8_report[8] ^= 1
    titles = Titles(store=st)
    s8comp = CompetitionService(st, report_dir=tempfile.gettempdir(), titles=titles)
    s8comp.ingest_report(bytes(s8_report), csid="s8a", title=titles.get("section8"))
    rid8 = st.create_record("s8:ServerStatus_v5", 10000003)
    st.set_fields(
        "s8:ServerStatus_v5", 10000003, rid8, [("Status_Ranked", "byte", "1"), ("Status_MaxHumans", "byte", "32")]
    )
    set_display_name(path, "10000001", "Leon <b>")  # The API gives names as they are; the pages show them as text.
    rid = st.create_record("ServerStatusTG09_v6", 10000003)
    st.set_fields(
        "ServerStatusTG09_v6", 10000003, rid, [("Status_GameMode", "int", "1"), ("Status_MaxHumans", "int", "16")]
    )
    return st


def png(path, w=8, h=8, rgb=(200, 120, 40)):
    """A plain test image (not game art)."""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )


def make_assets(root):
    shutil.rmtree(root, ignore_errors=True)
    png(os.path.join(root, "games", "Prejudice.png"), 64, 24, (40, 70, 110))
    png(os.path.join(root, "badges", "infantry-assault.png"))
    png(os.path.join(root, "feats", "F8.png"))
    png(os.path.join(root, "gear", "assault-rifle.png"), 32, 12)
    png(os.path.join(root, "exported", "T_Rank_01.png"))
    with open(os.path.join(root, "mapping.json"), "w") as f:
        json.dump({"ranks/1": "exported/T_Rank_01.png", "ranks/2": "../outside.png"}, f)
    with open(os.path.join(root, "unlocks.json"), "w") as f:
        json.dump(
            {
                "unlocks": [
                    {"name": "Test Rifle", "category": "Weapon", "level": 2},
                    {"name": "Late Gadget", "category": "Equipment", "level": 90},
                    {"name": "Aegis Armor", "category": "Veteran", "section8_level": 1},
                    {"name": "Broken", "level": "ten"},
                ]
            },
            f,
        )
    with open(os.path.join(root, "evil.svg"), "w") as f:
        f.write("<svg onload=alert(1)>")
    png(os.path.join(os.path.dirname(root), "outside.png"))


def get(port, path):
    """Gives (status, headers, body text)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.headers, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")


def get_json(port, path):
    return json.loads(get(port, path)[2])


def ctype(port, path):
    return get(port, path)[1].get("Content-Type", "")


def check_api(port, db, games):
    print("api:")
    s = get_json(port, "/api/summary")
    check("summary counts 2 players and 3 matches", (s["players"], s["matches"]) == (2, 3))
    check("summary sees the live server", s["servers_online"] == 1)
    check("summary sums kills", s["total_kills"] == 22)
    lb = get_json(port, "/api/leaderboard?sort=kills")
    check(
        "leaderboard sorted by kills",
        [r["kills"] for r in lb["rows"]] == sorted((r["kills"] for r in lb["rows"]), reverse=True),
    )
    check("leaderboard names its sort", lb["sort_label"] == "Kills")
    check("leaderboard uses the display name as it is", lb["rows"][0]["name"] == "Leon <b>")
    check("leaderboard positions", [r["position"] for r in lb["rows"]] == [1, 2])
    check("leaderboard rows carry a rank icon", all(r["icon"] for r in lb["rows"]))
    check("bad sort falls back to xp", get_json(port, "/api/leaderboard?sort=zzz&limit=abc")["sort"] == "xp")
    p = get_json(port, "/api/players/10000001")
    check("player career", (p["kills"], p["matches"]) == (20, 3))
    check("player keeps the GameSpy nick", p["uniquenick"] == "T8iPHostNick")
    check("player badge earned", any(b["name"] == "Infantry Assault" and b["count"] == 1 for b in p["badges"]))
    check("player gear listed", any(g["Kills"] > 0 for g in p["gear"]))
    check("player history", len(p["history"]) == 3)
    check("level progress bounds", p["level_floor"] <= p["xp"] < (p["level_next"] or 10**9))
    check(
        "player links to the Section 8 career",
        [c["url"] for c in p["other_careers"]] == ["/s8/players/10000001"],
    )
    friend = get_json(port, "/api/players/10000002")
    check("carried-over XP is not counted as a match", friend["matches"] == 1)
    check("but is shown in the history", any(h["carried_over"] for h in friend["history"]))
    check("unknown player is a 404", get(port, "/api/players/99")[0] == 404)
    check(
        "search by display name",
        [x["profileid"] for x in get_json(port, "/api/players?q=leon")] == [10000001],
    )
    check(
        "search by GameSpy nick",
        [x["profileid"] for x in get_json(port, "/api/players?q=t8iphost")] == [10000001],
    )
    srv = get_json(port, "/api/servers")
    check(
        "server online with its mode",
        srv and srv[0]["online"] and srv[0]["mode"] == "Conquest" and srv[0]["max_humans"] == 16,
    )
    status, headers, _ = get(port, "/api/nope")
    check("unknown api is a JSON 404", (status, headers["Content-Type"]) == (404, "application/json; charset=utf-8"))
    g = get_json(port, "/api/games")
    check(
        "games lists both, with their page prefixes",
        [(x["key"], x["prefix"]) for x in g] == [("prejudice", ""), ("section8", "/s8")],
    )
    check("games count their players", [x["players"] for x in g] == [2, 1])

    named = PortalData(db, {"t8ipfriendnick": "StoneCold"}, games[0]).player(10000002)
    check("config portal_names names a player by nick, case-insensitive", named["name"] == "StoneCold")
    check("by profileid too", PortalData(db, {"10000002": "SC"}, games[0]).player(10000002)["name"] == "SC")
    odd = os.path.join(tempfile.gettempdir(), "s8 portal #1")
    os.makedirs(odd, exist_ok=True)
    for suffix in ("", "-wal"):
        shutil.copy(db + suffix, os.path.join(odd, "section8.db" + suffix))
    check("a '#' in the database path", len(PortalData(os.path.join(odd, "section8.db"), {}, games[0]).players()) == 2)
    shutil.rmtree(odd)
    missing = os.path.join(tempfile.gettempdir(), "s8_no_such.db")
    try:
        set_display_name(missing, "1", "x")
        check("naming refuses a missing database", False)
    except FileNotFoundError:
        check("naming refuses a missing database", not os.path.exists(missing))

    print("section 8 (2009):")
    s8s = get_json(port, "/api/s8/summary")
    check(
        "Section 8 has its own players and matches", (s8s["game"], s8s["players"], s8s["matches"]) == ("section8", 1, 1)
    )
    p8 = get_json(port, "/api/s8/players/10000001")
    check("Section 8 career read from s8:PlayerStats_v5", p8["has_stats"] and p8["kills"] == 9 and p8["xp"] == 43)
    check(
        "Section 8 badge names",
        any(b["name"] == "Specialist" and b["count"] == 1 for b in p8["badges"]) and len(p8["badges"]) == 32,
    )
    check("Section 8 has 16 feats and no per-mode time", len(p8["feats"]) == 16 and p8["modes"] == [])
    check("Section 8 links back to Prejudice", [c["url"] for c in p8["other_careers"]] == ["/players/10000001"])
    check("Section 8 has no unlocks list", p8["unlocks"] == [])
    check("Prejudice career untouched by the Section 8 match", get_json(port, "/api/players/10000001")["kills"] == 20)
    check(
        "Section 8 level linked into Prejudice",
        get_json(port, "/api/players/10000001")["section8_level"] == p8["level"],
    )
    srv8 = get_json(port, "/api/s8/servers")
    check(
        "Section 8 servers from s8:ServerStatus_v5",
        srv8 and srv8[0]["ranked"] and srv8[0]["max_humans"] == 32 and srv8[0]["mode"] == "Conquest",
    )
    check("a player with no Section 8 career has no stats", not get_json(port, "/api/s8/players/10000002")["has_stats"])


def check_pages(port):
    print("pages:")
    pages = {
        "/": "overview",
        "/leaderboard?sort=kd": "leaderboard",
        "/leaderboard?page=%C2%B2": "leaderboard",
        "/servers": "servers",
        "/players?q=friend": "search",
        "/players/10000001": "player",
        "/api": "plain",
        "/s8": "overview",
        "/s8/leaderboard": "leaderboard",
        "/s8/servers": "servers",
        "/s8/players?q=leon": "search",
        "/s8/players/10000002": "player",
        "/api/s8": "plain",
    }
    for path, script in pages.items():
        status, headers, body = get(port, path)
        check(
            f"{path} serves the {script} page",
            status == 200
            and headers["Content-Type"].startswith("text/html")
            and f'<script type="module" src="/static/js/{script}.js">' in body
            and "<!--#include" not in body
            and "Leon" not in body,
        )
    status, headers, body = get(port, "/")
    check("pages allow only their own scripts and styles", "script-src 'self'" in headers["Content-Security-Policy"])
    check("missing player is the not-found page with a 404", get(port, "/players/123")[0] == 404)
    check("unknown page is a 404", get(port, "/x")[0] == 404 and "Not found" in get(port, "/x")[2])

    print("static files:")
    check("stylesheet", ctype(port, "/static/portal.css") == "text/css; charset=utf-8")
    check(
        "scripts",
        all(ctype(port, f"/static/js/{s}.js") == "text/javascript; charset=utf-8" for s in set(pages.values())),
    )
    for path in (
        "/static/index.html",
        "/static/partials/header.html",
        "/static/../portal.py",
        "/static/..%2Fportal.py",
        "/static/js/nope.js",
    ):
        check(f"{path} is not served", get(port, path)[0] == 404)

    print("drawn icons:")
    status, headers, body = get(port, "/drawn/rank.svg?level=50")
    check("rank insignia", status == 200 and headers["Content-Type"] == "image/svg+xml" and body.startswith("<svg"))
    body = get(port, "/drawn/badge.svg?text=%3Cscript%3E&tone=x")[2]
    check("badge text is reduced to two letters", "<script" not in body and ">SC</text>" in body)
    check("unknown icon kind is a 404", get(port, "/drawn/nope.svg")[0] == 404)


def check_page_sources():
    """Checks the files in server/web without a browser."""
    print("page sources:")

    def ids_in_js(text):
        return set(re.findall(r'\$\(\s*["]([\w-]+)["]\s*\)', text)) | set(re.findall(r'render\(\s*"([\w-]+)"', text))

    def ids_in_html(text):
        return set(re.findall(r'\bid="([\w-]+)"', text))

    def read(*parts):
        with open(os.path.join(WEB, *parts), encoding="utf-8") as f:
            return f.read()

    shared = set()
    for name in os.listdir(os.path.join(WEB, "partials")):
        shared |= ids_in_html(read("partials", name))
    missing = ids_in_js(read("js", "portal.js")) - shared
    check("every id portal.js uses is in a shared partial", not missing or print("    missing:", missing))

    for name in sorted(n for n in os.listdir(WEB) if n.endswith(".html")):
        html = portal.read_page(name)
        script = re.search(r'<script type="module" src="/static/js/([\w-]+)\.js">', html).group(1)
        missing = ids_in_js(read("js", f"{script}.js")) - ids_in_html(html)
        check(f"{name}: every id {script}.js uses is in the page", not missing or print("    missing:", missing))
        check(f"{name}: has the shared header and footer", {"page-error", "footer-game"} <= ids_in_html(html))

    scripts = {n: read("js", n) for n in os.listdir(os.path.join(WEB, "js"))}
    markup = [
        n for n, text in scripts.items() if re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", text)
    ]
    check("no script writes markup", not markup or print("    in:", markup))
    if shutil.which("node"):
        bad = []
        with tempfile.TemporaryDirectory() as tmp:
            for n, text in scripts.items():
                # Any Node version reads a .mjs file as a module, which these scripts are.
                path = os.path.join(tmp, n.replace(".js", ".mjs"))
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                if subprocess.run(["node", "--check", path], capture_output=True).returncode:
                    bad.append(n)
        check("every script parses (node --check)", not bad or print("    in:", bad))


def main():
    log.configure(os.path.join(tempfile.gettempdir(), "s8_portal_test.log"))
    db = os.path.join(tempfile.gettempdir(), "s8_portal.db")
    seed(db)
    port = 18080
    assets = os.path.join(tempfile.gettempdir(), "s8_portal_assets")
    make_assets(assets)
    games = games_for(Titles())
    httpd = portal.start(db, "127.0.0.1", port, games, assets)
    check("portal starts", httpd is not None)
    check("a taken port is skipped, not fatal", portal.start(db, "127.0.0.1", port, games) is None)
    portal_obj = httpd.RequestHandlerClass.portal

    def fresh():
        for d in portal_obj.data.values():
            d._cache.clear()

    check_api(port, db, games)
    check_pages(port)
    check_page_sources()

    print("images:")
    rows = get_json(port, "/api/leaderboard")["rows"]
    check("rank image from mapping.json", "/assets/exported/T_Rank_01.png" in [r["icon"] for r in rows])
    p = get_json(port, "/api/players/10000001")
    badge = {b["name"]: b["icon"] for b in p["badges"]}
    check("badge image by name", badge["Infantry Assault"] == "/assets/badges/infantry-assault.png")
    check(
        "a badge without an image is drawn",
        all(u.startswith(("/assets/", "/drawn/badge.svg?text=")) for u in badge.values()),
    )
    check("feat image by id", {f["id"]: f["icon"] for f in p["feats"]}[8] == "/assets/feats/F8.png")
    check("gear image by weapon", "/assets/gear/assault-rifle.png" in [g["icon"] for g in p["gear"]])
    check("game banner", get_json(port, "/api/games")[0]["banner"] == "/assets/games/Prejudice.png")
    unlocks = {u["name"]: u["unlocked"] for u in p["unlocks"]}
    check(
        "unlocks listed and lit by level (Aegis Armor by the Section 8 level); a broken entry is skipped",
        unlocks == {"Test Rifle": True, "Late Gadget": False, "Aegis Armor": True},
    )
    status, headers, _ = get(port, "/assets/badges/infantry-assault.png")
    check("image served as png", status == 200 and headers["Content-Type"] == "image/png")
    check(
        "a mapping that leaves the folder is ignored",
        "outside.png" not in json.dumps(get_json(port, "/api/players/10000002")),
    )
    check(
        "path traversal refused",
        get(port, "/assets/..%2Foutside.png")[0] == 404 and get(port, "/assets/../outside.png")[0] == 404,
    )
    check(
        "non-image files are not served",
        get(port, "/assets/evil.svg")[0] == 404 and get(port, "/assets/mapping.json")[0] == 404,
    )

    print("freshness:")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE record_times SET updated_at=? WHERE table_id='ServerStatusTG09_v6'", (time.time() - 3600,))
    conn.commit()
    conn.close()
    fresh()
    check("a server silent for an hour shows offline", not get_json(port, "/api/servers")[0]["online"])

    print("database written by an older server (no portal tables):")
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE portal_names")
    conn.execute("DROP TABLE record_times")
    conn.commit()
    conn.close()
    fresh()
    check(
        "the API still answers",
        all(get(port, x)[0] == 200 for x in ("/api/summary", "/api/servers", "/api/players/10000001")),
    )
    check("names fall back to the nick", get_json(port, "/api/players/10000001")["name"] == "T8iPHostNick")

    shutil.rmtree(assets)
    portal_obj.assets._scanned = 0
    p = get_json(port, "/api/players/10000001")
    check(
        "with no images at all, every icon is drawn",
        "/assets/" not in json.dumps(p) and p["icon"].startswith("/drawn/rank.svg"),
    )

    if "--keep" in sys.argv:
        make_assets(assets)
        seed(db)
        print(f"portal running on http://127.0.0.1:{port}/ (Ctrl+C)")
        while True:
            time.sleep(60)
    httpd.shutdown()
    print("ALL PORTAL TESTS PASSED" if not failures else f"{failures} PORTAL TEST(S) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
