# Section 8 GameSpy Server Emulator

An open-source server emulator for the **Section 8** shooters by TimeGate Studios, implementing the
GameSpy / ATLAS backend protocols the games reach through **XLSP** - so login, ranked stats, awards,
XP and leaderboards work again after the official GameSpy shutdown.

It is the external-service half of the XLiveLessNess (XLLN) Section 8 support. XLLN (`xlive.dll`)
tunnels the game's backend traffic through a set of XLSP "service gateway" ports to wherever this
server listens; this server answers the GameSpy protocol on those ports and persists everything to a
single SQLite file.

## Supported Games

| Game | `gamename` | GameSpy `gameid` | Status |
|------|-----------|------------------|--------|
| **Section 8: Prejudice** (2011) | `tg09pc` | 3160 | Login + stats + ranked XP validated in-game |
| **Section 8** (base game, 2009) | `section8pc` | TBD | Config stub only - untested |

## Project Goals

- **Preservation** - keep a shut-down online game playable, including its ranked progression, with no
  reliance on any third-party service.
- **Documentation** - write down the GameSpy/ATLAS wire formats and the Section 8 stat schema that were
  reverse-engineered to build this, so the knowledge outlives the code (see [`docs/`](docs)).
- **Player empowerment** - anyone can host for their friends: one machine, one SQLite file, real
  cross-player leaderboards on a LAN or over the internet.
- **Reference implementation** - a small, dependency-free, readable codebase that shows how the GameSpy
  presence, certificate, Sake storage, and ATLAS competition protocols actually fit together.

## Features

### Implemented

- [x] **GPCM login & account creation** - `\newuser\` / `\login\`, GameSpy `passenc` decode, correct
      swapped-challenge `\proof\`, and stable per-uniquenick `profileid`s that persist across sessions
      and machines.
- [x] **Login certificate** - AuthService `LoginUniqueNick` returns the certificate the game requires
      (placeholder / unsigned; accepted in-game via the XLLN quick-patch - see below).
- [x] **Sake persistent storage** - `SearchForRecords`, `CreateRecord`, `UpdateRecord`, `GetMyRecords`,
      `GetRecordCount` over a schema-free entity-attribute-value store that learns each field's Sake type
      from the client's own writes. Serves `PlayerStats_v6`, `S8Level_v6`, `NewsStats_v6`,
      `PlayerProfile2_v6` (synthetic zeroed rows for a fresh player).
- [x] **ATLAS ranked match reports** - the full `CheckProfileOnBanList -> CreateSession ->
      SetReportIntention -> SubmitReport` flow, including decoding the binary SC report blob.
- [x] **Per-round XP accumulation** - the report ships the XP *earned that round* (report keyid 11); the
      server sums it into each player's career `Ranked_xp`, idempotent by report timestamp so a resent
      report can't double-count. **Verified in-game against the game's own XP counter for both the host
      and a non-host client.**
- [x] **Level / rank derivation** and mirroring into the `S8Level_v6` leaderboard table.
- [x] **Shared leaderboards** - point several PCs at one server and they read/write the same DB, so a
      sort across all owners ranks everyone who has ever connected.
- [x] **Dedicated ranked server mode** - a Prejudice dedicated server logs in through GameSpy, is issued
      a certificate, passes the ATLAS trusted-server check, and publishes its live `ServerStatusTG09_v6`
      record, so it appears in the browser with the ranked (ladder) icon and its reports count. Records
      are keyed per owner, so many dedicated servers (and players) coexist without recordid collisions.
      See [Dedicated Ranked Server Mode](#dedicated-ranked-server-mode).
- [x] **News delivery** - the `NewsStats_v6` index plus the `SakeFileServer` download, served UTF-16LE.
      This is what unlocks the **Assault** and **Skirmish** game modes (entitlement-gated behind an
      unlock class with no local criteria) and what drives the in-game **MOTD** banner, with no binary
      patching. See [News](#news-unlocking-assault--skirmish-and-the-motd).
- [x] **MOTD** service - answers `motd.asp`/`vercheck` so the requests do not fail (the on-screen
      banner itself comes from the news file, not from here).

### Missing / Planned

- [ ] **GameSpy ServerBrowser / matchmaking** - Section 8 multiplayer discovery runs over XLLN's own
      XNet/LiveOverLan layer, not GameSpy QR2/NAT-negotiation, so this is not required to play; a
      GameSpy server browser is simply not implemented.
- [ ] **Exact XP->level curve** - the game computes the *displayed* level from `Ranked_xp` itself via a
      custom table; our stored `Ranked_Level` is a leaderboard-sort proxy tuned to the observed
      thresholds (213 XP -> L5, 250 -> L6) and drifts a few levels at the high end.
- [ ] **Naming the non-XP report keyids** - only keyid 11 (XP) is decoded; the rest (10, 12, 281, 1358,
      1428, the client-only 221, ...) are stored verbatim as `Ranked_wkey_<id>` for later analysis.
- [ ] **Career-cumulative stats not in the report** - e.g. TimePlayed; the report carries per-round
      scoring stats only, so any career total the report never sends would need a server-side derivation.
- [ ] **Robust attribution for 3+ humans** - report blocks are attributed to participants by join order,
      which is correct for 2-player co-op but could mislabel if a *middle* player drops in a 3+ human
      match.
- [ ] **A signed certificate** - the cert is an unsigned placeholder; it only works because the
      quick-patch neuters the client's signature check. A properly signed cert would remove that patch.
- [ ] **Section 8 (base game)** support - present as a config stub only.

## How it fits together

The game (patched by XLLN) dials five XLSP tunnel ports. Each carries one service family:

| Port | Service | Protocol | Purpose |
|------|---------|----------|---------|
| 8901 | GPCM | GameSpy `\final\` KV, **server speaks first** | account creation (`\newuser\`) |
| 8902 | GPCM | GameSpy `\final\` KV, **server speaks first** | login (`\login\` -> `\lc\2\`) |
| 8903 | AuthService | SOAP over **HTTP** | login certificate (`LoginUniqueNick`) |
| 8904 | CompetitionService | SOAP over **HTTP** | ranked match reports (ATLAS) |
| 8905 | Sake | SOAP over **HTTP** | stats/awards store |
| 8800 | (spare HTTP) | SOAP over **HTTP** | routed by URL path, same as above |

AuthService and Competition are HTTPS in the shipped game; the XLLN **quick-patch** rewrites their URLs
to `http://` and neuters the certificate signature check so this server can serve them as plain HTTP.
Sake is plain HTTP already. The HTTP listeners route purely by URL path, so the HTTP ports are
interchangeable - the table is just the game's convention.

