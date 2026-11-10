"""Stats portal accounts: sign-up, sign-in, and claiming a game profile with the link code the game shows.

One account claims a profile, once. The claim sets Profile_LinkCodeUsed, so the game shows the profile as
activated instead of a new code. The owner, or `python -m server.portal release`, frees it again.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone

from .persistence import write_fields
from .profile_fields import (
    CLAN_TAG_FIELD,
    PROFILE_TABLE,
    RANDOM_FIELD,
    USED_FIELD,
    clan_tag_error,
    clan_tag_from_value,
    clan_tag_to_int,
    parse_link_code,
    sake_int,
)

SESSION_SECONDS = 14 * 24 * 3600
MINUTE, HOUR, DAY = 60, 3600, 86400
LOGIN_FAILS_PER_NAME = 10
LOGIN_FAILS_PER_ADDRESS = 30
SIGNUPS_PER_ADDRESS = 5
# The random part of a link code has 32768 values, so a few wrong tries cannot find it.
CLAIM_FAILS_PER_CODE = 5
CLAIM_FAILS_PER_ACCOUNT = 20

USERNAME = re.compile(r"[A-Za-z0-9_.-]{3,24}")
DISPLAY_NAME = re.compile(r"[A-Za-z0-9_.-](?:[A-Za-z0-9_. -]{0,22}[A-Za-z0-9_.-])")
PASSWORD_MIN, PASSWORD_MAX = 8, 128

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 1 << 14, 8, 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_accounts (
    account_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    created_at    TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS portal_sessions (
    token_sha256 TEXT    PRIMARY KEY,
    account_id   INTEGER NOT NULL,
    expires_at   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portal_sessions_account ON portal_sessions(account_id);
CREATE TABLE IF NOT EXISTS portal_claims (
    profileid  INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    claimed_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portal_claims_account ON portal_claims(account_id);
CREATE TABLE IF NOT EXISTS portal_names (profileid INTEGER PRIMARY KEY, name TEXT NOT NULL);
"""


