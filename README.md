# Section 8 GameSpy Server Emulator

Server emulator for the **Section 8** games by TimeGate Studios. It implements the GameSpy / ATLAS
backend that the games reach through XLSP. Login, ranked stats, awards, XP and levels work again after
the GameSpy shutdown.

## Quick Start

**Prerequisites**

- **Python 3.10 or newer.** The server uses only the standard library. `pip install -r requirements.txt`
  installs nothing.
- **The game with XLLN**, and the **Section 8 quick-patch module**. The module changes the AuthService
  and Competition URLs to `http://` and skips the certificate signature check. See
  [Companion pieces](#companion-pieces-not-in-this-repo).

**Game client setup**

1. Install XLLN (`xlive.dll`) with the game and turn on the generic XLSP transport for Section 8.
2. Install the Section 8 quick-patch module. It patches in memory, so the `.exe` on disk does not change.
3. Set XLLN's title-server address to the machine that runs this server: localhost for one PC, or the
   host's LAN IP for several PCs.

**Install and run**

```bash
git clone <this-repo> section8-gamespy-server
cd section8-gamespy-server
python -m server            # or: python run.py
```

To change the bind address, DB path or port map, copy the example config and pass it:

```bash
cp config.example.json config.json
python -m server config.json
```

- The server binds `0.0.0.0`, so one machine can host the whole LAN. All PCs then read and write the
  same `section8.db`, so every career is in one place.
- Set `"bind_address": "127.0.0.1"` for one PC.

## Supported Games

| Game | `gamename` | GameSpy `gameid` | Status |
|------|-----------|------------------|--------|
| **Section 8: Prejudice** (2011) | `tg09pc` | 3160 | Login, stats and ranked XP validated in-game |
| **Section 8** (base game, 2009) | `section8pc` | TBD | Config stub only, untested |

## Running a ranked dedicated server

- Conquest, Assault and Skirmish are ranked only on a dedicated server. Swarm is also ranked on a normal
  in-game listen server.
- The dedicated server has no window and starts from the main game launcher.
- Start this server first, then the game.

Conquest, the default when `?game=` is not set:

```
S9.exe server TER01_Base-LargeA?servername=SokieeTest?ranked=1?adminpassword=123?maxplayers=40?bots=Yes?FF=part?difficulty=3?goalscore=2000?timelimit=15?mapcycle=TER01_Base-LargeA+ARC02_Base-LargeA+DES01_Base-LargeA+LAV02_Base-LargeA -login=123 -password=123 -unattended -xlln_local_instance_id=2
```

Assault, the same with `?game=`:

```
S9.exe server TER01_Base-LargeA?servername=SokieeTest?ranked=1?game=S9Game.S9GameInfoAssault?adminpassword=123?maxplayers=40?bots=Yes?FF=part?difficulty=3?goalscore=2000?timelimit=15?mapcycle=TER01_Base-LargeA+ARC02_Base-LargeA+DES01_Base-LargeA+LAV02_Base-LargeA -login=123 -password=123 -unattended -xlln_local_instance_id=2
```

| Argument | Use |
|---|---|
| `-login` / `-password` | The server's own GameSpy account. This server creates it on first use and gives it a certificate, as for a player, so nothing needs to be registered first. |
| `?ranked=1` | Makes the match ranked. Only the server sets it, so a client cannot force it. The engine adds `?Dedicated` in `server` mode, so do not add it. |
| `-xlln_local_instance_id=2` | Required on every dedicated server launch. Without it the server and your client share one XLLN config, the server's `-login` name replaces your username, and your ranked stats read empty. |

- Assault and Skirmish also need the news file `news/section8_news.txt`. Without it the server starts,
  reads the mode, then exits with "This game mode is not available."
- Game modes, maps, XLLN instances and the ranked settings the game requires:
  [`docs/dedicated_ranked_server.md`](docs/dedicated_ranked_server.md).

## Persistence

- One SQLite file, `section8.db`, created on first run.
- Sake records are stored entity-attribute-value, so the code holds no schema. Every ranked match is
  kept as history.
- On start the server migrates a database from an older build. It keeps career XP and clears the stats
  stored under wrong names.
- Details: [`docs/architecture.md`](docs/architecture.md#persistence).

Read a career by stat name, with the server stopped:

```bash
python scripts/stats_tool.py show <nick>       # rank, totals, time per mode, records, badges, feats, gear
python scripts/stats_tool.py history <nick>    # the matches behind it
```

The views `player_career` and `player_awards` show the same in any SQLite browser.

## Running Tests

The smoke tests run in-process, with no game. They cover the codecs, Sake searches and writes, the GPCM
login handshake, and the ranked stats path from the reports in `tests/fixtures` to the career that Sake
returns:

```bash
python -m tests.smoke
```

CI runs the lint, test and type-check jobs on every push. They need the dev tools:

```bash
pip install -r requirements-dev.txt
ruff format --check .    # formatting
ruff check .             # lint
coverage run -m tests.smoke && coverage report
mypy server --ignore-missing-imports   # non-blocking, reports only
```

## Building a standalone executable

A `v*` tag builds one executable each for Linux, Windows and both macOS architectures, and attaches them
to a GitHub release. To build one locally:

```bash
pip install -r requirements-dev.txt
pyinstaller section8-server.spec      # -> dist/section8-server[.exe]
```

- The executable finds `config.json`, the database and the log next to itself, from any start directory.
- `news/section8_news.txt` is not inside the executable, because you edit it to unlock Assault and
  Skirmish and to set the MOTD. Keep it in a `news/` folder next to the executable. The release archives
  ship it that way.

## Documentation

| Document | Content |
|----------|---------|
| [`docs/architecture.md`](docs/architecture.md) | Service ports, components, one session end to end, the report layout, and how ranked stats are mapped, folded and read |
| [`docs/stat_catalog.csv`](docs/stat_catalog.csv) | Every ranked stat: report key, property id, `PlayerStats_v6` column, fold and name |
| [`docs/dedicated_ranked_server.md`](docs/dedicated_ranked_server.md) | Game modes, maps, XLLN instances, and the settings the game requires for a ranked match |
| [`docs/game_modes_and_news.md`](docs/game_modes_and_news.md) | Why Assault and Skirmish are locked, and how to write the news file that unlocks them |
| [`docs/playerstats_v6_fields.txt`](docs/playerstats_v6_fields.txt) | The ~991 `PlayerStats_v6` fields the game requests |
| [`docs/section8_stat_schema.json`](docs/section8_stat_schema.json) | The stat read views from `S9Game.u`: read column id to name, per view. Read ids are not report keys |

## Companion pieces (not in this repo)

- **XLiveLessNess (XLLN):** the `xlive.dll` that gives the game an address for this server and tunnels
  the XLSP ports.
- **Section 8 quick-patch module:** changes the two `https` URLs to `http` and the certificate check
  `JNZ` to `JMP`. It patches in memory and only for a known exe SHA, so the `.exe` on disk does not
  change.

## Project Goals

- **Preservation:** keep a shut-down online game playable with ranked progression, with no third-party
  service.
- **Documentation:** record the GameSpy/ATLAS wire formats and the Section 8 stat schema. See
  [`docs/`](docs).
- **Self-hosting:** one machine and one SQLite file give shared ranked careers, on a LAN or the internet.
- **Reference implementation:** a small codebase with no dependencies that shows how the GameSpy
  presence, certificate, Sake storage and ATLAS competition protocols fit together.

## Features

### Implemented

- [x] **GPCM login and account creation.** `\newuser\` and `\login\`, GameSpy `passenc` decode, the
      swapped-challenge `\proof\`, and a stable `profileid` per uniquenick on every session and machine.
- [x] **Login certificate.** AuthService `LoginUniqueNick` returns the certificate the game requires. It
      is an unsigned placeholder that the XLLN quick-patch makes the game accept.
- [x] **Sake storage.** `SearchForRecords`, `CreateRecord`, `UpdateRecord`, `GetMyRecords`,
      `GetSpecificRecords` and `GetRecordCount`. Each field takes its Sake type from the client's
      writes. Searches support `filter`, multi-column `sort`, `offset` and `max`, `targetRecordFilter`
      with `surrounding`, `ownerids`, and `row` as the rank. Serves `PlayerStats_v6`,
      `PlayerProfile2_v6`, `S8Level_v6`, `ServerStatusTG09_v6` and `NewsStats_v6`, with zeroed rows for
      a new player.
- [x] **ATLAS ranked match reports.** `CheckProfileOnBanList`, `CreateSession`, `SetReportIntention`,
      `SubmitReport`, with the binary SC report decoded section by section.
- [x] **Every ranked stat mapped.** All 991 report keys, named from the game's packages
      (`StatsKeyMappings` in `OnlineSubsystemGameSpy.u`): career totals, records in a life, time played
      per game mode, 32 badges, 32 feats with their x1/x2/x3 tiers, and 6 stats for each of the 46 weapon
      mods. See [`docs/stat_catalog.csv`](docs/stat_catalog.csv).
- [x] **Lifetime careers.** Each match folds onto the player's career as the game defines each stat:
      totals add up, records keep their best value and the date it was set, overwrites replace. Level
      and rank come from the game's XP table. Every match is kept as history, a resent report counts
      once, and careers can be rebuilt from the history.
- [x] **Shared stats.** Several PCs that use one server read and write the same DB.
- [x] **Dedicated ranked server mode.** The dedicated server logs in, gets a certificate, passes the ATLAS
      trusted-server check and publishes its live `ServerStatusTG09_v6` record. It shows in the browser
      with the ranked (ladder) icon and its reports count. Records are keyed per owner, so many servers
      and players have no recordid collisions.
- [x] **News delivery.** The `NewsStats_v6` index and the `SakeFileServer` download, served UTF-16LE.
      This unlocks **Assault** and **Skirmish** and sets the in-game MOTD banner, with no binary patch.
- [x] **MOTD service.** Answers `motd.asp` and `vercheck`, so those requests do not fail.

### Not implemented

- [ ] **Section 8 (base game) support.** Config stub only. Prejudice reads the base game level from
      `S8Level_v6` to unlock its veteran items, so that table stays empty until the base game reports
      here.

Not part of this server: leaderboard screens and achievements use Games for Windows - Live, which XLLN
serves. The client decides unlocks from the level and achievements. See
[`docs/architecture.md`](docs/architecture.md#not-stored-on-the-server).

## Contributing

- Contributions are welcome: protocol captures and base-game support.
- Keep the codebase small and free of dependencies.
- `scripts/stats_tool.py set-xp` sets a player's XP. `scripts/probe_columns.py` writes marker values that
  show which screen row a column drives.

## License

See the repository for license details.

## Disclaimer

This is a fan-made preservation project. It is not affiliated with, endorsed by or connected to TimeGate
Studios, GameSpy or any rights holder. It ships no game code or assets, only a clean-room
reimplementation of the network services, for interoperability and preservation. Use it only with games
you own.
