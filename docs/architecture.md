# Architecture

The services this server runs, their ports, and how ranked stats work.

## Service ports

XLLN tunnels the game's backend traffic over five XLSP ports, one service per port:

| Port | Service | Protocol | Purpose |
|------|---------|----------|---------|
| 8901 | GPCM | GameSpy `\final\` KV, server speaks first | account creation (`\newuser\`) |
| 8902 | GPCM | GameSpy `\final\` KV, server speaks first | login (`\login\` to `\lc\2\`) |
| 8903 | AuthService | SOAP over HTTP | login certificate (`LoginUniqueNick`) |
| 8904 | CompetitionService | SOAP over HTTP | ranked match reports (ATLAS) |
| 8905 | Sake | SOAP over HTTP | stats and awards store |

- The game uses HTTPS for AuthService and Competition. The XLLN quick-patch changes both URLs to
  `http://` and turns off the certificate signature check.
- Sake uses plain HTTP.
- The HTTP listeners route by URL path only, so 8903, 8904 and 8905 are interchangeable. 8800 is a spare
  HTTP port.

## Components

```
   Section 8 client  (+ XLLN xlive.dll + quick-patch)
        |  XLSP tunnel ports
        v
 +----------------------------------------------------------+
 |  server.transport   TCP listeners + HTTP framing         |
 |     8901/8902 gpcm (server-speaks-first)                 |
 |     8800/8903/8904/8905 http (routed by URL path)        |
 +----------------------------------------------------------+
        |               |               |
        v               v               v
    GpcmService     AuthService     HttpRouter --> SakeService (sakefilter: search filters)
  (login/newuser)  (certificate)      |        --> CompetitionService --> screport (SC blob decode)
        |               |             |                     |        --> stats    (fold onto the career,
        |               |             |                     |                      statdefs catalogue)
        +---------------+-------------+---------------------+
                                 |
                                 v
          persistence.Store  (SQLite: Sake records / fields, profiles, match history)
```

## Session flow

