import os
import sqlite3
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import log, screport, statdefs, stats
from server.codecs import _gslame_next, login_ticket_for, md5_hex, parse_kv, password_decode, profileid_from_ticket
from server.competition import CompetitionService
from server.gpcm import GpcmService
from server.persistence import Store
from server.sake import SakeService

log.configure(os.path.join(tempfile.gettempdir(), "s8_smoke.log"))
failures = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        failures.append(name)


def _passencode(plaintext: str) -> str:
    # Inverse of codecs.password_decode.
    import base64
    import struct

    data = bytearray(plaintext.encode("latin-1"))
    num = struct.unpack("<L", b"gspy")[0]
    for i in range(len(data)):
        num = _gslame_next(num)
        data[i] ^= num % 0xFF
    enc = base64.b64encode(bytes(data)).decode()
    for a, b in (("+", "["), ("/", "]"), ("=", "_")):
        enc = enc.replace(a, b)
    return enc


print("codecs:")
_kv = parse_kv("\\login\\1\\uniquenick\\bob\\final\\")
check("parse_kv", _kv.get("login") == "1" and _kv.get("uniquenick") == "bob")
check("login_ticket roundtrip", profileid_from_ticket(login_ticket_for(10000007)) == 10000007)
check("login_ticket is 24 chars", len(login_ticket_for(10000001)) == 24)
check("passenc roundtrip", password_decode(_passencode("TGS8pwd97F87F4C")) == "TGS8pwd97F87F4C")

print("sake SearchForRecords -> valid Success:")
db = os.path.join(tempfile.gettempdir(), "s8_smoke.db")
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(db + suffix)
    except OSError:
        pass
store = Store(db)
sake = SakeService(store)

search_body = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:ns1="http://gamespy.net/sake"><SOAP-ENV:Body><ns1:SearchForRecords>'
    "<ns1:gameid>3160</ns1:gameid><ns1:secretKey>OGmgyP</ns1:secretKey>"
    "<ns1:loginTicket>00989681000000000000000000</ns1:loginTicket>"
    "<ns1:tableid>PlayerStats_v6</ns1:tableid><ns1:filter></ns1:filter>"
    "<ns1:sort>Ranked_Kills desc</ns1:sort><ns1:offset>0</ns1:offset><ns1:max>1</ns1:max>"
    "<ns1:surrounding>0</ns1:surrounding><ns1:ownerids><ns1:int>10000001</ns1:int></ns1:ownerids>"
    "<ns1:cacheFlag>0</ns1:cacheFlag><ns1:fields><ns1:string>Ranked_Kills</ns1:string>"
    "<ns1:string>Ranked_Deaths</ns1:string></ns1:fields>"
    "</ns1:SearchForRecords></SOAP-ENV:Body></SOAP-ENV:Envelope>"
)
resp = sake.handle("http://gamespy.net/sake/SearchForRecords", search_body)
root = ET.fromstring(resp)
result = next((e.text for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "SearchForRecordsResult"), None)
check("response is well-formed XML", root is not None)
check("result == Success", result == "Success")
_synth_rows = [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "ArrayOfRecordValue"]
check("empty player -> one synthetic zeroed row", len(_synth_rows) == 1)
check("synthetic row is zeroed", [e.text for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "value"] == ["0", "0"])

print("sake write -> read cycle:")
lt = login_ticket_for(10000001)
create_body = (
    f'<ns1:CreateRecord xmlns:ns1="http://gamespy.net/sake"><ns1:tableid>PlayerStats_v6</ns1:tableid>'
    f"<ns1:loginTicket>{lt}</ns1:loginTicket><ns1:values>"
    f"<ns1:RecordField><ns1:name>Ranked_Kills</ns1:name><ns1:value><ns1:intValue><ns1:value>42</ns1:value>"
    f"</ns1:intValue></ns1:value></ns1:RecordField></ns1:values></ns1:CreateRecord>"
)
cresp = sake.handle("", create_body)
croot = ET.fromstring(cresp)
rid = next((e.text for e in croot.iter() if e.tag.rsplit("}", 1)[-1] == "recordid"), None)
check("CreateRecord returned a recordid", rid is not None and rid.isdigit())

