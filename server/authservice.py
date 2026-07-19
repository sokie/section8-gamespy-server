"""AuthService - issues the GameSpy login certificate (SOAP over HTTP after the https->http quick-patch).

Section 8 uses LoginUniqueNick. The returned certificate carries a placeholder peer keypair and an
all-zero (unsigned) signature: the game accepts it because the quick-patch neuters the certificate
signature check (JNZ->JMP). Without that patch the client would verify the signature against a modulus
baked into the exe and reject this. The embedded profileid is drawn from the shared store so it matches
the identity GPCM issues for the same uniquenick.
"""
import re

from . import log

AUTH_NS = "http://gamespy.net/AuthService/"

# A REAL 1024-bit RSA keypair (modulus n, private exponent d; public exponent 65537). The game parses
# peerkeymodulus/peerkeyexponent/peerkeyprivate into bignums and uses them for the peer secure session,
# so a placeholder that is the wrong length or not a valid keypair makes the certificate parse/handshake
# fail *before* the (patched-out) signature check even runs -> AuthService fails -> ranked unavailable.
# One shared keypair is fine: peers encrypt to each other's public key, which round-trips against this d.
# The signature stays all-zero because the quick-patch neuters the client's signature verification.
PEER_MODULUS = ("DF1634EB0807695FEED8E3089F2D2F87BAD05C0545775CF9B505A2DA621A7D73"
                "87ADC0682312A0204E2251414063658D025EEB8AC0C064B998539C5B2A2D6BB9"
                "9D4BCF1A072726BFBB733D2DC83AE2138685B0D33E4216A60E2DA632924321E2"
                "830E3864FD684B8D588C40E187F5F17104D0FB3A43C91A8599ED504AD3169167")  # 128 bytes
PEER_PRIVATE = ("A78DDF4B13E9B52C779170DBFDEA0B43EF7D2550543F75969B6ED34520DFF28F"
                "7E3D734103EEAE53F53B733A062961918A514EAA1561AB8576327E423EA884BA"
                "2AAC5876E8F60AFB2759D9FCD016919CC5D306E6A7F587BDB66337AF4E31DA4E"
                "50DBD524EF09EE2348EFC00B82ACE4840E53BACC4B2596026259062FE6AE2F19")  # 128 bytes
SERVER_DATA = "5A" * 128                # 128 bytes
SIGNATURE = "00" * 128                  # 128 bytes (verify is patched out, so an unsigned cert passes)


class AuthService:
    def __init__(self, store):
        self._store = store

    def handle(self, head: str, body: str) -> str:
        for method in ("LoginUniqueNick", "LoginProfile", "LoginRemoteAuth"):
            if method in body:
                m = re.search(r"<(?:\w+:)?uniquenick>([^<]*)<", body)
                uniq = m.group(1) if m else "player"
                profileid = self._store.get_or_create_profile(uniq)
                log.log(f"    [auth] {method} uniquenick={uniq} profileid={profileid} -> cert (placeholder sig)")
                return self._cert_response(method, uniq, profileid)
        log.log("    [auth] unrecognised AuthService request -> empty OK")
        return _empty_ok()

    def _cert_response(self, method: str, uniq: str, profileid: int) -> str:
        fields = "".join([
            "<length>303</length>",
            "<version>1</version>",
            "<partnercode>0</partnercode>",
            "<namespaceid>80</namespaceid>",
            f"<userid>{profileid}</userid>",
            f"<profileid>{profileid}</profileid>",
            "<expiretime>0</expiretime>",
            f"<profilenick>{uniq}</profilenick>",
            f"<uniquenick>{uniq}</uniquenick>",
            "<cdkeyhash>00000000000000000000000000000000</cdkeyhash>",
            f"<peerkeymodulus>{PEER_MODULUS}</peerkeymodulus>",
            "<peerkeyexponent>010001</peerkeyexponent>",
            f"<serverdata>{SERVER_DATA}</serverdata>",
            f"<signature>{SIGNATURE}</signature>",
        ])
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
            '<soap:Body>'
            f'<{method}Response xmlns="{AUTH_NS}">'
            f'<{method}Result>'
            '<responseCode>0</responseCode>'
            f'<certificate>{fields}</certificate>'
            f'<peerkeyprivate>{PEER_PRIVATE}</peerkeyprivate>'
            f'</{method}Result>'
            f'</{method}Response>'
            '</soap:Body></soap:Envelope>'
        )


def _empty_ok() -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body/></soap:Envelope>')
