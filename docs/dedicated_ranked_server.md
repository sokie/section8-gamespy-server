# Dedicated ranked server

Overview of the dedicated server modes, the launch parameters and the map variants.

The launch lines themselves are in the [README](../README.md#running-a-ranked-dedicated-server).

## Which modes need a dedicated server

Whether a match can be ranked, and how you host it, both come down to the mode:

| Mode | Ranked? | How it is hosted |
|------|---------|------------------|
| Conquest | Yes | Dedicated ranked server |
| Assault | Yes | Dedicated ranked server |
| Skirmish | Yes | Dedicated ranked server |
| Swarm (co-op) | Yes, after the game's later updates | In-game listen server, if difficulty is Medium or higher and at least 2 humans are present |
| Private matches | No | Player-hosted, never ranked |

Ranked progression runs through the same GameSpy backend either way: login, certificate, ATLAS match
reports. Only the host differs. Swarm reports straight from the in-game listen server, while the three
competitive modes have to be hosted by a dedicated server.

For those three, this server handles the full handshake. The dedicated server logs in through GameSpy
and gets a login certificate like any player, then passes the ATLAS trusted-server check and publishes
its live status. That is what puts it in the browser with the ranked (ladder) icon and makes its match
reports count.

## Selecting the game mode

The mode comes from the `?game=<Package.Class>` URL option. No underscore in the class name, and note
that `S9GameInfoArcade` is what the game calls Skirmish:

| Mode (as the game names it) | `?game=` value | `GameModeID` |
|---|---|---|
| Conquest | `S9Game.S9GameInfoConquest` | 1 |
| Swarm | `S9Game.S9GameInfoSwarm` | 2 |
| Skirmish | `S9Game.S9GameInfoArcade` | 3 |
| Assault | `S9Game.S9GameInfoAssault` | 4 |

Leave `?game=` off and you get Conquest. Assault and Skirmish also need the news file, otherwise the
server starts, resolves the mode, then exits with "This game mode is not available." Edit
[`news/section8_news.txt`](../news/section8_news.txt), and see
[`game_modes_and_news.md`](game_modes_and_news.md) for the format and why the gate is there at all.

A wrong or misspelled `?game=` is never an error. The game just falls back to the first mode the map
supports. To check what actually took effect, read back the `GameModeID` the server published:

```bash
python -c "import sqlite3;print(list(sqlite3.connect('section8.db').execute(\"select owner_id,value from fields where table_id='ServerStatusTG09_v6' and name='Status_GameMode'\")))"
```

## Maps and variants

A map URL is `<MapName>-<Variant>`, for example `TER01_Base-LargeA`. The engine splits the two itself,
and the variant is what decides which modes are legal there:

| Variant suffix | Modes it allows | Mode used if `?game=` is omitted |
|---|---|---|
| `-LargeA`, `-MediumA`, `-SmallA`, `-SmallB` | Conquest, Skirmish, Assault | Conquest |
| `-SwarmA` | Swarm | Swarm |
| `-Campaign` | Campaign | Campaign |

So Assault and Skirmish run on the same maps as Conquest. There is no `-AssaultA` or `-SkirmishA`
variant, and no such file ships in any version or DLC, so ignore guidance telling you to look for a map
with "Assault" in the name. Swarm needs no `?game=` because `-SwarmA` supports exactly one mode.

The eight multiplayer maps, each shipping all five variants:

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

`?mapcycle=` entries need the variant suffix too, so `TER01_Base-LargeA` and not `TER01_Base`. A bare
map name is rejected at startup and dropped from the rotation. You do not repeat the mode per entry:
rotation travels relative to the current URL, so one `?game=` at launch carries across the whole cycle.

## A separate XLLN instance for the server

The dedicated server and your game client are the same `S9-Win32-F.exe`, so by default both load
XLiveLessNess's `xlln-config-1.ini`. The server's `-login` name gets written back into that shared
config as `xlive_username_p1`, your client then logs in as the server's account, and your own ranked
stats read empty.

Give the server its own XLLN instance so the two identities stay apart:

- Add `-xlln_local_instance_id=2` to the server's launch line. It then loads its own
  `xlln-config-2.ini`, on a separate network port and debug log.
- In `xlln-config-2.ini`, set `xlln_network_instance_port = 39002`, since instance 1 uses 39001. Set
  `xlive_username_p1` to the same name you pass to the server's `-login`.

The client keeps instance 1 and its own username; the server keeps instance 2 and the `-login`
identity.

## Ranked requirements

The game will not run a match as ranked unless the settings sit inside the official ranked bounds.
These are the game's rules, not this server's:

- `timelimit` must be between 15 and 35.
- `goalscore` (score limit) must be between 500 and 2000.

Step outside either range and the server still starts, just not as ranked. No ladder icon, no stat
tracking.

## Minimum players

Ranked stat tracking needs at least 2 human players. With fewer, the server reports:

> minimum players not met (2). Ranked stats tracking temporarily disabled

and holds ranked reporting until a second human joins. Bots do not count toward the minimum, so a
bots-only server never stores ranked stats.
