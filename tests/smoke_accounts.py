"""Checks the portal accounts: the link code and clan tag values, then sign-up, claim, display name, clan tag and
release over HTTP, with the game's own Sake write and read on each side.

python -m tests.smoke_accounts
"""

import contextlib
import http.client
import json
import os
import tempfile
import xml.etree.ElementTree as ET

from server import accounts as accounts_mod
from server import log, portal
from server.codecs import login_ticket_for
from server.persistence import Store
from server.portal_data import games_for
from server.profile_fields import (
    clan_tag_error,
    clan_tag_from_value,
    clan_tag_to_int,
    format_link_code,
    parse_link_code,
    sake_int,
)
from server.sake import SakeService
from server.titles import Titles

PORT = 18081
PID, NICK = 10000001, "T8iPFFF6FFFF51364E01"
OTHER_PID, OTHER_NICK = 10000002, "T8iPFFF6FFFF298F1450"
failures = 0


def check(name, cond):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name)
    failures += not cond


def fresh_db(name):
    path = os.path.join(tempfile.gettempdir(), name)
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(OSError):
            os.remove(path + suffix)
    return path


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def game_writes_profile(sake, pid, random_code, clan_tag=0):
    """The PlayerProfile2_v6 write that the game sends after it shows a link code."""
    fields = (
        ("Profile_ClanTag", *sake_int(clan_tag)),
        ("Profile_AchievementsUnlocked", "int64Value", "0"),
        ("Profile_RandomLinkCode", *sake_int(random_code)),
        ("Profile_LiveIdentifier", "int64Value", "8024176277066302030"),
    )
    values = "".join(
        f"<ns1:RecordField><ns1:name>{n}</ns1:name><ns1:value><ns1:{t}><ns1:value>{v}</ns1:value></ns1:{t}>"
        f"</ns1:value></ns1:RecordField>"
        for n, t, v in fields
    )
    sake.handle(
        "",
        f'<ns1:CreateRecord xmlns:ns1="http://gamespy.net/sake"><ns1:gameid>3160</ns1:gameid>'
        f"<ns1:tableid>PlayerProfile2_v6</ns1:tableid><ns1:loginTicket>{login_ticket_for(pid)}</ns1:loginTicket>"
        f"<ns1:values>{values}</ns1:values></ns1:CreateRecord>",
    )


def game_reads_profile(sake, pid):
    """The Profile view read the game makes at sign-in. Gives {field: (Sake type, value)}."""
    names = ["Profile_ClanTag", "Profile_AchievementsUnlocked", "Profile_LinkCodeUsed"]
    body = (
        '<ns1:SearchForRecords xmlns:ns1="http://gamespy.net/sake"><ns1:gameid>3160</ns1:gameid>'
        f"<ns1:loginTicket>{login_ticket_for(pid)}</ns1:loginTicket><ns1:tableid>PlayerProfile2_v6</ns1:tableid>"
        "<ns1:sort>recordid desc</ns1:sort><ns1:offset>0</ns1:offset><ns1:max>1</ns1:max>"
        f"<ns1:ownerids><ns1:int>{pid}</ns1:int></ns1:ownerids><ns1:fields>"
        + "".join(f"<ns1:string>{n}</ns1:string>" for n in names)
        + "</ns1:fields></ns1:SearchForRecords>"
    )
    root = ET.fromstring(sake.handle("", body))
    cells = [e for e in root.iter() if _local(e.tag) == "RecordValue"]
    out = {}
    for name, cell in zip(names, cells, strict=True):
        typed = next(iter(cell))
        out[name] = (_local(typed.tag), next(iter(typed)).text)
    return out