update_body = (
    f'<ns1:UpdateRecord xmlns:ns1="http://gamespy.net/sake"><ns1:tableid>PlayerStats_v6</ns1:tableid>'
    f"<ns1:loginTicket>{lt}</ns1:loginTicket><ns1:recordid>{rid}</ns1:recordid><ns1:values>"
    f"<ns1:RecordField><ns1:name>Ranked_Kills</ns1:name><ns1:value><ns1:intValue><ns1:value>99</ns1:value>"
    f"</ns1:intValue></ns1:value></ns1:RecordField></ns1:values></ns1:UpdateRecord>"
)
sake.handle("", update_body)

resp2 = sake.handle("http://gamespy.net/sake/SearchForRecords", search_body)
root2 = ET.fromstring(resp2)
rows = [e for e in root2.iter() if e.tag.rsplit("}", 1)[-1] == "ArrayOfRecordValue"]
values = [e.text for e in root2.iter() if e.tag.rsplit("}", 1)[-1] == "value"]
check("record now returned by search", len(rows) == 1)
check("persisted Ranked_Kills == 99", "99" in values)

print("gpcm login handshake:")
SERVER_CHAL = "ABCDEFGHIJ"
CLIENT_CHAL = "0123456789ABCDEF0123456789ABCDEF"
NICK = "T8iPFFF6FFFF51364E01"
PW = "TGS8pwd97F87F4C"
PWH = md5_hex(PW)
# The hash the client sends as \response\. \proof\ is the same hash with the challenges swapped.
CLIENT_RESP = md5_hex(PWH + " " * 48 + NICK + CLIENT_CHAL + SERVER_CHAL + PWH)

gpcm = GpcmService(store, SERVER_CHAL)
conn = gpcm.new_connection()
greet = conn.greeting().decode()
check("greeting sends \\lc\\1\\ challenge", greet.startswith("\\lc\\1\\challenge\\ABCDEFGHIJ"))

login = f"\\login\\1\\uniquenick\\{NICK}\\challenge\\{CLIENT_CHAL}\\response\\{CLIENT_RESP}\\final\\"
unknown = conn.feed(login.encode())[0].decode()
check("login for an unregistered nick is refused", "\\err\\265\\" in unknown)
check("a refused login does not mark the session logged in", conn.logged_in is False)

newuser = f"\\newuser\\\\nick\\{NICK}\\uniquenick\\{NICK}\\passenc\\{_passencode(PW)}\\final\\"
nur = gpcm.new_connection().feed(newuser.encode())[0].decode()
check("newuser answered with \\nur\\", nur.startswith("\\nur\\"))
# gpiProcessConnect fails the registration with GP_PARSE when either field is absent.
nur_kv = parse_kv(nur)
check("nur carries \\userid\\", nur_kv.get("userid", "").isdigit())
check("nur carries \\profileid\\", nur_kv.get("profileid", "").isdigit())

conn = gpcm.new_connection()
lc2 = conn.feed(login.encode())[0].decode()
check("login after newuser is answered with \\lc\\2\\", lc2.startswith("\\lc\\2\\"))
check("lt present", "\\lt\\" in lc2)
kv = parse_kv(lc2)
expected_proof = md5_hex(PWH + " " * 48 + NICK + SERVER_CHAL + CLIENT_CHAL + PWH)
check("proof is the correct swapped-challenge hash", kv.get("proof") == expected_proof)

bad = f"\\login\\1\\uniquenick\\{NICK}\\challenge\\{CLIENT_CHAL}\\response\\{'0' * 32}\\final\\"
wrong = gpcm.new_connection().feed(bad.encode())[0].decode()
check("a wrong response is refused", "\\err\\265\\" in wrong)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def fresh_db(name: str) -> str:
    path = os.path.join(tempfile.gettempdir(), name)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass
    return path


