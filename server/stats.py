"""Ranked stats: the report-key catalogue of a title and the career fold.

The module-level names (BY_KEY, fold_match, level_for_xp, ...) use Prejudice's catalogue.
"""

import json
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


SAKE_DATE_ZERO = "0001-01-01T00:00:00"
UNMAPPED_PREFIX = "Unmapped_Key"

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


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


class Catalog:
    def __init__(
        self,
        rows=(),
        level_xp=(),
        ranks=(),
        game_modes=None,
        badges=None,
        feats=None,
        gear=None,
        xp_column="Ranked_xp",
        level_column="Ranked_Level",
        player_table=None,
        name="",
    ):
        self.name = name
        self.by_key = {row[0]: StatDef(*row) for row in rows}
        self.by_column = {s.column: s for s in self.by_key.values()}
        self.date_column = {s.date_of: s.column for s in self.by_key.values() if s.fold == "date"}
        self.level_xp = tuple(level_xp or ())
        self.ranks = tuple(tuple(r) for r in (ranks or ()))
        self.game_modes = dict(game_modes or {})
        self.badges = dict(badges or {})
        self.feats = dict(feats or {})
        self.gear = dict(gear or {})
        self.xp_column = xp_column
        self.level_column = level_column
        self.player_table = player_table

    @classmethod
    def empty(cls, name=""):
        return cls(name=name)

    @classmethod
    def from_json(cls, path: str):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        rows = []
        for s in data.get("stats", []):
            rows.append(
                (
                    int(s["key"]),
                    int(s.get("property", -1)),
                    s["column"],
                    s.get("fold", "replace"),
                    s.get("family", "career"),
                    s.get("index", s.get("item")),
                    s.get("label", s["column"]),
                    s.get("sake_type", "int"),
                    s.get("date_of"),
                    bool(s.get("read_by_game", True)),
                )
            )

        def int_keys(d):
            return {int(k): (tuple(v) if isinstance(v, list) else v) for k, v in (d or {}).items()}

        return cls(
            rows,
            data.get("level_xp", ()),
            data.get("ranks", ()),
            int_keys(data.get("game_modes")),
            int_keys(data.get("badges")),
            int_keys(data.get("feats")),
            int_keys(data.get("gear")),
            data.get("xp_column", "Ranked_xp"),
            data.get("level_column", "Ranked_Level"),
            data.get("player_table"),
            data.get("title", ""),
        )

    @property
    def has_levels(self) -> bool:
        return bool(self.level_xp) and self.xp_column in self.by_column

    def sake_type(self, column: str):
        s = self.by_column.get(column)
        if s is None:
            return None
        return {"int": "intValue", "date": "dateAndTimeValue", "string": "asciiStringValue"}.get(
            s.sake_type, "intValue"
        )

    def level_for_xp(self, xp: int) -> int:
        """Gives the level for a career XP."""
        for level, needed in enumerate(self.level_xp):
            if xp < needed:
                return level
        return len(self.level_xp)

    def rank_for_level(self, level) -> str:
        """Gives the rank name for a level."""
        if not self.ranks:
            return ""
        name = self.ranks[0][1]
        for needed, rank in self.ranks:
            if needed > (level or 0):
                break
            name = rank
        return name

    def describe(self, column: str) -> str:
        s = self.by_column.get(column)
        return s.label if s else column

    def keys_from_stored(self, values: dict) -> dict:
        keys = {}
        for key, text in values.items():
            s = self.by_key.get(key)
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

    def rebuild_career(self, matches: list[dict]) -> dict:
        """Folds a career from zero. The matches must be oldest first."""
        career: dict = {}
        for values in matches:
            result = self.fold_match(career, self.keys_from_stored(values))
            career.update(result.updates)
        return career

    def fold_match(self, career: dict[str, tuple[str, str]], keys: dict[int, tuple[int, object]]) -> MatchResult:
        """Folds one player's report keys onto their career and returns the fields to write."""
        out = MatchResult()
        applied = set()

        def current(column):
            return out.updates.get(column, career.get(column, (None, None)))[1]

        for key, (wire_type, value) in sorted(keys.items()):
            s = self.by_key.get(key)
            if s is None:
                column = f"{UNMAPPED_PREFIX}{key}"
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
            s = self.by_key.get(key)
            if s is None or s.fold != "date" or s.date_of not in applied or not value:
                continue
            date = filetime_to_iso(value)
            # A record keeps the date it was set. A total keeps the later date, so an old report cannot move it back.
            if self.by_column[s.date_of].fold != "max" and (current(s.column) or "") > date:
                continue
            out.updates[s.column] = ("dateAndTimeValue", date)

        xp_def = self.by_column.get(self.xp_column)
        if xp_def is not None:
            out.xp_gained = _int(keys.get(xp_def.key, (0, 0))[1])
        level_def = self.by_column.get(self.level_column)
        if self.has_levels:
            # Derive the level from career XP, so it always agrees with the XP that Sake returns.
            level = self.level_for_xp(_int(current(self.xp_column)))
            out.level = level
            reported = keys.get(level_def.key) if level_def is not None else None
            if reported is not None and _int(reported[1]) != level:
                out.notes.append(f"reported level {reported[1]} differs from level {level} for the career XP")
            if _int(current(self.level_column)) != level:
                out.updates[self.level_column] = ("intValue", str(level))
        elif level_def is not None and current(self.level_column) is not None:
            # With no level table, keep the level that the client reports.
            out.level = _int(current(self.level_column))
        return out

    def game_mode_of(self, players_keys) -> int | None:
        best, mode = 0, None
        for keys in players_keys:
            for key, (_t, value) in keys.items():
                s = self.by_key.get(key)
                if s is not None and s.family == "game_mode" and s.fold == "sum" and _int(value) > best:
                    best, mode = _int(value), s.item
        return mode


PREJUDICE = Catalog(
    statdefs.STATS,
    statdefs.LEVEL_XP,
    statdefs.RANKS,
    statdefs.GAME_MODES,
    statdefs.BADGES,
    statdefs.FEATS,
    statdefs.GEAR,
    name="prejudice",
)

BY_KEY = PREJUDICE.by_key
BY_COLUMN = PREJUDICE.by_column
DATE_COLUMN = PREJUDICE.date_column
XP_COLUMN = PREJUDICE.xp_column
LEVEL_COLUMN = PREJUDICE.level_column
sake_type = PREJUDICE.sake_type
keys_from_stored = PREJUDICE.keys_from_stored
rebuild_career = PREJUDICE.rebuild_career
level_for_xp = PREJUDICE.level_for_xp
rank_for_level = PREJUDICE.rank_for_level
fold_match = PREJUDICE.fold_match
game_mode_of = PREJUDICE.game_mode_of
describe = PREJUDICE.describe
