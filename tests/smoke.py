"""In-process smoke tests, no game required. Exercises the codecs, the Sake SearchForRecords path, a
write-then-read record cycle, and the GPCM login handshake. Run: python -m tests.smoke"""

import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import log
from server.codecs import _gslame_next, login_ticket_for, md5_hex, parse_kv, password_decode, profileid_from_ticket
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
    # Inverse of codecs.password_decode, to prove the cipher round-trips.
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
root = ET.fromstring(resp)  # must be well-formed
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
# gpiProcessConnect looks both up by name and fails the registration with GP_PARSE if either is absent.
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

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL SMOKE TESTS PASSED")
