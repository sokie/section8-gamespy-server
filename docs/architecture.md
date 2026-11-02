# Architecture

How the server works, for contributors. Everything here comes from the games' packages
(`S9Game.u`, `S8Game.u`, `OnlineSubsystemGameSpy.u`) and executables, checked against the running game.

## Services

The games send GameSpy traffic through the Games for Windows - Live title-server tunnel (XLSP). The
game's `SockRedirect` answers each GameSpy hostname with the title server's address and a port from an
LSP base (8900); XLLN then connects that port to the `endpoint` in its XLSP file. Both games use the
same layout, and XLLN redirects TCP only.

| Port | Host the game asks for | Service | Protocol |
|---|---|---|---|
| 8901 | `gpsp.gamespy.com` | GPSP: `\newuser\`, `\check\`, `\nicks\` | GameSpy `\final\` key-values, server speaks first |
| 8902 | `gpcm.gamespy.com` | GPCM: `\login\` | same |
| 8903 | `<game>.auth.pubsvs.gamespy.com` | AuthService: login certificate | SOAP over HTTP |
| 8904 | `<game>.comp.pubsvs.gamespy.com` | CompetitionService: ATLAS match reports | SOAP over HTTP |
| 8905 | `<game>.sake.gamespy.com` | Sake storage and SakeFileServer | SOAP / HTTP |
| 8906 | `*.gamespy.net` | `motd.asp`, `vercheck` | HTTP |
| 27900/udp | `<game>.available.gamespy.com` | availability probe | GameSpy UDP |

- The HTTP listeners route by URL path, so 8903–8906 (and the spare 8800) are interchangeable.
- The games use HTTPS for AuthService and Competition. The quick-patch switches both to `http://` and
  accepts the unsigned placeholder certificate.
- The SDK treats a missing UDP answer as "available", so the 27900 responder only shortens the wait.
- Each Sake and Competition request is routed to a title (Prejudice or Section 8) by its gameid or
  game key.

