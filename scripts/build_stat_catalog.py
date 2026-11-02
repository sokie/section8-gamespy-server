#!/usr/bin/env python3
"""Generates server/statdefs.py and docs/stat_catalog.csv from the game's packages.

Unpack the packages first with decompress.exe from S9Game/CookedPCFinal:

    decompress.exe -out=unpacked OnlineSubsystemGameSpy.u S9Game.u TGEngine.u
    python scripts/build_stat_catalog.py <unpacked dir>
"""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from upk import Package, struct_rows

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RANKED_VIEW = 1
GEAR_SLOTS = 60

FOLD = {"TGE_SB_COUNTER": "sum", "TGE_SB_IALMAX": "max", "TGE_SB_OVERWRITE": "replace"}
STAT_LABEL = {
    ("TGE_SE_KILL", "TGE_SB_COUNTER"): "Kills",
    ("TGE_SE_DEATH", "TGE_SB_COUNTER"): "Deaths",
    ("TGE_SE_LEVEL", "TGE_SB_OVERWRITE"): "Level",
    ("TGE_SE_XP", "TGE_SB_COUNTER"): "XP",
    ("TGE_SE_DESTRUCTION", "TGE_SB_COUNTER"): "Destructions",
    ("TGE_SE_TIMEPLAYED", "TGE_SB_COUNTER"): "Time played (s)",
    ("TGE_SE_SCORETEAMWORK", "TGE_SB_COUNTER"): "Teamwork score",
    ("TGE_SE_SCORECOMBAT", "TGE_SB_COUNTER"): "Combat score",
    ("TGE_SE_KILL", "TGE_SB_IALMAX"): "Kills in a life",
    ("TGE_SE_CAPTURE", "TGE_SB_IALMAX"): "Captures in a life",
    ("TGE_SE_XP", "TGE_SB_IALMAX"): "XP in a life",
    ("TGE_SE_SCORETEAMWORK", "TGE_SB_IALMAX"): "Teamwork score in a life",
    ("TGE_SE_SCORECOMBAT", "TGE_SB_IALMAX"): "Combat score in a life",
    ("TGE_SE_DAMAGEDEALT", "TGE_SB_IALMAX"): "Damage in a life",
    ("TGE_SE_REPAIR", "TGE_SB_IALMAX"): "Repairs in a life",
    ("TGE_SE_ASSIST", "TGE_SB_IALMAX"): "Assists in a life",
    ("TGE_SE_DEFENSE", "TGE_SB_IALMAX"): "Defenses in a life",
    ("TGE_SE_DESTRUCTION", "TGE_SB_IALMAX"): "Destructions in a life",
}
FEAT_TIER = {
    "TGE_SE_FEAT": ("Feats", "times completed"),
    "TGE_SE_FEAT1": ("Feats1", "completed at 1x the target"),
    "TGE_SE_FEAT2": ("Feats2", "completed at 2x the target"),
    "TGE_SE_FEAT3": ("Feats3", "completed at 3x the target or more"),
}
# The cooked script has no PROPERTY_GSSTAT_* names, so these ids take the S9StatReadRanked column names.
SYSTEM_STATS = {2500: ("ClanTag", "Clan tag", "string"), 2501: ("Locale", "Client locale", "int")}
# PROPERTY_GSSTAT_RANKEDPLAYERNAME. The game writes it only to the PS3 stats table.
PS3_PLAYER_NAME = 2526