print("stat catalogue:")
all_keys = [s.key for s in stats.BY_KEY.values()]
check("991 report keys, none shared", len(all_keys) == 991 and len(set(all_keys)) == 991)
check("every date names a stat", all(s.date_of in stats.BY_COLUMN for s in stats.BY_KEY.values() if s.date_of))
check("key 10 is TimePlayed", stats.BY_KEY[10].column == "Ranked_TimePlayed")
check("key 11 is the XP counter", stats.BY_KEY[11].column == "Ranked_xp" and stats.BY_KEY[11].fold == "sum")
check("key 12 is Level, replaced", stats.BY_KEY[12].column == "Ranked_Level" and stats.BY_KEY[12].fold == "replace")
check("key 3 is KillsIAL, a max", stats.BY_KEY[3].column == "Ranked_KillsIAL" and stats.BY_KEY[3].fold == "max")
check("key 1427 is Conquest time played", stats.BY_KEY[1427].column == "Ranked_TimePlayed_GM1")
check("key 281 is frag grenade time played", stats.BY_KEY[281].column == "Ranked_TimePlayed_G8")
check("badge B<n> is key 613 + 2n", all(stats.BY_COLUMN[f"Ranked_Badge_B{n}"].key == 613 + 2 * n for n in range(32)))
check("32 badges and 32 feats are named", len(statdefs.BADGES) == 32 and len(statdefs.FEATS) == 32)
check("badge 30 is Security Automation", statdefs.BADGES[30][0] == "Security Automation")

print("level table:")
check("0 XP is level 1", stats.level_for_xp(0) == 1)
check("25 XP is level 2", stats.level_for_xp(24) == 1 and stats.level_for_xp(25) == 2)
check("151 XP is level 5, as RANKED STATS shows", stats.level_for_xp(151) == 5)
check("70000 XP is level 100", stats.level_for_xp(70000) == 100 and stats.level_for_xp(10**9) == 100)
check("level 5 is Private E-2", stats.rank_for_level(5) == "Private E-2")
check("level 100 is General", stats.rank_for_level(100) == "General")

print("SC report decode:")
r1 = screport.parse(fixture("report_1p_handles.bin"))
p1 = {k: v for k, (_t, v) in r1.players[0].keys.items()}
check("single player report: 1 player, 39 keys", len(r1.players) == 1 and len(r1.players[0].keys) == 39)
check("kills 10, deaths 1, xp 29", (p1[1], p1[2], p1[11]) == (10, 1, 29))
r2 = screport.parse(fixture("report_2p_ccid.bin"))
check("two player report decodes both blocks", [len(p.keys) for p in r2.players] == [40, 42])
check("roster carries the minted profileids", [p.connection_dword() for p in r2.players] == [10000002, 10000001])
for block in r1.players + r2.players:
    v = {k: x for k, (_t, x) in block.keys.items()}
    gear_kills = sum(x for k, x in v.items() if stats.BY_KEY[k].column.startswith("Ranked_Kills_G"))
    check(f"slot {block.index}: TimePlayed equals Conquest time", v.get(10) == v.get(1427))
    check(f"slot {block.index}: kills equal the sum of gear kills", v.get(1, 0) == gear_kills)
try:
    screport.parse(fixture("report_2p_ccid.bin")[:300])
    check("a truncated report is refused", False)
except screport.ReportError:
    check("a truncated report is refused", True)

print("lifetime stats:")
st = Store(fresh_db("s8_stats.db"))
for nick in ("Host", "Friend"):
    st.get_or_create_profile(nick)
comp = CompetitionService(st, report_dir=tempfile.gettempdir())


def career(pid):
    rid = st.record_id_for_owner("PlayerStats_v6", pid)
    if rid is None:
        return {}
    return {n: v for n, (_t, v) in st.get_fields("PlayerStats_v6", pid, rid).items()}


def with_minted_ccid(blob: bytes, pids: list[int]) -> bytes:
    """Writes each player's minted ccid into the roster, as a host does."""
    out = bytearray(blob)
    for i, pid in enumerate(pids):
        out[0x44 + 20 * i : 0x44 + 20 * i + 16] = bytes.fromhex(f"{pid:08X}BEEFCAFE") + bytes(8)
    return bytes(out)