class Browser:
    """Keeps the session cookie between requests."""

    def __init__(self):
        self.cookie = None
        self.last_set_cookie = ""

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        h = {"Content-Type": "application/json", **(headers or {})}
        if self.cookie:
            h["Cookie"] = self.cookie
        data = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
        conn.request(method, path, body=data if method == "POST" else None, headers=h)
        r = conn.getresponse()
        text = r.read().decode()
        set_cookie = r.getheader("Set-Cookie")
        if set_cookie:
            self.last_set_cookie = set_cookie
            pair = set_cookie.split(";")[0]
            self.cookie = None if pair.endswith("=") else pair
        conn.close()
        return r.status, (json.loads(text) if text.startswith(("{", "[")) else text), r

    def post(self, path, body=None, **kw):
        return self.call("POST", "/api/account" + path, body, **kw)

    def me(self):
        return self.call("GET", "/api/account")[1]


def check_values():
    print("link code and clan tag values:")
    check("the code from the game names profile 10000001", parse_link_code("758B-338A-154D") == (PID, 0x154D))
    check("the code reads without dashes and in lower case", parse_link_code(" 758b338a154d ") == (PID, 0x154D))
    check("a code round-trips", parse_link_code(format_link_code(10000123, 32767)) == (10000123, 32767))
    check("text that is not a code is refused", parse_link_code("758B-338A-15") is None)
    check("a tag is its ASCII bytes, little-endian", clan_tag_to_int("ABC") == 0x00434241)
    check("an int tag reads back", clan_tag_from_value(str(0x00434241)) == "ABC")
    check("a one-letter tag fits a byte", sake_int(clan_tag_to_int("A")) == ("byteValue", "65"))
    check("a longer tag is an int", sake_int(clan_tag_to_int("AB"))[0] == "intValue")
    check("0 is no tag", clan_tag_from_value("0") is None and clan_tag_from_value(None) is None)
    check("a tag has at most 3 characters", clan_tag_error("ABCD") is not None)
    check("a tag is letters and digits only", clan_tag_error("A B") is not None and clan_tag_error("Ä") is not None)
    check("TG is reserved in any case", clan_tag_error("tg") is not None)
    check("an empty tag clears it", clan_tag_error("") is None)
    stored = accounts_mod.hash_password("correct horse")
    check(
        "a password hash is scrypt with a salt",
        stored.startswith("scrypt$") and stored != accounts_mod.hash_password("correct horse"),
    )
    check("the password verifies", accounts_mod.verify_password("correct horse", stored))
    check("another password does not", not accounts_mod.verify_password("wrong horse", stored))


