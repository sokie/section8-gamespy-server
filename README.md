# Section 8 GameSpy Server Emulator

An open-source server emulator for the **Section 8** shooters by TimeGate Studios. It implements the
GameSpy / ATLAS backend protocols the games reach through XLSP, so login, ranked stats, awards, XP and
leaderboards work again after the official GameSpy shutdown.

## Quick Start

**Prerequisites**

- **Python 3.10 or newer.** There are no packages to install, because the server uses only the standard
  library. `pip install -r requirements.txt` is a valid no-op.
- **The game, patched with XLLN**, plus the **Section 8 quick-patch module** that rewrites the
  AuthService and Competition URLs to `http://` and skips the certificate signature check. See
  [Companion pieces](#companion-pieces-not-in-this-repo).

**Game client setup**

1. Install XLLN (`xlive.dll`) alongside the game and enable the generic XLSP transport for Section 8.
2. Install the Section 8 quick-patch module. The shipped `.exe` stays untouched on disk, because the
   patch is applied in memory.
3. Point XLLN's title-server address at the machine running this server: localhost for solo, or the
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

The server binds `0.0.0.0`, so one machine hosts the whole LAN: point every player's XLLN title-server
address at it and all PCs read and write the same `section8.db`, which gives real cross-player
leaderboards. Set `"bind_address": "127.0.0.1"` for solo.

## Supported Games

| Game | `gamename` | GameSpy `gameid` | Status |
|------|-----------|------------------|--------|
| **Section 8: Prejudice** (2011) | `tg09pc` | 3160 | Login, stats and ranked XP validated in-game |
| **Section 8** (base game, 2009) | `section8pc` | TBD | Config stub only, untested |

## Running a ranked dedicated server

Conquest, Assault and Skirmish can only be played ranked on a dedicated server; Swarm can be played ranked from an
ordinary in-game listen server. The dedicated server is headless and runs from the main game launcher.
Start this backend first, then launch the game.

Conquest, which is the mode you get when `?game=` is omitted:

```
S9.exe server TER01_Base-LargeA?servername=SokieeTest?ranked=1?adminpassword=123?maxplayers=40?bots=Yes?FF=part?difficulty=3?goalscore=2000?timelimit=15?mapcycle=TER01_Base-LargeA+ARC02_Base-LargeA+DES01_Base-LargeA+LAV02_Base-LargeA -login=123 -password=123 -unattended -xlln_local_instance_id=2
```

Assault, otherwise identical:

```
S9.exe server TER01_Base-LargeA?servername=SokieeTest?ranked=1?game=S9Game.S9GameInfoAssault?adminpassword=123?maxplayers=40?bots=Yes?FF=part?difficulty=3?goalscore=2000?timelimit=15?mapcycle=TER01_Base-LargeA+ARC02_Base-LargeA+DES01_Base-LargeA+LAV02_Base-LargeA -login=123 -password=123 -unattended -xlln_local_instance_id=2
```

- **`-login` / `-password`** are the server's own GameSpy account credentials. This server creates the
  account on first use, logs it in, and issues its certificate, exactly as it does for a player, so
  nothing has to be registered in advance.
- **`?ranked=1`** declares the match ranked. It is server-authoritative, so a joining client cannot
  force or fake it. The engine appends `?Dedicated` itself in `server` mode, so do not add it.
- **`-xlln_local_instance_id=2`** is required on every dedicated-server launch. Without it the server
  and your game client share one XLLN config, the server's `-login` name overwrites your client's
  username, and your own ranked stats read empty.
- **Assault and Skirmish also need the news file**, `news/section8_news.txt`. Without it the server
  starts, resolves the mode, then exits with "This game mode is not available."

Game modes, maps, the XLLN instance setup and the ranked settings the game enforces are in
[`docs/dedicated_ranked_server.md`](docs/dedicated_ranked_server.md).

## Persistence

One SQLite file, `section8.db`, created on first run. Sake records are stored entity-attribute-value, 
so no schema is hardcoded. See [`docs/architecture.md`](docs/architecture.md#persistence).

## Running Tests

In-process smoke tests, no game required. They exercise the codecs, the Sake `SearchForRecords` and
write-then-read cycle, and the GPCM login handshake:

```bash
python -m tests.smoke
```

The lint, test and type-check jobs that CI runs on every push need the dev tools:

```bash
pip install -r requirements-dev.txt
ruff format --check .    # formatting
ruff check .             # lint
coverage run -m tests.smoke && coverage report
mypy server --ignore-missing-imports   # non-blocking, reports only
```

## Building a standalone executable

Tagging a `v*` release builds a single-file executable for Linux, Windows and both macOS
architectures, and attaches them to a GitHub release. To build one locally:

```bash
pip install -r requirements-dev.txt
pyinstaller section8-server.spec      # -> dist/section8-server[.exe]
```

The executable resolves `config.json`, the database and the log next to itself, whatever directory you
start it from. `news/section8_news.txt` is deliberately **not** bundled inside it, since editing that
file is how you unlock Assault and Skirmish and set the MOTD. Keep it in a `news/` folder beside the
executable, which is how the release archives ship it.

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/architecture.md`](docs/architecture.md) | Service ports, components, and one online session end to end |
| [`docs/dedicated_ranked_server.md`](docs/dedicated_ranked_server.md) | Game modes, maps, XLLN instances, and the settings the game requires for a ranked match |
| [`docs/game_modes_and_news.md`](docs/game_modes_and_news.md) | Why Assault and Skirmish are locked, and how to write the news file that unlocks them |
| [`docs/playerstats_v6_fields.txt`](docs/playerstats_v6_fields.txt) | The ~991 `PlayerStats_v6` fields the game requests |
| [`docs/section8_stat_schema.json`](docs/section8_stat_schema.json) | The ATLAS stat schema (keyid to name, per view), from `S9Game.u` |
| [`docs/section8_stat_keyids.txt`](docs/section8_stat_keyids.txt) | Flat keyid to name listing |
| [`docs/stat_keymap.json`](docs/stat_keymap.json) | Read-side keyid to `Ranked_<field>` column map |

## Companion pieces (not in this repo)

- **XLiveLessNess (XLLN)**, the `xlive.dll` that hands the game a routable address for this server and
  tunnels the XLSP ports.
- **The Section 8 quick-patch module**, which does the two `https` to `http` URL rewrites and the
  certificate-check `JNZ` to `JMP`, in memory and gated on the exe SHA, so the shipped `.exe` stays
  pristine on disk.

## Project Goals

- **Preservation.** Keep a shut-down online game playable with ranked progression, with no
  reliance on any third-party service.
- **Documentation.** Write down the GameSpy/ATLAS wire formats and the Section 8 stat schema that were
  reverse-engineered to build this, so the knowledge outlives the code. See [`docs/`](docs).
- **Player empowerment.** Anyone can host for their friends: one machine, one SQLite file, real
  cross-player leaderboards on a LAN or over the internet.
- **Reference implementation.** A small, dependency-free codebase that shows how the GameSpy presence,
  certificate, Sake storage and ATLAS competition protocols fit together.

## Features

### Implemented

- [x] **GPCM login and account creation.** `\newuser\` and `\login\`, GameSpy `passenc` decode, the
      swapped-challenge `\proof\`, and stable per-uniquenick `profileid`s that persist across sessions
      and machines.
- [x] **Login certificate.** AuthService `LoginUniqueNick` returns the certificate the game requires.
      It is an unsigned placeholder, accepted in-game through the XLLN quick-patch.
- [x] **Sake persistent storage.** `SearchForRecords`, `CreateRecord`, `UpdateRecord`, `GetMyRecords`
      and `GetRecordCount` over a schema-free store that learns each field's Sake type from the
      client's own writes. Serves `PlayerStats_v6`, `S8Level_v6`, `NewsStats_v6` and
      `PlayerProfile2_v6`, with zeroed synthetic rows for a fresh player.
- [x] **ATLAS ranked match reports.** The full `CheckProfileOnBanList` to `CreateSession` to
      `SetReportIntention` to `SubmitReport` flow, including decoding the binary SC report blob.
- [x] **Per-round XP accumulation.** The report ships the XP earned that round, which the server sums
      into each player's career `Ranked_xp`, idempotent by report timestamp so a resent report cannot
      double-count. Verified in-game against the game's own XP counter, for the host and for a joining
      client.
- [x] **Level and rank derivation**, mirrored into the `S8Level_v6` leaderboard table.
- [x] **Shared leaderboards.** Point several PCs at one server and they read and write the same DB, so
      a sort ranks everyone who has ever connected.
- [x] **Dedicated ranked server mode.** The dedicated server logs in, is issued a certificate, passes
      the ATLAS trusted-server check, and publishes its live `ServerStatusTG09_v6` record, so it
      appears in the browser with the ranked (ladder) icon and its reports count. Records are keyed per
      owner, so many dedicated servers and players coexist without recordid collisions.
- [x] **News delivery.** The `NewsStats_v6` index plus the `SakeFileServer` download, served UTF-16LE.
      This unlocks the **Assault** and **Skirmish** game modes and drives the in-game MOTD banner, with
      no binary patching.
- [x] **MOTD service.** Answers `motd.asp` and `vercheck` so those requests do not fail.

### Not implemented

- [ ] **The exact XP-to-level curve.** The game computes the level it displays from `Ranked_xp` itself,
      using a custom table. The stored `Ranked_Level` is a leaderboard-sort proxy fitted to the
      observed low-level thresholds, and it drifts a few levels at the high end.
- [ ] **Names for the non-XP report keyids.** Only keyid 11 (XP) is decoded. The rest are stored
      verbatim as `Ranked_wkey_<id>` for later analysis.
- [ ] **Career totals the report never carries**, such as TimePlayed. The report holds per-round
      scoring stats only, so any such total needs a server-side derivation.
- [ ] **Section 8 (base game) support**, which is present as a config stub only.

## Contributing

Contributions are welcome: protocol captures, new stat-keyid decodes or base-game support. The codebase is deliberately small and
dependency-free, so please keep it that way where you can. `scripts/set_test_xp.py` sets a player's XP
directly, which is handy for testing the client-side level display.

## License

See the repository for license details.

## Disclaimer

This is a fan-made preservation project. It is not affiliated with, endorsed by, or connected to
TimeGate Studios, GameSpy, or any rights holder. It ships no game code or assets, only a clean-room
reimplementation of the network services, built for interoperability and preservation. Use it only
with games you own.
