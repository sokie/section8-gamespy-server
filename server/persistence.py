import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import statdefs, stats

SCHEMA_VERSION = 2

_PLAYER_STATS = "PlayerStats_v6"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    table_id   TEXT    NOT NULL,
    owner_id   INTEGER NOT NULL,
    record_id  INTEGER NOT NULL,
    PRIMARY KEY (table_id, owner_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_records_owner ON records(table_id, owner_id);
CREATE TABLE IF NOT EXISTS fields (
    table_id   TEXT    NOT NULL,
    owner_id   INTEGER NOT NULL,
    record_id  INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    value_type TEXT    NOT NULL,
    value      TEXT,
    PRIMARY KEY (table_id, owner_id, record_id, name)
);
CREATE TABLE IF NOT EXISTS profiles (
    uniquenick TEXT PRIMARY KEY,
    profileid  INTEGER UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS passwords (
    uniquenick TEXT PRIMARY KEY,
    password   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    match_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    report_sha256  TEXT    NOT NULL UNIQUE,
    received_at    TEXT    NOT NULL,
    csid           TEXT,
    host_profileid INTEGER,
    game_mode      INTEGER,
    game_mode_name TEXT,
    report_version INTEGER,
    report_status  INTEGER,
    player_count   INTEGER
);
CREATE TABLE IF NOT EXISTS match_players (
    match_id      INTEGER NOT NULL,
    slot          INTEGER NOT NULL,
    profileid     INTEGER,
    connection_id TEXT,
    team          INTEGER,
    result        INTEGER,
    xp_gained     INTEGER,
    level_after   INTEGER,
    PRIMARY KEY (match_id, slot)
);
CREATE INDEX IF NOT EXISTS idx_match_players_profile ON match_players(profileid);
CREATE TABLE IF NOT EXISTS match_stats (
    match_id    INTEGER NOT NULL,
    slot        INTEGER NOT NULL,
    key         INTEGER NOT NULL,
    column_name TEXT    NOT NULL,
    value       TEXT,
    PRIMARY KEY (match_id, slot, key)
);

CREATE TABLE IF NOT EXISTS stat_defs (
    key          INTEGER PRIMARY KEY,
    property_id  INTEGER NOT NULL,
    column_name  TEXT    NOT NULL UNIQUE,
    fold         TEXT    NOT NULL,
    family       TEXT    NOT NULL,
    item_id      INTEGER,
    item_name    TEXT,
    label        TEXT    NOT NULL,
    date_of      TEXT,
    read_by_game INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS award_defs (
    kind        TEXT    NOT NULL,
    award_id    INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    category    TEXT,
    description TEXT,
    target      INTEGER,
    reward_xp   INTEGER,
    PRIMARY KEY (kind, award_id)
);

DROP VIEW IF EXISTS player_career;
CREATE VIEW player_career AS
    SELECT f.owner_id AS profileid, p.uniquenick, d.family, d.item_id, d.item_name, d.label,
           f.name AS column_name, f.value
    FROM fields f
    JOIN stat_defs d ON d.column_name = f.name
    LEFT JOIN profiles p ON p.profileid = f.owner_id
    WHERE f.table_id = 'PlayerStats_v6';

DROP VIEW IF EXISTS player_awards;
CREATE VIEW player_awards AS
    SELECT f.owner_id AS profileid, p.uniquenick, a.kind, a.award_id, a.name, a.category,
           CAST(f.value AS INTEGER) AS times_earned, dt.value AS earned_date
    FROM fields f
    JOIN stat_defs d ON d.column_name = f.name AND d.family IN ('badge', 'feat') AND d.fold = 'sum'
         AND (d.family = 'badge' OR d.column_name GLOB 'Ranked_Feats_F*')
    JOIN award_defs a ON a.kind = d.family AND a.award_id = d.item_id
    LEFT JOIN stat_defs dd ON dd.date_of = d.column_name
    LEFT JOIN fields dt ON dt.table_id = f.table_id AND dt.owner_id = f.owner_id
         AND dt.record_id = f.record_id AND dt.name = dd.column_name
    LEFT JOIN profiles p ON p.profileid = f.owner_id
    WHERE f.table_id = 'PlayerStats_v6' AND CAST(f.value AS INTEGER) > 0;
"""


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        self._depth = 0
        self._db.execute("PRAGMA journal_mode=WAL")
        self._migrate_owner_keyed()
        self._db.executescript(_SCHEMA)
        self._migrate_stats_v2()
        self._sync_stat_defs()
        self._db.commit()

    @contextmanager
    def transaction(self):
        """Groups writes into one commit. A nested call joins the outer transaction."""
        with self._lock:
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self._db.rollback()
                raise
            self._depth -= 1
            if self._depth == 0:
                self._db.commit()

    def _commit(self):
        if self._depth == 0:
            self._db.commit()

    def _migrate_owner_keyed(self) -> None:
        """Moves a database from before owner keys onto owner keys."""
        cols = [r[1] for r in self._db.execute("PRAGMA table_info(fields)")]
        if not cols or "owner_id" in cols:
            return
        self._db.executescript(
            """
            CREATE TABLE fields_new (
                table_id   TEXT    NOT NULL,
                owner_id   INTEGER NOT NULL,
                record_id  INTEGER NOT NULL,
                name       TEXT    NOT NULL,
                value_type TEXT    NOT NULL,
                value      TEXT,
                PRIMARY KEY (table_id, owner_id, record_id, name)
            );
            INSERT OR IGNORE INTO fields_new
                SELECT f.table_id, r.owner_id, f.record_id, f.name, f.value_type, f.value
                FROM fields f JOIN records r
                    ON r.table_id = f.table_id AND r.record_id = f.record_id;
            DROP TABLE fields;
            ALTER TABLE fields_new RENAME TO fields;

            CREATE TABLE records_new (
                table_id   TEXT    NOT NULL,
                owner_id   INTEGER NOT NULL,
                record_id  INTEGER NOT NULL,
                PRIMARY KEY (table_id, owner_id, record_id)
            );
            INSERT OR IGNORE INTO records_new SELECT table_id, owner_id, record_id FROM records;
            DROP TABLE records;
            ALTER TABLE records_new RENAME TO records;
            """
        )
        self._db.commit()

    def _migrate_stats_v2(self) -> None:
        """Clears the stats an older schema stored under wrong names, except career XP. Also clears
        S8Level_v6, which older builds filled with this title's level."""
        (version,) = self._db.execute("PRAGMA user_version").fetchone()
        if version >= SCHEMA_VERSION:
            return
        self._db.execute(
            "DELETE FROM fields WHERE table_id=? AND name NOT IN (?, ?)", (_PLAYER_STATS, stats.XP_COLUMN, "ownerid")
        )
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        xp_key = stats.BY_COLUMN[stats.XP_COLUMN].key
        for owner_id, record_id, xp in self._db.execute(
            "SELECT owner_id, record_id, value FROM fields WHERE table_id=? AND name=?",
            (_PLAYER_STATS, stats.XP_COLUMN),
        ).fetchall():
            level = stats.level_for_xp(int(xp or 0))
            self._db.execute(
                "INSERT OR REPLACE INTO fields VALUES(?,?,?,?,?,?)",
                (_PLAYER_STATS, owner_id, record_id, stats.LEVEL_COLUMN, "intValue", str(level)),
            )
            # Keep the XP as a match of its own, so a rebuilt career still has it.
            cur = self._db.execute(
                "INSERT INTO matches(report_sha256, received_at, csid, player_count) VALUES(?,?,?,1)",
                (f"migrated-career-xp:{owner_id}", now, "migration"),
            )
            self._db.execute(
                "INSERT INTO match_players(match_id, slot, profileid, xp_gained, level_after) VALUES(?,0,?,?,?)",
                (cur.lastrowid, owner_id, int(xp or 0), level),
            )
            self._db.execute(
                "INSERT INTO match_stats VALUES(?,0,?,?,?)", (cur.lastrowid, xp_key, stats.XP_COLUMN, str(int(xp or 0)))
            )
        self._db.execute("DELETE FROM fields WHERE table_id='S8Level_v6'")
        self._db.execute("DELETE FROM records WHERE table_id='S8Level_v6'")
        self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self._db.commit()

    def _sync_stat_defs(self) -> None:
        """Rewrites the name tables on every start, so they always match statdefs."""
        self._db.execute("DELETE FROM stat_defs")
        rows = []
        for s in stats.BY_KEY.values():
            rows.append(
                (
                    s.key,
                    s.property,
                    s.column,
                    s.fold,
                    s.family,
                    s.item,
                    _item_name(s),
                    s.label,
                    s.date_of,
                    int(s.read_by_game),
                )
            )
        self._db.executemany("INSERT INTO stat_defs VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        self._db.execute("DELETE FROM award_defs")
        self._db.executemany(
            "INSERT INTO award_defs VALUES('badge',?,?,?,?,?,?)",
            [(i, *b) for i, b in statdefs.BADGES.items()],
        )
        self._db.executemany(
            "INSERT INTO award_defs VALUES('feat',?,?,?,?,?,NULL)",
            [(i, *f) for i, f in statdefs.FEATS.items()],
        )

    # GameSpy profile ids have 8 digits.
    PROFILE_ID_BASE = 10000001

    def get_or_create_profile(self, uniquenick: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT profileid FROM profiles WHERE uniquenick=?", (uniquenick,)).fetchone()
            if row:
                return row[0]
            row = self._db.execute(
                "SELECT COALESCE(MAX(profileid), ?) + 1 FROM profiles", (self.PROFILE_ID_BASE - 1,)
            ).fetchone()
            profileid = row[0]
            self._db.execute("INSERT INTO profiles(uniquenick, profileid) VALUES(?,?)", (uniquenick, profileid))
            self._commit()
        return profileid

    def nick_for_profile(self, profileid: int):
        with self._lock:
            row = self._db.execute("SELECT uniquenick FROM profiles WHERE profileid=?", (profileid,)).fetchone()
        return row[0] if row else None

    def profile_for_nick(self, uniquenick: str):
        with self._lock:
            row = self._db.execute(
                "SELECT profileid FROM profiles WHERE uniquenick=? COLLATE NOCASE", (uniquenick,)
            ).fetchone()
        return row[0] if row else None

    def get_password(self, uniquenick: str):
        with self._lock:
            row = self._db.execute("SELECT password FROM passwords WHERE uniquenick=?", (uniquenick,)).fetchone()
        return row[0] if row else None

    def set_password(self, uniquenick: str, password: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO passwords(uniquenick, password) VALUES(?,?) "
                "ON CONFLICT(uniquenick) DO UPDATE SET password=excluded.password",
                (uniquenick, password),
            )
            self._commit()

    def record_id_for_owner(self, table_id: str, owner_id: int):
        with self._lock:
            row = self._db.execute(
                "SELECT record_id FROM records WHERE table_id=? AND owner_id=? ORDER BY record_id LIMIT 1",
                (table_id, owner_id),
            ).fetchone()
        return row[0] if row else None

    def create_record(self, table_id: str, owner_id: int) -> int:
        with self._lock:
            record_id = self._db.execute(
                "SELECT COALESCE(MAX(record_id), 0) + 1 FROM records WHERE table_id=? AND owner_id=?",
                (table_id, owner_id),
            ).fetchone()[0]
            self._db.execute(
                "INSERT INTO records(table_id, owner_id, record_id) VALUES(?,?,?)",
                (table_id, owner_id, record_id),
            )
            self._commit()
        return record_id

    def record_for_owner(self, table_id: str, owner_id: int) -> int:
        with self._lock:
            return self.record_id_for_owner(table_id, owner_id) or self.create_record(table_id, owner_id)

    def owner_for_record(self, table_id: str, record_id: int):
        with self._lock:
            rows = self._db.execute(
                "SELECT owner_id FROM records WHERE table_id=? AND record_id=?",
                (table_id, record_id),
            ).fetchall()
        return rows[0][0] if len(rows) == 1 else None

    def ensure_record(self, table_id: str, owner_id: int, record_id: int) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO records(table_id, owner_id, record_id) VALUES(?,?,?)",
                (table_id, owner_id, record_id),
            )
            self._commit()

    def set_fields(self, table_id: str, owner_id: int, record_id: int, values: list[tuple[str, str, str]]) -> None:
        with self._lock:
            self._db.executemany(
                "INSERT INTO fields(table_id, owner_id, record_id, name, value_type, value) "
                "VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(table_id, owner_id, record_id, name) DO UPDATE SET "
                "value_type=excluded.value_type, value=excluded.value",
                [(table_id, owner_id, record_id, n, t, v) for (n, t, v) in values],
            )
            self._commit()

    def get_fields(self, table_id: str, owner_id: int, record_id: int) -> dict[str, tuple[str, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT name, value_type, value FROM fields WHERE table_id=? AND owner_id=? AND record_id=?",
                (table_id, owner_id, record_id),
            ).fetchall()
        return {name: (vtype, value) for (name, vtype, value) in rows}

    def all_records(self, table_id: str, names: list[str]) -> list[tuple[int, int, dict]]:
        with self._lock:
            records = self._db.execute(
                "SELECT owner_id, record_id FROM records WHERE table_id=?", (table_id,)
            ).fetchall()
            values: dict = {}
            if names:
                placeholders = ",".join("?" * len(names))
                for owner_id, record_id, name, vtype, value in self._db.execute(
                    f"SELECT owner_id, record_id, name, value_type, value FROM fields "
                    f"WHERE table_id=? AND name IN ({placeholders})",
                    [table_id, *names],
                ):
                    values.setdefault((owner_id, record_id), {})[name] = (vtype, value)
        return [(o, r, values.get((o, r), {})) for o, r in records]

    def record_count(self, table_id: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM records WHERE table_id=?", (table_id,)).fetchone()
        return row[0]

    def add_match(self, report_sha256: str, csid: str, host_profileid: int, game_mode, report, player_count: int):
        """Returns the new match id, or None when the same report is already stored."""
        with self._lock:
            try:
                cur = self._db.execute(
                    "INSERT INTO matches(report_sha256, received_at, csid, host_profileid, game_mode, game_mode_name, "
                    "report_version, report_status, player_count) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        report_sha256,
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        csid,
                        host_profileid,
                        game_mode,
                        statdefs.GAME_MODES.get(game_mode) if game_mode is not None else None,
                        report.version,
                        report.status,
                        player_count,
                    ),
                )
            except sqlite3.IntegrityError:
                return None
            self._commit()
        return cur.lastrowid

    def add_match_player(self, match_id: int, block, profileid, result=None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO match_players VALUES(?,?,?,?,?,?,?,?)",
                (
                    match_id,
                    block.index,
                    profileid,
                    block.connection_id.hex(),
                    block.team,
                    block.result,
                    result.xp_gained if result else None,
                    result.level if result else None,
                ),
            )
            if result:
                self._db.executemany(
                    "INSERT OR REPLACE INTO match_stats VALUES(?,?,?,?,?)",
                    [(match_id, block.index, k, c, v) for k, c, v in result.match_values],
                )
            self._commit()

    def match_history(self, profileid: int) -> list[tuple]:
        with self._lock:
            return self._db.execute(
                "SELECT m.match_id, m.received_at, m.game_mode_name, p.xp_gained, p.level_after "
                "FROM match_players p JOIN matches m ON m.match_id = p.match_id "
                "WHERE p.profileid=? ORDER BY m.match_id",
                (profileid,),
            ).fetchall()

    def match_keys(self, profileid: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT s.match_id, s.key, s.value FROM match_stats s JOIN match_players p "
                "ON p.match_id = s.match_id AND p.slot = s.slot WHERE p.profileid=? ORDER BY s.match_id, s.key",
                (profileid,),
            ).fetchall()
        out: dict = {}
        for match_id, key, value in rows:
            out.setdefault(match_id, {})[key] = value
        return [out[m] for m in sorted(out)]

    def career(self, profileid: int) -> list[tuple]:
        with self._lock:
            return self._db.execute(
                "SELECT family, item_id, item_name, label, column_name, value FROM player_career "
                "WHERE profileid=? ORDER BY family, item_id, label",
                (profileid,),
            ).fetchall()

    def awards(self, profileid: int) -> list[tuple]:
        with self._lock:
            return self._db.execute(
                "SELECT kind, award_id, name, category, times_earned, earned_date FROM player_awards "
                "WHERE profileid=? ORDER BY kind, award_id",
                (profileid,),
            ).fetchall()

    def replace_record_fields(self, table_id: str, owner_id: int, record_id: int, values) -> None:
        with self.transaction():
            self._db.execute(
                "DELETE FROM fields WHERE table_id=? AND owner_id=? AND record_id=?", (table_id, owner_id, record_id)
            )
            self.set_fields(table_id, owner_id, record_id, values)


def _item_name(s) -> str | None:
    if s.item is None:
        return None
    if s.family == "badge":
        return statdefs.BADGES.get(s.item, (f"Badge {s.item}",))[0]
    if s.family == "feat":
        return statdefs.FEATS.get(s.item, (f"Feat {s.item}",))[0]
    if s.family == "gear":
        g = statdefs.GEAR.get(s.item)
        return f"{g[0]} ({g[1]})" if g else f"Unused gear slot {s.item}"
    if s.family == "game_mode":
        return statdefs.GAME_MODES.get(s.item, f"Game mode {s.item}")
    return None