comp.ingest_report(fixture("report_2p_handles.bin"), csid="t0")
check("slots without a minted ccid change no career", not career(10000001) and not career(10000002))
one_player = with_minted_ccid(fixture("report_1p_handles.bin"), [10000001])
comp.ingest_report(one_player, csid="t1")
c1 = career(10000001)
check("kills folded in", c1.get("Ranked_Kills") == "10")
check("xp folded in", c1.get("Ranked_xp") == "29")
check("level derived from the XP table", c1.get("Ranked_Level") == "2")
check("time played and its mode slot", (c1.get("Ranked_TimePlayed"), c1.get("Ranked_TimePlayed_GM1")) == ("238", "238"))
check(
    "feat count and tiers",
    (c1.get("Ranked_Feats_F8"), c1.get("Ranked_Feats1_F8"), c1.get("Ranked_Feats2_F8")) == ("3", "2", "1"),
)
check("gear kills for Assault Rifle (Rail Rounds)", c1.get("Ranked_Kills_G33") == "10")
check("dates stored", c1.get("Ranked_Kills_Date", "").startswith("2026-07-27T"))

comp.ingest_report(one_player, csid="t1")
check("the same report applied twice counts once", career(10000001) == c1)

comp.ingest_report(fixture("report_2p_ccid.bin"), csid="t2")
c2 = career(10000001)
check("ccid attribution sums across matches", (c2.get("Ranked_Kills"), c2.get("Ranked_xp")) == ("11", "47"))
check("a lower per-life record keeps the old record", c2.get("Ranked_KillsIAL") == "7")
check("and keeps the old record's date", c2.get("Ranked_KillsIAL_Date") == c1.get("Ranked_KillsIAL_Date"))
check("a higher per-life record replaces it", c2.get("Ranked_DestructionIAL") == "1")
friend = career(10000002)
check("the second player gets their own block", (friend.get("Ranked_xp"), friend.get("Ranked_Kills")) == ("7", "2"))

comp.ingest_report(fixture("report_1p_ccid_badges.bin"), csid="t3")
earned = {(k, n): t for k, _i, n, _c, t, _d in st.awards(10000001)}
check("badge Infantry Assault earned", earned.get(("badge", "Infantry Assault")) == 1)
check("feat Assault Rifle Killer counted", earned.get(("feat", "Assault Rifle Killer")) == 5)
named = {label: value for _f, _i, _n, label, _c, value in st.career(10000001)}
check("the career view names stats", named.get("Kills") == "20")
check("match history kept", len(st.match_history(10000001)) == 3)
check("the second player has their one match", len(st.match_history(10000002)) == 1)
rid1 = st.record_id_for_owner("PlayerStats_v6", 10000001)
check(
    "a career rebuilt from history matches the folded one",
    stats.rebuild_career(st.match_keys(10000001)) == st.get_fields("PlayerStats_v6", 10000001, rid1),
)

lone = Store(fresh_db("s8_lone.db"))
CompetitionService(lone, report_dir=tempfile.gettempdir()).ingest_report(
    fixture("report_2p_ccid.bin"), csid="t4", host=10000003
)
check(
    "a minted ccid names the player with no session and no profile, and never the host",
    lone.record_id_for_owner("PlayerStats_v6", 10000001) is not None
    and lone.record_id_for_owner("PlayerStats_v6", 10000003) is None,
)

print("sake reads of the career:")
sk = SakeService(st)