| Module | Role |
|---|---|
| `transport` | TCP listeners, HTTP framing, path routing |
| `gpcm` | GPSP and GPCM: accounts, `passenc` decode, `\proof\`, stable profileids |
| `authservice` | `LoginUniqueNick` certificate |
| `sake`, `sakefilter` | Sake records and search filters |
| `competition`, `screport`, `stats`, `statdefs` | ATLAS sessions, report decoding, the career fold, the Prejudice catalogue |
| `news`, `motd`, `available` | News files, MOTD and vercheck, the UDP responder |
| `persistence` | SQLite store and migrations |
| `titles`, `xlsp` | Per-game settings, XLLN service files |
| `portal`, `portal_data`, `portal_assets` | Stats website: routing and the JSON API, its database queries, images and drawn icons |
| `web/` | The stats website's static pages, styles and scripts |
| `container` | Docker entrypoint: config merge, backup on a new image, health check |

## Session flow

1. **Login.** `\newuser\` creates the account on 8901. `\login\` on 8902 returns `\proof\` and a login
   ticket, with a profileid that is stable per uniquenick.
2. **Certificate.** `LoginUniqueNick` on 8903.
3. **Read stats.** Sake reads `PlayerStats_v6`, `PlayerProfile2_v6`, `S8Level_v6` and `NewsStats_v6`. A
   new player gets zeroed rows. Login needs these answers.
4. **Ranked match.** The host calls `CheckProfileOnBanList`, `CreateSession`, `SetReportIntention` and
   `SubmitReport` on 8904. The report is decoded and each player is folded onto their career.
5. **Read back.** The next login reads the new totals.

## Storage

- One SQLite file in WAL mode. `PRAGMA user_version` is the schema version; the server migrates older
  databases on start.
- Sake tables are entity-attribute-value: one `records` row per `(table, owner, recordid)` and one
  `fields` row per typed value, so the ~991 columns of `PlayerStats_v6` need no schema. Each field takes
  its Sake type from the client's writes.
- Records are keyed by owner, and recordids count per owner: to a client, recordid 1 means "my record".
- `profiles` maps uniquenick to profileid. `record_times` holds each record's last write.
- Section 8 (2009) tables carry an `s8:` prefix.

| Table or view | Holds |
|---|---|
| `matches` | one row per report, unique on its SHA-256, so a resent report counts once |
| `match_players`, `match_stats` | each player's slot, XP and level, and every value the report sent |
| `stat_defs`, `award_defs` | the name of every column, badge and feat |
| `player_career`, `player_awards` (views) | careers and awards by name |

## Ranked stats

### Mapping

[`scripts/build_stat_catalog.py`](../scripts/build_stat_catalog.py) reads the mapping from the
packages and writes `server/statdefs.py` and [`stat_catalog.csv`](stat_catalog.csv). Nothing comes
from captured reports.

| Step | Source | Gives |
|---|---|---|
| 1 | `S9StatManager.InitStats`, `GearStats`, `GameModeStats` | every tracked stat: behaviour and column stem |
| 2 | `S9StatWriteRanked.StatManagerMapping` | the property id. Gear, feat, badge and mode stats add `id * stride` (12, 5, 2, 1) |
| 3 | `OnlineSubsystemGameSpy.StatsKeyMappings` | (view, property id) → the ATLAS key in the report: 991 entries |
| 4 | `S9StatReadRanked.ColumnMappings` | the columns read back from Sake, as `Ranked_<Name>` |

- The server needs step 3. A report key is neither a property id nor a read column id.
- GameSpy assigned the keys. Badge `n` is key `613 + 2n` with its date at `614 + 2n`; gear and feat
  keys follow no pattern.
- 991 stats: the 988 columns the game reads, plus `Ranked_ScoreTeamwork`, `Ranked_ScoreCombat` and the
  PS3-only `Ranked_PlayerName`, which it sends and never reads.

### Report format

All values are big-endian.

| Part | Content |
|---|---|
| header `0x00` | u32 protocol version (2), u32 developer version (6, the ATLAS ruleset) |
| header `0x08` | 16-byte report checksum |
| header `0x18` | u32 game status (0 complete, 1 partial, 2 broken), u32 flags |
| header `0x20` | u16 player count, u16 team count, u16 game, player and team key counts, u16 reserved |
| header `0x2c` | u32 length of the roster, auth, results, game, player and team sections |
| roster | per player: 16-byte connection id, u32 team index |
| auth | per player: 16 bytes |
| results | per player, then per team: u32 result (Section 8 always sends 5, none) |
| game, player, team | the keys; player and team sections prefix each entry with a u16 key count |

A key is a u16 id, a u16 type and the value. Types: 0 int32, 1 int16, 2 byte, 3 string (7-bit length
prefix, UTF-8), 4 float32, 5 int64.

The server finds each player by the connection id that `CreateSession` and `SetReportIntention` hand
out: the first dword is the profileid, bytes 4–7 are `BEEFCAFE`. A roster slot without one is skipped.

### The career fold

| Behaviour | Fold | The report sends |
|---|---|---|
| `TGE_SB_COUNTER` | `sum` | this match's amount (kills, XP, seconds played, badge and feat counts) |
| `TGE_SB_IALMAX` | `max` | a new best "in a life", only when the match sets one |
| `TGE_SB_OVERWRITE` | `replace` | the current value (level, clan tag, locale) |

- An integer is sent only when above zero, so a missing key means zero.
- A date is a FILETIME with its own key, sent with its stat. A record keeps the date it was set.
- `Ranked_Level` is derived from career XP with `S9RankConquest.LevelExperienceRequired` (100 levels,
  70000 XP at level 100), as the client does.
- Time is the only per-mode stat: `TimePlayed_GM<n>` with 1 Conquest, 2 Swarm, 3 Skirmish, 4 Assault.
  `Ranked_TimePlayed` is the total.
- Badges `Ranked_Badge_B<0-31>` (eight each: Combat, Conquest, Mission, Tactics) and feats
  `Ranked_Feats_F<0-31>` (eight each: Siege, Assault, Recon, Support) count how often each was earned.
  `Ranked_Feats1/2/3_F<n>` count the x1, x2 and x3 tiers.
- Gear stats are per gear id, and each weapon mod has its own id (46 of 60 slots used). Time counts for
  the primary weapon and the gear in hand, so gear times add up to more than the match.

### Sake reads

| Table | The game reads | Written by |
|---|---|---|
| `PlayerStats_v6` | the player's own ranked stats | this server, from reports |
| `PlayerProfile2_v6` | clan tag, achievements, link code | the client |
| `S8Level_v6` | the Section 8 (2009) level, for Prejudice's veteran unlocks | this server, from Section 8 reports |
| `ServerStatusTG09_v6` | a dedicated server's live status | the dedicated server |
| `NewsStats_v6` | the news file index | `server/news.py` |

A read asks for `<View>_<Column>` columns plus `ownerid`, `row` and `recordid`: by `ownerids` (own
stats), by `sort`, `offset` and `max` (a ranked page), or by `targetRecordFilter` and `surrounding` (the
rows around a player). `row` is the 1-based rank. A column with no value reads as zero, and a player
with no record gets one zeroed row.

## News delivery

```
Sake SearchForRecords on NewsStats_v6           -> News_Settings_FileID + recordid
GET /SakeFileServer/download.aspx?fileid=<id>   -> the news file
```

- The field is `News_Settings_FileID`. The XLAST name `Settings_FileID` gives the game a file id of 0.
- `recordid` must be non-zero and change with the content: the game skips the download when it matches
  its cached copy.
- The game decodes the file as UTF-16LE with no BOM sniffing. `server/news.py` keeps it UTF-8 on disk,
  normalises line endings, and converts on the way out.
- The `motd.asp` text is not the in-game banner; the banner comes from the news file.

## Not stored on the server

- **Unlocks.** The client decides them from the level, achievements and the previous game's level. The
  server only has to return the right XP.
- **Achievements and leaderboard screens.** Games for Windows - Live, served by XLLN.

## Tools

| Command | Use |
|---|---|
| `python scripts/stats_tool.py decode reports/*.bin` | a saved report by stat name |
| `python scripts/stats_tool.py replay reports/*.bin` | apply saved reports; applied ones are skipped |
| `python scripts/probe_columns.py <set> <nick>` | write marker values to find which screen row a column drives |
| `python scripts/build_stat_catalog.py <unpacked dir>` | regenerate `server/statdefs.py` and the catalogue |
| `python scripts/check_server.py <host>` | check every service from outside |
| `scripts/upk.py` | read class default properties from UE3 packages |

Packages unpack with `decompress.exe` from `S9Game/CookedPCFinal`. The cooked `.u` files keep their
UnrealScript source text, but default properties need a package reader such as `upk.py`.
