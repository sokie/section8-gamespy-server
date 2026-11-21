# Ranked dedicated server (Prejudice)

Conquest, Assault and Skirmish are ranked only on a dedicated server. Swarm is also ranked on an in-game
listen server (difficulty Medium or higher, 2 or more humans). Private matches are never ranked.

The dedicated server logs in through this backend like a player, gets a certificate, passes the ATLAS
trusted-server check and publishes its status, so it shows in the browser with the ranked (ladder) icon
and its match reports count. Start this server first, then the dedicated server.

## Launch line

```
S9.exe server TER01_Base-LargeA?servername=MyServer?ranked=1?game=S9Game.S9GameInfoAssault?adminpassword=123?maxplayers=40?bots=Yes?FF=part?difficulty=3?goalscore=2000?timelimit=15?mapcycle=TER01_Base-LargeA+ARC02_Base-LargeA+DES01_Base-LargeA+LAV02_Base-LargeA -login=myserver -password=123 -unattended -xlln_local_instance_id=2
```

| Argument | Use |
|---|---|
| `-login`, `-password` | The server's own GameSpy account. It is created on first login |
| `?ranked=1` | Makes matches ranked. The engine adds `?Dedicated` itself |
| `?game=` | The mode, see below. Leave it out for Conquest |
| `?bots=Yes`, `?botratio=0.5` | Bots fill the server until humans and bots together reach `maxplayers` × `botratio` (default 1.0) |
| `-xlln_local_instance_id=2` | Required. Gives the server its own XLLN config, see below |

A match is ranked only while the server keeps the official rules. Outside them the server still runs,
unranked:

- `timelimit` 15–35 and `goalscore` 750–2000 (Conquest, Assault and Skirmish).
- Warmup on, which is the default, and no join `?password`.
- `maxplayers` 8 or more (Swarm: 4 or more).
- With bots on, `difficulty` 2 (Medium) or higher.

The server checks the rules every minute. A broken rule makes the rest of the map unranked, and the next
map checks again. Stats are recorded only with **2 or more humans**; bots do not count, and the game
prints "minimum players not met (2)" until a second human joins.

### A separate XLLN instance

The server and your client are the same `S9-Win32-F.exe` and would share `xlln-config-1.ini`. The
server's `-login` name then replaces your username, and your own stats read empty. With
`-xlln_local_instance_id=2` the server uses `xlln-config-2.ini`. In that file set
`xlln_network_instance_port = 39002` (instance 1 uses 39001) and `xlive_username_p1` to the `-login`
name.

## Game modes and maps

