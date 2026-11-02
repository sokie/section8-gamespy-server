"""Read-only queries behind the stats portal, one PortalData per game.

Each query opens its own read-only connection, so the portal never blocks a write by the game services.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import stats

# A dedicated server rewrites its status record while it runs. The portal shows an older record as offline.
SERVER_STALE_SECONDS = 300
# Seconds that the portal keeps the player and server lists, so one page reads them once.
CACHE_SECONDS = 5
# A match with this csid holds XP carried over from an older database.
CARRIED_OVER = "migration"

SORTS = {
    "xp": ("XP", lambda p: p["xp"]),
    "kills": ("Kills", lambda p: p["kills"]),
    "kd": ("K/D", lambda p: p["kd"]),
    "time": ("Time played", lambda p: p["time_played"]),
    "matches": ("Matches", lambda p: p["matches"]),
}

CAREER_COLUMNS = (
    ("Ranked_Kills", "Kills"),
    ("Ranked_Deaths", "Deaths"),
    ("Ranked_Destruction", "Destructions"),
    ("Ranked_ScoreCombat", "Combat score"),
    ("Ranked_ScoreTeamwork", "Teamwork score"),
)
GEAR_STATS = ("TimePlayed", "Kills", "Destruction", "KillsIAL", "DamageIAL", "DestructionIAL")

# URL prefix, short name, server status table and profile table of each title, as the game names them.
PAGES = {
    "prejudice": ("", "Prejudice", "ServerStatusTG09_v6", "PlayerProfile2_v6"),
    "section8": ("/s8", "Section 8", "ServerStatus_v5", "PlayerProfile_v5"),
}


@dataclass
class Game:
    key: str
    name: str
    short: str
    prefix: str
    player_table: str
    server_table: str
    profile_table: str
    catalog: stats.Catalog = field(repr=False)
    # (table, column) where another title writes its level for this game's unlocks.
    linked_level: tuple[str, str] | None = None


def games_for(titles) -> list[Game]:
    """Builds one Game for each title that has portal pages."""
    games = {}
    for t in titles:
        if t.key in PAGES:
            prefix, short, server, profile = PAGES[t.key]
            games[t.key] = Game(
                t.key, t.name, short, prefix, titles.player_table(t), t.table(server), t.table(profile), t.catalog
            )
    for t in titles:
        link = t.level_link
        if link and link["title"] in games:
            games[link["title"]].linked_level = (titles.get(link["title"]).table(link["table"]), link["column"])
    return list(games.values())


def connect_ro(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def set_display_name(db_path: str, who: str, name: str | None) -> int | None:
    """Sets the portal name of a player, found by uniquenick or profileid. None clears it. Gives the
    profileid, or None when no player matches. Opens no Store, so it runs no migration."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        row = conn.execute(
            "SELECT profileid FROM profiles WHERE uniquenick=? COLLATE NOCASE OR CAST(profileid AS TEXT)=?",
            (who, who),
        ).fetchone()
        if row is None:
            return None
        conn.execute("CREATE TABLE IF NOT EXISTS portal_names (profileid INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        if name:
            conn.execute(
                "INSERT INTO portal_names(profileid, name) VALUES(?,?) "
                "ON CONFLICT(profileid) DO UPDATE SET name=excluded.name",
                (row[0], name),
            )
        else:
            conn.execute("DELETE FROM portal_names WHERE profileid=?", (row[0],))
        conn.commit()
        return row[0]
    finally:
        conn.close()


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _date(v) -> str | None:
    if not v or str(v).startswith("0001-"):
        return None
    return str(v)


class PortalData:
    def __init__(self, db_path: str, names: dict | None, game: Game):
        self._db_path = db_path
        self.game = game
        self.cat = game.catalog
        self._config_names = {str(k).lower(): str(v) for k, v in (names or {}).items() if v}
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, object]] = {}
        modes = self.cat.game_modes
        self._only_mode = next(iter(modes.values())) if len(modes) == 1 else None
        self._gear_stats = [k for k in GEAR_STATS if any(c.startswith(f"Ranked_{k}_G") for c in self.cat.by_column)]

    # A connection per call, because the request threads cannot share one.
    @contextmanager
    def _conn(self):
        conn = connect_ro(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _cached(self, key: str, build):
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        value = build()
        with self._lock:
            self._cache[key] = (now, value)
        return value

    @staticmethod
    def _has_table(conn, name: str) -> bool:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None

    @staticmethod
    def _has_title(conn) -> bool:
        return "title" in [c[1] for c in conn.execute("PRAGMA table_info(matches)")]

    def _profiles(self, conn) -> dict[int, tuple[str, str]]:
        """Gives {profileid: (display name, uniquenick)}."""
        shown = {}
        if self._has_table(conn, "portal_names"):
            shown = {
                r["profileid"]: r["name"] for r in conn.execute("SELECT profileid, name FROM portal_names") if r["name"]
            }
        out = {}
        for r in conn.execute("SELECT profileid, uniquenick FROM profiles"):
            pid, nick = r["profileid"], r["uniquenick"]
            name = self._config_names.get(str(pid)) or self._config_names.get(str(nick).lower()) or shown.get(pid)
            out[pid] = (name or nick, nick)
        return out

    def _fields(self, conn, table: str, owner: int | None = None, names=None) -> dict[int, dict[str, str]]:
        """Gives {owner: {field: value}} for a table, from the first record of each owner."""
        sql = "SELECT owner_id, record_id, name, value FROM fields WHERE table_id=?"
        args: list = [table]
        if owner is not None:
            sql += " AND owner_id=?"
            args.append(owner)
        if names:
            sql += f" AND name IN ({','.join('?' * len(names))})"
            args.extend(names)
        out: dict[int, dict[str, str]] = {}
        first_record: dict[int, int] = {}
        for r in conn.execute(sql + " ORDER BY owner_id, record_id", args):
            rid = first_record.setdefault(r["owner_id"], r["record_id"])
            if r["record_id"] == rid:
                out.setdefault(r["owner_id"], {})[r["name"]] = r["value"]
        return out

    def _match_filter(self, conn) -> tuple[str, list]:
        """Gives the SQL condition on matches m that selects this game's played matches. A database with no
        matches.title holds only Prejudice's matches."""
        cond = f"m.csid IS NOT '{CARRIED_OVER}'"
        if self._has_title(conn):
            return cond + " AND m.title=?", [self.game.key]
        if self.game.key != "prejudice":
            return "0", []
        return cond, []

    def _match_counts(self, conn, pid: int | None = None) -> dict[int, int]:
        if not (self._has_table(conn, "match_players") and self._has_table(conn, "matches")):
            return {}
        cond, args = self._match_filter(conn)
        if pid is not None:
            cond, args = cond + " AND p.profileid=?", [*args, pid]
        sql = (
            "SELECT p.profileid, COUNT(*) FROM match_players p JOIN matches m ON m.match_id=p.match_id "
            f"WHERE p.profileid IS NOT NULL AND {cond} GROUP BY p.profileid"
        )
        return {r[0]: r[1] for r in conn.execute(sql, args)}

    def _summary_row(self, pid: int, f: dict, profiles: dict, matches: dict) -> dict:
        xp = _int(f.get(self.cat.xp_column))
        level = self.cat.level_for_xp(xp) if self.cat.level_xp else _int(f.get(self.cat.level_column)) or 1
        kills, deaths = _int(f.get("Ranked_Kills")), _int(f.get("Ranked_Deaths"))
        name, nick = profiles.get(pid, (str(pid), None))
        return {
            "profileid": pid,
            "name": name,
            "uniquenick": nick,
            "xp": xp,
            "level": level,
            "rank": self.cat.rank_for_level(level),
            "kills": kills,
            "deaths": deaths,
            "kd": round(kills / deaths, 2) if deaths else float(kills),
            "time_played": _int(f.get("Ranked_TimePlayed")),
            "matches": matches.get(pid, 0),
        }

    def players(self) -> list[dict]:
        return self._cached("players", self._read_players)

    def _read_players(self) -> list[dict]:
        with self._conn() as conn:
            profiles = self._profiles(conn)
            matches = self._match_counts(conn)
            wanted = [self.cat.xp_column, self.cat.level_column, "Ranked_Kills", "Ranked_Deaths", "Ranked_TimePlayed"]
            records = self._fields(conn, self.game.player_table, names=wanted)
        return [self._summary_row(pid, f, profiles, matches) for pid, f in records.items()]

    def exists(self, pid: int) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM profiles WHERE profileid=?", (pid,)).fetchone() is not None

    def has_career(self, pid: int) -> bool:
        with self._conn() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM fields WHERE table_id=? AND owner_id=? LIMIT 1", (self.game.player_table, pid)
                ).fetchone()
                is not None
            )

    def leaderboard(self, sort: str = "xp", offset: int = 0, limit: int = 50) -> dict:
        sort = sort if sort in SORTS else "xp"
        rows = [dict(p) for p in self.players()]
        rows.sort(key=lambda p: (SORTS[sort][1](p), p["xp"]), reverse=True)
        for i, row in enumerate(rows, 1):
            row["position"] = i
        return {
            "game": self.game.key,
            "sort": sort,
            "sort_label": SORTS[sort][0],
            "total": len(rows),
            "offset": offset,
            "rows": rows[offset : offset + limit],
        }

    def search(self, query: str) -> list[dict]:
        q = (query or "").strip().lower()
        if not q:
            return []
        return [
            p
            for p in self.players()
            if q in p["name"].lower() or q in (p["uniquenick"] or "").lower() or q == str(p["profileid"])
        ][:50]

    def player(self, pid: int) -> dict | None:
        cat = self.cat
        with self._conn() as conn:
            profiles = self._profiles(conn)
            if pid not in profiles:
                return None
            f = self._fields(conn, self.game.player_table, owner=pid).get(pid, {})
            profile = self._fields(conn, self.game.profile_table, owner=pid).get(pid, {})
            history = self._history(conn, pid)
            match_count = self._match_counts(conn, pid).get(pid, 0)
            linked = {}
            if self.game.linked_level:
                table, column = self.game.linked_level
                linked = self._fields(conn, table, owner=pid, names=[column]).get(pid, {})
        out = self._summary_row(pid, f, profiles, {pid: match_count})
        out["game"] = self.game.key
        out["has_stats"] = bool(f)
        clan = profile.get("Profile_ClanTag") or f.get("Ranked_clantag")
        out["clan_tag"] = clan if clan not in (None, "", "0") else None  # The client writes 0 for no tag.
        out["section8_level"] = _int(next(iter(linked.values()), None)) or None
        level = out["level"]
        table = cat.level_xp
        out["level_floor"] = table[level - 1] if 0 < level <= len(table) else 0
        out["level_next"] = table[level] if 0 < level < len(table) else None
        out["career"] = [
            {"column": col, "label": label, "value": _int(f.get(col))}
            for col, label in CAREER_COLUMNS
            if col in cat.by_column
        ]
        out["records"] = [
            {"label": s.label, "value": _int(f.get(s.column)), "date": _date(f.get(cat.date_column.get(s.column, "")))}
            for s in cat.by_key.values()
            if s.family == "career" and s.fold == "max"
        ]
        out["modes"] = [
            {"id": gm, "name": name, "seconds": _int(f.get(f"Ranked_TimePlayed_GM{gm}"))}
            for gm, name in cat.game_modes.items()
            if gm != 0 and f"Ranked_TimePlayed_GM{gm}" in cat.by_column
        ]
        out["badges"] = [
            {
                "id": i,
                "name": b[0],
                "category": b[1],
                "description": b[2].replace("%%", "%"),
                "count": _int(f.get(f"Ranked_Badge_B{i}")),
                "date": _date(f.get(f"Ranked_Badge_Date_B{i}")),
            }
            for i, b in sorted(cat.badges.items())
        ]
        out["feats"] = [
            {
                "id": i,
                "name": ft[0],
                "category": ft[1],
                "description": ft[2].replace("%%", "%"),
                "count": _int(f.get(f"Ranked_Feats_F{i}")),
                "tiers": [_int(f.get(f"Ranked_Feats{t}_F{i}")) for t in (1, 2, 3)],
                "date": _date(f.get(f"Ranked_Feats_Date_F{i}")),
            }
            for i, ft in sorted(cat.feats.items())
        ]
        gear = []
        for g, item in cat.gear.items():
            weapon, mod = (list(item) + ["", ""])[:2]
            row = {k: _int(f.get(f"Ranked_{k}_G{g}")) for k in self._gear_stats}
            if any(row.values()):
                gear.append({"id": g, "weapon": weapon, "mod": mod, **row})
        gear.sort(key=lambda r: (-r.get("Kills", 0), -r.get("TimePlayed", 0)))
        out["gear_stats"] = self._gear_stats
        out["gear"] = gear
        out["history"] = history
        return out

    def _history(self, conn, pid: int, limit: int = 25) -> list[dict]:
        if not self._has_table(conn, "matches"):
            return []
        has_title = self._has_title(conn)
        if not has_title and self.game.key != "prejudice":
            return []
        sql = (
            "SELECT m.match_id, m.received_at, m.game_mode_name, m.player_count, p.xp_gained, p.level_after, m.csid "
            "FROM match_players p JOIN matches m ON m.match_id=p.match_id WHERE p.profileid=?"
        )
        args: list = [pid]
        if has_title:
            sql += " AND m.title=?"
            args.append(self.game.key)
        rows = conn.execute(sql + " ORDER BY m.match_id DESC LIMIT ?", (*args, limit)).fetchall()
        return [
            {
                "match_id": r["match_id"],
                "date": r["received_at"],
                "mode": "XP carried over from an older database"
                if r["csid"] == CARRIED_OVER
                else (r["game_mode_name"] or self._only_mode),
                "carried_over": r["csid"] == CARRIED_OVER,
                "players": r["player_count"],
                "xp_gained": r["xp_gained"],
                "level_after": r["level_after"],
            }
            for r in rows
        ]

    def servers(self) -> list[dict]:
        return self._cached("servers", self._read_servers)

    def _read_servers(self) -> list[dict]:
        now = time.time()
        table = self.game.server_table
        with self._conn() as conn:
            profiles = self._profiles(conn)
            records = self._fields(conn, table)
            touched: dict[int, float] = {}
            if self._has_table(conn, "record_times"):
                for r in conn.execute(
                    "SELECT owner_id, MAX(updated_at) FROM record_times WHERE table_id=? GROUP BY owner_id", (table,)
                ):
                    touched[r[0]] = r[1]
        modes = self.cat.game_modes
        out = []
        for owner, f in records.items():
            updated = touched.get(owner)
            age = now - updated if updated else None
            mode = f.get("Status_GameMode")
            out.append(
                {
                    "owner": owner,
                    "host": profiles.get(owner, (str(owner),))[0],
                    "mode": modes.get(_int(mode), mode) if mode not in (None, "") else self._only_mode,
                    "max_humans": _int(f.get("Status_MaxHumans")) or None,
                    "players": _int(f.get("Status_NumPlayers")) if "Status_NumPlayers" in f else None,
                    "ranked": (_int(f.get("Status_Ranked")) == 1) if "Status_Ranked" in f else None,
                    "updated": datetime.fromtimestamp(updated, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if updated
                    else None,
                    "age_seconds": int(age) if age is not None else None,
                    "online": age is not None and age < SERVER_STALE_SECONDS,
                    "fields": dict(sorted(f.items())),
                }
            )
        out.sort(key=lambda s: (not s["online"], s["age_seconds"] if s["age_seconds"] is not None else 1e12))
        return out

    def summary(self) -> dict:
        players = self.players()
        servers = self.servers()
        with self._conn() as conn:
            total_matches = 0
            if self._has_table(conn, "matches"):
                cond, args = self._match_filter(conn)
                total_matches = conn.execute(f"SELECT COUNT(*) FROM matches m WHERE {cond}", args).fetchone()[0]
        return {
            "game": self.game.key,
            "players": len(players),
            "matches": total_matches,
            "servers_online": sum(1 for s in servers if s["online"]),
            "total_kills": sum(p["kills"] for p in players),
            "total_seconds": sum(p["time_played"] for p in players),
        }