def title(s: str) -> str:
    return " ".join(w.capitalize() if w.isupper() else w for w in s.split())


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    pkgs = argv[1]
    gamespy = Package(os.path.join(pkgs, "OnlineSubsystemGameSpy.u"))
    s9 = Package(os.path.join(pkgs, "S9Game.u"))
    tg = Package(os.path.join(pkgs, "TGEngine.u"))

    key_for = {
        m["PropertyId"]: m["KeyId"]
        for m in struct_rows(gamespy.defaults("OnlineSubsystemGameSpy")["StatsKeyMappings"])
        if m.get("ViewId") == RANKED_VIEW
    }
    write = s9.defaults("S9StatWriteRanked")
    write_base = tg.defaults("TGStatWriteBase")
    stride = {
        "gear": write_base["StatsPerGearId"],
        "badge": write_base["StatsPerBadgeId"],
        "feat": write_base["StatsPerFeatId"],
        "game_mode": write_base["StatsPerGameModeID"],
    }
    manager = s9.defaults("S9StatManager")
    manager_base = tg.defaults("TGStatManager")
    read_columns = {
        c["Name"].lower(): c["Name"] for c in struct_rows(s9.defaults("S9StatReadRanked")["ColumnMappings"])
    }

    badges, feats = read_awards(s9)
    gear = read_gear(s9)
    game_modes = read_game_modes(s9)
    ranks = s9.defaults("S9RankConquest")
    level_xp = ranks["LevelExperienceRequired"]
    rank_names = [(r["LevelRequired"], r["DisplayName"]) for r in struct_rows(ranks["GameRanks"])]

    stats = []

    def column(stem):
        return "Ranked_" + read_columns.get(stem.lower(), stem)

    def add(prop, stem, fold, family, index, label, sake_type="int", date_of=None):
        stats.append(
            {
                "key": key_for.get(prop),
                "property": prop,
                "column": column(stem),
                "fold": fold,
                "family": family,
                "index": index,
                "label": label,
                "sake_type": "date" if fold == "date" else sake_type,
                "date_of": column(date_of) if date_of else None,
                "read_by_game": stem.lower() in read_columns,
            }
        )

    def field(event, behavior, items):
        for item in struct_rows(items):
            if item.get("StatEvent") == event and item.get("Behavior") == behavior and item.get("OnlineField"):
                return item["OnlineField"]
        raise KeyError((event, behavior))

    for m in struct_rows(write["StatManagerMapping"]):
        event, behavior = m["StatEvent"], m["Behavior"]
        prop, date_prop = m["PropertyId"], m.get("DatePropertyId", -1)
        fold = FOLD[behavior]
        if m.get("GameModeID", -1) >= 0:
            stem = field(event, behavior, manager["GameModeStats"])
            for gm in range(manager_base["NumberOfGameModes"]):
                name = game_modes.get(gm, f"Game mode {gm}")
                step = stride["game_mode"] * gm
                add(prop + step, f"{stem}_GM{gm}", fold, "game_mode", gm, f"Time played (s): {name}")
                if date_prop >= 0:
                    add(
                        date_prop + step,
                        f"{stem}_Date_GM{gm}",
                        "date",
                        "game_mode",
                        gm,
                        f"Time played: {name}, last played",
                        date_of=f"{stem}_GM{gm}",
                    )
        elif m.get("GearId", -1) >= 0:
            stem = field(event, behavior, manager["GearStats"])
            what = STAT_LABEL[(event, behavior)]
            for g in range(GEAR_SLOTS):
                name = gear_label(gear.get(g), g)
                step = stride["gear"] * g
                add(prop + step, f"{stem}_G{g}", fold, "gear", g, f"{name}: {what}")
                if date_prop >= 0:
                    add(
                        date_prop + step,
                        f"{stem}_Date_G{g}",
                        "date",
                        "gear",
                        g,
                        f"{name}: {what.replace(' (s)', '')}, date",
                        date_of=f"{stem}_G{g}",
                    )
        elif m.get("FeatId", -1) >= 0:
            stem, what = FEAT_TIER[event]
            for f in range(manager_base["NumberOfFeats"]):
                name = feats.get(f, {}).get("name", f"Feat {f}")
                step = stride["feat"] * f
                add(prop + step, f"{stem}_F{f}", fold, "feat", f, f"Feat {name}: {what}")
                if date_prop >= 0:
                    add(
                        date_prop + step,
                        f"{stem}_Date_F{f}",
                        "date",
                        "feat",
                        f,
                        f"Feat {name}: date earned",
                        date_of=f"{stem}_F{f}",
                    )
        elif m.get("BadgeId", -1) >= 0:
            for b in range(manager_base["NumberOfBadges"]):
                name = badges.get(b, {}).get("name", f"Badge {b}")
                step = stride["badge"] * b
                add(prop + step, f"Badge_B{b}", fold, "badge", b, f"Badge {name}: times earned")
                if date_prop >= 0:
                    add(
                        date_prop + step,
                        f"Badge_Date_B{b}",
                        "date",
                        "badge",
                        b,
                        f"Badge {name}: date earned",
                        date_of=f"Badge_B{b}",
                    )
        else:
            stem = field(event, behavior, manager["InitStats"])
            label = STAT_LABEL[(event, behavior)]
            add(prop, stem, fold, "career", None, label)
            if date_prop >= 0:
                add(
                    date_prop,
                    f"{stem}_Date",
                    "date",
                    "career",
                    None,
                    f"{label.replace(' (s)', '')}, date",
                    date_of=stem,
                )

    for prop in write["SystemStats"]:
        stem, label, sake_type = SYSTEM_STATS[prop]
        add(prop, stem, "replace", "system", None, label, sake_type)
    add(PS3_PLAYER_NAME, "PlayerName", "replace", "system", None, "Player name (PS3 only, not sent on PC)", "string")

    check(stats, key_for, read_columns)
    stats.sort(key=lambda s: s["key"])
    write_module(stats, badges, feats, gear, game_modes, level_xp, rank_names, stride)
    write_csv(stats, badges, feats, gear, game_modes)
    print(f"{len(stats)} stats, {len(badges)} badges, {len(feats)} feats, {len(gear)} gear, {len(game_modes)} modes")
    return 0


