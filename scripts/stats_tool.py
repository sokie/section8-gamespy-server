#!/usr/bin/env python3
"""Inspect and maintain ranked careers in a section8.db. Stop the server before writing to the DB.

    python scripts/stats_tool.py show <nick|profileid>        career, records, awards and gear by name
    python scripts/stats_tool.py history <nick|profileid>     the matches behind a career
    python scripts/stats_tool.py decode <report.bin>...       print a saved report by stat name
    python scripts/stats_tool.py replay <report.bin>...      apply saved reports, as SubmitReport does
    python scripts/stats_tool.py rebuild [<nick|profileid>]   recompute careers from match history
    python scripts/stats_tool.py set-xp <xp> <nick|profileid> set career XP and its level, for testing

--db selects the database (default: section8.db next to the repo). --title selects the game
(prejudice, the default, or section8). A report applies only to roster slots that carry the ccid this
server hands out.

For Section 8 (2009) without a stat catalogue, `show` lists the raw stored fields; once
catalog/section8.json exists, `rebuild --title section8` refolds every career from match history.
"""

import argparse
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import log, screport, statdefs, stats
from server.competition import PLAYER_STATS, CompetitionService
from server.persistence import Store
from server.titles import Titles


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def resolve(store, who: str) -> int:
    if who.isdigit():
        return int(who)
    pid = store.profile_for_nick(who)
    if pid is None:
        sys.exit(f"no profile named {who!r}")
    return pid


def hms(seconds) -> str:
    s = int(seconds or 0)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def cmd_show(store, args):
    if args.title_obj.key != "prejudice":
        return cmd_show_raw(store, args)
    pid = resolve(store, args.who)
    rid = store.record_id_for_owner(PLAYER_STATS, pid)
    if rid is None:
        sys.exit(f"profile {pid} has no ranked stats")
    f = {name: value for name, (_t, value) in store.get_fields(PLAYER_STATS, pid, rid).items()}

    def v(column):
        return f.get(column, "0")

    xp = int(v(stats.XP_COLUMN))
    level = stats.level_for_xp(xp)
    nxt = statdefs.LEVEL_XP[level] if level < len(statdefs.LEVEL_XP) else None
    print(f"{store.nick_for_profile(pid) or '?'} (profileid {pid})")
    print(f"  Rank {stats.rank_for_level(level)}, level {level}, {xp} XP" + (f" (next level at {nxt})" if nxt else ""))
    print("\nCareer")
    for column in ("Ranked_Kills", "Ranked_Deaths", "Ranked_Destruction", "Ranked_ScoreTeamwork", "Ranked_ScoreCombat"):
        print(f"  {stats.describe(column):<26} {v(column)}")
    print("\nTime played")
    for gm, name in statdefs.GAME_MODES.items():
        print(f"  {name:<26} {hms(v(f'Ranked_TimePlayed_GM{gm}'))}")
    print(f"  {'All modes (TimePlayed)':<26} {hms(v('Ranked_TimePlayed'))}")
    print("\nRecords in a life")
    for s in stats.BY_KEY.values():
        if s.family == "career" and s.fold == "max":
            date = f.get(stats.DATE_COLUMN.get(s.column, ""), "")
            print(f"  {s.label:<26} {v(s.column):>8}  {date}")
    print("\nBadges")
    for i, (name, category, *_rest) in statdefs.BADGES.items():
        count = int(v(f"Ranked_Badge_B{i}"))
        if count:
            print(f"  {category:<9} {name:<24} x{count}  {f.get(f'Ranked_Badge_Date_B{i}', '')}")
    print("\nFeats (times completed, 1x/2x/3x target)")
    for i, (name, category, *_rest) in statdefs.FEATS.items():
        count = int(v(f"Ranked_Feats_F{i}"))
        if count:
            tiers = "/".join(v(f"Ranked_Feats{t}_F{i}") for t in (1, 2, 3))
            print(f"  {category:<9} {name:<26} x{count} ({tiers})  {f.get(f'Ranked_Feats_Date_F{i}', '')}")
    print("\nGear (time, kills, destructions; in a life: kills, damage, destructions)")
    for g, (weapon, mod) in statdefs.GEAR.items():
        row = [
            v(f"Ranked_{c}_G{g}")
            for c in ("TimePlayed", "Kills", "Destruction", "KillsIAL", "DamageIAL", "DestructionIAL")
        ]
        if any(x != "0" for x in row):
            print(
                f"  {weapon + ' (' + mod + ')':<40} {hms(row[0])} {row[1]:>5} {row[2]:>5} | {row[3]:>4} {row[4]:>6} {row[5]:>4}"
            )


