"""Seeds a database from ATLAS report fixtures and checks every portal page and API endpoint over HTTP.

python -m tests.smoke_portal [--keep]     --keep leaves the portal running on port 18080
"""

import json
import os
import shutil
import struct
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
    import sqlite3

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
    set_display_name(path, "10000001", "Leon <b>")  # Markup in a name must come out escaped.
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
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8", "replace")


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

    def fresh():
        for d in httpd.RequestHandlerClass.portal.data.values():
            d._cache.clear()

    print("api:")
    s = json.loads(get(port, "/api/summary")[2])
    check("summary counts 2 players and 3 matches", (s["players"], s["matches"]) == (2, 3))
    check("summary sees the live server", s["servers_online"] == 1)
    check("summary sums kills", s["total_kills"] == 22)
    lb = json.loads(get(port, "/api/leaderboard?sort=kills")[2])
    check(
        "leaderboard sorted by kills",
        [r["kills"] for r in lb["rows"]] == sorted((r["kills"] for r in lb["rows"]), reverse=True),
    )
    check("leaderboard uses the display name", lb["rows"][0]["name"] == "Leon <b>")
    check("leaderboard positions", [r["position"] for r in lb["rows"]] == [1, 2])
    check("bad sort falls back to xp", json.loads(get(port, "/api/leaderboard?sort=zzz&limit=abc")[2])["sort"] == "xp")
    p = json.loads(get(port, "/api/players/10000001")[2])
    check("player career", (p["kills"], p["matches"]) == (20, 3))
    check("player keeps the GameSpy nick", p["uniquenick"] == "T8iPHostNick")
    check("player badge earned", any(b["name"] == "Infantry Assault" and b["count"] == 1 for b in p["badges"]))
    check("player gear listed", any(g["Kills"] > 0 for g in p["gear"]))
    check("player history", len(p["history"]) == 3)
    check("level progress bounds", p["level_floor"] <= p["xp"] < (p["level_next"] or 10**9))
    friend = json.loads(get(port, "/api/players/10000002")[2])
    check("carried-over XP is not counted as a match", friend["matches"] == 1)
    check("but is shown in the history", any(h["carried_over"] for h in friend["history"]))
    check("unknown player is a 404", get(port, "/api/players/99")[0] == 404)
    check(
        "search by display name",
        [x["profileid"] for x in json.loads(get(port, "/api/players?q=leon")[2])] == [10000001],
    )
    check(
        "search by GameSpy nick",
        [x["profileid"] for x in json.loads(get(port, "/api/players?q=t8iphost")[2])] == [10000001],
    )
    srv = json.loads(get(port, "/api/servers")[2])
    check(
        "server online with its mode",
        srv and srv[0]["online"] and srv[0]["mode"] == "Conquest" and srv[0]["max_humans"] == 16,
    )
    check("unknown api is a JSON 404", get(port, "/api/nope")[:2] == (404, "application/json; charset=utf-8"))

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
    s8s = json.loads(get(port, "/api/s8/summary")[2])
    check(
        "Section 8 has its own players and matches", (s8s["game"], s8s["players"], s8s["matches"]) == ("section8", 1, 1)
    )
    p8 = json.loads(get(port, "/api/s8/players/10000001")[2])
    check("Section 8 career read from s8:PlayerStats_v5", p8["has_stats"] and p8["kills"] == 9 and p8["xp"] == 43)
    check(
        "Section 8 badge names",
        any(b["name"] == "Specialist" and b["count"] == 1 for b in p8["badges"]) and len(p8["badges"]) == 32,
    )
    check("Section 8 has 16 feats and no per-mode time", len(p8["feats"]) == 16 and p8["modes"] == [])
    check(
        "Prejudice career untouched by the Section 8 match",
        json.loads(get(port, "/api/players/10000001")[2])["kills"] == 20,
    )
    check(
        "Section 8 level linked into Prejudice",
        json.loads(get(port, "/api/players/10000001")[2])["section8_level"] == p8["level"],
    )
    srv8 = json.loads(get(port, "/api/s8/servers")[2])
    check(
        "Section 8 servers from s8:ServerStatus_v5",
        srv8 and srv8[0]["ranked"] and srv8[0]["max_humans"] == 32 and srv8[0]["mode"] == "Conquest",
    )
    for path in ("/s8", "/s8/leaderboard", "/s8/servers", "/s8/players?q=leon", "/s8/players/10000001"):
        status, ctype, body = get(port, path)
        check(f"{path} renders", status == 200 and "</html>" in body)
    page8 = get(port, "/s8/players/10000001")[2]
    check("Section 8 profile links to the Prejudice career", 'href="/players/10000001"' in page8)
    check("and Prejudice's links back", 'href="/s8/players/10000001"' in get(port, "/players/10000001")[2])
    check(
        "a player with no Section 8 career says so",
        "No ranked Section 8 career yet" in get(port, "/s8/players/10000002")[2],
    )
    check("the API index lists both games", "/api/s8/summary" in get(port, "/api")[2])

    print("pages:")
    for path in (
        "/",
        "/leaderboard",
        "/leaderboard?sort=kd",
        "/leaderboard?page=%C2%B2",
        "/servers",
        "/players?q=friend",
        "/players/10000001",
        "/api",
    ):
        status, ctype, body = get(port, path)
        check(f"{path} renders", status == 200 and ctype.startswith("text/html") and "</html>" in body)
    home = get(port, "/")[2]
    check("names are escaped", "Leon &lt;b&gt;" in home and "Leon <b>" not in home)
    prof = get(port, "/players/10000001")[2]
    check("profile shows badges and feats", "Infantry Assault" in prof and "Assault Rifle Killer" in prof)
    check("profile shows the GameSpy nick under the display name", "T8iPHostNick" in prof)
    check("missing page is 404", get(port, "/players/123")[0] == 404 and get(port, "/x")[0] == 404)
    check("search with markup is escaped", "&lt;script&gt;" in get(port, "/players?q=<script>")[2])

    print("freshness:")
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute("UPDATE record_times SET updated_at=? WHERE table_id='ServerStatusTG09_v6'", (time.time() - 3600,))
    conn.commit()
    conn.close()
    fresh()
    check("a server silent for an hour shows offline", not json.loads(get(port, "/api/servers")[2])[0]["online"])

    print("database written by an older server (no portal tables):")
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE portal_names")
    conn.execute("DROP TABLE record_times")
    conn.commit()
    conn.close()
    fresh()
    check("pages still render", all(get(port, x)[0] == 200 for x in ("/", "/servers", "/players/10000001")))
    check("names fall back to the nick", json.loads(get(port, "/api/players/10000001")[2])["name"] == "T8iPHostNick")

    print("images:")
    home = get(port, "/")[2]
    check("game banner used on the home page", "/assets/games/Prejudice.png" in home)
    check("rank image from mapping.json", "/assets/exported/T_Rank_01.png" in home)
    prof = get(port, "/players/10000001")[2]
    check("badge image by name", "/assets/badges/infantry-assault.png" in prof)
    check("feat image by id", "/assets/feats/F8.png" in prof)
    check("gear image by weapon", "/assets/gear/assault-rifle.png" in prof)
    check("missing images are drawn", prof.count("<svg") > 50)
    check(
        "unlocks listed, lit by level (Aegis Armor by the Section 8 level)",
        "Unlocks &middot; 2 of 3" in prof and "Test Rifle" in prof,
    )
    status, ctype, _ = get(port, "/assets/badges/infantry-assault.png")
    check("image served as png", status == 200 and ctype == "image/png")
    check("a mapping that leaves the folder is ignored", "outside.png" not in get(port, "/players/10000002")[2])
    check(
        "path traversal refused",
        get(port, "/assets/..%2Foutside.png")[0] == 404 and get(port, "/assets/../outside.png")[0] == 404,
    )
    check(
        "non-image files are not served",
        get(port, "/assets/evil.svg")[0] == 404 and get(port, "/assets/mapping.json")[0] == 404,
    )
    shutil.rmtree(assets)
    portal_obj = httpd.RequestHandlerClass.portal
    portal_obj.assets._scanned = 0
    check(
        "no images at all still renders",
        get(port, "/players/10000001")[0] == 200 and "<img" not in get(port, "/players/10000001")[2],
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