class AccountError(Exception):
    """A request the portal refuses, with the HTTP status and the message to show."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    b64 = base64.b64encode
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt).decode()}${b64(key).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, key = stored.split("$")
        if kind != "scrypt":
            return False
        want = base64.b64decode(key)
        got = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(want)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Throttle:
    """Counts recent failures per key, in memory."""

    def __init__(self):
        self._hits: dict = {}
        self._lock = threading.Lock()

    def count(self, key, window: float) -> int:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, ()) if now - t < window]
            if hits:
                self._hits[key] = hits
            else:
                self._hits.pop(key, None)
            return len(hits)

    def add(self, key) -> None:
        now = time.monotonic()
        with self._lock:
            self._hits.setdefault(key, []).append(now)
            if len(self._hits) > 10000:
                self._hits = {k: v for k, v in self._hits.items() if now - v[-1] < DAY}


class Accounts:
    def __init__(self, db_path: str, profile_table: str = PROFILE_TABLE):
        self._db = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        self._db.executescript(_SCHEMA)
        self._db.commit()
        self._lock = threading.Lock()
        self.throttle = Throttle()
        self.profile_table = profile_table
        # Checked when a username is unknown, so a sign-in takes as long either way.
        self._dummy_hash = hash_password(secrets.token_hex(8))

    # Accounts and sessions

    def register(self, username, password, address: str) -> str:
        """Creates an account and gives the token of its first session."""
        username = str(username or "").strip()
        if not USERNAME.fullmatch(username):
            raise AccountError(400, "A username has 3 to 24 letters, digits, dots, dashes or underscores.")
        password = self._checked_password(password)
        if self.throttle.count(("signup", address), HOUR) >= SIGNUPS_PER_ADDRESS:
            raise AccountError(429, "Too many new accounts from this address. Try again later.")
        password_hash = hash_password(password)
        with self._lock, self._db:
            try:
                cur = self._db.execute(
                    "INSERT INTO portal_accounts(username, password_hash, created_at) VALUES(?,?,?)",
                    (username, password_hash, _now_iso()),
                )
            except sqlite3.IntegrityError:
                raise AccountError(409, "That username is taken.") from None
            self.throttle.add(("signup", address))
            return self._new_session(cur.lastrowid)

    def login(self, username, password, address: str) -> str:
        """Gives a new session token for a correct username and password."""
        username = str(username or "").strip()
        keys = (("login", username.lower()), LOGIN_FAILS_PER_NAME), (("login-from", address), LOGIN_FAILS_PER_ADDRESS)
        if any(self.throttle.count(key, 15 * MINUTE) >= limit for key, limit in keys):
            raise AccountError(429, "Too many failed sign-ins. Wait 15 minutes, then try again.")
        with self._lock:
            row = self._db.execute(
                "SELECT account_id, password_hash FROM portal_accounts WHERE username=?", (username,)
            ).fetchone()
        matches = verify_password(str(password or ""), row[1] if row else self._dummy_hash)
        if not (row and matches):
            for key, _limit in keys:
                self.throttle.add(key)
            raise AccountError(401, "Wrong username or password.")
        with self._lock, self._db:
            self._db.execute("DELETE FROM portal_sessions WHERE expires_at <= ?", (time.time(),))
            return self._new_session(row[0])

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock, self._db:
                self._db.execute("DELETE FROM portal_sessions WHERE token_sha256=?", (_token_hash(token),))

    def account_for(self, token: str | None) -> tuple[int, str] | None:
        """Gives (account id, username) of a live session, or None."""
        if not token:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT a.account_id, a.username FROM portal_sessions s "
                "JOIN portal_accounts a ON a.account_id = s.account_id "
                "WHERE s.token_sha256=? AND s.expires_at > ?",
                (_token_hash(token), time.time()),
            ).fetchone()
        return (row[0], row[1]) if row else None

    def change_password(self, account_id: int, current, new, keep_token: str) -> None:
        """Sets a new password and ends every other session of the account."""
        new = self._checked_password(new)
        with self._lock:
            row = self._db.execute(
                "SELECT password_hash FROM portal_accounts WHERE account_id=?", (account_id,)
            ).fetchone()
        if not row or not verify_password(str(current or ""), row[0]):
            raise AccountError(403, "The current password is wrong.")
        password_hash = hash_password(new)
        with self._lock, self._db:
            self._db.execute(
                "UPDATE portal_accounts SET password_hash=? WHERE account_id=?", (password_hash, account_id)
            )
            self._db.execute(
                "DELETE FROM portal_sessions WHERE account_id=? AND token_sha256 != ?",
                (account_id, _token_hash(keep_token)),
            )

    def _new_session(self, account_id: int) -> str:
        token = secrets.token_urlsafe(32)
        self._db.execute(
            "INSERT INTO portal_sessions(token_sha256, account_id, expires_at) VALUES(?,?,?)",
            (_token_hash(token), account_id, time.time() + SESSION_SECONDS),
        )
        return token

    @staticmethod
    def _checked_password(password) -> str:
        password = str(password or "")
        if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
            raise AccountError(400, f"A password has {PASSWORD_MIN} to {PASSWORD_MAX} characters.")
        return password

    # Claimed profiles

    def claim(self, account_id: int, code) -> int:
        """Claims the profile that showed this link code for the account. Gives the profile id."""
        parsed = parse_link_code(str(code or ""))
        if parsed is None:
            raise AccountError(400, "Enter the 12-character code from the game, such as 758B-338A-154D.")
        pid, random_code = parsed
        account_key = ("claim-account", account_id)
        if self.throttle.count(account_key, HOUR) >= CLAIM_FAILS_PER_ACCOUNT:
            raise AccountError(429, "Too many wrong codes. Try again in an hour.")
        with self._lock, self._db:
            known = self._db.execute("SELECT 1 FROM profiles WHERE profileid=?", (pid,)).fetchone()
            if not known:
                self.throttle.add(account_key)
                raise AccountError(404, "No profile has this code. Check the code you entered.")
            owner = self._owner(pid)
            if owner == account_id:
                raise AccountError(409, "This profile is already yours.")
            if owner is not None:
                raise AccountError(409, "Another account has claimed this profile. Ask the server admin to release it.")
            record_id = self._record(pid)
            stored = self._int_field(pid, record_id, RANDOM_FIELD)
            if not stored:
                raise AccountError(
                    409,
                    "This profile has no active code. In the game, sign in to Prejudice Online, open the "
                    "Stats Portal screen, then enter the code it shows.",
                )
            code_key = ("claim-code", pid, stored)
            if self.throttle.count(code_key, DAY) >= CLAIM_FAILS_PER_CODE:
                raise AccountError(
                    429, "Too many wrong tries for this code. Open the Stats Portal screen again for a new one."
                )
            if random_code != stored:
                self.throttle.add(code_key)
                self.throttle.add(account_key)
                raise AccountError(
                    403,
                    "The code does not match. The game makes a new code each time the Stats Portal screen "
                    "opens, so enter the newest one.",
                )
            self._db.execute(
                "INSERT INTO portal_claims(profileid, account_id, claimed_at) VALUES(?,?,?)",
                (pid, account_id, _now_iso()),
            )
            # A used code cannot claim the profile again after a release.
            write_fields(
                self._db, self.profile_table, pid, record_id, [(USED_FIELD, *sake_int(1)), (RANDOM_FIELD, *sake_int(0))]
            )
            if not self._db.execute("SELECT 1 FROM portal_names WHERE profileid=?", (pid,)).fetchone():
                (username,) = self._db.execute(
                    "SELECT username FROM portal_accounts WHERE account_id=?", (account_id,)
                ).fetchone()
                try:
                    self._check_name(username, pid, account_id)
                    self._db.execute("INSERT INTO portal_names(profileid, name) VALUES(?,?)", (pid, username))
                except AccountError:
                    pass
        return pid

    def profiles(self, account_id: int) -> list[dict]:
        """Gives the account's profiles with their display name and clan tag."""
        with self._lock:
            rows = self._db.execute(
                "SELECT c.profileid, p.uniquenick, n.name, c.claimed_at FROM portal_claims c "
                "LEFT JOIN profiles p ON p.profileid = c.profileid "
                "LEFT JOIN portal_names n ON n.profileid = c.profileid "
                "WHERE c.account_id=? ORDER BY c.claimed_at, c.profileid",
                (account_id,),
            ).fetchall()
            out = []
            for pid, nick, name, claimed_at in rows:
                tag = self._field(pid, self._record(pid), CLAN_TAG_FIELD)
                out.append(
                    {
                        "profileid": pid,
                        "uniquenick": nick,
                        "display_name": name,
                        "clan_tag": clan_tag_from_value(tag),
                        "claimed_at": claimed_at,
                    }
                )
        return out

    def update_profile(self, account_id: int, pid: int, display_name=None, clan_tag=None) -> None:
        """Sets the display name and the clan tag of an owned profile. None leaves one as it is."""
        with self._lock, self._db:
            self._require_owner(account_id, pid)
            if display_name is not None:
                name = " ".join(str(display_name).split())
                if name:
                    self._check_name(name, pid, account_id)
                    self._db.execute(
                        "INSERT INTO portal_names(profileid, name) VALUES(?,?) "
                        "ON CONFLICT(profileid) DO UPDATE SET name=excluded.name",
                        (pid, name),
                    )
                else:
                    self._db.execute("DELETE FROM portal_names WHERE profileid=?", (pid,))
            if clan_tag is not None:
                tag = str(clan_tag).strip()
                error = clan_tag_error(tag)
                if error:
                    raise AccountError(400, error)
                record_id = self._record(pid)
                if record_id is None:
                    raise AccountError(409, "Sign in to Prejudice Online once, then set the clan tag.")
                write_fields(
                    self._db, self.profile_table, pid, record_id, [(CLAN_TAG_FIELD, *sake_int(clan_tag_to_int(tag)))]
                )

    def release(self, account_id: int, pid: int) -> None:
        with self._lock, self._db:
            self._require_owner(account_id, pid)
            self._release(pid)

    def release_profile(self, who: str) -> int | None:
        """Frees a profile, found by uniquenick or profileid, whoever claimed it. Gives the profileid."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT profileid FROM profiles WHERE uniquenick=? COLLATE NOCASE OR CAST(profileid AS TEXT)=?",
                (who, who),
            ).fetchone()
            if row is None:
                return None
            self._release(row[0])
            return row[0]

    def _release(self, pid: int) -> None:
        self._db.execute("DELETE FROM portal_claims WHERE profileid=?", (pid,))
        self._db.execute("DELETE FROM portal_names WHERE profileid=?", (pid,))
        record_id = self._record(pid)
        if record_id is not None:
            write_fields(
                self._db, self.profile_table, pid, record_id, [(USED_FIELD, *sake_int(0)), (RANDOM_FIELD, *sake_int(0))]
            )

    def _check_name(self, name: str, pid: int, account_id: int) -> None:
        """Refuses a display name that breaks the rules or that another player shows or logs in with."""
        if not DISPLAY_NAME.fullmatch(name):
            raise AccountError(400, "A display name has 2 to 24 letters, digits, spaces, dots, dashes or underscores.")
        other = self._db.execute(
            "SELECT n.profileid FROM portal_names n LEFT JOIN portal_claims c ON c.profileid = n.profileid "
            "WHERE n.name = ? COLLATE NOCASE AND n.profileid != ? AND (c.account_id IS NULL OR c.account_id != ?)",
            (name, pid, account_id),
        ).fetchone()
        nick = self._db.execute(
            "SELECT 1 FROM profiles WHERE uniquenick = ? COLLATE NOCASE AND profileid != ?", (name, pid)
        ).fetchone()
        if other or nick:
            raise AccountError(409, "That name is taken by another player.")

    def _require_owner(self, account_id: int, pid: int) -> None:
        if self._owner(pid) != account_id:
            raise AccountError(404, "You have not claimed this profile.")

    def _owner(self, pid: int) -> int | None:
        row = self._db.execute("SELECT account_id FROM portal_claims WHERE profileid=?", (pid,)).fetchone()
        return row[0] if row else None

    def _record(self, pid: int) -> int | None:
        row = self._db.execute(
            "SELECT record_id FROM records WHERE table_id=? AND owner_id=? ORDER BY record_id LIMIT 1",
            (self.profile_table, pid),
        ).fetchone()
        return row[0] if row else None

    def _field(self, pid: int, record_id: int | None, name: str):
        if record_id is None:
            return None
        row = self._db.execute(
            "SELECT value FROM fields WHERE table_id=? AND owner_id=? AND record_id=? AND name=?",
            (self.profile_table, pid, record_id, name),
        ).fetchone()
        return row[0] if row else None

    def _int_field(self, pid: int, record_id: int | None, name: str) -> int:
        try:
            return int(self._field(pid, record_id, name) or 0)
        except ValueError:
            return 0
