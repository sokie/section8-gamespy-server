"""Maps the SC report's write-keyids to Sake `PlayerStats_v6` columns, and derives the columns the
report never carries (xp, Level, Rank).

Two keyid namespaces exist and only partly overlap. The READ ids are the OnlineStatsRead `ColumnIds`
behind each `Ranked_<Name>` column (see docs/playerstats_v6_fields.txt); the WRITE ids are what the
game stamps into the SubmitReport blob, and past the base score range they diverge from the read ids.
Only the XP delta below is resolved, so every other key is kept verbatim as `Ranked_wkey_<id>` and no
false column name is asserted.
"""

import math
from datetime import datetime, timedelta, timezone

# Keyid 11 carries the XP earned THIS ROUND, not a career total, so career Ranked_xp is a running sum.
# Verified against the game's own counter: a two-round run gained 37 XP and reported 26 then 11.
XP_DELTA_KEYID = 11

DERIVED_FIELDS = ("Ranked_xp", "Ranked_XPIAL", "Ranked_Level", "Ranked_Rank")

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def field_for_keyid(keyid: int) -> str:
    return f"Ranked_wkey_{keyid}"


def filetime_to_iso(ticks: int) -> str:
    """Windows FILETIME (100-ns ticks since 1601 UTC) -> Sake dateAndTime string. 0 -> the SDK's zero."""
    if not ticks:
        return "0001-01-01T00:00:00"
    try:
        dt = _FILETIME_EPOCH + timedelta(microseconds=(ticks & 0xFFFFFFFFFFFFFFFF) / 10)
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "0001-01-01T00:00:00"


def sake_type_for(field: str) -> str:
    if field.endswith("_Date") or "_Date_" in field or field == "Ranked_Locale":
        return "dateAndTimeValue" if ("Date" in field) else "asciiStringValue"
    if field == "Ranked_ClanTag":
        return "asciiStringValue"
    return "intValue"


# --- Level / Rank derivation from the accumulated XP -----------------------------------------------
# The client computes the level it DISPLAYS from Ranked_xp itself, using a table we do not have. The
# stored Ranked_Level only backs the S8Level_v6 leaderboard sort, so this proxy curve, XP(L) = A*L^2 -
# B*L, is fitted to the low-level thresholds seen in play (213 XP -> level 5). It drifts at the top end.
LEVEL_CURVE_A = 7.8
LEVEL_CURVE_B = 5.0
MAX_LEVEL = 100


def xp_for_level(level: int) -> int:
    """Cumulative XP required to reach `level` (the curve above)."""
    return int(LEVEL_CURVE_A * level * level - LEVEL_CURVE_B * level)


def level_for_xp(xp: int) -> int:
    """Highest level whose cumulative XP threshold is <= xp (inverse of the curve)."""
    if xp <= 0:
        return 1
    lvl = int((LEVEL_CURVE_B + math.sqrt(LEVEL_CURVE_B**2 + 4 * LEVEL_CURVE_A * xp)) / (2 * LEVEL_CURVE_A))
    return max(1, min(MAX_LEVEL, lvl))


def progression_for_xp(total_xp: int) -> dict:
    """Given a player's accumulated career XP, return the derived display columns."""
    total_xp = max(0, int(total_xp))
    level = level_for_xp(total_xp)
    return {
        "Ranked_xp": total_xp,
        "Ranked_XPIAL": total_xp,
        "Ranked_Level": level,
        "Ranked_Rank": level,
    }
