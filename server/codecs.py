"""GameSpy wire codecs: the \\final\\ key-value framing, the GPCM login proof, and the password
cipher used inside \\newuser\\ \\passenc\\.

The password cipher is GameSpy's `gslame`: GameSpy-base64 (with `[]_` standing in for `+/=`) XORed
against a MINSTD LCG stream seeded from the literal bytes 'gspy'. The XOR byte is `state % 0xff`
(modulo 255, NOT & 0xff) - that off-by-one is the whole trick and is verified against a known plaintext.
"""
import base64
import hashlib
import struct


def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("latin-1")).hexdigest()


def _gslame_next(num: int) -> int:
    # One step of GameSpy's MINSTD-flavoured LCG (multiplier 0x41a7), done in 16-bit halves
    # exactly as the SDK does it so the stream matches byte-for-byte.
    hi = (num >> 16) & 0xffff
    lo = num & 0xffff
    hi *= 0x41a7
    lo *= 0x41a7
    lo += ((hi & 0x7fff) << 16)
    lo += (hi >> 15)
    if lo >= 0x80000000:
        lo &= 0x7fffffff
        lo += 1
    return lo


def password_decode(enc: str) -> str:
    """Decode a GameSpy \\passenc\\ blob back to the plaintext password."""
    for a, b in (("[", "+"), ("]", "/"), ("_", "=")):
        enc = enc.replace(a, b)
    enc += "=" * ((4 - len(enc) % 4) % 4)
    data = bytearray(base64.b64decode(enc))
    num = struct.unpack("<L", b"gspy")[0]
    for i in range(len(data)):
        num = _gslame_next(num)
        data[i] ^= num % 0xff
    return data.decode("latin-1")


def parse_kv(msg: str) -> dict:
    r"""Parse a \k1\v1\k2\v2\final\ GameSpy message into a dict (values may be empty)."""
    parts = msg.split("\\")
    if parts and parts[0] == "":
        parts = parts[1:]
    out = {}
    i = 0
    while i + 1 < len(parts):
        out[parts[i]] = parts[i + 1]
        i += 2
    return out


def login_proof(password_md5: str, uniquenick: str, server_challenge: str, client_challenge: str) -> str:
    """Server's \\proof\\: MD5(md5(pwd) + 48 spaces + user + serverChal + clientChal + md5(pwd)).

    This is the client's `response` hash with the two challenges swapped - the client checks the
    first 32 bytes to confirm the server knows the password hash.
    """
    return md5_hex(password_md5 + " " * 48 + uniquenick + server_challenge + client_challenge + password_md5)


def login_ticket_for(profileid: int) -> str:
    """Mint the 24-char GPCM \\lt\\ login ticket, encoding the profileid in its first 8 hex chars.

    The game echoes this ticket verbatim to Sake/Competition. Encoding the profileid in it lets those
    services recover the authenticated owner with no shared session state - so a backend restart mid
    game does not orphan the player's writes."""
    return f"{profileid & 0xFFFFFFFF:08X}" + "0" * 16


def profileid_from_ticket(login_ticket: str) -> int:
    try:
        return int((login_ticket or "")[:8], 16)
    except ValueError:
        return 0
