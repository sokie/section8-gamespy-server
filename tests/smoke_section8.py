"""Section 8 (2009) and multi-title tests.  python -m tests.smoke_section8"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import available, log, stats
from server.codecs import login_ticket_for, md5_hex, parse_kv
from server.competition import CompetitionService
from server.config import Config
from server.gpcm import GpcmService
from server.persistence import Store
from server.sake import SakeService
from server.titles import Titles
from server.xlsp import service_file

log.configure(os.path.join(tempfile.gettempdir(), "s8_classic_smoke.log"))
failures = []
HERE = os.path.dirname(os.path.abspath(__file__))
S8_GAMEID = 2238  # A gameid that no title claims. The server sends it to Section 8.


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        failures.append(name)


def fresh_db(name):
    path = os.path.join(tempfile.gettempdir(), name)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass
    return path


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), "rb") as f:
        return f.read()


def with_minted_ccid(blob, pids):
    out = bytearray(blob)
    for i, pid in enumerate(pids):
        out[0x44 + 20 * i : 0x44 + 20 * i + 16] = bytes.fromhex(f"{pid:08X}BEEFCAFE") + bytes(8)
    return bytes(out)


def sake_body(op, table, gameid=None, inner="", ticket_pid=10000001):
    gid = f"<ns1:gameid>{gameid}</ns1:gameid><ns1:secretKey>x</ns1:secretKey>" if gameid is not None else ""
    return (
        '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:ns1="http://gamespy.net/sake"><SOAP-ENV:Body><ns1:{op}>{gid}'
        f"<ns1:loginTicket>{login_ticket_for(ticket_pid)}</ns1:loginTicket><ns1:tableid>{table}</ns1:tableid>"
        f"{inner}</ns1:{op}></SOAP-ENV:Body></SOAP-ENV:Envelope>"
    )


def values(xml):
    return [e.text for e in ET.fromstring(xml).iter() if e.tag.rsplit("}", 1)[-1] == "value"]


def search_inner(fields, owners=(10000001,)):
    o = "".join(f"<ns1:int>{x}</ns1:int>" for x in owners)
    f = "".join(f"<ns1:string>{x}</ns1:string>" for x in fields)
    return f"<ns1:ownerids>{o}</ns1:ownerids><ns1:max>1</ns1:max><ns1:fields>{f}</ns1:fields>"


def write_inner(name, value):
    return (
        f"<ns1:values><ns1:RecordField><ns1:name>{name}</ns1:name><ns1:value><ns1:intValue>"
        f"<ns1:value>{value}</ns1:value></ns1:intValue></ns1:value></ns1:RecordField></ns1:values>"
    )


print("title resolution:")
titles = Titles()
check("no identifier -> Prejudice", titles.resolve().key == "prejudice")
check("gameid 3160 -> Prejudice", titles.resolve("3160").key == "prejudice")
check("secretKey OGmgyP -> Prejudice", titles.resolve(None, "OGmgyP").key == "prejudice")
check("an unclaimed gameid -> Section 8", titles.resolve(str(S8_GAMEID)).key == "section8")
check("config can claim a gameid", Titles({"section8": {"gameids": [77]}}).resolve(77).key == "section8")
s8 = titles.get("section8")
check("Section 8 GFWL title id 0x535007E3", s8.xbox_title_id == 0x535007E3)
check("Section 8 tables are namespaced", s8.table("PlayerStats_v1") == "s8:PlayerStats_v1")
check("Prejudice tables are not", titles.legacy.table("PlayerStats_v6") == "PlayerStats_v6")
check(
    "a ':' in a wire table cannot reach another namespace",
    titles.legacy.table("s8:PlayerStats_v5") != "s8:PlayerStats_v5",
)

print("sake per title:")
store = Store(fresh_db("s8c_sake.db"))
# These checks are about running with no catalogue, so switch the shipped catalog/section8.json off.
NO_CAT = {"section8": {"catalog": None}}
titles = Titles(NO_CAT, store=store)
s8 = titles.get("section8")
sake = SakeService(store, titles=titles)
sake.handle("", sake_body("CreateRecord", "PlayerStats_v1", S8_GAMEID, write_inner("Ranked_Kills", 7)))
check("a Section 8 write lands in its namespace", store.record_id_for_owner("s8:PlayerStats_v1", 10000001) is not None)
check("and not in the bare table", store.record_id_for_owner("PlayerStats_v1", 10000001) is None)
r = sake.handle("", sake_body("SearchForRecords", "PlayerStats_v1", S8_GAMEID, search_inner(["Ranked_Kills"])))
check("Section 8 reads its own value back", values(r) == ["7"])
r = sake.handle("", sake_body("SearchForRecords", "PlayerStats_v1", 3160, search_inner(["Ranked_Kills"])))
check("Prejudice reading the same table name sees a zeroed row", values(r) == ["0"])
check("the player table was learnt from the read", titles.player_table(s8) == "s8:PlayerStats_v1")
check("and persisted", store.get_meta("player_table:section8") == "PlayerStats_v1")
reload = Titles(NO_CAT, store=store)
check("a reload remembers it", reload.player_table(reload.get("section8")) == "s8:PlayerStats_v1")
sake.handle("", sake_body("SearchForRecords", "ServerStatus_v1", S8_GAMEID, search_inner(["Ranked_Kills"])))
check("a non-PlayerStats table is never learnt", store.get_meta("player_table:section8") == "PlayerStats_v1")

print("ranked report, no catalogue:")
comp = CompetitionService(store, report_dir=tempfile.gettempdir(), titles=titles)
cs = comp.handle(
    'SOAPAction: "http://gamespy.net/competition/CreateSession"',
    f"<gsc:CreateSession><gsc:profileid>10000001</gsc:profileid><gsc:gameid>{S8_GAMEID}</gsc:gameid></gsc:CreateSession>",
)
csid = ET.fromstring(cs).find(".//{http://gamespy.net/competition/}csid").text
check("CreateSession remembers the title of its csid", titles.title_for_session(csid).key == "section8")
blob = with_minted_ccid(fixture("report_1p_handles.bin"), [10000001])
comp.ingest_report(blob, csid=csid, title=s8)
rid = store.record_id_for_owner("s8:PlayerStats_v1", 10000001)
f = {n: v for n, (_t, v) in store.get_fields("s8:PlayerStats_v1", 10000001, rid).items()}
check("every key kept as Unmapped_Key<n>", f.get("Unmapped_Key1") == "10" and f.get("Unmapped_Key11") == "29")
check("the earlier client write survives", f.get("Ranked_Kills") == "7")
check("Prejudice's career is untouched", store.record_id_for_owner("PlayerStats_v6", 10000001) is None)
comp.ingest_report(blob, csid="replay", title=s8)
check("a replay of a live report is a resend", len(store.match_history(10000001, "section8")) == 1)
check("the match is stored as a Section 8 match", len(store.match_history(10000001, "section8")) == 1)
check("and is not a Prejudice match", len(store.match_history(10000001, "prejudice")) == 0)
check("no level link without a level", store.record_id_for_owner("S8Level_v6", 10000001) is None)

check(
    "CheckProfileOnBanList's <gsc:GameId> picks the title",
    comp._title_of(f"<gsc:GameId>{S8_GAMEID}</gsc:GameId>".encode()).key == "section8",
)

print("shipped Section 8 catalogue:")
shipped = Titles().get("section8").catalog
check("340 keys from OnlineSubsystemGameSpy.StatsKeyMappings", len(shipped.by_key) == 340)
check(
    "career lives in PlayerStats_v5, XP in Ranked_XP",
    (shipped.player_table, shipped.xp_column) == ("PlayerStats_v5", "Ranked_XP"),
)
check(
    "key 11 is XP, key 1 Kills, key 12 Level",
    [shipped.by_key[k].column for k in (11, 1, 12)] == ["Ranked_XP", "Ranked_Kills", "Ranked_Level"],
)
check("100 levels and 21 ranks", len(shipped.level_xp) == 100 and len(shipped.ranks) == 21)
check("32 badges, 16 feats", len(shipped.badges) == 32 and len(shipped.feats) == 16)
folded = shipped.fold_match({}, {1: (0, 10), 11: (0, 151), 12: (0, 3)})
check(
    "a report folds to named columns and a derived level",
    (folded.updates["Ranked_Kills"][1], folded.updates["Ranked_XP"][1], folded.level) == ("10", "151", 5),
)

print("JSON catalogue + level link + rebuild:")
cat_path = os.path.join(tempfile.gettempdir(), "s8c_catalog.json")
rows = [s for s in stats.BY_KEY.values() if s.family == "career"]
with open(cat_path, "w") as fh:
    json.dump(
        {
            "title": "section8",
            "level_xp": list(stats.PREJUDICE.level_xp),
            "ranks": [list(r) for r in stats.PREJUDICE.ranks],
            "stats": [
                {
                    "key": s.key,
                    "column": s.column,
                    "fold": s.fold,
                    "label": s.label,
                    "sake_type": s.sake_type,
                    "date_of": s.date_of,
                }
                for s in rows
            ],
        },
        fh,
    )
titles2 = Titles({"section8": {"catalog": cat_path}}, store=store)
s8b = titles2.get("section8")
check("catalogue loaded", len(s8b.catalog.by_key) == len(rows) and s8b.catalog.has_levels)
comp2 = CompetitionService(store, report_dir=tempfile.gettempdir(), titles=titles2)
# This fixture already carries minted ccids (slot 0 = 10000002, slot 1 = 10000001).
comp2.ingest_report(fixture("report_2p_ccid.bin"), csid="x", title=s8b)
f = {n: v for n, (_t, v) in store.get_fields("s8:PlayerStats_v1", 10000001, rid).items()}
check("mapped keys fold onto named columns", f.get("Ranked_xp") is not None and f.get("Ranked_Level") is not None)
lvl_rid = store.record_id_for_owner("S8Level_v6", 10000001)
lvl = store.get_fields("S8Level_v6", 10000001, lvl_rid) if lvl_rid else {}
check(
    "Section 8 level copied to Prejudice's S8Level_v6", lvl.get("Ranked_Level", (0, None))[1] == f.get("Ranked_Level")
)
career = s8b.catalog.rebuild_career(store.match_keys(10000001, "section8"))
check("rebuild over both Section 8 matches sums them", career["Ranked_xp"][1] == str(29 + 18))
check("rebuild names the first match's keys too", "Unmapped_Key1" not in career and career["Ranked_Kills"][1] == "11")

print("gpcm / gpsp:")
gp = GpcmService(store, "ABCDEFGHIJ")
gp.learn_password("Leon", "pw")
pid = store.get_or_create_profile("Leon")
out = gp.new_connection().feed(b"\\check\\\\nick\\Leon\\id\\3\\final\\")[0].decode()
check("\\check\\ finds a nick", parse_kv(out).get("cur") == "0" and parse_kv(out).get("pid") == str(pid))
out = gp.new_connection().feed(b"\\check\\\\nick\\Nobody\\id\\3\\final\\")[0].decode()
check("\\check\\ reports a missing nick", parse_kv(out).get("cur") == "265")
check("\\valid\\ answers", gp.new_connection().feed(b"\\valid\\\\email\\a@b\\final\\")[0].startswith(b"\\vr\\1"))
pwh, cc, sc = md5_hex("pw"), "C" * 32, "ABCDEFGHIJ"
user = "Leon@leon.local"
resp = md5_hex(pwh + " " * 48 + user + cc + sc + pwh)
out = gp.new_connection().feed(
    f"\\login\\\\user\\{user}\\challenge\\{cc}\\response\\{resp}\\gamename\\section8pc\\final\\".encode()
)[0]
check("\\user\\ login form accepted", out.startswith(b"\\lc\\2\\") and f"\\profileid\\{pid}\\".encode() in out)

print("xlln + ports + udp:")
doc = service_file(Titles().get("section8"))
check("titleId is the hex title id", doc["titleId"] == "0x535007E3")
check("six services, base+1..+6", [s["port"] for s in doc["services"]] == list(range(8901, 8907)))
check("endpoints are host:port", all(s["endpoint"] == f"127.0.0.1:{s['port']}" for s in doc["services"]))
ports = Config({}).resolve_ports(Titles())
check(
    "derived listeners cover base+1..+6 and 8800",
    ports == {8800: "http", 8901: "gpcm", 8902: "gpcm", 8903: "http", 8904: "http", 8905: "http", 8906: "http"},
)
check("an explicit port map still wins", Config({"ports": {"9000": "http"}}).resolve_ports(Titles()) == {9000: "http"})
check("availability probe parsed", available.parse_probe(b"\x09\x00\x00\x00\x00section8pc\x00") == "section8pc")
check("other packets ignored", available.parse_probe(b"\\heartbeat\\") is None)

print("end to end over sockets:")
from server.__main__ import build  # noqa: E402
from server.transport import Server  # noqa: E402

base = 18900
cfg = Config(
    {
        "bind_address": "127.0.0.1",
        "db_path": fresh_db("s8c_e2e.db"),
        "log_path": os.path.join(tempfile.gettempdir(), "s8c_e2e.log"),
        "titles": {"section8": {"lsp_base_port": base}, "prejudice": {"lsp_base_port": base + 100}},
    }
)
_t, gpcm_svc, router, _s = build(cfg)
cfg.resolve_ports(_t)
cfg.ports.pop(8800, None)
threading.Thread(target=Server(cfg, gpcm_svc, router).serve_forever, daemon=True).start()
time.sleep(0.5)


def recv_until(sock, marker, limit=5.0):
    sock.settimeout(limit)
    buf = b""
    while marker not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def _passencode(plaintext):
    """Gives the \\passenc\\ form of a password, the inverse of codecs.password_decode."""
    import base64
    import struct

    from server.codecs import _gslame_next

    data = bytearray(plaintext.encode("latin-1"))
    num = struct.unpack("<L", b"gspy")[0]
    for i in range(len(data)):
        num = _gslame_next(num)
        data[i] ^= num % 0xFF
    enc = base64.b64encode(bytes(data)).decode()
    for a, b in (("+", "["), ("/", "]"), ("=", "_")):
        enc = enc.replace(a, b)
    return enc


with socket.create_connection(("127.0.0.1", base + 1)) as s:
    recv_until(s, b"\\final\\")
    s.sendall(
        f"\\newuser\\\\nick\\Classic\\uniquenick\\Classic\\passenc\\{_passencode('pw8')}\\id\\1\\final\\".encode()
    )
    nur = recv_until(s, b"\\final\\")
check("GPSP port (base+1) registers the account", nur.startswith(b"\\nur\\"))
with socket.create_connection(("127.0.0.1", base + 2)) as s:
    greet = parse_kv(recv_until(s, b"\\final\\").decode())
    pwh = md5_hex("pw8")
    resp = md5_hex(pwh + " " * 48 + "Classic" + cc + greet["challenge"] + pwh)
    s.sendall(
        f"\\login\\\\challenge\\{cc}\\uniquenick\\Classic\\response\\{resp}\\gamename\\section8pc\\id\\1\\final\\".encode()
    )
    lc = recv_until(s, b"\\lc\\2\\")
check("GPCM port (base+2) logs in", b"\\lc\\2\\" in lc)
lt = parse_kv(lc[lc.index(b"\\lc\\2\\") :].decode("latin-1")).get("lt", "")


def http(port, path, body, action):
    req = (
        f'POST {path} HTTP/1.1\r\nHost: section8pc.x.gamespy.com\r\nSOAPAction: "{action}"\r\n'
        f"Content-Type: text/xml\r\nContent-Length: {len(body)}\r\n\r\n{body}"
    )
    with socket.create_connection(("127.0.0.1", port)) as s:
        s.sendall(req.encode("latin-1"))
        return recv_until(s, b"</soap:Envelope>").decode("latin-1")


auth = http(
    base + 3,
    "/AuthService/AuthService.asmx",
    "<ns1:LoginUniqueNick><ns1:uniquenick>Classic</ns1:uniquenick></ns1:LoginUniqueNick>",
    "http://gamespy.net/AuthService/LoginUniqueNick",
)
check(
    "AuthService (base+3) issues a certificate", "<certificate>" in auth and "<uniquenick>Classic</uniquenick>" in auth
)
ban = http(
    base + 4,
    "/CompetitionService/CompetitionService.asmx",
    f"<gsc:CheckProfileOnBanList><gsc:profileid>1</gsc:profileid><gsc:gameid>{S8_GAMEID}</gsc:gameid></gsc:CheckProfileOnBanList>",
    "http://gamespy.net/competition/CheckProfileOnBanList",
)
check("Competition (base+4) answers", "<IsBanned>0</IsBanned>" in ban)
body = sake_body("SearchForRecords", "PlayerStats_v1", S8_GAMEID, search_inner(["Ranked_Kills", "ownerid"]))
sk = http(base + 5, "/SakeStorageServer/StorageServer.asmx", body, "http://gamespy.net/sake/SearchForRecords")
check("Sake (base+5) answers Success", "<SearchForRecordsResult>Success</SearchForRecordsResult>" in sk)
with socket.create_connection(("127.0.0.1", base + 6)) as s:
    s.sendall(b"GET /motd/motd.asp?gamename=section8pc HTTP/1.0\r\n\r\n")
    motd = recv_until(s, b"MOTD_INT", 3.0)
check("motd (base+6) answers", b"MOTD_INT" in motd)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ALL SECTION 8 TESTS PASSED")
