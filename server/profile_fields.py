"""Values in the Prejudice player profile record (PlayerProfile2_v6): the link code and the clan tag.

A link code such as 758B-338A-154D is the GameSpy profile id XOR LINK_CODE_KEY, then the random number
that the game writes to Profile_RandomLinkCode. The game makes a new one each time its Stats Portal screen
opens.
"""

from __future__ import annotations

import re

PROFILE_TABLE = "PlayerProfile2_v6"
RANDOM_FIELD = "Profile_RandomLinkCode"
USED_FIELD = "Profile_LinkCodeUsed"
CLAN_TAG_FIELD = "Profile_ClanTag"

LINK_CODE_KEY = 0x7513A50B
_LINK_CODE = re.compile(r"([0-9A-F]{4})-?([0-9A-F]{4})-?([0-9A-F]{4})")

CLAN_TAG_MAX = 3
# The game refuses these tags for a player.
RESERVED_CLAN_TAGS = ("TG",)


def format_link_code(profileid: int, random_code: int) -> str:
    x = (profileid ^ LINK_CODE_KEY) & 0xFFFFFFFF
    return f"{x >> 16:04X}-{x & 0xFFFF:04X}-{random_code & 0xFFFF:04X}"


def parse_link_code(text: str) -> tuple[int, int] | None:
    """Gives (profileid, random part) of a link code, or None if the text is not one."""
    m = _LINK_CODE.fullmatch(re.sub(r"\s+", "", text or "").upper())
    if not m:
        return None
    hi, lo, random_code = (int(g, 16) for g in m.groups())
    return ((hi << 16) | lo) ^ LINK_CODE_KEY, random_code


def sake_int(value: int) -> tuple[str, str]:
    """Gives (Sake type, value) of an integer: a byte when it fits in one, else an int."""
    return ("byteValue" if 0 <= value <= 0xFF else "intValue", str(value))


def clan_tag_error(tag: str) -> str | None:
    """Gives why a clan tag is refused, or None. An empty tag removes the tag."""
    if len(tag) > CLAN_TAG_MAX:
        return f"A clan tag has at most {CLAN_TAG_MAX} characters."
    if tag and not (tag.isascii() and tag.isalnum()):
        return "A clan tag has only the letters A-Z and the digits 0-9."
    if tag.upper() in RESERVED_CLAN_TAGS:
        return f"The clan tag {tag} is reserved."
    return None


def clan_tag_to_int(tag: str) -> int:
    """Gives the tag's ASCII bytes as a little-endian integer."""
    return int.from_bytes(tag.encode("ascii"), "little")


def clan_tag_from_value(value) -> str | None:
    """Gives the clan tag in a stored Profile_ClanTag or Ranked_ClanTag value, or None for no tag."""
    text = str(value if value is not None else "").strip()
    if text in ("", "0"):
        return None
    if not text.lstrip("-").isdigit():
        return text[:CLAN_TAG_MAX]
    raw = (int(text) & 0xFFFFFFFF).to_bytes(4, "little").rstrip(b"\0")
    tag = raw.decode("latin-1")
    return tag if tag.isascii() and tag.isalnum() else None
