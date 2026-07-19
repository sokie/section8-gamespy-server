"""Mapping between the SC report's write-keyids and the Sake `PlayerStats_v6` columns the game reads,
plus the server-side derivation of the columns the report never carries (xp / Level / Rank).

Two keyid namespaces exist and only partly overlap:
  * READ  - the columns the game asks Sake for are `Ranked_<Name>`; their ids are the OnlineStatsRead
            `ColumnIds` (see docs/playerstats_v6_fields.txt, derived from S9Game.u).
  * WRITE - the ids the game stamps into the SC SubmitReport blob. The base score keys coincide with
            the read ids (confirmed against an in-game scoreboard: Combat/Teamwork), but the per-mode
            "group" variants use ids that do NOT line up with the read ColumnIds.

So we map the confirmed write keys to their real `Ranked_` column and keep every other reported key
verbatim under `Ranked_wkey_<id>` - nothing is dropped, and unresolved keys can be named later from a
PvP capture (different gamemode bucket + kills/deaths in play) without changing the storage.
"""
from datetime import datetime, timedelta, timezone

# The report ships a PER-ROUND XP delta in keyid 11, NOT a Sake column value. Verified twice against the
# game's own XP counter (non-circular): a single-round run gained 12 XP and carried keyid 11 = 12; a
# two-round run gained 37 XP and carried keyid 11 = 26 then 11 (26 + 11 = 37). So career Ranked_xp is the
# running SUM of keyid 11 across every report - the accumulation the original ATLAS server performed.
XP_DELTA_KEYID = 11

# No other write-keyid maps to a Sake column with any confidence: the values don't match the in-game
# scoreboard (they are neither the displayed Combat/Teamwork nor a clean cumulative), and the write ids
# past the base range diverge from the read ColumnIds. keyid 281 was a wrong guess for XP (it read 80 on a
# match that awarded 12). Everything except the XP delta is stored verbatim as Ranked_wkey_<id> so nothing
# is lost and no false column is asserted.
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
# The client displays XP straight from Ranked_xp and computes the *displayed* level itself from a
# client-side table, so our stored Ranked_Level only needs to agree with what the player sees (it backs
# the S8Level_v6 leaderboard sort). level = inverse of XP(L) = A*L^2 - B*L. A=7.8 is tuned to the game's
# own low-level thresholds observed in play (213 XP -> Level 5, ~L6 at 250 XP); it drifts a few levels at
# the high end (the real curve is a custom table, not a formula) but that only affects the proxy sort.
import math

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
    lvl = int((LEVEL_CURVE_B + math.sqrt(LEVEL_CURVE_B ** 2 + 4 * LEVEL_CURVE_A * xp)) / (2 * LEVEL_CURVE_A))
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