1. **Login.** GPCM on 8901 and 8902. `\newuser\` creates the account. `\login\` returns the `\proof\`
   and a login ticket. `GpcmService` decodes `passenc` and gives a stable `profileid`.
2. **Certificate.** AuthService `LoginUniqueNick` on 8903 returns a placeholder certificate. The
   quick-patch makes the client accept it.
3. **Read stats.** Sake `SearchForRecords` and `GetMyRecords` on 8905 read `PlayerStats_v6`,
   `PlayerProfile2_v6`, `S8Level_v6` and `NewsStats_v6`. A new player gets zeroed rows. Login needs
   these answers, and they fill the AWARDS and ranked screens.
4. **Ranked match.** At match end the host calls CompetitionService on 8904: `CheckProfileOnBanList`,
   `CreateSession`, `SetReportIntention`, `SubmitReport`. `screport` decodes the report. `stats` folds
   each player onto their career. See [Ranked stats](#ranked-stats).
5. **Read back.** At the next login the game reads the new totals from Sake.

## Persistence

- One SQLite file, `section8.db`, created on first run.
- Sake tables are entity-attribute-value: one `records` row per `(table, owner, recordid)`, one `fields`
  row per typed value. `PlayerStats_v6` has about 991 columns, so the code holds no schema. Each field
  takes its Sake type from the client's `UpdateRecord` writes.
- `profiles` maps each `uniquenick` to a stable `profileid`.
- Records are keyed by owner. Recordid numbers start again for each owner, so two dedicated servers can
  each have `ServerStatusTG09_v6` recordid 1. To a client, "recordid 1" means "my record".
- PCs that use the same server share the file. A `SearchForRecords` sorted on `Ranked_xp` ranks every
  player.
- `PRAGMA user_version` is the schema version.

Match history and names:

| Table or view | Holds |
|---|---|
| `matches` | one row per report, unique on its SHA-256 |
| `match_players` | each player's roster slot, XP gained, level after |
| `match_stats` | every value the report sent for that player |
| `stat_defs`, `award_defs` | the name of every `PlayerStats_v6` column, badge and feat |
| `player_career` (view) | every stored stat with its label and its badge, feat, gear or game mode |
| `player_awards` (view) | badges and feats with category, count and date |

## Ranked stats

[`stat_catalog.csv`](stat_catalog.csv) lists every stat with its report key, column and name.

### Mapping source

`scripts/build_stat_catalog.py` reads the mapping from the game's packages. It writes
`server/statdefs.py` and the catalogue. Nothing comes from captured reports.

| Step | Package | Table | Gives |
|---|---|---|---|
| 1 | `S9Game.u` | `S9StatManager.InitStats`, `GearStats`, `GameModeStats` | every stat the game tracks: event, behaviour, column stem (`OnlineField`) |
| 2 | `S9Game.u` | `S9StatWriteRanked.StatManagerMapping` | the stat property id; gear, feat, badge and game mode stats add `id * stride` (`TGStatWriteBase.StatsPer*Id`: gear 12, feat 5, badge 2, mode 1) |
| 3 | `OnlineSubsystemGameSpy.u` | `OnlineSubsystemGameSpy.StatsKeyMappings` | (view, property id) to the ATLAS key in the SubmitReport blob, 991 entries |
| 4 | `S9Game.u` | `S9StatReadRanked.ColumnMappings` | the columns the game reads back from Sake, as `Ranked_<Name>` |

- The server needs step 3. A report key is not a property id and not a read column id.
- GameSpy assigned the keys on its ATLAS site. Badge `n` is key `613 + 2n`, with its date at `614 + 2n`.
  Gear and feat keys have no pattern.
- The client applies the table before it writes the report (`GS_AddPlayerStatsToSCReport` in
  `S9-Win32-F.exe`).
- 991 stats: all 988 columns the game reads, plus 3 it sends and never reads: `Ranked_ScoreTeamwork`,
  `Ranked_ScoreCombat` and the PS3-only `Ranked_PlayerName`.

### Report layout

All values are big-endian.

| Part | Content |
|---|---|
| header `0x00` | u32 protocol version (2) |
| header `0x04` | u32 developer version (6, the ATLAS ruleset version) |
| header `0x08` | 16-byte report checksum |
| header `0x18` | u32 game status: 0 complete, 1 partial, 2 broken |
| header `0x1c` | u32 flags |
| header `0x20` | u16 player count, u16 team count |
| header `0x24` | u16 game, player and team key counts, u16 reserved |
| header `0x2c` | u32 lengths of the roster, auth, results, game, player and team sections |
| roster | per player: 16-byte connection id, u32 team index |
| auth | per player: 16 bytes |
| results | per player, then per team: u32 result (Section 8 always sends 5, none) |
| game | the game keys |
| player | per player: u16 key count, then the keys |
| team | per team: u16 key count, then the keys |

- A key is a u16 key id, a u16 type, then the value.
- Types: 0 int32, 1 int16, 2 byte, 3 string (7-bit length prefix, then UTF-8), 4 float32, 5 int64.

### Report values

| Behaviour | Fold | Value in the report |
|---|---|---|
| `TGE_SB_COUNTER` | `sum` | this match's amount: kills, deaths, XP, time played in seconds, badge and feat counts |
| `TGE_SB_IALMAX` | `max` | the player's best "in a life", sent only when the match sets a new best |
| `TGE_SB_OVERWRITE` | `replace` | the current value: level, clan tag, locale |

- The client sends an integer only when it is above zero. A missing key means zero.
- A stat's date is a FILETIME with its own key. The client sends it only with its stat.

### Career fold

- `sum` adds, `max` keeps the larger, `replace` overwrites.
- A date changes only with its stat. A record keeps the date it was set. A total keeps the latest date.
- `Ranked_Level` comes from career XP and the game's table `S9RankConquest.LevelExperienceRequired`
  (100 levels, 70000 XP for level 100), as `TGRankManager.GetLevelFromXP` does on the client.
- A report applied twice changes nothing, because matches are unique on the report hash.
- `scripts/stats_tool.py rebuild` folds careers again from match history.
- The server finds a player by the ccid that `CreateSession` and `SetReportIntention` give out. Its
  first dword is the profileid and bytes 4-7 are `BEEFCAFE`. The host copies it into the report roster.
- A roster slot without that ccid is skipped, because nothing else in the report names the player.

### Game modes

- Only time played is kept per game mode, in `TimePlayed_GM<n>` by `GameModeID`: 0 Campaign,
  1 Conquest, 2 Swarm, 3 Skirmish, 4 Assault.
- `Ranked_TimePlayed` is the total over all modes. The report sends both.
- The CAREER screen shows Conquest, Swarm, Assault and a total.
- Every other stat is one career total. The game keeps no kills or XP per mode.
- Each match stores its mode, taken from its time-played key.
- Unranked matches send no report.

### Badges, feats and gear

- **Badges.** `Ranked_Badge_B<n>` counts how often badge `n` was earned. `Ranked_Badge_Date_B<n>` holds
  the last date. AWARDS shows a badge as earned when its count is above zero. Categories: Combat 0-7,
  Conquest 8-15, Mission 16-23, Tactics 24-31.
- **Feats.** `Ranked_Feats_F<n>` counts completions. `Ranked_Feats1/2/3_F<n>` count completions at 1x, 2x
  and 3x or more of the target: the x1, x2 and x3 on AWARDS. Categories: Siege 0-7, Assault 8-15, Recon
  16-23, Support 24-31.
- **Gear.** Per gear id, `Kills_G`, `TimePlayed_G` and `Destruction_G` are totals. `KillsIAL_G`,
  `DamageIAL_G` and `DestructionIAL_G` are records in a life.
- Each weapon mod has its own gear id: 46 used of 60 slots. The GEAR screen adds up a weapon's mods
  itself for "ALL MODS".
- Time counts for the primary weapon and for the gear in hand, so gear times add up to more than the
  match.

### Sake reads

| Table | The game reads | Written by |
|---|---|---|
| `PlayerStats_v6` | the Ranked view: a player's own record at login and in the stats screens | this server, from reports |
| `PlayerProfile2_v6` | `Profile_ClanTag`, `Profile_AchievementsUnlocked`, `Profile_LinkCodeUsed` | the client (`UpdateRecord`) |
| `S8Level_v6` | `Ranked_Level`, the player's level in the first Section 8 | nothing on this server |
| `ServerStatusTG09_v6` | `Status_*`, a dedicated server's live state | the dedicated server |
| `NewsStats_v6` | the news file index | `server/news.py` |

A read asks for `<ViewName>_<ColumnName>` columns plus `ownerid`, `row` and `recordid`, in one of three
shapes:

| Shape | Request |
|---|---|
| a player's own stats | `ownerids` = the players |
| a ranked page | `sort` = `<column> desc`, `offset` = first rank - 1, `max` = rows |
| the rows around a player | `targetRecordFilter` = `ownerid=<id>`, `surrounding` = rows each side |

- `row` is the 1-based position in the sorted result.
- `filter` uses Sake's SQL-like syntax.
- A column with no stored value reads as zero in its catalogue type.
- A player with no record gets one zeroed row.

### Not stored on the server

- **Unlocks.** Each unlock (`TGUnlock`) is a bit in the local profile. The client decides it from the
  level (from `Ranked_xp`), achievements, offline stars and the previous title's level. The server only
  has to return the right XP.
- **Achievements.** Games for Windows - Live achievements, served by XLLN.
- **Leaderboards.** The leaderboard screens read `TGStatReadLeaderboard` views from Live, not GameSpy.
  `S9StatLBWriteRanked` writes them to Live. XLLN serves both.
- **`S8Level_v6`.** The level in Section 8 (2009). Prejudice reads it as the "previous title level",
  which unlocks Reactive Plating and Aegis Armor at 1, Ablative Layering at 25 and Explosives Amplifier at
  50. This server never writes it, so a Prejudice level cannot unlock those items early.

### Tools

| Command | Use |
|---|---|
| `python scripts/stats_tool.py show <nick>` | a career by name: rank, totals, time per mode, records, badges, feats, gear |
| `python scripts/stats_tool.py history <nick>` | the matches behind a career |
| `python scripts/stats_tool.py decode reports/*.bin` | a saved report by stat name |
| `python scripts/stats_tool.py replay reports/*.bin` | apply saved reports; already applied ones are skipped |
| `python scripts/stats_tool.py rebuild` | fold every career again from match history |
| `python scripts/probe_columns.py <set> <nick>` | write marker values to see which screen row a column drives |
| `python scripts/build_stat_catalog.py <unpacked dir>` | generate `server/statdefs.py` and the catalogue from the packages |