## Dedicated Ranked Server Mode

In Section 8: Prejudice, **whether a match can be ranked - and how you host it - depends on the game
mode**. The three competitive modes require a dedicated server; the co-op Swarm mode can be ranked from
an ordinary in-game listen server; private matches are never ranked:

| Mode | Ranked? | How it is hosted |
|------|---------|------------------|
| Conquest | Yes | Dedicated ranked server |
| Assault | Yes | Dedicated ranked server |
| Skirmish | Yes | Dedicated ranked server |
| Swarm (co-op) | Yes, after the game's later updates | In-game listen server is enough, if difficulty is Medium or higher and at least 2 humans are present |
| Private matches | No | Player-hosted, never ranked |

Either way the ranked progression flows through the **same GameSpy back end this server provides** -
login, certificate, and ATLAS match reports. The difference is only the host: Swarm reports straight
from the in-game listen server, while the competitive modes must be hosted by a dedicated server.

For those competitive modes, this server carries the full dedicated-server handshake end to end: a
Prejudice dedicated server logs in through GameSpy, is issued a login certificate, passes the ATLAS
"trusted server" check, and publishes its live status - so it shows up in the server browser with the
**ranked (ladder) icon** and its match reports count toward the leaderboard.

### Launching a ranked dedicated server

The dedicated server is headless, and can be run from the main game launcher..
Start this backend first, then launch the game:

```
S9.exe server TER01_Base-LargeA?servername=SokieeTest?ranked=1?adminpassword=123?maxplayers=40?bots=Yes?FF=part?difficulty=3?goalscore=2000?timelimit=15?mapcycle=TER01_Base-LargeA+ARC02_Base-LargeA+DES01_Base-LargeA+LAV02_Base-LargeA -login=123 -password=123 -unattended
```