def read_awards(s9):
    categories = {}
    for e in s9.exports:
        if e["name"].startswith("Default__S9AwardCategory"):
            d = s9.defaults(e["name"][len("Default__") :])
            if d.get("DisplayName"):
                categories[e["name"][len("Default__") :]] = d["DisplayName"]
    badges, feats = {}, {}
    for e in s9.exports:
        name = e["name"]
        if not (name.startswith("Default__S9Badge") or name.startswith("Default__S9Feat")):
            continue
        d = s9.defaults(name[len("Default__") :])
        if d.get("AwardId", -1) < 0:
            continue
        entry = {
            "name": d["DisplayName"],
            "category": categories.get(d.get("AwardCategory"), ""),
            "description": d.get("Description", "").replace("%Amount%", str(d.get("TargetValue", 1))),
            "target": d.get("TargetValue", 1),
            "class": name[len("Default__") :],
        }
        if name.startswith("Default__S9Badge"):
            entry["reward_xp"] = d.get("BadgeRewardXP", 0)
            badges[d["AwardId"]] = entry
        else:
            feats[d["AwardId"]] = entry
    return badges, feats


def read_gear(s9):
    classes = {e["name"]: e for e in s9.exports if s9.class_name(e) == "Class"}
    gear = {}
    for e in s9.exports:
        name = e["name"]
        if not name.startswith("Default__S9GearDefinition"):
            continue
        cls = name[len("Default__") :]
        d = s9.defaults(cls)
        if d.get("GearId", -1) < 0 or d.get("PointCost", 0) <= 0:
            continue
        parent = s9.object_name(classes[cls]["super"])
        weapon = s9.defaults(parent).get("DisplayName", parent)
        gear[d["GearId"]] = {"weapon": title(weapon), "mod": title(d.get("DisplayName", cls)), "class": cls}
    return gear


