#!/usr/bin/env python3
"""Fake-XP probe, to fit statmap's LEVEL_CURVE_A/B to the client's own XP-to-level curve.

Usage:  python scripts/set_test_xp.py <xp> [profileid]   (profileid defaults to 10000001, the host)

Sets Ranked_xp for that owner, with the level statmap predicts, then log in and read the level the
client actually shows on RANKED STATS plus the "NEXT" threshold on the bar. Sweep a spread, one login
each, for example 25, 125, 250, 1000, 14800, 59500.
"""

import os
import sys

# Runnable from any directory: resolve the repo root, and the DB inside it, from this file.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from server import statmap
from server.persistence import Store

DB = os.path.join(_ROOT, "section8.db")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return
    xp = int(argv[1])
    owner = int(argv[2]) if len(argv) > 2 else 10000001
    store = Store(DB)
    rid = store.record_id_for_owner("PlayerStats_v6", owner) or store.create_record("PlayerStats_v6", owner)
    predicted = statmap.level_for_xp(xp)
    store.set_fields(
        "PlayerStats_v6",
        owner,
        rid,
        [
            ("Ranked_xp", "intValue", str(xp)),
            ("Ranked_XPIAL", "intValue", str(xp)),
            ("Ranked_Level", "intValue", str(predicted)),
            ("Ranked_Rank", "intValue", str(predicted)),
        ],
    )
    lrid = store.record_id_for_owner("S8Level_v6", owner) or store.create_record("S8Level_v6", owner)
    store.set_fields("S8Level_v6", owner, lrid, [("Ranked_Level", "intValue", str(predicted))])
    nxt = statmap.xp_for_level(predicted + 1)
    print(
        f"owner {owner}: Ranked_xp={xp}  -> our curve predicts Level {predicted} "
        f"(next level at {nxt} XP). Log in and note the level/NEXT the GAME shows."
    )


if __name__ == "__main__":
    main(sys.argv)
