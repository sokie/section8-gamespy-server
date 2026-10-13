#!/usr/bin/env python3
"""Writes marker values into a player's PlayerStats_v6 columns, to show which column drives which row
on the RANKED STATS and AWARDS screens. Stop the server first. Restart the game after, because it reads
the stats at login.

    python scripts/probe_columns.py <set> <nick|profileid> [--clear] [--db section8.db]

Sets:
  records     the RECORDS (in a life) rows, each with its own value
  career      Kills, Deaths, Destruction, Level
  timeplayed  the TIME PLAYED rows; each mode gets a distinct h:mm:ss
  badges      Badge_B<n> = n (slot 0 shows 100), with a date, on the AWARDS screen
  feats       Feats_F<n> = n (slot 0 shows 100), with a date, on the AWARDS screen
  gear        Kills_G<n> = n (slot 0 shows 100) on the GEAR screen

--clear writes zeros instead. The probe overwrites real values, so copy the DB first.
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import statdefs, stats
from server.competition import PLAYER_STATS
from server.persistence import Store

PROBE_DATE = "2026-01-01T00:00:00"


def slot_value(i: int) -> int:
    # 0 reads as "not earned", so slot 0 shows 100 instead.
    return 100 if i == 0 else i


def build(set_name: str) -> list[tuple[str, int]]:
    if set_name == "records":
        columns = [s.column for s in stats.BY_KEY.values() if s.family == "career" and s.fold == "max"]
        return [(c, 11111 * (i + 1)) for i, c in enumerate(columns)]
    if set_name == "career":
        return [("Ranked_Kills", 31337), ("Ranked_Deaths", 31338), ("Ranked_Destruction", 31339), ("Ranked_Level", 42)]
    if set_name == "timeplayed":
        # 3661 s per step renders as 01:01:01, 02:02:02, ...
        cols = ["Ranked_TimePlayed"] + [f"Ranked_TimePlayed_GM{gm}" for gm in statdefs.GAME_MODES]
        return [(c, 3661 * (i + 1)) for i, c in enumerate(cols)]
    if set_name == "badges":
        return [(f"Ranked_Badge_B{i}", slot_value(i)) for i in statdefs.BADGES]
    if set_name == "feats":
        return [(f"Ranked_Feats_F{i}", slot_value(i)) for i in statdefs.FEATS]
    if set_name == "gear":
        return [(f"Ranked_Kills_G{i}", slot_value(i)) for i in range(60)]
    raise SystemExit(f"unknown set {set_name}")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set", choices=["records", "career", "timeplayed", "badges", "feats", "gear"])
    ap.add_argument("who")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--db", default=os.path.join(ROOT, "section8.db"))
    a = ap.parse_args(argv[1:])

    store = Store(a.db)
    pid = int(a.who) if a.who.isdigit() else store.profile_for_nick(a.who)
    if not pid:
        raise SystemExit(f"no profile named {a.who!r}")
    rid = store.record_for_owner(PLAYER_STATS, pid)
    writes = []
    print(f"{'column':<34} {'value':>8}  shown as")
    for column, value in build(a.set):
        v = 0 if a.clear else value
        writes.append((column, "intValue", str(v)))
        date_column = stats.DATE_COLUMN.get(column)
        if date_column and a.set in ("badges", "feats"):
            writes.append((date_column, "dateAndTimeValue", stats.SAKE_DATE_ZERO if a.clear else PROBE_DATE))
        print(f"{column:<34} {v:>8}  {stats.describe(column)}")
    store.set_fields(PLAYER_STATS, pid, rid, writes)
    print(f"\n{len(writes)} field(s) written for profile {pid}. Restart the game, open the screen, compare.")


if __name__ == "__main__":
    main(sys.argv)
