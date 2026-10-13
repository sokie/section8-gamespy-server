from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from . import statdefs
from .screport import KEY_INT64, KEY_STRING


class StatDef(NamedTuple):
    key: int
    property: int
    column: str
    fold: str
    family: str
    item: int | None  # the gear, badge, feat or game mode id
    label: str
    sake_type: str
    date_of: str | None
    read_by_game: bool


BY_KEY = {row[0]: StatDef(*row) for row in statdefs.STATS}
BY_COLUMN = {s.column: s for s in BY_KEY.values()}
DATE_COLUMN = {s.date_of: s.column for s in BY_KEY.values() if s.fold == "date"}

XP_COLUMN = "Ranked_xp"
LEVEL_COLUMN = "Ranked_Level"
SAKE_DATE_ZERO = "0001-01-01T00:00:00"

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def sake_type(column: str):
    s = BY_COLUMN.get(column)
    if s is None:
        return None
    return {"int": "intValue", "date": "dateAndTimeValue", "string": "asciiStringValue"}[s.sake_type]


def filetime_to_iso(ticks: int) -> str:
    if not ticks:
        return SAKE_DATE_ZERO
    try:
        return (_FILETIME_EPOCH + timedelta(microseconds=ticks // 10)).strftime("%Y-%m-%dT%H:%M:%S")
    except (OverflowError, ValueError):
        return SAKE_DATE_ZERO


def iso_to_filetime(text: str) -> int:
    try:
        dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return 0
    if text == SAKE_DATE_ZERO:
        return 0
    delta = dt - _FILETIME_EPOCH
    return (delta.days * 86400 + delta.seconds) * 10_000_000 + delta.microseconds * 10


def keys_from_stored(values: dict) -> dict:
    keys = {}
    for key, text in values.items():
        s = BY_KEY.get(key)
        if s is not None and s.fold == "date":
            keys[key] = (KEY_INT64, iso_to_filetime(text))
        elif s is not None and s.sake_type == "string":
            keys[key] = (KEY_STRING, text)
        else:
            try:
                keys[key] = (0, int(text))
            except (TypeError, ValueError):
                keys[key] = (KEY_STRING, text)
    return keys


def rebuild_career(matches: list[dict]) -> dict:
    """Folds a career from zero. The matches must be oldest first."""
    career: dict = {}
    for values in matches:
        result = fold_match(career, keys_from_stored(values))
        career.update(result.updates)
    return career


def level_for_xp(xp: int) -> int:
    """Gives the level as the game's TGRankManager.GetLevelFromXP does."""
    for level, needed in enumerate(statdefs.LEVEL_XP):
        if xp < needed:
            return level
    return len(statdefs.LEVEL_XP)


def rank_for_level(level: int) -> str:
    """Gives the rank name as the game's TGRankManager.CalcRankFromLevel does."""
    name = statdefs.RANKS[0][1]
    for needed, rank in statdefs.RANKS:
        if needed > level:
            break
        name = rank
    return name


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class MatchResult:
    __slots__ = ("updates", "match_values", "notes", "xp_gained", "level")

    def __init__(self):
        self.updates: dict[str, tuple[str, str]] = {}
        self.match_values: list[tuple[int, str, str]] = []
        self.notes: list[str] = []
        self.xp_gained = 0
        self.level: int | None = None


def fold_match(career: dict[str, tuple[str, str]], keys: dict[int, tuple[int, object]]) -> MatchResult:
    """Folds one player's report keys onto their career and returns the fields to write."""
    out = MatchResult()
    applied = set()

    def current(column):
        return out.updates.get(column, career.get(column, (None, None)))[1]

    for key, (wire_type, value) in sorted(keys.items()):
        s = BY_KEY.get(key)
        if s is None:
            column = f"Unmapped_Key{key}"
            vtype = {KEY_INT64: "int64Value", KEY_STRING: "asciiStringValue"}.get(wire_type, "intValue")
            out.updates[column] = (vtype, str(value))
            out.match_values.append((key, column, str(value)))
            out.notes.append(f"key {key} is not in the catalogue, kept as {column}")
            continue
        if s.fold == "date":
            out.match_values.append((key, s.column, filetime_to_iso(value)))
            continue
        out.match_values.append((key, s.column, str(value)))
        if s.sake_type == "string" or wire_type == KEY_STRING:
            text = str(value)
            out.updates[s.column] = ("asciiStringValue" if text.isascii() else "unicodeStringValue", text)
            applied.add(s.column)
            continue
        old = current(s.column)
        if s.fold == "sum":
            new = _int(old) + _int(value)
        elif s.fold == "max":
            if old is not None and _int(value) <= _int(old):
                continue
            new = _int(value)
        else:
            new = _int(value)
        out.updates[s.column] = ("intValue", str(new))
        applied.add(s.column)

    for key, (_wire_type, value) in keys.items():
        s = BY_KEY.get(key)
        if s is None or s.fold != "date" or s.date_of not in applied or not value:
            continue
        date = filetime_to_iso(value)
        # A record keeps the date it was set. A total keeps the later date, so an old report cannot move it back.
        if BY_COLUMN[s.date_of].fold != "max" and (current(s.column) or "") > date:
            continue
        out.updates[s.column] = ("dateAndTimeValue", date)

    # Derive the level from career XP, so it always agrees with the XP that Sake returns.
    out.xp_gained = _int(keys.get(BY_COLUMN[XP_COLUMN].key, (0, 0))[1])
    level = level_for_xp(_int(current(XP_COLUMN)))
    out.level = level
    reported = keys.get(BY_COLUMN[LEVEL_COLUMN].key)
    if reported is not None and _int(reported[1]) != level:
        out.notes.append(f"reported level {reported[1]} differs from level {level} for the career XP")
    if _int(current(LEVEL_COLUMN)) != level:
        out.updates[LEVEL_COLUMN] = ("intValue", str(level))
    return out


def game_mode_of(players_keys) -> int | None:
    best, mode = 0, None
    for keys in players_keys:
        for key, (_t, value) in keys.items():
            s = BY_KEY.get(key)
            if s is not None and s.family == "game_mode" and s.fold == "sum" and _int(value) > best:
                best, mode = _int(value), s.item
    return mode


def describe(column: str) -> str:
    s = BY_COLUMN.get(column)
    return s.label if s else column
