"""SQLite-backed Sake record store.

Sake tables are stored entity-attribute-value, a `records` row per (table, owner, recordid) and a
`fields` row per typed value, rather than as real columns: PlayerStats_v6 alone has about 991 of them,
and this way the schema is never hardcoded and each field's Sake type is learnt from the client's own
UpdateRecord writes.

A recordid is owner-relative, and numbering restarts per owner. Every request is authenticated, and a
client only ever searches by its own ownerid, so two dedicated servers can each hold their own
ServerStatusTG09_v6 recordid 1 without colliding.
"""

import sqlite3
import threading


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute("PRAGMA journal_mode=WAL")
        self._migrate()
        self._db.executescript(
            """
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
            """
        )
        self._db.commit()

    def _migrate(self) -> None:
        """Rebuild a pre-owner-keyed DB, whose `fields` table carried no owner_id, onto the owner-scoped
        keying. owner_id comes from the records table, so an orphaned field row is dropped."""
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

    # Real GameSpy profile ids are 8 digits, so start where one looks plausible to the game.
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
            self._db.commit()
        return profileid

    def get_password(self, uniquenick: str):
        """The plaintext GameSpy password learnt from a prior \\newuser\\, or None."""
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
            self._db.commit()

    def record_id_for_owner(self, table_id: str, owner_id: int):
        with self._lock:
            row = self._db.execute(
                "SELECT record_id FROM records WHERE table_id=? AND owner_id=? ORDER BY record_id LIMIT 1",
                (table_id, owner_id),
            ).fetchone()
        return row[0] if row else None

    def create_record(self, table_id: str, owner_id: int) -> int:
        """Allocate and bind the next recordid for this owner. Numbering is per-owner, so each owner's
        first record is 1."""
        with self._lock:
            record_id = self._db.execute(
                "SELECT COALESCE(MAX(record_id), 0) + 1 FROM records WHERE table_id=? AND owner_id=?",
                (table_id, owner_id),
            ).fetchone()[0]
            self._db.execute(
                "INSERT INTO records(table_id, owner_id, record_id) VALUES(?,?,?)",
                (table_id, owner_id, record_id),
            )
            self._db.commit()
        return record_id

    def owner_for_record(self, table_id: str, record_id: int):
        """Owner of a recordid, for a write that carries no login ticket to key on. Returns None when
        the recordid is absent or several owners hold it, so the caller cannot misattribute the write."""
        with self._lock:
            rows = self._db.execute(
                "SELECT owner_id FROM records WHERE table_id=? AND record_id=?",
                (table_id, record_id),
            ).fetchall()
        return rows[0][0] if len(rows) == 1 else None

    def ensure_record(self, table_id: str, owner_id: int, record_id: int) -> None:
        """Bind (table, owner, recordid), leaving an existing binding intact. A dedicated server reuses
        the synthetic recordid SearchForRecords returns and updates it without ever calling
        CreateRecord, so the row has to be created here instead."""
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO records(table_id, owner_id, record_id) VALUES(?,?,?)",
                (table_id, owner_id, record_id),
            )
            self._db.commit()

    def set_fields(self, table_id: str, owner_id: int, record_id: int, values: list[tuple[str, str, str]]) -> None:
        """values = [(name, value_type, value), ...]."""
        with self._lock:
            self._db.executemany(
                "INSERT INTO fields(table_id, owner_id, record_id, name, value_type, value) "
                "VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(table_id, owner_id, record_id, name) DO UPDATE SET "
                "value_type=excluded.value_type, value=excluded.value",
                [(table_id, owner_id, record_id, n, t, v) for (n, t, v) in values],
            )
            self._db.commit()

    def get_fields(self, table_id: str, owner_id: int, record_id: int) -> dict:
        """Return {name: (value_type, value)} for a record."""
        with self._lock:
            rows = self._db.execute(
                "SELECT name, value_type, value FROM fields WHERE table_id=? AND owner_id=? AND record_id=?",
                (table_id, owner_id, record_id),
            ).fetchall()
        return {name: (vtype, value) for (name, vtype, value) in rows}

    # Newest SC-report FILETIME already applied to a record, which guards against a replayed report
    # double-counting. The game never asks for this column, so it stays out of every search response.
    _LAST_FT = "_LastReportFT"

    def _record_id_or_create(self, table_id: str, owner_id: int) -> int:
        row = self._db.execute(
            "SELECT record_id FROM records WHERE table_id=? AND owner_id=? ORDER BY record_id LIMIT 1",
            (table_id, owner_id),
        ).fetchone()
        if row:
            return row[0]
        record_id = self._db.execute(
            "SELECT COALESCE(MAX(record_id), 0) + 1 FROM records WHERE table_id=? AND owner_id=?",
            (table_id, owner_id),
        ).fetchone()[0]
        self._db.execute(
            "INSERT INTO records(table_id, owner_id, record_id) VALUES(?,?,?)",
            (table_id, owner_id, record_id),
        )
        return record_id

    def apply_report(self, table_id: str, owner_id: int, typed_values: dict, filetime: int) -> tuple:
        """Upsert a player's cumulative stats from one SC report block.

        typed_values: {column -> (value_type, value_str)}. A report older than the last one applied is
        ignored. Returns (record_id, applied).
        """
        with self._lock:
            record_id = self._record_id_or_create(table_id, owner_id)
            prev = self._db.execute(
                "SELECT value FROM fields WHERE table_id=? AND owner_id=? AND record_id=? AND name=?",
                (table_id, owner_id, record_id, self._LAST_FT),
            ).fetchone()
            last_ft = int(prev[0]) if prev and prev[0] else 0
            if filetime and last_ft and filetime < last_ft:
                return record_id, False
            rows = [(table_id, owner_id, record_id, n, t, v) for n, (t, v) in typed_values.items()]
            rows.append((table_id, owner_id, record_id, self._LAST_FT, "int64Value", str(filetime or last_ft)))
            self._db.executemany(
                "INSERT INTO fields(table_id, owner_id, record_id, name, value_type, value) "
                "VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(table_id, owner_id, record_id, name) DO UPDATE SET "
                "value_type=excluded.value_type, value=excluded.value",
                rows,
            )
            self._db.commit()
        return record_id, True

    def add_xp_delta(self, table_id: str, owner_id: int, xp_delta: int, filetime: int) -> tuple:
        """Accumulate a per-round XP delta into a player's career Ranked_xp.

        Idempotent by FILETIME: a report no newer than the last one applied to this record is ignored,
        so a resent report cannot double-count. Returns (record_id, new_total_xp, applied).
        """
        with self._lock:
            record_id = self._record_id_or_create(table_id, owner_id)
            prev_ft = self._db.execute(
                "SELECT value FROM fields WHERE table_id=? AND owner_id=? AND record_id=? AND name=?",
                (table_id, owner_id, record_id, self._LAST_FT),
            ).fetchone()
            last_ft = int(prev_ft[0]) if prev_ft and prev_ft[0] else 0
            cur_row = self._db.execute(
                "SELECT value FROM fields WHERE table_id=? AND owner_id=? AND record_id=? AND name='Ranked_xp'",
                (table_id, owner_id, record_id),
            ).fetchone()
            cur_xp = int(cur_row[0]) if cur_row and cur_row[0] else 0
            if filetime and last_ft and filetime <= last_ft:
                return record_id, cur_xp, False
            new_xp = cur_xp + max(0, int(xp_delta))
            # Write the total and the guard timestamp together, so the next report reads the updated
            # Ranked_xp back as its base without the caller also having to write it.
            self._db.executemany(
                "INSERT INTO fields(table_id, owner_id, record_id, name, value_type, value) "
                "VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(table_id, owner_id, record_id, name) DO UPDATE SET "
                "value_type=excluded.value_type, value=excluded.value",
                [
                    (table_id, owner_id, record_id, "Ranked_xp", "intValue", str(new_xp)),
                    (table_id, owner_id, record_id, self._LAST_FT, "int64Value", str(filetime or last_ft)),
                ],
            )
            self._db.commit()
        return record_id, new_xp, True

    def search(
        self,
        table_id: str,
        sort_field: str | None,
        descending: bool,
        offset: int,
        limit: int,
        owner_ids: list[int] | None,
    ) -> list[tuple[int, int]]:
        """Return (owner_id, record_id) pairs matching the search, ordered by a numeric sort field.
        owner_ids restricts the search to those owners; None searches all of them, for a leaderboard."""
        params: list = [table_id]
        where = "r.table_id=?"
        if owner_ids:
            placeholders = ",".join("?" * len(owner_ids))
            where += f" AND r.owner_id IN ({placeholders})"
            params.extend(owner_ids)
        if sort_field:
            join = (
                "LEFT JOIN fields f ON f.table_id=r.table_id AND f.owner_id=r.owner_id "
                "AND f.record_id=r.record_id AND f.name=?"
            )
            params_full = [sort_field] + params
            order = "ORDER BY CAST(COALESCE(f.value,'0') AS REAL) " + ("DESC" if descending else "ASC")
        else:
            join = ""
            params_full = params
            order = "ORDER BY r.record_id " + ("DESC" if descending else "ASC")
        sql = (
            f"SELECT r.owner_id, r.record_id FROM records r {join} WHERE {where} {order} "
            f"LIMIT {int(limit)} OFFSET {int(offset)}"
        )
        with self._lock:
            rows = self._db.execute(sql, params_full).fetchall()
        return [(row[0], row[1]) for row in rows]

    def record_count(self, table_id: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM records WHERE table_id=?", (table_id,)).fetchone()
        return row[0]
