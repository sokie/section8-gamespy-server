"""Runs scripts/carry_over.py on a synthetic old-build database.  python -m tests.smoke_carry"""

import contextlib
import io
import os
import sqlite3
import sys
import tempfile

from scripts import carry_over
from server import log, stats
from server.persistence import Store

failures = 0


def check(name, cond):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name)
    failures += not cond


def fresh(name):
    path = os.path.join(tempfile.gettempdir(), name)
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(OSError):
            os.remove(path + suffix)
    return path


log.configure(os.path.join(tempfile.gettempdir(), "s8_carry.log"))
old_path, new_path = fresh("s8_carry_old.db"), fresh("s8_carry_new.db")
old = sqlite3.connect(old_path)
old.executescript(
    "CREATE TABLE profiles(uniquenick TEXT PRIMARY KEY, profileid INTEGER);"
    "CREATE TABLE fields(table_id TEXT, owner_id INTEGER, record_id INTEGER, name TEXT, value_type TEXT, value TEXT);"
)
old.execute("INSERT INTO profiles VALUES('T8iPPLAYER', 10000001)")
rows = {
    "Ranked_xp": "4260",
    "Ranked_Level": "27",
    "Ranked_Kills": "595",
    "Ranked_Kills_Date": "2026-10-12T13:08:27",
    "Ranked_Deaths": "176.0",
    "Ranked_Kills_G33": "289",
    "Ranked_Destruction_G33": "186",
    "Ranked_Destruction_G3": "75",
    "Ranked_TimePlayed_GM1": "19232",
    "Ranked_TimePlayed_GM2": "926",
    "Ranked_Badge_B8": "29",
    "Ranked_DefenseIAL": "224",
    "Ranked_DamageIAL": "23414",
    "Ranked_wkey_1": "6",
    "Ranked_Rank": "27",
}
old.executemany("INSERT INTO fields VALUES('PlayerStats_v6', 10000001, 1, ?, 'intValue', ?)", list(rows.items()))
old.execute("INSERT INTO fields VALUES('PlayerProfile2_v6', 10000001, 1, 'Profile_RandomLinkCode', 'intValue', '2207')")
old.commit()
old.close()

store = Store(new_path)
store.get_or_create_profile("DediHost")
pid = store.get_or_create_profile("T8iPPLAYER")
del store


def run(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        carry_over.main([old_path, new_path, *args])
    return out.getvalue()


dry = run()
check("dry run reports the carry", "4260" in dry and "dry run" in dry)
check("dry run writes nothing", sqlite3.connect(new_path).execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0)
run("--apply")
st = Store(new_path)
car = {
    n: v for n, (_t, v) in st.get_fields("PlayerStats_v6", pid, st.record_id_for_owner("PlayerStats_v6", pid)).items()
}
check("player keeps the new profileid", pid == 10000002)
check(
    "xp, level, kills, badge carried",
    (car["Ranked_xp"], car["Ranked_Level"], car["Ranked_Kills"], car["Ranked_Badge_B8"]) == ("4260", "27", "595", "29"),
)
check("dates carried", car.get("Ranked_Kills_Date") == "2026-10-12T13:08:27")
check("totals derived", (car.get("Ranked_TimePlayed"), car.get("Ranked_Destruction")) == ("20158", "261"))
check(
    "untrusted record and raw keys left out",
    "Ranked_DefenseIAL" not in car and not any(k.startswith("Ranked_wkey") for k in car),
)
check("trusted record kept", car.get("Ranked_DamageIAL") == "23414")
check("a whole number stored as text is carried as a number", car.get("Ranked_Deaths") == "176")
check(
    "history rebuilds the same career",
    stats.rebuild_career(st.match_keys(pid))
    == st.get_fields("PlayerStats_v6", pid, st.record_id_for_owner("PlayerStats_v6", pid)),
)
check("profile record copied", st.record_id_for_owner("PlayerProfile2_v6", pid) is not None)
del st
again = run("--apply")
check("a second run changes nothing", "already carried over" in again)
again = run("--apply", "--xp-only")
st = Store(new_path)
xp = st.get_fields("PlayerStats_v6", pid, st.record_id_for_owner("PlayerStats_v6", pid))["Ranked_xp"][1]
del st
check("a second run with other flags changes nothing", "already carried over" in again and xp == "4260")
print("ALL CARRY-OVER TESTS PASSED" if not failures else f"{failures} CARRY-OVER TEST(S) FAILED")
sys.exit(1 if failures else 0)
