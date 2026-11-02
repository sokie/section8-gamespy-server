#!/usr/bin/env python3
"""Carries Prejudice careers from a database written by an older build into a current one.

    python scripts/carry_over.py OLD.db NEW.db              dry run: shows what would be carried
    python scripts/carry_over.py OLD.db NEW.db --apply      writes NEW.db (a backup is made first)
    python scripts/carry_over.py OLD.db NEW.db --apply --xp-only   carries XP only

Stop the server first, and keep each database's -wal and -shm files next to it. The tool matches players
by GameSpy uniquenick and adds the old career with the server's own fold, as one "carried over" match, so
a second run changes nothing. It skips the two records older builds stored wrongly (Ranked_AssistIAL,
Ranked_DefenseIAL), the level, S8Level_v6 and unknown names. It derives Ranked_TimePlayed and
Ranked_Destruction from the per-mode and per-gear values, unless --no-derive.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import stats  # noqa: E402

PLAYER = "PlayerStats_v6"
PROFILE = "PlayerProfile2_v6"
ZERO = ("", "0", "0.0", stats.SAKE_DATE_ZERO)
# An older build stored other stats under these names. New matches set them again.
UNTRUSTED = {"Ranked_AssistIAL", "Ranked_DefenseIAL"}


def fields(conn, table, owner):
    rows = conn.execute(
        "SELECT record_id, name, value_type, value FROM fields WHERE table_id=? AND owner_id=? ORDER BY record_id",
        (table, owner),
    ).fetchall()
    if not rows:
        return None, {}
    rid = rows[0][0]
    return rid, {n: (t, v) for r, n, t, v in rows if r == rid}


def whole(value) -> str | None:
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return None


def old_keys(old: dict, derive: bool, xp_only: bool):
    """Gives the carried values as report keys {key: value text}, plus notes."""
    notes, keys = [], {}
    values = {n: v for n, (_t, v) in old.items()}
    if xp_only:
        xp = whole(values.get(stats.XP_COLUMN)) or "0"
        return {stats.BY_COLUMN[stats.XP_COLUMN].key: xp}, notes
    dropped = sorted(n for n in values if n not in stats.BY_COLUMN and n != "ownerid")
    for name, value in values.items():
        s = stats.BY_COLUMN.get(name)
        if s is None or name == stats.LEVEL_COLUMN or value in ZERO or value is None:
            continue
        if name in UNTRUSTED or (s.fold == "date" and s.date_of in UNTRUSTED):
            continue
        # An older build can store a whole number as text such as "12.0".
        if s.sake_type == "int":
            value = whole(value)
            if value is None:
                dropped.append(name)
                continue
        keys[s.key] = value
    if derive:
        for total, pattern in (
            ("Ranked_TimePlayed", r"Ranked_TimePlayed_GM\d+"),
            ("Ranked_Destruction", r"Ranked_Destruction_G\d+"),
        ):
            have = values.get(total)
            if have not in (None, *ZERO) or total not in stats.BY_COLUMN:
                continue
            parts = sum(int(float(v or 0)) for n, v in values.items() if re.fullmatch(pattern, n))
            if parts:
                keys[stats.BY_COLUMN[total].key] = str(parts)
                notes.append(f"{total} was never stored; derived {parts} from {pattern.replace(chr(92) + 'd+', 'N')}")
    skipped = sorted(n for n in UNTRUSTED if values.get(n) not in (None, *ZERO))
    if skipped:
        notes.append(
            "left out per-life records that disagree with the raw keys: "
            + ", ".join(f"{n}={values[n]}" for n in skipped)
        )
    wkeys = [n for n in dropped if n.startswith("Ranked_wkey_")]
    other = [n for n in dropped if not n.startswith("Ranked_wkey_")]
    if wkeys:
        notes.append(f"left out {len(wkeys)} Ranked_wkey_<n> fields (last-match raw values, not lifetime totals)")
    if other:
        notes.append("left out unknown names: " + ", ".join(other))
    return keys, notes


def ensure_profile(conn, nick):
    row = conn.execute("SELECT profileid FROM profiles WHERE uniquenick=?", (nick,)).fetchone()
    if row:
        return row[0], False
    pid = conn.execute("SELECT COALESCE(MAX(profileid), 10000000) + 1 FROM profiles").fetchone()[0]
    conn.execute("INSERT INTO profiles(uniquenick, profileid) VALUES(?,?)", (nick, pid))
    return pid, True


def ensure_record(conn, table, owner):
    row = conn.execute("SELECT record_id FROM records WHERE table_id=? AND owner_id=?", (table, owner)).fetchone()
    if row:
        return row[0]
    rid = conn.execute("SELECT COALESCE(MAX(record_id), 0) + 1 FROM records WHERE table_id=?", (table,)).fetchone()[0]
    conn.execute("INSERT INTO records(table_id, owner_id, record_id) VALUES(?,?,?)", (table, owner, rid))
    return rid


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--xp-only", action="store_true")
    ap.add_argument("--no-derive", action="store_true")
    a = ap.parse_args(argv)

    old = sqlite3.connect(f"file:{os.path.abspath(a.old)}?mode=ro", uri=True)
    if not os.path.exists(a.new):
        sys.exit(f"no database at {a.new}")
    version = sqlite3.connect(a.new).execute("PRAGMA user_version").fetchone()[0]
    if version < 2:
        sys.exit(
            f"{a.new} has schema {version}. Start the current server on it once (it upgrades the schema), "
            "stop it, then run this again."
        )
    if a.apply:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(a.new + suffix):
                shutil.copy2(a.new + suffix, f"{a.new}.before-carry-{stamp}{suffix}")
        print(f"backup: {a.new}.before-carry-{stamp}")
    new = sqlite3.connect(a.new, timeout=1)
    try:
        new.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        sys.exit(f"{a.new} is locked. Stop the server first.")

    match_cols = [c[1] for c in new.execute("PRAGMA table_info(matches)")]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    owners = [r[0] for r in old.execute("SELECT DISTINCT owner_id FROM fields WHERE table_id=?", (PLAYER,))]
    if not owners:
        print("the old database holds no PlayerStats_v6 careers")
    for owner in owners:
        nick_row = old.execute("SELECT uniquenick FROM profiles WHERE profileid=?", (owner,)).fetchone()
        if not nick_row:
            print(f"old profile {owner}: no uniquenick, skipped")
            continue
        nick = nick_row[0]
        _rid, career_old = fields(old, PLAYER, owner)
        keys, notes = old_keys(career_old, not a.no_derive, a.xp_only)
        pid, created = ensure_profile(new, nick)
        sha = f"carried-over:{nick}"
        print(f"\n{nick}: old profile {owner} -> new profile {pid}{' (created)' if created else ''}")
        if new.execute("SELECT 1 FROM matches WHERE report_sha256=?", (sha,)).fetchone():
            print("  already carried over, nothing to do")
            continue
        xp_key = stats.BY_COLUMN[stats.XP_COLUMN].key
        if (
            xp_key in keys
            and new.execute("SELECT 1 FROM matches WHERE report_sha256=?", (f"migrated-career-xp:{pid}",)).fetchone()
        ):
            del keys[xp_key]
            notes.append("XP left out: the new database kept it when it upgraded this career")
        rid, career_new = fields(new, PLAYER, pid)
        result = stats.fold_match(career_new, stats.keys_from_stored(keys))
        xp_old = int(keys.get(xp_key, "0"))
        xp_new = int(float(career_new.get(stats.XP_COLUMN, ("", "0"))[1] or 0))
        print(
            f"  XP {xp_new} + {xp_old} = {xp_new + xp_old} -> level {result.level} ({stats.rank_for_level(result.level)})"
        )
        print(f"  {len(keys)} stats carried, {len(result.updates)} career fields written")
        for label in (
            "Ranked_Kills",
            "Ranked_Deaths",
            "Ranked_TimePlayed",
            "Ranked_ScoreCombat",
            "Ranked_ScoreTeamwork",
        ):
            if label in result.updates:
                print(f"    {stats.describe(label)}: {result.updates[label][1]}")
        badges = sum(1 for k in result.updates if re.fullmatch(r"Ranked_Badge_B\d+", k))
        feats = sum(1 for k in result.updates if re.fullmatch(r"Ranked_Feats_F\d+", k))
        print(f"    badges earned: {badges}, feats earned: {feats}")
        for n in notes:
            print(f"  note: {n}")
        if not a.apply:
            continue
        rid = rid or ensure_record(new, PLAYER, pid)
        new.executemany(
            "INSERT INTO fields(table_id, owner_id, record_id, name, value_type, value) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(table_id, owner_id, record_id, name) DO UPDATE SET value_type=excluded.value_type, value=excluded.value",
            [(PLAYER, pid, rid, n, t, v) for n, (t, v) in result.updates.items()],
        )
        cols = {"report_sha256": sha, "received_at": now, "csid": "migration", "player_count": 1}
        if "title" in match_cols:
            cols["title"] = "prejudice"
        cur = new.execute(
            f"INSERT INTO matches({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})", tuple(cols.values())
        )
        new.execute(
            "INSERT INTO match_players(match_id, slot, profileid, xp_gained, level_after) VALUES(?,0,?,?,?)",
            (cur.lastrowid, pid, result.xp_gained, result.level),
        )
        new.executemany(
            "INSERT INTO match_stats(match_id, slot, key, column_name, value) VALUES(?,0,?,?,?)",
            [(cur.lastrowid, k, c, v) for k, c, v in result.match_values],
        )
        prof_rid, prof_new = fields(new, PROFILE, pid)
        _r, prof_old = fields(old, PROFILE, owner)
        if prof_old and not prof_new:
            prid = ensure_record(new, PROFILE, pid)
            new.executemany(
                "INSERT OR REPLACE INTO fields(table_id, owner_id, record_id, name, value_type, value) VALUES(?,?,?,?,?,?)",
                [(PROFILE, pid, prid, n, t, v) for n, (t, v) in prof_old.items()],
            )
            print(f"  copied {PROFILE} ({len(prof_old)} fields)")
    if a.apply:
        new.commit()
        new.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        print("\nwritten. Start the server and log in.")
    else:
        new.rollback()
        print("\ndry run: nothing written. Add --apply to write (stop the server first).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