def search(**kw):
    owners = "".join(f"<ns1:int>{o}</ns1:int>" for o in kw.get("owners", []))
    fields = "".join(f"<ns1:string>{f}</ns1:string>" for f in kw.get("fields", ["ownerid", "row", "Ranked_xp"]))
    body = (
        '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://gamespy.net/sake"><SOAP-ENV:Body><ns1:SearchForRecords>'
        f"<ns1:loginTicket>{login_ticket_for(10000001)}</ns1:loginTicket>"
        "<ns1:tableid>PlayerStats_v6</ns1:tableid>"
        f"<ns1:filter>{kw.get('filter', '')}</ns1:filter><ns1:sort>{kw.get('sort', '')}</ns1:sort>"
        f"<ns1:offset>{kw.get('offset', 0)}</ns1:offset><ns1:max>{kw.get('max', 10)}</ns1:max>"
        f"<ns1:targetRecordFilter>{kw.get('target', '')}</ns1:targetRecordFilter>"
        f"<ns1:surrounding>{kw.get('surrounding', 0)}</ns1:surrounding>"
        f"<ns1:ownerids>{owners}</ns1:ownerids><ns1:fields>{fields}</ns1:fields>"
        "</ns1:SearchForRecords></SOAP-ENV:Body></SOAP-ENV:Envelope>"
    )
    rows = ET.fromstring(sk.handle("", body)).iter()
    return [
        [v.text for v in row.iter() if v.tag.rsplit("}", 1)[-1] == "value"]
        for row in rows
        if row.tag.rsplit("}", 1)[-1] == "ArrayOfRecordValue"
    ]


check("own read returns the folded XP", search(owners=[10000001]) == [["10000001", "1", "90"]])
check(
    "ranked page sorted by XP with row numbers",
    search(sort="Ranked_xp desc") == [["10000001", "1", "90"], ["10000002", "2", "7"]],
)
check("ranked page offset", search(sort="Ranked_xp desc", offset=1, max=1) == [["10000002", "2", "7"]])
check(
    "around a player",
    search(sort="Ranked_xp desc", target="ownerid=10000002", surrounding=1)
    == [["10000001", "1", "90"], ["10000002", "2", "7"]],
)
check("filter", search(filter="Ranked_Kills &gt; 5") == [["10000001", "1", "90"]])
check("a ranked page with no records stays empty", search(filter="Ranked_Kills &gt; 500") == [])
check(
    "a column never written reads zero in its catalogue type",
    search(owners=[10000001], fields=["Ranked_RepairIAL", "Ranked_Badge_Date_B3"]) == [["0", "0001-01-01T00:00:00"]],
)

print("migration of an earlier database:")
old = fresh_db("s8_old.db")
con = sqlite3.connect(old)
con.executescript(
    "CREATE TABLE records(table_id TEXT, owner_id INTEGER, record_id INTEGER,"
    " PRIMARY KEY(table_id, owner_id, record_id));"
    "CREATE TABLE fields(table_id TEXT, owner_id INTEGER, record_id INTEGER, name TEXT, value_type TEXT, value TEXT,"
    " PRIMARY KEY(table_id, owner_id, record_id, name));"
    "INSERT INTO records VALUES('PlayerStats_v6', 10000001, 1);"
    "INSERT INTO fields VALUES('PlayerStats_v6', 10000001, 1, 'Ranked_xp', 'intValue', '300');"
    "INSERT INTO fields VALUES('PlayerStats_v6', 10000001, 1, 'Ranked_wkey_10', 'intValue', '238');"
    "INSERT INTO records VALUES('S8Level_v6', 10000001, 1);"
    "INSERT INTO fields VALUES('S8Level_v6', 10000001, 1, 'Ranked_Level', 'intValue', '6');"
)
con.commit()
con.close()
mig = Store(old)
mf = mig.get_fields("PlayerStats_v6", 10000001, 1)
check("XP kept", mf.get("Ranked_xp", (None, None))[1] == "300")
check("placeholder columns dropped", "Ranked_wkey_10" not in mf)
check("level derived", mf.get("Ranked_Level", (None, None))[1] == "6")
check("S8Level_v6 cleared", mig.record_id_for_owner("S8Level_v6", 10000001) is None)
check(
    "carried-over XP survives a rebuild",
    stats.rebuild_career(mig.match_keys(10000001)).get("Ranked_xp") == ("intValue", "300"),
)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL SMOKE TESTS PASSED")
