#!/usr/bin/env python3
"""Generates catalog/section8.json, the Section 8 (2009) stat catalogue, from the game's packages, and
docs/stat_catalog_section8.csv from that.

Unpack OnlineSubsystemGameSpy.u first with decompress.exe. S8Game.u ships uncompressed.

    python scripts/build_section8_catalog.py <S8Game.u> <unpacked OnlineSubsystemGameSpy.u>
    python scripts/build_section8_catalog.py --csv      only the CSV, from catalog/section8.json
"""

import csv
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from upk import Package, struct_rows  # noqa: E402

CATALOG_PATH = os.path.join(ROOT, "catalog", "section8.json")
CSV_PATH = os.path.join(ROOT, "docs", "stat_catalog_section8.csv")
RANKED_VIEW = 1
GEAR_SLOTS = 16
CAREER_BLOCKS = ((0, 15), (100, 115))  # In these ranges, the write property id is one below the read id.
SYSTEM = {"clantag": ("Clan tag", "string"), "Locale": ("Client locale", "int")}
CAREER_LABEL = {
    "Kills": "Kills",
    "Deaths": "Deaths",
    "Level": "Level",
    "XP": "XP",
    "Destruction": "Destructions",
    "TimePlayed": "Time played (s)",
    "KillsIAL": "Kills in a life",
    "CapturesIAL": "Captures in a life",
    "XPIAL": "XP in a life",
    "ScoreTeamworkIAL": "Teamwork score in a life",
    "ScoreCombatIAL": "Combat score in a life",
    "DamageIAL": "Damage in a life",
    "RepairIAL": "Repairs in a life",
    "AssistIAL": "Assists in a life",
    "DefenseIAL": "Defenses in a life",
    "DestructionIAL": "Destructions in a life",
}
FEAT_TIER = {
    "Feats": "times completed",
    "Feats1": "completed at 1x the target",
    "Feats2": "completed at 2x the target",
    "Feats3": "completed at 3x the target or more",
}


def read_id(prop: int) -> int:
    return prop + 1 if any(a <= prop <= b for a, b in CAREER_BLOCKS) else prop


def tagged(pkg: Package, export: dict, name: str, typ: str):
    """Finds the value offset of one tagged property in an export by its tag bytes. Works on property
    types the generic reader cannot parse, which some badge and feat defaults use."""
    try:
        pat = struct.pack("<iiii", pkg.names.index(name), 0, pkg.names.index(typ), 0)
    except ValueError:
        return None
    p = pkg.data.find(pat, export["off"], export["off"] + export["size"])
    return None if p < 0 else p + 24


def int_prop(pkg, e, name, default=None):
    q = tagged(pkg, e, name, "IntProperty")
    return default if q is None else struct.unpack_from("<i", pkg.data, q)[0]


def str_prop(pkg, e, name):
    q = tagged(pkg, e, name, "StrProperty")
    if q is None:
        return None
    n = struct.unpack_from("<i", pkg.data, q)[0]
    if n >= 0:
        return pkg.data[q + 4 : q + 4 + n - 1].decode("latin-1")
    return pkg.data[q + 4 : q + 4 - 2 * n - 2].decode("utf-16le")


def obj_prop(pkg, e, name):
    q = tagged(pkg, e, name, "ObjectProperty")
    return None if q is None else pkg.object_name(struct.unpack_from("<i", pkg.data, q)[0])


def title(s):
    return " ".join(w.capitalize() if w.isupper() else w for w in (s or "").split())


def awards(s8: Package):
    cats = {}
    for e in s8.exports:
        if e["name"].startswith("Default__S8AwardCategory"):
            cats[e["name"][len("Default__") :]] = str_prop(s8, e, "DisplayName") or ""
    badges, feats = {}, {}
    for e in s8.exports:
        n = e["name"]
        kind = "badge" if n.startswith("Default__S8Badge") else "feat" if n.startswith("Default__S8Feat") else None
        if not kind:
            continue
        aid = int_prop(s8, e, "AwardId")
        if aid is None or aid < 0:
            continue
        target = int_prop(s8, e, "TargetValue", 1)
        desc = (str_prop(s8, e, "Description") or "").replace("%Amount%", str(target)).replace("%%", "%")
        row = [str_prop(s8, e, "DisplayName") or n, cats.get(obj_prop(s8, e, "AwardCategory"), ""), desc, target]
        if kind == "badge":
            badges[aid] = row + [int_prop(s8, e, "BadgeRewardXP", 0)]
        else:
            feats[aid] = row
    return badges, feats


