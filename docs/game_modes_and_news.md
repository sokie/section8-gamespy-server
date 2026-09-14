# Unlocking Assault and Skirmish

Two of Prejudice's four multiplayer modes refuse to start against a private backend. This is what is
actually going on, and why [`news/section8_news.txt`](../news/section8_news.txt) is the fix rather than
a binary patch. It also covers how the game picks a mode in the first place, since that trips people up
on its own, and the MOTD banner, since it rides along in the same file.

All of it comes from the shipped `S9Game.u` and `TGEngine.u` packages and from `S9-Win32-F.exe`, then
checked against what the game actually does. Launch lines and the map tables are in
[`dedicated_ranked_server.md`](dedicated_ranked_server.md).

## How the game picks a mode

`?game=<Package.Class>` on the map URL. No underscore in the class name, and watch out for Skirmish:

| Mode (as the game names it) | `?game=` value | `GameModeID` |
|---|---|---|
| Conquest | `S9Game.S9GameInfoConquest` | 1 |
| Swarm | `S9Game.S9GameInfoSwarm` | 2 |
| Skirmish | `S9Game.S9GameInfoArcade` | 3 |
| Assault | `S9Game.S9GameInfoAssault` | 4 |

`S9GameInfoArcade` really is Skirmish. Arcade was the internal name and the class still carries
`DisplayName="SKIRMISH"`. As for `S9GameInfo_Assault`, the underscored spelling you will find in
community guides, that is not a class at all. Passing it does not error. You just get Conquest.

Two layers read the option. Native first: `UGameEngine::LoadMap` pulls `GAME=` out of the URL options
and `StaticLoadClass`es it, which shows up as `Log: Game class is 'S9GameInfoAssault'` in `Launch.log`.
Then script: `TGGameInfo.SetGameType` re-parses the same option, but hands back the class only if it
turns up in `GetValidGameTypes()`. If it does not, it falls back to `valid[0]` without a word. That is
why a typo, or a mode the map cannot host, never errors. It just puts you in the wrong mode.

`GetValidGameTypes` reads `TGMapInfo.Variants[?Variant=].SupportedGameClasses` off the loaded map. You
never write `?Variant=` yourself, because the engine splits it out of `MAP-Suffix`.

So the variant is what really decides. From the compiled class defaults in `S9Game.u`:

```
S9MapVariantConquest.SupportedGameClasses = (S9GameInfoConquest, S9GameInfoArcade, S9GameInfoAssault)
S9MapVariantSwarm.SupportedGameClasses    = (S9GameInfoSwarm)
S9MapVariantCampaign.SupportedGameClasses = (S9GameInfoCampaign)
```

`LargeA`, `MediumA`, `SmallA` and `SmallB` all inherit from `S9MapVariantConquest`, so Assault and
Skirmish run on the plain Conquest maps. No `-AssaultA` variant ships in any version or DLC, so ignore
any guide telling you to hunt for a map file with "Assault" in the name. Index 0 is the fallback, which
is why leaving `?game=` off gives you Conquest on those maps and Swarm on `-SwarmA`.

Two smaller things that save an ini edit. `S9GameTypeDescriptorAssault` and `...Arcade` both extend
`...Conquest`, so `IsMapValidForGameMode` accepts them even on maps whose `DefaultGame.ini` lists only
Conquest under `ValidGameModes`. And map rotation keeps the mode, because `RestartGame` travels with
`GetTravelType() == false`, which is relative, and nothing overrides it.

## Why Assault and Skirmish quit at startup

Picking the mode is not enough. `TGGameReplicationInfo.ReceivedGameClass()` runs this on the server:

```unrealscript
tggi = GetDefaultTGGameInfo();
pc   = TGPlayerController(WorldInfo.GetALocalPlayerController());   // always none on a dedicated server
if (!tggi.IsAvailable(pc))
    if (WorldInfo.NetMode == NM_DedicatedServer)
        RequestExit(false, GameModeUnavailable);
```

`IsAvailable` boils down to `StaticIsUnlocked(default.Unlock, none)`, and the native short-circuits to
TRUE whenever `Unlock` is `None`:

```
00782971: 85C9    TEST ECX,ECX          ; ECX = the Unlock class
00782973: 7510    JNZ  0x00782985       ; non-None -> real profile check
00782975: ...     MOV EAX,1             ; None -> return TRUE
```

Only Assault and Skirmish declare an `Unlock` at all, `S9UnlockAssault` and `S9UnlockArcade`. Conquest
and Swarm declare none. That is the whole reason those two always worked and these two never did. And
`S9UnlockAssault` carries no `UnlockID` and no criteria whatsoever, so nothing a player or a server
does locally can satisfy it. TimeGate flipped it on for everyone server-side when the community hit ten
million kills.

What you see when it fires:

```
Log: Game class is 'S9GameInfoAssault'
ScriptLog: GameTypeDescriptor:  S9GameTypeDescriptorAssault
Log: appRequestExit(0)
Error: Error, This game mode is not available.
```

The trial check in `IsAvailable` is a red herring, incidentally. `IsTrialEnabled` is literally
`MOV EAX,[GIsTrialMode]; RET`, and the log prints `GIsTrialMode: FALSE`.

There is a two-byte patch that works: `75 10` to `90 90` at file offset `0x00381d73` forces the gate
open. Do not bother with it. It unlocks every `TGBUnlockBase` item as a side effect, and the news file
opens the two modes the way the game intends.