- **`-login` / `-password`** are the server's **own** GameSpy account credentials. They go straight to
  GameSpy for authentication, which **this server handles**: the account is created on first use
  (`\newuser\`), logged in (`\login\`), and issued a login certificate (AuthService `LoginUniqueNick`),
  exactly like a player. No account needs to be pre-registered.
- **`?ranked=1`** declares the match ranked. It is server-authoritative - a joining client cannot force
  or fake it. The engine appends `?Dedicated` itself for `server` mode, so you do not add it.
- With those, the server passes the ATLAS trusted-server check (`CheckProfileOnBanList`) and publishes a
  `ServerStatusTG09_v6` record with `Status_Ranked=1`, and shows up in the browser with the ladder icon.

### Selecting the game mode

The mode is chosen by the **`?game=<Package.Class>` URL option**. There is no separate mode switch, and
the class name does *not* match the name the game shows for the mode:

| Mode (as the game names it) | `?game=` value | `GameModeID` |
|---|---|---|
| Conquest | `S9Game.S9GameInfoConquest` | 1 |
| Swarm | `S9Game.S9GameInfoSwarm` | 2 |
| Skirmish | `S9Game.S9GameInfoArcade` | 3 |
| Assault | `S9Game.S9GameInfoAssault` | 4 |

`S9GameInfoArcade` really is Skirmish - "Arcade" is the internal name; the class itself carries
`DisplayName="SKIRMISH"`. And there is **no** `S9GameInfo_Assault` / `S9GameInfo_Conquest`: the
underscored spelling that circulates in community guides is not a class name, and passing it does not
error - the server just starts in Conquest.

An Assault server, otherwise identical to the command above:

```
S9.exe server TER01_Base-LargeA?servername=SokieeTest?ranked=1?game=S9Game.S9GameInfoAssault?adminpassword=123?maxplayers=40?bots=Yes?FF=part?difficulty=3?goalscore=2000?timelimit=15?mapcycle=TER01_Base-LargeA+ARC02_Base-LargeA+DES01_Base-LargeA+LAV02_Base-LargeA -login=123 -password=123 -unattended
```

The chosen mode's `GameModeID` is what the server publishes as `ServerStatusTG09_v6.Status_GameMode`,
so you can confirm which mode actually took effect from this server's own database - no need to read
anything in-game:

```bash
python -c "import sqlite3;print(list(sqlite3.connect('section8.db').execute(\"select owner_id,value from fields where table_id='ServerStatusTG09_v6' and name='Status_GameMode'\")))"
```

### Maps and variants

A map URL is `<MapName>-<Variant>`, e.g. `TER01_Base-LargeA` is the map `TER01_Base` in its `LargeA`
variant. The engine splits the two itself and re-appends `?Variant=LargeA` to the options internally,
so you never write `?Variant=` by hand. **The variant decides which modes are legal on that map**, and
`?game=` then picks one of them:

| Variant suffix | Modes it allows | Mode used if `?game=` is omitted |
|---|---|---|
| `-LargeA`, `-MediumA`, `-SmallA`, `-SmallB` | Conquest, Skirmish, Assault | Conquest |
| `-SwarmA` | Swarm | Swarm |
| `-Campaign` | Campaign | Campaign |

So Assault and Skirmish run on **the same maps as Conquest**. There is no `-AssaultA` or `-SkirmishA`
variant - the guidance you may find that says to look for a map file with "Assault" in its name is
wrong, no such file ships in any version or DLC.

**A mismatched `?game=` is never an error.** The engine loads the class you named, checks it against
the variant's supported list, and on a miss silently falls back to the first mode that variant
supports. That is exactly why Swarm needs no `?game=` at all: `-SwarmA` supports precisely one mode, so
the fallback *is* Swarm. The same rule cuts the other way - `?game=S9Game.S9GameInfoSwarm` on a
`-LargeA` map does not fail, it quietly gives you Conquest. If a server comes up in the wrong mode,
suspect a class-name typo or a variant/mode mismatch, and check `Status_GameMode` rather than waiting
for an error that will never appear.

Swarm is also the only mode with a feature gate: it declares `FeaturesRequired="SwarmGameMode"`, which
the game's connection-status manager must have enabled, or Swarm is dropped from the legal list even on
a `-SwarmA` map. The three competitive modes have no such requirement.

The eight multiplayer maps, each shipping all five variants above:

| Map | Name in game | Ships with |
|---|---|---|
| `ARC02_Base` | Whiteout | base game |
| `DES01_Base` | Eden | base game |
| `LAV02_Base` | Prometheus | base game |
| `TER01_Base` | Zephyr | base game |
| `ARC01_Base` | Sky Dock | DLC2 |
| `LAV01_Base` | Abaddon | DLC2 |
| `DES02_Base` | Desolation | DLC3 |
| `TER02_Base` | Overseer | DLC3 |

`?mapcycle=` entries must carry the variant suffix too (`TER01_Base-LargeA`, not `TER01_Base`); a bare
map name is rejected at startup with `Map <name> does not exist!` and dropped from the rotation. The
mode is **not** repeated per entry - map rotation travels relative to the current URL, so a single
`?game=` at launch carries across the whole cycle.

### News: unlocking Assault / Skirmish, and the MOTD

Two of the four modes are not merely selected by `?game=` - they are **entitlement-gated**. Assault and
Skirmish each carry an unlock class (`S9UnlockAssault` / `S9UnlockArcade`) with no local criteria at all,
so nothing a player or server does can satisfy them. TimeGate flipped them on server-side for everyone at
once when the community passed the ten-million-kill milestone. Conquest and Swarm carry no unlock, which
is exactly why only those two ever worked against a private backend.

Until the gate is opened, a dedicated server hosting Assault or Skirmish loads the map, resolves the
right game type, and then exits during startup:

```
Log: Game class is 'S9GameInfoAssault'
ScriptLog: GameTypeDescriptor:  S9GameTypeDescriptorAssault
Log: appRequestExit(0)
Error: Error, This game mode is not available.
```

This server opens it the way the game intends, through **news** - no binary patching:

```
Sake SearchForRecords on NewsStats_v6   -> News_Settings_FileID + recordid
GET /SakeFileServer/download.aspx?fileid=<id>   -> news/section8_news.txt
```

`recordid` must be non-zero **and must change whenever the content changes**: the game compares it
against its cached news version and skips the download when they match. It is derived from a CRC of the
file, so editing the file re-triggers the apply by itself. The file is re-read per request, so no server
restart is needed to iterate on it.

**The file is served UTF-16LE with a BOM.** The game decodes it with its own reader, not a BOM-sniffing
helper; served as ASCII every section header silently fails to match and the whole file no-ops with
nothing logged anywhere - indistinguishable from a syntax error. Author it as UTF-8; `server/news.py`
converts on the way out.

#### `[Settings]` - the mode unlocks

```
<GameInfoFilter>-<Class>.<Property>=<Value>
```

```
[Settings]
TGGameInfo-S9GameInfoAssault.Unlock=None
TGGameInfo-S9GameInfoArcade.Unlock=None
```

Three rules, each of which fails silently or confusingly if broken:

- **`GameInfoFilter` must match the GameInfo that is live when news is applied.** News is applied during
  login, while the entry map's `S9GameInfoEntryEmpty` is current - and that derives from
  `TGGameInfoEntry`, *not* `S9GameInfo`. Filtering on `S9GameInfo` matches nothing and the line is
  skipped with no error and no effect. `TGGameInfo` is their common ancestor.
- **`Class` must be a bare class name.** Package-qualifying it (`S9Game.S9GameInfoAssault`) adds a dot
  that breaks the class/property split and rejects the whole section.
- **A malformed line aborts the rest of the section**, so later lines are never read. Change one thing
  per line, and put the most important line first.

`;` starts a comment *in this section only*. An optional version gate is supported:
`<Filter>:<min>,<max>-<Class>.<Property>=<Value>`.

Diagnostics appear in the game's own log (`My Games\Section 8 Prejudice - PC\S9Game\Logs\Launch.log`,
launch with `-FORCELOGFLUSH`):

| Log line | Meaning |
|---|---|
| `Improper News Settings file on line N` | structural - the line does not split into the four fields |
| `Invalid setting filter class X` | filter token did not resolve to a class |
| `Unrecognized class X` / `Unrecognized property X` | class or property token wrong |
| *(silence)* | line parsed and applied, or filter did not match the live GameInfo |

#### `[MOTD]` - the online-menu banner

```
[MOTD]
MOTD_INT=Welcome to the server.
```

The key is `MOTD_` plus the game's language ext, with `MOTD_INT` as the fallback (`TRIAL_<lang>` in
trial mode); everything right of the first `=` is displayed.

**This section is not the same parser as `[Settings]`, and the difference bites.** It has no comment
handling and no section terminator: every line from the header onward is scanned, the first line
*containing* the key wins, and everything right of its first `=` becomes the banner. A comment that
merely mentions the key is therefore displayed instead of the real line - a comment reading
`;...with MOTD_INT as the fallback; value is split on '='.` renders a banner of `'.`. Keep this block to
a single line and never mention the key after the header.

Note that `motd.asp` (served by `server/motd.py`) is **not** the source of this banner despite its name;
it is answered only so the request does not fail.

### Running the server and client from one folder

The dedicated server and your game client are the same `S9-Win32-F.exe`, so by default both load
XLiveLessNess's `xlln-config-1.ini` - and the server's `-login` name gets written back into that shared
config as `xlive_username_p1`. Your client then logs in as the server's account, and its ranked stats
read empty. Give the dedicated server its own XLLN instance so the two identities stay separate:

- Add **`-xlln_local_instance_id=2`** to the server's launch line. It then loads its own
  `xlln-config-2.ini` on a separate network port and debug log, instead of the client's `xlln-config-1.ini`.
- In `xlln-config-2.ini`, set `xlln_network_instance_port = 39002` (instance 1 uses 39001, so the two do
  not clash) and set `xlive_username_p1` to the same name you pass to the server's `-login` (e.g. `123`).

The client keeps instance 1 and its own username; the server keeps instance 2 and the `-login` identity.

### Ranked requirements (enforced by the game)

The **game itself** refuses to run as ranked unless the match settings sit inside the official ranked
bounds. These are the game's rules, not this server's - but the server will silently fall back to an
unranked match if you step outside them:

- **`timelimit` must be between 15 and 35.**
- **`goalscore` (score limit) must be between 500 and 2000.**

Outside either range the server still starts, but **not as ranked** (no ladder icon, no stat tracking).

### Minimum players

Ranked stat tracking needs at least **2 human players**. With fewer, the server reports:

> minimum players not met (2). Ranked stats tracking temporarily disabled

and holds ranked reporting until a second human joins. Bots do not count toward the minimum, so a
bots-only server never stores ranked stats.

## Quick Start

### Prerequisites

- **Python 3.10 or newer.** No packages to install - the server uses only the standard library
  (`pip install -r requirements.txt` is a valid no-op).
- **The game, patched with XLLN** so its GameSpy/XLSP traffic is routed to this server, **plus the
  Section 8 quick-patch module** that does the `https`->`http` URL rewrites and the certificate-check
  bypass. See [Companion pieces](#companion-pieces-not-in-this-repo).

### Game client setup

1. Install XLLN (`xlive.dll`) alongside the game and enable the generic XLSP transport for Section 8.
2. Install the Section 8 quick-patch module so the AuthService/Competition URLs become `http://` and the
   cert signature check is skipped (the shipped `.exe` stays untouched on disk - the patch is applied in
   memory).
3. Point XLLN's title-server address at the machine running this server (localhost for solo, or the
   host's LAN IP for several PCs).

### Install & run

```bash
git clone <this-repo> section8-gamespy-server
cd section8-gamespy-server

# no dependencies to install; this is a no-op
pip install -r requirements.txt

# run it (solo - server on the same PC as the game)
python -m server
# or, equivalently
python run.py
```

To customise the bind address, DB path, or port map, copy the example config and pass it:

```bash
cp config.example.json config.json
python -m server config.json
```

### Hosting for several PCs (shared leaderboards)

The server binds `0.0.0.0` by default. Run it on one machine, point every player's XLLN title-server
address at that machine's LAN IP, and all PCs read and write the **same** `section8.db` - so a
`SearchForRecords sort=Ranked_xp desc` ranks every player who has ever connected. Set
`"bind_address": "127.0.0.1"` in `config.json` for solo / local-only.

## Persistence

One SQLite file (`section8.db`, created on first run). Sake tables (`PlayerStats_v6` alone has ~991
columns) are stored entity-attribute-value - a `records` row per `(table, recordid)` and a `fields`
row per typed value - rather than as hundreds of real columns, so the schema is never hardcoded and
each field's Sake type is learned from the client's own `UpdateRecord` writes. A `profiles` table maps
each `uniquenick` to a stable, unique `profileid`.

## Running Tests

In-process smoke tests - no game required. They exercise the codecs, the Sake `SearchForRecords` and
write->read cycle, and the GPCM login handshake:

```bash
python -m tests.smoke
```

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/playerstats_v6_fields.txt`](docs/playerstats_v6_fields.txt) | The ~991 `PlayerStats_v6` fields the game requests |
| [`docs/section8_stat_schema.json`](docs/section8_stat_schema.json) | The ATLAS stat schema (keyid -> name, per view) reverse-engineered from `S9Game.u` |
| [`docs/section8_stat_keyids.txt`](docs/section8_stat_keyids.txt) | Flat keyid -> name listing (human-readable form of the schema) |
| [`docs/stat_keymap.json`](docs/stat_keymap.json) | Read-side keyid -> `Ranked_<field>` column map (reference) |

## Architecture Overview

```
   Section 8 client  (+ XLLN xlive.dll  + quick-patch)
        |  XLSP tunnel ports
        v
 +----------------------------------------------------------+
 |  server.transport  - TCP listeners + HTTP framing        |
 |     8901/8902 gpcm (server-speaks-first)                 |
 |     8800/8903/8904/8905 http (routed by URL path)        |
 +----------------------------------------------------------+
        |               |               |
        v               v               v
    GpcmService     AuthService     HttpRouter --> SakeService
  (login/newuser)  (certificate)      |        --> CompetitionService --> screport (SC blob decode)
        |               |             |                     |
        +---------------+-------------+---------------------+
                                 |
                                 v
                    persistence.Store  (SQLite: records / fields / profiles)
```

Typical online session:

1. **Login** - the game hits GPCM (8901/8902): `\newuser\` creates the account, `\login\` returns the
   `\proof\` and a login ticket. `GpcmService` decodes `passenc` and issues a stable `profileid`.
2. **Certificate** - the game calls AuthService `LoginUniqueNick` (8903); `AuthService` returns the
   placeholder certificate the client needs to proceed (validated by the quick-patch, not by signature).
3. **Read stats** - the game calls Sake `SearchForRecords` / `GetMyRecords` (8905) for
   `PlayerStats_v6` / `S8Level_v6` / `NewsStats_v6`; `SakeService` serves the stored rows (or synthetic
   zeroed rows for a fresh player), which unblocks the login and populates the Awards / ranked screens.
4. **Ranked match** - at match end the host runs the ATLAS flow on the CompetitionService (8904):
   `CheckProfileOnBanList -> CreateSession -> SetReportIntention -> SubmitReport`. The binary report blob
   is decoded by `screport`, each player's per-round XP (keyid 11) is accumulated into their
   `Ranked_xp`, and the level/rank are derived and mirrored to `S8Level_v6`.
5. **Read-back** - on the next login the game reads the updated totals straight out of Sake and shows
   the new XP, level and leaderboard standing.

## Companion pieces (not in this repo)

- **XLiveLessNess (XLLN)** - the `xlive.dll` that hands the game a routable address for this server and
  tunnels the XLSP ports (the "generic XLSP transport" work in the `xlivelessness` project).
- **Section 8 quick-patch module** - does the two `https`->`http` URL rewrites and the certificate-check
  `JNZ`->`JMP`, in memory, gated on the exe SHA, so the shipped `.exe` stays pristine on disk.

## Contributing

Contributions are welcome - protocol captures, new stat-keyid decodes, base-game support, or a signed
certificate that removes the need for the quick-patch. The codebase is deliberately small and
dependency-free; please keep it that way where you can. `scripts/set_test_xp.py` is a handy way to set a
player's XP directly for testing the client-side level display.

## License

See the repository for license details.

## Disclaimer

This is a fan-made preservation project and is not affiliated with, endorsed by, or connected to
TimeGate Studios, GameSpy, or any rights holder. It ships no game code or assets - only a
clean-room reimplementation of the network services, built for interoperability and preservation.
Use it only with games you own.