def check_flow(sake, store):
    print("account flow:")
    alice, bob = Browser(), Browser()
    check("signed out at first", alice.me() == {"signed_in": False})
    status, body, _ = alice.post("/register", {"username": "x", "password": "longenough"})
    check("a short username is refused", status == 400 and "username" in body["error"])
    status, body, _ = alice.post("/register", {"username": "Alice", "password": "short"})
    check("a short password is refused", status == 400)
    status, body, _ = alice.post("/register", {"username": "Alice", "password": "alice-password"})
    check("sign-up signs in", status == 201 and body["signed_in"] and body["username"] == "Alice")
    cookie = alice.last_set_cookie
    check(
        "the session cookie is HttpOnly and SameSite=Strict, not Secure on plain HTTP",
        "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" not in cookie,
    )
    status, _, _ = bob.post("/register", {"username": "alice", "password": "another-password"})
    check("usernames are unique in any case", status == 409)
    bob.post("/register", {"username": "Bob", "password": "bob-password"})

    status, body, _ = alice.post("/claim", {"code": "758B-338A-154D"})
    check("no claim before the game wrote a code", status == 409 and "no active code" in body["error"])
    game_writes_profile(sake, PID, 0x154D)
    stored = store.get_fields("PlayerProfile2_v6", PID, 1)
    check("the game's write stores the random part", stored.get("Profile_RandomLinkCode") == ("intValue", "5453"))
    check(
        "before a claim the game reads LinkCodeUsed = 0",
        game_reads_profile(sake, PID)["Profile_LinkCodeUsed"][1] == "0",
    )

    status, body, _ = alice.post("/claim", {"code": format_link_code(PID, 0x1111)})
    check("a wrong random part is refused", status == 403)
    status, body, _ = alice.post("/claim", {"code": format_link_code(99999999, 0x154D)})
    check("an unknown profile is refused", status == 404)
    status, body, _ = alice.post("/claim", {"code": "758B-338A-154D"})
    check("the right code claims the profile", status == 200 and body["claimed"] == PID)
    check(
        "the claimed profile is listed with the username as its display name",
        [(p["profileid"], p["display_name"], p["uniquenick"]) for p in body["profiles"]] == [(PID, "Alice", NICK)],
    )
    read = game_reads_profile(sake, PID)
    check("the game now reads LinkCodeUsed = 1", read["Profile_LinkCodeUsed"] == ("byteValue", "1"))
    check("the used code is spent", store.get_fields("PlayerProfile2_v6", PID, 1)["Profile_RandomLinkCode"][1] == "0")
    status, body, _ = bob.post("/claim", {"code": "758B-338A-154D"})
    check("another account cannot claim it", status == 409)
    status, body, _ = alice.post("/claim", {"code": "758B-338A-154D"})
    check("the owner is told it is theirs", status == 409 and "already yours" in body["error"])

    status, body, _ = alice.post(f"/profiles/{PID}", {"display_name": "  Sokiee   Prime ", "clan_tag": "S8x"})
    profile = body["profiles"][0] if status == 200 else {}
    check(
        "a display name and clan tag are saved",
        (profile.get("display_name"), profile.get("clan_tag")) == ("Sokiee Prime", "S8x"),
    )
    read = game_reads_profile(sake, PID)
    check("the game reads the clan tag as an int", read["Profile_ClanTag"] == ("intValue", str(clan_tag_to_int("S8x"))))
    _, player, _ = alice.call("GET", f"/api/players/{PID}")
    check(
        "the player page shows the name and the decoded tag",
        (player["name"], player["clan_tag"]) == ("Sokiee Prime", "S8x"),
    )
    status, body, _ = alice.post(f"/profiles/{PID}", {"clan_tag": "TG"})
    check("a reserved tag is refused", status == 400)
    status, body, _ = alice.post(f"/profiles/{PID}", {"clan_tag": ""})
    check("an empty tag clears it", status == 200 and body["profiles"][0]["clan_tag"] is None)
    status, _, _ = bob.post(f"/profiles/{PID}", {"display_name": "Mine"})
    check("only the owner changes a profile", status == 404)

    game_writes_profile(sake, OTHER_PID, 0x0042)
    bob.post("/claim", {"code": format_link_code(OTHER_PID, 0x0042)})
    status, body, _ = bob.post(f"/profiles/{OTHER_PID}", {"display_name": "sokiee prime"})
    check("a name another player shows is refused", status == 409)
    status, body, _ = bob.post(f"/profiles/{OTHER_PID}", {"display_name": NICK})
    check("a name that is another player's uniquenick is refused", status == 409)

    status, body, _ = alice.post(f"/profiles/{PID}/release")
    check("the owner releases the profile", status == 200 and body["profiles"] == [])
    check("the game reads LinkCodeUsed = 0 again", game_reads_profile(sake, PID)["Profile_LinkCodeUsed"][1] == "0")
    status, body, _ = bob.post("/claim", {"code": "758B-338A-154D"})
    check("the old code does not claim it after the release", status == 409 and "no active code" in body["error"])
    game_writes_profile(sake, PID, 0x2222)
    status, body, _ = bob.post("/claim", {"code": format_link_code(PID, 0x2222)})
    check("a new code from the game claims it", status == 200 and body["claimed"] == PID)

    status, body, _ = alice.post("/password", {"current": "nope", "new": "alice-password-2"})
    check("a password change needs the current password", status == 403)
    status, body, _ = alice.post("/password", {"current": "alice-password", "new": "alice-password-2"})
    check("the password changes", status == 200)
    status, body, _ = alice.post("/logout")
    check("sign-out clears the cookie", status == 200 and alice.cookie is None and not alice.me()["signed_in"])
    status, _, _ = alice.post("/login", {"username": "ALICE", "password": "alice-password"})
    check("the old password no longer signs in", status == 401)
    status, body, _ = alice.post("/login", {"username": "ALICE", "password": "alice-password-2"})
    check("sign-in ignores the case of the username", status == 200 and body["username"] == "Alice")