def read_game_modes(s9):
    modes = {}
    for e in s9.exports:
        if e["name"].startswith("Default__S9GameInfo"):
            d = s9.defaults(e["name"][len("Default__") :])
            if d.get("DisplayName") and d.get("GameModeID", -1) >= 0:
                modes[d["GameModeID"]] = title(d["DisplayName"])
    return modes


def gear_label(g, slot):
    return f"{g['weapon']} ({g['mod']})" if g else f"Unused gear slot {slot}"


def check(stats, key_for, read_columns):
    keys = [s["key"] for s in stats]
    assert None not in keys, "a written property has no StatsKeyMappings entry"
    assert len(keys) == len(set(keys)), "two properties share a report key"
    assert {s["property"] for s in stats} == set(key_for), "StatsKeyMappings and the catalogue disagree"
    written = {s["column"][len("Ranked_") :].lower() for s in stats}
    assert set(read_columns) <= written, f"read but never written: {set(read_columns) - written}"


def write_module(stats, badges, feats, gear, game_modes, level_xp, rank_names, stride):
    lines = [
        '"""Generated by scripts/build_stat_catalog.py from the game\'s packages. Do not edit.',
        "",
        "Each STATS row holds the fields of stats.StatDef.",
        '"""',
        "",
        f"STRIDE = {stride!r}",
        "",
        "STATS = (",
    ]
    for s in stats:
        row = (
            s["key"],
            s["property"],
            s["column"],
            s["fold"],
            s["family"],
            s["index"],
            s["label"],
            s["sake_type"],
            s["date_of"],
            s["read_by_game"],
        )
        lines.append(f"    {row!r},")
    lines += [")", "", "# id: (name, category, description, target, XP reward)", "BADGES = {"]
    for i in sorted(badges):
        b = badges[i]
        lines.append(f"    {i}: {(b['name'], b['category'], b['description'], b['target'], b['reward_xp'])!r},")
    lines += ["}", "", "# id: (name, category, description, target)", "FEATS = {"]
    for i in sorted(feats):
        f = feats[i]
        lines.append(f"    {i}: {(f['name'], f['category'], f['description'], f['target'])!r},")
    lines += ["}", "", "# gear id: (weapon, mod). Ids missing here are unused slots.", "GEAR = {"]
    for i in sorted(gear):
        lines.append(f"    {i}: {(gear[i]['weapon'], gear[i]['mod'])!r},")
    lines += ["}", "", f"GAME_MODES = {dict(sorted(game_modes.items()))!r}", ""]
    lines += [
        "# Cumulative XP needed for each level.",
        "LEVEL_XP = (",
    ]
    for i in range(0, len(level_xp), 10):
        lines.append("    " + ", ".join(str(x) for x in level_xp[i : i + 10]) + ",")
    lines += [")", "", "# (lowest level, rank name)", "RANKS = ("]
    for lvl, name in rank_names:
        lines.append(f"    {(lvl, name)!r},")
    lines += [")", ""]
    with open(os.path.join(ROOT, "server", "statdefs.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


def write_csv(stats, badges, feats, gear, game_modes):
    names = {
        "badge": {i: f"{b['name']} ({b['category']})" for i, b in badges.items()},
        "feat": {i: f"{f['name']} ({f['category']})" for i, f in feats.items()},
        "gear": {i: gear_label(g, i) for i, g in gear.items()},
        "game_mode": game_modes,
    }
    path = os.path.join(ROOT, "docs", "stat_catalog.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        out = csv.writer(f, lineterminator="\n")
        out.writerow(["key", "property", "column", "fold", "family", "id", "name", "stat", "read_by_game"])
        for s in stats:
            item = names.get(s["family"], {}).get(s["index"], "") if s["index"] is not None else ""
            out.writerow(
                [
                    s["key"],
                    s["property"],
                    s["column"],
                    s["fold"],
                    s["family"],
                    "" if s["index"] is None else s["index"],
                    item,
                    s["label"],
                    "yes" if s["read_by_game"] else "no",
                ]
            )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