## How the news file reaches the game

```
Sake SearchForRecords on NewsStats_v6         -> News_Settings_FileID + recordid
GET /SakeFileServer/download.aspx?fileid=<id> -> the news file
```

Four things about this chain are easy to get wrong:

- The wire field is `News_Settings_FileID`. The XLAST column mapping calls it `Settings_FileID`, and
  answering under that name hands the game a file id of 0.
- `recordid` has to be non-zero, and it has to change when the content changes. The game checks
  `Rows[0].RecordId != NewsVersion` against its cached copy and skips the download when they match, so
  any constant means the file is never fetched. The 0 that a synthetic zeroed row returns counts as a
  constant.
- `NewsStats` is a `SystemOwnedTable`: read-only, just an index pointing at the payload. Nothing gets
  written back to it.
- The `Sake-File-Result:` and `Sake-File-Id:` headers are never read. Those strings have no code
  references anywhere, because the ANSI GameSpy sake-file client is not linked into this build and the
  download goes through UE3's own wide-string ghttp path instead.

### The payload has to be UTF-16LE

The game decodes the download with its own `GetNewsFileAsStringArray`, which does not sniff for a BOM.
Serve the file as ASCII and every section header quietly fails to match, so the whole thing does
nothing and logs nothing, anywhere. That looks exactly like a syntax error from the outside, and it was
the single fault sitting behind both the dead unlock and the missing MOTD.

`server/news.py` keeps the file as UTF-8 on disk, so it stays editable, and converts on the way out. It
normalises line endings too, so a `core.autocrlf` checkout cannot slip a trailing `\r` into every
value.

## The `[Settings]` block

```
<GameInfoFilter>-<Class>.<Property>=<Value>
```

Each line becomes a `TGSNewsSettings {GameInfoFilter, SettingClass, SettingParam, NewSettingValue}`,
applied as a console `SET <Class> <Property> <Value>`. `FillNewsSettings` splits on `;` for a comment,
`-` between filter and body, `=` for the assignment, and `.` between class and property. That last
split runs from the end, so `Class'Pkg.Name'` works too. The filter takes an optional `:min,max`
version gate.

Three ways to get this wrong, none of which say so clearly.

**The filter has to match whatever GameInfo is live when news is applied.** News lands during login,
while the entry map's `S9GameInfoEntryEmpty` is current, and that one comes from `TGGameInfoEntry`,
not from `S9GameInfo`:

```
GameInfo
 └ TGGameInfo
    ├ TGGameInfoEntry → S9GameInfoEntryEmpty    ← live when news applies
    └ S9GameInfo      → S9GameInfoAssault
```

Filter on `S9GameInfo` and nothing matches. No error, no effect. Use `TGGameInfo`, the common ancestor.

**Class names have to be bare.** Package-qualify one as `S9Game.S9GameInfoAssault` and the extra dot
breaks the class and property split, which rejects the entire section.

**A malformed line kills the rest of the section.** Nothing after it is read, so a file full of
candidate spellings only ever tests its first line. Change one thing at a time.

Diagnostics go to the game's own log, at
`Documents\My Games\Section 8 Prejudice - PC\S9Game\Logs\Launch.log`. Launch with `-FORCELOGFLUSH` or
you lose the tail whenever the process hangs on exit.

| Log line | Meaning |
|---|---|
| `Improper News Settings file on line N` | structural: the line does not split into the four fields |
| `Invalid setting filter class X` | filter token did not resolve to a class |
| `Unrecognized class X` / `Unrecognized property X` | class or property token wrong |
| *(silence)* | parsed and applied, **or** the filter did not match the live GameInfo |

One message covers several different failures, so the quickest way to find a fault is a throwaway probe
line that separates structure from naming. `A-B.C=D` comes back with `Unrecognized class B` and
`Invalid setting filter class A`, labelling every field at once.

## The `[MOTD]` block

```
[MOTD]
MOTD_INT=Welcome to the server.
```

The key is `MOTD_` plus the game's language ext, falling back to `MOTD_INT`, or `TRIAL_<lang>` when
`GIsTrialMode` is set. Everything to the right of the first `=` is what appears on screen.

Now the trap. This section is not parsed like `[Settings]` at all: no comment handling, no section
terminator. Every line from the header down gets scanned, the first one containing the key wins, and
everything right of its first `=` becomes the banner. So a comment that merely mentions the key is
displayed instead of your real line. A comment reading

```
;Key is "MOTD_" + the game's language ext, with MOTD_INT as the fallback; value is split on '='.
```

renders a banner of `'.`. Keep the block to one line, and never mention the key again after the header.

Despite the name, `motd.asp` in `server/motd.py` is not where this banner comes from. Changing the text
served there to a marker string left the banner untouched, while the banner's content traced character
for character to a line in the news file. `motd.py` is only there so those requests do not fail.

## Digging into the packages yourself

- `S9Game/CookedPCFinal/decompress.exe` (Gildor's) unpacks `.u` and `.tgm` packages.
- The cooked `.u` files still hold full UnrealScript source text, so grep them directly.
- `defaultproperties` are not in that text. They live in the compiled class default object, which needs
  a package parser to read.
- Native functions are registered as `{name_ptr, func_ptr}` pairs, and the pointer comes after the
  name. Read that backwards and you get the previous entry's function, which patches cleanly and
  changes nothing at all.