def check_limits(sake):
    print("limits and requests from other sites:")
    mallory = Browser()
    mallory.post("/register", {"username": "Mallory", "password": "mallory-password"})
    game_writes_profile(sake, PID + 2, 0x0777)
    code = [format_link_code(PID + 2, r) for r in range(1, accounts_mod.CLAIM_FAILS_PER_CODE + 2)]
    answers = [mallory.post("/claim", {"code": c})[0] for c in code]
    check(
        "wrong tries for one code stop after the limit",
        answers == [403] * accounts_mod.CLAIM_FAILS_PER_CODE + [429],
    )
    status, _, _ = mallory.post("/claim", {"code": format_link_code(PID + 2, 0x0777)})
    check("the right code is refused too once the code is locked", status == 429)
    game_writes_profile(sake, PID + 2, 0x0888)
    status, _, _ = mallory.post("/claim", {"code": format_link_code(PID + 2, 0x0888)})
    check("a new code from the game opens it again", status == 200)

    guesser = Browser()
    fails = [guesser.post("/login", {"username": "Bob", "password": f"guess-{i}"})[0] for i in range(11)]
    check("failed sign-ins for one name stop after 10", fails == [401] * 10 + [429])

    status, body, _ = mallory.post("/claim", {"code": "x"}, headers={"Content-Type": "text/plain"})
    check("a form post from another site is refused", status == 403)
    status, _, _ = mallory.post("/logout", headers={"Origin": "http://evil.example"})
    check("a request with another site's Origin is refused", status == 403)
    status, _, _ = mallory.post("/logout", headers={"Sec-Fetch-Site": "cross-site"})
    check("a cross-site fetch is refused", status == 403)
    check("the session survived those requests", mallory.me()["signed_in"])
    status, _, _ = mallory.post("/logout", body=b"[1]")
    check("a body that is not a JSON object is refused", status == 400)
    status, _, _ = mallory.post("/claim", body=b"{" + b" " * 9000 + b"}")
    check("a large body is refused", status == 413)
    status, _, r = Browser().call(
        "POST",
        "/api/account/register",
        {"username": "Proxied", "password": "proxied-pw"},
        headers={"X-Forwarded-Proto": "https"},
    )
    check("behind an HTTPS proxy the cookie is Secure", status == 201 and "Secure" in (r.getheader("Set-Cookie") or ""))
    check("the account page is served", Browser().call("GET", "/account")[0] == 200)
    check("a POST outside the account API is a 404", Browser().call("POST", "/api/leaderboard")[0] == 404)


def main():
    log.configure(os.path.join(tempfile.gettempdir(), "s8_accounts_test.log"))
    db = fresh_db("s8_accounts.db")
    store = Store(db)
    for nick in (NICK, OTHER_NICK, "T8iPFFF6FFFF00000003"):
        store.get_or_create_profile(nick)
    titles = Titles(store=store)
    sake = SakeService(store, titles=titles)
    check_values()
    httpd = portal.start(db, "127.0.0.1", PORT, games_for(titles))
    check("portal starts", httpd is not None)
    try:
        check_flow(sake, store)
        check_limits(sake)
    finally:
        httpd.shutdown()
    print("\nALL ACCOUNT TESTS PASSED" if not failures else f"\n{failures} ACCOUNT TEST(S) FAILED")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