| Mode | `?game=` | `GameModeID` |
|---|---|---|
| Conquest | `S9Game.S9GameInfoConquest` | 1 |
| Swarm | `S9Game.S9GameInfoSwarm` | 2 |
| Skirmish | `S9Game.S9GameInfoArcade` (Arcade is Skirmish's internal name) | 3 |
| Assault | `S9Game.S9GameInfoAssault` | 4 |

A map is `<Map>-<Variant>`, and the variant decides which modes it allows:

| Variant | Modes | Without `?game=` |
|---|---|---|
| `-LargeA`, `-MediumA`, `-SmallA`, `-SmallB` | Conquest, Skirmish, Assault | Conquest |
| `-SwarmA` | Swarm | Swarm |
| `-Campaign` | Campaign | Campaign |

- Assault and Skirmish run on the Conquest variants. No `-AssaultA` map exists in any version or DLC.
- A misspelled `?game=` (such as `S9GameInfo_Assault` from older guides) is not an error: the game
  silently falls back to the variant's first mode.

### Maps

Every map has all five variants. A variant is a part of the same terrain: `-LargeA` is the whole map,
`-MediumA` a smaller part, and `-SmallA` and `-SmallB` two different small parts. The game suggests 32
players for Large, 24 for Medium and 16 for Small. In game, `-LargeA` shows as the map's name,
`-MediumA` as `<name> - Medium`, and the others as `<name> - <area>`:

| Map | Name | From | `-SmallA` | `-SmallB` | `-SwarmA` |
|---|---|---|---|---|---|
| `TER01_Base` | Zephyr | base game | East | West | Hilltop |
| `ARC02_Base` | Whiteout | base game | West | East | Excavators |
| `DES01_Base` | Eden | base game | East | North | Solar Array |
| `LAV02_Base` | Prometheus | base game | South | North | Tower |
| `ARC01_Base` | Sky Dock | Overdrive map pack (DLC2) | North | South | Barracks |
| `LAV01_Base` | Abaddon | Overdrive map pack (DLC2) | North | South | Generator |
| `DES02_Base` | Desolation | Frontier Colonies map pack (DLC3) | Central | East | Thermal |
| `TER02_Base` | Overseer | Frontier Colonies map pack (DLC3) | North | Central | Base |

The Blitz Pack (DLC1) has no maps. A map pack loads from `S9game\CookedPCFinal\DLC\DLC2` and `DLC3`, so
the server and every player need it installed there.

### Map cycle

`?mapcycle=` lists maps joined with `+`, each with its variant suffix. The cycle of all eight large maps:

```
?mapcycle=TER01_Base-LargeA+ARC02_Base-LargeA+DES01_Base-LargeA+LAV02_Base-LargeA+ARC01_Base-LargeA+LAV01_Base-LargeA+DES02_Base-LargeA+TER02_Base-LargeA
```

- Put the start map (the one before the first `?`) first. After the start map, the server plays the
  second entry.
- After the last map, the cycle starts again.
- The server removes a map that does not exist from the cycle and logs `Map <name> does not exist!`.
  It does not check the mode, so keep `-SwarmA` maps out of a Conquest, Assault or Skirmish cycle.
- One `?game=` carries across the whole cycle.
- `?mapcycle=random` plays random maps that suit `maxplayers`.

To see which mode actually started, read the mode the server published:

```bash
python -c "import sqlite3;print(list(sqlite3.connect('section8.db').execute(\"select owner_id,value from fields where table_id='ServerStatusTG09_v6' and name='Status_GameMode'\")))"
```

## Remote administration

Set `?adminpassword=` on the launch line. Join the server, open the console with `~` or `=`, and type
`AdminLogin <password>`. Then type each command as `Admin <command>`, for example
`Admin ChangeMap TER01_Base-SmallA`. `AdminLogout` ends the login. On a listen server, only the host can
log in.

In the Ranked column, ✓ means a ranked server runs the command and ✗ means it refuses it. A ⚠ condition
makes the rest of the map unranked.

| Command | What it does | Ranked |
|---|---|---|
| `PlayerList` | Lists every player and bot with its ID | ✓ |
| `NetId <name>` | Shows the ID and ping of the players with that name | ✓ |
| `Kick <name or ID>` | Removes a player or a bot | ✓ |
| `Ban <name or ID>` | Kicks the player and bans them for good | ✓ |
| `Banned` | Lists the banned players | ✓ |
| `UnBan <name or ID>` | Removes a ban | ✓ |
| `MOTD <text>` | Sets the message of the day, up to 255 characters, and saves it in the server config | ✓ |
| `BroadcastMessage <text>`, `BM <text>` | Shows a banner to all players, up to 50 characters | ✓ |
| `ChangeMap <map>` | Ends the match and loads the map. Refuses a map that does not suit the mode | ✓ |
| `ChangeMapNext <map>` | Sets the next map; the match continues | ✓ |
| `CycleMap` | Ends the match and loads the next map of the cycle | ✓ |
| `RestartMap` | Loads the current map again | ✓ |
| `MapRotation <map+map+…>` | Replaces the map cycle and checks each map against the mode | ✓ |
| `SetTeam <player> <team>` | Moves a player to team 0 (Section 8) or 1 (Arm). Fails when the team is full or autobalance moves them back | ✓ |
| `SetSpectator <player>` | Moves a player to the spectators | ✓ |
| `ShuffleTeams` | Mixes the teams, or rebalances them when autobalance is on | ✓ |
| `Bots ON`, `Bots OFF` | Turns the bot fill on or off. Off removes the filled bots | ✓ ⚠ on, with `Difficulty` below 2 |
| `AddBot <team>` | Adds one bot to the team. Needs `Bots OFF`. Refused when `bDisableAdminPlayerLimit` is set | ✗ |
| `RemoveBot <team>` | Removes one bot from the team. Needs `Bots OFF` | ✗ |
| `Difficulty <0-4>` | Sets the bot difficulty: 0 Trivial, 1 Easy, 2 Medium, 3 Hard, 4 Insane | ✓ ⚠ below 2, with bots on |
| `PlayerLimit <n>` | Sets the player limit, from the number of humans up to the server maximum. A lower limit kicks every bot first. Refused when `bDisableAdminPlayerLimit` is set | ✓ ⚠ below 8 (Swarm: 4) |
| `ScoreLimit <points>` | Sets the score limit of the current match | ✓ ⚠ outside 750–2000 |
| `TimeLimit <minutes>` | Sets the time limit; the clock of the current match moves by the difference | ✓ ⚠ outside 15–35 |
| `Warmup ON`, `Warmup OFF` | Turns the warmup before a match on or off. Off ends a running warmup | ✓ ⚠ off |
| `Password <text>` | Sets a join password | ✓ ⚠ any password |
| `RemovePassword` | Removes the join password | ✓ |
| `AdminPassword <text>` | Changes the admin password and saves it in the server config | ✓ |
| `AutoBalance ON`, `AutoBalance OFF` | Turns team autobalance on or off | ✓ |
| `FFMode ON`, `FFMode OFF`, `FFMode PART` | Sets friendly fire on, off, or shields only | ✓ |
| `Spectator ON`, `Spectator OFF` | Allows or blocks spectators | ✓ |
| `GrantRPToAll <points>` | Gives every player requisition points | ✗ |
| `ServerName`, `ServerClan` | Do nothing; they reply that remote administration cannot set them | ✓ |

- A ranked server tests its current status, so the ✗ commands run once the map is unranked.
- Password, bots, difficulty, limits, warmup and the map cycle carry over to the next maps. A broken rule
  stays broken until the admin sets the value back.

## The news file

Assault and Skirmish declare an unlock (`S9UnlockAssault`, `S9UnlockArcade`) with no criteria.
TimeGate opened them for everyone from their backend. Without that, a dedicated server hosting either
mode exits at startup with "This game mode is not available." The news file
[`news/section8_news.txt`](../news/section8_news.txt) opens them again and sets the in-game banner. The
server reads it on every request, so edits apply without a restart.

```
[Settings]
TGGameInfo-S9GameInfoAssault.Unlock=None
TGGameInfo-S9GameInfoArcade.Unlock=None
[MOTD]
MOTD_INT=Welcome to the server.
```

`[Settings]` lines are `<GameInfoFilter>-<Class>.<Property>=<Value>`, applied as a console `SET`.

- The filter must match the GameInfo that is live at login, which is the entry map's. Use
  `TGGameInfo`; `S9GameInfo` matches nothing.
- Class names must be bare. `S9Game.S9GameInfoAssault` breaks the parse.
- `;` starts a comment. A malformed line silently drops the rest of the section.

`[MOTD]` holds `MOTD_<language>`, falling back to `MOTD_INT`. This block has no comment handling: the
first line that contains the key wins, even a comment. Keep it to one line, and never mention the key
again below it.

Problems show in the game's log, `Documents\My Games\Section 8 Prejudice - PC\S9Game\Logs\Launch.log`
(launch with `-FORCELOGFLUSH`):

| Log line | Meaning |
|---|---|
| `Improper News Settings file on line N` | The line does not split into filter, class, property and value |
| `Invalid setting filter class X` | The filter is not a class |
| `Unrecognized class X` / `Unrecognized property X` | The class or property is wrong |
| nothing | Applied, or the filter did not match the live GameInfo |