def cmd_show_raw(store, args):
    pid = resolve(store, args.who)
    title = args.title_obj
    table = args.titles.player_table(title)
    print(f"{store.nick_for_profile(pid) or '?'} (profileid {pid}) - {title.name}, table {table}")
    rows = store.raw_fields(table, pid)
    if not rows:
        print("  no ranked stats stored")
    for _rid, name, vtype, value in rows:
        print(f"  {title.catalog.describe(name):<40} {value!s:>14}  ({vtype})")


def cmd_history(store, args):
    pid = resolve(store, args.who)
    for match_id, received, mode, xp, level in store.match_history(pid, args.title_obj.key):
        print(f"  match {match_id:<5} {received}  {mode or '?':<9} +{xp or 0} XP -> level {level}")


def cmd_decode(store, args):
    for path in args.reports:
        report = screport.parse(read_bytes(path))
        print(f"# {os.path.basename(path)}: v{report.version}, status {report.status}, {len(report.players)} player(s)")
        for block in report.players:
            print(f"  slot {block.index} connection {block.connection_id.hex()} team {block.team}")
            for key, (_t, value) in sorted(block.keys.items()):
                s = args.title_obj.catalog.by_key.get(key)
                shown = stats.filetime_to_iso(value) if s and s.fold == "date" else value
                print(f"    {key:5} {s.column if s else '(unknown key)':34} {shown!s:>20}  {s.label if s else ''}")


def cmd_replay(store, args):
    comp = CompetitionService(store, report_dir=os.devnull, titles=args.titles)
    for path in sorted(args.reports):
        print(f"replaying {os.path.basename(path)}")
        comp.ingest_report(read_bytes(path), csid="replay", title=args.title_obj)


def cmd_rebuild(store, args):
    title = args.title_obj
    table = args.titles.player_table(title)
    owners = [resolve(store, args.who)] if args.who else sorted({o for o, _r, _v in store.all_records(table, [])})
    for pid in owners:
        career = title.catalog.rebuild_career(store.match_keys(pid, title.key))
        rid = store.record_for_owner(table, pid)
        store.replace_record_fields(table, pid, rid, [(c, t, v) for c, (t, v) in career.items()])
        print(f"  {pid}: {len(career)} fields from {len(store.match_history(pid, title.key))} match(es) -> {table}")


def cmd_set_xp(store, args):
    pid = resolve(store, args.who)
    catalog = args.title_obj.catalog
    table = args.titles.player_table(args.title_obj)
    rid = store.record_for_owner(table, pid)
    level = catalog.level_for_xp(args.xp)
    store.set_fields(
        table,
        pid,
        rid,
        [(catalog.xp_column, "intValue", str(args.xp)), (catalog.level_column, "intValue", str(level))],
    )
    print(f"{pid}: {args.xp} XP, level {level} ({catalog.rank_for_level(level)})")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.join(ROOT, "section8.db"))
    ap.add_argument("--title", default="prejudice", help="prejudice (default) or section8")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").add_argument("who")
    sub.add_parser("history").add_argument("who")
    sub.add_parser("decode").add_argument("reports", nargs="+")
    sub.add_parser("replay").add_argument("reports", nargs="+")
    sub.add_parser("rebuild").add_argument("who", nargs="?")
    p = sub.add_parser("set-xp")
    p.add_argument("xp", type=int)
    p.add_argument("who")
    args = ap.parse_args(argv[1:])

    log.configure(os.path.join(tempfile.gettempdir(), "section8_stats_tool.log"))
    store = Store(args.db)
    args.titles = Titles(store=store)
    args.title_obj = args.titles.get(args.title)
    {
        "show": cmd_show,
        "history": cmd_history,
        "decode": cmd_decode,
        "replay": cmd_replay,
        "rebuild": cmd_rebuild,
        "set-xp": cmd_set_xp,
    }[args.cmd](store, args)


if __name__ == "__main__":
    main(sys.argv)
