"""SQLite-backed Sake record store.

Sake tables (PlayerStats_v6 has ~991 columns) are stored as an entity-attribute-value model rather
than 991 real columns: a `records` row per (table, recordid) and a `fields` row per typed value. This
avoids hardcoding the schema and lets the store learn each field's Sake type from the client's own
UpdateRecord writes. One SQLite file is the shared, hostable leaderboard store - point every game PC
at one host and SearchForRecords ranks across all of them.
"""
import sqlite3
import threading


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                table_id   TEXT    NOT NULL,
                record_id  INTEGER NOT NULL,
                owner_id   INTEGER NOT NULL,
                PRIMARY KEY (table_id, record_id)
            );
            CREATE INDEX IF NOT EXISTS idx_records_owner ON records(table_id, owner_id);
            CREATE TABLE IF NOT EXISTS fields (
                table_id   TEXT NOT NULL,
                record_id  INTEGER NOT NULL,
                name       TEXT NOT NULL,
                value_type TEXT NOT NULL,
                value      TEXT,
                PRIMARY KEY (table_id, record_id, name)
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

    # GameSpy profile ids start at a plausible value; every distinct uniquenick gets a stable, unique
    # id that persists in the shared DB so the same player ranks consistently across PCs and sessions.
    PROFILE_ID_BASE = 10000001

    def get_or_create_profile(self, uniquenick: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT profileid FROM profiles WHERE uniquenick=?", (uniquenick,)
            ).fetchone()
            if row:
                return row[0]
            row = self._db.execute("SELECT COALESCE(MAX(profileid), ?) + 1 FROM profiles",
                                   (self.PROFILE_ID_BASE - 1,)).fetchone()
            profileid = row[0]
            self._db.execute("INSERT INTO profiles(uniquenick, profileid) VALUES(?,?)",
                             (uniquenick, profileid))
            self._db.commit()
        return profileid

    def get_password(self, uniquenick: str):
        """The plaintext GameSpy password learned from a prior \\newuser\\, or None. Persisting it lets
        the FIRST \\login\\ of a later launch answer with a correct proof even though that launch's
        \\newuser\\ (which reveals the password) has not arrived yet."""
        with self._lock:
            row = self._db.execute(
                "SELECT password FROM passwords WHERE uniquenick=?", (uniquenick,)
            ).fetchone()
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
                "SELECT record_id FROM records WHERE table_id=? AND owner_id=?",
                (table_id, owner_id),
            ).fetchone()
        return row[0] if row else None

    def create_record(self, table_id: str, owner_id: int) -> int:
        """Allocate the next recordid for a table and bind it to an owner."""
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(record_id), 0) + 1 FROM records WHERE table_id=?",
                (table_id,),
            ).fetchone()
            record_id = row[0]
            self._db.execute(
                "INSERT INTO records(table_id, record_id, owner_id) VALUES(?,?,?)",
                (table_id, record_id, owner_id),
            )
            self._db.commit()
        return record_id

    def set_fields(self, table_id: str, record_id: int, values: list[tuple[str, str, str]]) -> None:
        """values = [(name, value_type, value), ...]."""
        with self._lock:
            self._db.executemany(
                "INSERT INTO fields(table_id, record_id, name, value_type, value) VALUES(?,?,?,?,?) "
                "ON CONFLICT(table_id, record_id, name) DO UPDATE SET value_type=excluded.value_type, "
                "value=excluded.value",
                [(table_id, record_id, n, t, v) for (n, t, v) in values],
            )
            self._db.commit()

    def get_fields(self, table_id: str, record_id: int) -> dict:
        """Return {name: (value_type, value)} for a record."""
        with self._lock:
            rows = self._db.execute(
                "SELECT name, value_type, value FROM fields WHERE table_id=? AND record_id=?",
                (table_id, record_id),
            ).fetchall()
        return {name: (vtype, value) for (name, vtype, value) in rows}

    # Per-record marker holding the newest SC-report FILETIME already applied. It is never a requested
    # column, so it stays out of every SearchForRecords response while guarding against a stale/replayed
    # report lowering a cumulative stat.
    _LAST_FT = "_LastReportFT"

    def apply_report(self, table_id: str, owner_id: int, typed_values: dict, filetime: int) -> tuple:
        """Upsert a player's cumulative stats from one SC report block.

        typed_values: {column -> (value_type, value_str)}. Section 8's reports are cumulative snapshots
        and the game re-reads the stored total at login, so the newest report replaces the columns
        (overwrite) rather than summing - summing would double-count the baseline the game already sent
        back. A report older than the last one applied to this record is ignored. Returns (record_id,
        applied: bool).
        """
        with self._lock:
            row = self._db.execute(
                "SELECT record_id FROM records WHERE table_id=? AND owner_id=?",
                (table_id, owner_id),
            ).fetchone()
            if row:
                record_id = row[0]
            else:
                nxt = self._db.execute(
                    "SELECT COALESCE(MAX(record_id), 0) + 1 FROM records WHERE table_id=?",
                    (table_id,),
                ).fetchone()[0]
                record_id = nxt
                self._db.execute(
                    "INSERT INTO records(table_id, record_id, owner_id) VALUES(?,?,?)",
                    (table_id, record_id, owner_id),
                )
            prev = self._db.execute(
                "SELECT value FROM fields WHERE table_id=? AND record_id=? AND name=?",
                (table_id, record_id, self._LAST_FT),
            ).fetchone()
            last_ft = int(prev[0]) if prev and prev[0] else 0
            if filetime and last_ft and filetime < last_ft:
                return record_id, False
            rows = [(table_id, record_id, n, t, v) for n, (t, v) in typed_values.items()]
            rows.append((table_id, record_id, self._LAST_FT, "int64Value", str(filetime or last_ft)))
            self._db.executemany(
                "INSERT INTO fields(table_id, record_id, name, value_type, value) VALUES(?,?,?,?,?) "
                "ON CONFLICT(table_id, record_id, name) DO UPDATE SET value_type=excluded.value_type, "
                "value=excluded.value",
                rows,
            )
            self._db.commit()
        return record_id, True

    def add_xp_delta(self, table_id: str, owner_id: int, xp_delta: int, filetime: int) -> tuple:
        """Accumulate a per-round XP delta into a player's career Ranked_xp.

        Section 8's report carries the XP *earned this round* (keyid 11), not the total, so the career XP
        is a running sum. Idempotent by FILETIME: a report whose timestamp is not newer than the last one
        applied to this record is ignored, so a resent/duplicate report can't double-count. Returns
        (record_id, new_total_xp, applied: bool).
        """
        with self._lock:
            row = self._db.execute(
                "SELECT record_id FROM records WHERE table_id=? AND owner_id=?",
                (table_id, owner_id),
            ).fetchone()
            if row:
                record_id = row[0]
            else:
                record_id = self._db.execute(
                    "SELECT COALESCE(MAX(record_id), 0) + 1 FROM records WHERE table_id=?",
                    (table_id,),
                ).fetchone()[0]
                self._db.execute(
                    "INSERT INTO records(table_id, record_id, owner_id) VALUES(?,?,?)",
                    (table_id, record_id, owner_id),
                )
            prev_ft = self._db.execute(
                "SELECT value FROM fields WHERE table_id=? AND record_id=? AND name=?",
                (table_id, record_id, self._LAST_FT),
            ).fetchone()
            last_ft = int(prev_ft[0]) if prev_ft and prev_ft[0] else 0
            cur_row = self._db.execute(
                "SELECT value FROM fields WHERE table_id=? AND record_id=? AND name='Ranked_xp'",
                (table_id, record_id),
            ).fetchone()
            cur_xp = int(cur_row[0]) if cur_row and cur_row[0] else 0
            if filetime and last_ft and filetime <= last_ft:
                return record_id, cur_xp, False
            new_xp = cur_xp + max(0, int(xp_delta))
            # Persist the new total AND the guard timestamp together, so this call is self-contained: the
            # next report reads the updated Ranked_xp back as its base (accumulation must not depend on the
            # caller also writing Ranked_xp).
            self._db.executemany(
                "INSERT INTO fields(table_id, record_id, name, value_type, value) VALUES(?,?,?,?,?) "
                "ON CONFLICT(table_id, record_id, name) DO UPDATE SET value_type=excluded.value_type, "
                "value=excluded.value",
                [
                    (table_id, record_id, "Ranked_xp", "intValue", str(new_xp)),
                    (table_id, record_id, self._LAST_FT, "int64Value", str(filetime or last_ft)),
                ],
            )
            self._db.commit()
        return record_id, new_xp, True

    def owner_of(self, table_id: str, record_id: int):
        with self._lock:
            row = self._db.execute(
                "SELECT owner_id FROM records WHERE table_id=? AND record_id=?",
                (table_id, record_id),
            ).fetchone()
        return row[0] if row else None

    def search(self, table_id: str, sort_field: str | None, descending: bool,
               offset: int, limit: int, owner_ids: list[int] | None) -> list[int]:
        """Return record_ids matching the search, ordered by a numeric sort field. owner_ids filters
        to those owners (used by GetMyRecords-style lookups); None = all owners (leaderboard)."""
        params: list = [table_id]
        where = "r.table_id=?"
        if owner_ids:
            where += " AND r.owner_id IN (%s)" % ",".join("?" * len(owner_ids))
            params.extend(owner_ids)
        if sort_field:
            join = ("LEFT JOIN fields f ON f.table_id=r.table_id AND f.record_id=r.record_id "
                    "AND f.name=?")
            params_full = [sort_field] + params
            order = "ORDER BY CAST(COALESCE(f.value,'0') AS REAL) " + ("DESC" if descending else "ASC")
        else:
            join = ""
            params_full = params
            order = "ORDER BY r.record_id " + ("DESC" if descending else "ASC")
        sql = (f"SELECT r.record_id FROM records r {join} WHERE {where} {order} "
               f"LIMIT {int(limit)} OFFSET {int(offset)}")
        with self._lock:
            rows = self._db.execute(sql, params_full).fetchall()
        return [row[0] for row in rows]

    def record_count(self, table_id: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM records WHERE table_id=?", (table_id,)
            ).fetchone()
        return row[0]
