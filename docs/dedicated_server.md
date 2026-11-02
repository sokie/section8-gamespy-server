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
| `-xlln_local_instance_id=2` | Required. Gives the server its own XLLN config, see below |

The game runs a match as ranked only within its official bounds: `timelimit` 15–35 and `goalscore`
500–2000. Outside them the server still starts, unranked. Stats are recorded only with **2 or more
humans**; bots do not count, and the game prints "minimum players not met (2)" until a second human
joins.

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
- `?mapcycle=` entries need the variant suffix. One `?game=` carries across the whole cycle.

Maps, each in all variants: `ARC02_Base` Whiteout, `DES01_Base` Eden, `LAV02_Base` Prometheus,
`TER01_Base` Zephyr (base game); `ARC01_Base` Sky Dock, `LAV01_Base` Abaddon (DLC2); `DES02_Base`
Desolation, `TER02_Base` Overseer (DLC3).

To see which mode actually started, read the mode the server published:

```bash
python -c "import sqlite3;print(list(sqlite3.connect('section8.db').execute(\"select owner_id,value from fields where table_id='ServerStatusTG09_v6' and name='Status_GameMode'\")))"
```

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