def gear(s8: Package):
    out = {}
    for e in s8.exports:
        n = e["name"]
        if n.startswith("Default__S8Gear"):
            gid = int_prop(s8, e, "GearId")
            if gid is not None and gid >= 0:
                out[gid] = [title(str_prop(s8, e, "DisplayName") or n[len("Default__S8Gear") :]), ""]
    return out


def item_name(catalog, family, idx) -> str:
    """The badge, feat or weapon that a stat belongs to, named as in docs/stat_catalog.csv."""
    table = {"badge": "badges", "feat": "feats", "gear": "gear"}.get(family)
    if table is None or idx is None:
        return ""
    entry = catalog.get(table, {}).get(str(idx))
    if family == "gear":
        if not entry:
            return f"Unused gear slot {idx}"
        return f"{entry[0]} ({entry[1]})" if entry[1] else entry[0]
    return f"{entry[0]} ({entry[1]})" if entry else ""


def write_csv(catalog, path: str = CSV_PATH):
    """Writes the catalogue's stats in the layout of docs/stat_catalog.csv."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        out = csv.writer(f, lineterminator="\n")
        out.writerow(["key", "property", "column", "fold", "family", "id", "name", "stat", "read_by_game"])
        for s in catalog["stats"]:
            idx = s.get("index")
            out.writerow(
                [
                    s["key"],
                    s["property"],
                    s["column"],
                    s["fold"],
                    s["family"],
                    "" if idx is None else idx,
                    item_name(catalog, s["family"], idx),
                    s["label"],
                    "yes" if s.get("read_by_game", True) else "no",
                ]
            )


def main(argv):
    if argv[1:] == ["--csv"]:
        with open(CATALOG_PATH, encoding="utf-8") as fh:
            write_csv(json.load(fh))
        print(f"wrote {CSV_PATH}")
        return 0
    if len(argv) != 3:
        print(__doc__)
        return 2
    s8, gs = Package(argv[1]), Package(argv[2])
    key_for = {
        m["PropertyId"]: m["KeyId"]
        for m in struct_rows(gs.defaults("OnlineSubsystemGameSpy")["StatsKeyMappings"])
        if m.get("ViewId") == RANKED_VIEW
    }
    columns = {c["Id"]: c["Name"] for c in struct_rows(s8.defaults("S8StatReadRanked")["ColumnMappings"])}
    ranks = s8.defaults("S8RankConquest")
    badges, feats = awards(s8)
    gear_names = gear(s8)

    def item_label(family, idx):
        if family == "gear":
            g = gear_names.get(idx)
            return g[0] if g else f"Unused gear slot {idx}"
        if family == "badge":
            return f"Badge {badges.get(idx, [f'{idx}'])[0]}"
        return f"Feat {feats.get(idx, [f'{idx}'])[0]}"

    stats, unread = [], []
    for prop, key in sorted(key_for.items()):
        name = columns.get(read_id(prop))
        if name is None:
            # The game writes this key but reads no column for it. Use Prejudice's column for the same key,
            # so careers keep the match-total Teamwork and Combat scores.
            from server import stats as pj

            known = pj.BY_KEY.get(key)
            if known is None:
                unread.append((prop, key))
                continue
            stats.append(
                {
                    "key": key,
                    "property": prop,
                    "column": known.column,
                    "fold": known.fold,
                    "family": "career",
                    "label": known.label,
                    "read_by_game": False,
                }
            )
            continue
        column = "Ranked_" + name
        row = {"key": key, "property": prop, "column": column}
        g = re.fullmatch(r"(\w+?)_G(\d+)", name)
        f = re.fullmatch(r"(Feats\d?)_F(\d+)", name)
        b = re.fullmatch(r"Badge_B(\d+)", name)
        if name.startswith("Badge_Date_B"):
            idx = int(name[len("Badge_Date_B") :])
            row.update(
                fold="date",
                family="badge",
                index=idx,
                date_of=f"Ranked_Badge_B{idx}",
                label=f"{item_label('badge', idx)}: date earned",
            )
        elif "_Date" in name:
            stem, _, rest = name.partition("_Date")
            target = "Ranked_" + stem + rest
            fam = {"_G": "gear", "_F": "feat"}.get(rest[:2], "career")
            idx = int(rest[2:]) if fam != "career" else None
            base = CAREER_LABEL.get(stem, stem).replace(" (s)", "")
            what = (
                f"{item_label(fam, idx)}: {base}"
                if fam == "gear"
                else (f"{item_label('feat', idx)}: date earned" if fam == "feat" else base)
            )
            row.update(
                fold="date", family=fam, index=idx, date_of=target, label=what + ("" if fam == "feat" else ", date")
            )
        elif b:
            idx = int(b.group(1))
            row.update(fold="sum", family="badge", index=idx, label=f"{item_label('badge', idx)}: times earned")
        elif f:
            idx = int(f.group(2))
            row.update(
                fold="sum", family="feat", index=idx, label=f"{item_label('feat', idx)}: {FEAT_TIER[f.group(1)]}"
            )
        elif g:
            idx = int(g.group(2))
            stem = g.group(1)
            row.update(
                fold="max" if stem.endswith("IAL") else "sum",
                family="gear",
                index=idx,
                label=f"{item_label('gear', idx)}: {CAREER_LABEL.get(stem, stem)}",
            )
        elif name in SYSTEM:
            label, sake_type = SYSTEM[name]
            row.update(fold="replace", family="system", label=label, sake_type=sake_type)
        else:
            fold = "replace" if name == "Level" else "max" if name.endswith("IAL") else "sum"
            row.update(fold=fold, family="career", label=CAREER_LABEL.get(name, name))
        if row["fold"] == "date":
            row["sake_type"] = "date"
        stats.append(row)

    check_against_prejudice(stats)
    out = {
        "title": "section8",
        "_comment": "Generated by scripts/build_section8_catalog.py from S8Game.u and OnlineSubsystemGameSpy.u. Do not edit.",
        "player_table": "PlayerStats_v5",
        "xp_column": "Ranked_XP",
        "level_column": "Ranked_Level",
        "level_xp": list(ranks["LevelExperienceRequired"]),
        "ranks": [[r["LevelRequired"], r["DisplayName"]] for r in struct_rows(ranks["GameRanks"])],
        "game_modes": {"1": "Conquest"},
        "badges": {str(k): v for k, v in sorted(badges.items())},
        "feats": {str(k): v for k, v in sorted(feats.items())},
        "gear": {str(k): v for k, v in sorted(gear_names.items())},
        "unread_keys": {str(k): f"property {p}: written by the game, never read back" for p, k in unread},
        "stats": sorted(stats, key=lambda s: s["key"]),
    }
    path = CATALOG_PATH
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=1)
        fh.write("\n")
    write_csv(out)
    print(
        f"wrote {path}: {len(stats)} stats, {len(badges)} badges, {len(feats)} feats, {len(gear_names)} gear, "
        f"{len(out['level_xp'])} levels, {len(out['ranks'])} ranks; {len(unread)} written-only keys {unread}"
    )
    return 0


def check_against_prejudice(stats):
    from server import stats as pj

    keys = [s["key"] for s in stats]
    assert len(keys) == len(set(keys)), "two columns share a report key"
    for s in stats:
        p = pj.BY_KEY.get(s["key"])
        assert p is not None, f"key {s['key']} unknown to Prejudice"
        assert p.column.lower() == s["column"].lower(), (s["key"], s["column"], p.column)
        assert p.fold == s["fold"], (s["key"], s["column"], s["fold"], p.fold)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
