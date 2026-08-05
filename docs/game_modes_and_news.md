# Game modes, the unlock gate, and the news file

How Section 8: Prejudice picks a game mode, why two of the four modes refuse to start against a private
backend, and the exact format of the news payload that fixes it. Reverse-engineered from the shipped
`S9Game.u` / `TGEngine.u` script packages and `S9-Win32-F.exe`, then validated in-game.

For the practical launch lines, see [Selecting the game mode](../README.md#selecting-the-game-mode) in
the README. This document is the "why", and the reference for changing the news file.

---

## 1. Mode selection

The mode is chosen by the `?game=<Package.Class>` URL option. Class names carry **no underscore**, and
one of them does not match the name the game displays:

| Mode (as the game names it) | `?game=` value | `GameModeID` |
|---|---|---|
| Conquest | `S9Game.S9GameInfoConquest` | 1 |
| Swarm | `S9Game.S9GameInfoSwarm` | 2 |
| Skirmish | `S9Game.S9GameInfoArcade` | 3 |
| Assault | `S9Game.S9GameInfoAssault` | 4 |

`S9GameInfoArcade` really is Skirmish - "Arcade" is the internal name and the class carries
`DisplayName="SKIRMISH"`. There is no `S9GameInfo_Assault`: the underscored spelling that circulates in
community guides is not a class, and passing it does not error, it just starts in Conquest.

Two layers resolve it:

- **Native.** `UGameEngine::LoadMap` parses `GAME=` out of the URL options and `StaticLoadClass`es it.
  Confirmed by `Log: Game class is 'S9GameInfoAssault'` in `Launch.log`.
- **Script.** `TGGameInfo.SetGameType` then re-parses the option and returns the class **only if it
  appears in `GetValidGameTypes()`**; otherwise it silently falls back to `valid[0]`. A typo or a mode
  the map does not support is therefore never an error, just the wrong mode.

`GetValidGameTypes` reads the loaded map's `TGMapInfo.Variants[?Variant=].SupportedGameClasses`. The
engine derives `?Variant=` itself by splitting `MAP-Suffix`, so it is never written by hand.

The variant is what gates which modes are legal, from the compiled class defaults in `S9Game.u`:

```
S9MapVariantConquest.SupportedGameClasses = (S9GameInfoConquest, S9GameInfoArcade, S9GameInfoAssault)
S9MapVariantSwarm.SupportedGameClasses    = (S9GameInfoSwarm)
S9MapVariantCampaign.SupportedGameClasses = (S9GameInfoCampaign)
```

`LargeA`/`MediumA`/`SmallA`/`SmallB` all inherit from `S9MapVariantConquest`, so **Assault and Skirmish
run on the ordinary Conquest maps**. There is no `-AssaultA` variant in any version or DLC, contrary to
guidance that says to look for a map file with "Assault" in its name. Index 0 is the fallback, which is
why omitting `?game=` on those maps yields Conquest, and on `-SwarmA` yields Swarm.

`S9GameTypeDescriptorAssault` and `...Arcade` both extend `...Conquest`, so `IsMapValidForGameMode`
accepts them on every map whose `DefaultGame.ini` `ValidGameModes` lists only Conquest - no ini edit is
needed. Map rotation preserves the mode: `RestartGame` travels with `GetTravelType() == false`
(relative), and nothing overrides it.

---

## 2. The unlock gate

Selecting the mode is not sufficient. `TGGameReplicationInfo.ReceivedGameClass()` runs this on the
server side:

```unrealscript
tggi = GetDefaultTGGameInfo();
pc   = TGPlayerController(WorldInfo.GetALocalPlayerController());   // always none on a dedicated server
if (!tggi.IsAvailable(pc))
    if (WorldInfo.NetMode == NM_DedicatedServer)
        RequestExit(false, GameModeUnavailable);
```

`IsAvailable` reduces to `StaticIsUnlocked(default.Unlock, none)`, and the native returns TRUE
unconditionally when `Unlock` is `None`:

```
00782971: 85C9    TEST ECX,ECX          ; ECX = the Unlock class
00782973: 7510    JNZ  0x00782985       ; non-None -> real profile check
00782975: ...     MOV EAX,1             ; None -> return TRUE
```

Only Assault and Skirmish declare an `Unlock` (`S9UnlockAssault` / `S9UnlockArcade`); Conquest and Swarm
do not. That is the entire reason only those two ever worked against a private backend. Worse,
`S9UnlockAssault` declares no `UnlockID` and no criteria at all - nothing a player or server does can
satisfy it, because it was flipped server-side for everyone at the ten-million-kill milestone.

Symptom:

```
Log: Game class is 'S9GameInfoAssault'
ScriptLog: GameTypeDescriptor:  S9GameTypeDescriptorAssault
Log: appRequestExit(0)
Error: Error, This game mode is not available.
```

Ruled out along the way: the trial check in `IsAvailable` cannot fire, because `IsTrialEnabled` is
literally `MOV EAX,[GIsTrialMode]; RET` and the log prints `GIsTrialMode: FALSE`.

A two-byte patch at file offset `0x00381d73` (`75 10` -> `90 90`) forces the gate open and does work,
but it unlocks *all* `TGBUnlockBase` content and is unnecessary - news opens it properly.

---

## 3. The news delivery chain

```
Sake SearchForRecords on NewsStats_v6        -> News_Settings_FileID + recordid
GET /SakeFileServer/download.aspx?fileid=<id> -> the news file
```

- The wire field is **`News_Settings_FileID`**, not the `Settings_FileID` named in the XLAST column
  mapping. Answering the XLAST name hands the game a file id of 0.
- **`recordid` must be non-zero and must change with the content.** The game compares it against its
  cached news version (`Rows[0].RecordId != NewsVersion`) and skips the download when they match, so a
  constant - notably the 0 a synthetic zeroed row returns - means the file is never fetched.
- `NewsStats` is a `SystemOwnedTable`: read-only, an index pointing at the payload. Nothing is written
  back to it.
- The `Sake-File-Result:` / `Sake-File-Id:` headers are **not** consulted. Those strings have zero code
  references - the ANSI GameSpy sake-file client is not linked into this build, and the download runs
  through UE3's own wide-string ghttp path.

### Encoding: the payload must be UTF-16LE

The game decodes the download with its own `GetNewsFileAsStringArray`, not a BOM-sniffing helper.
Served as ASCII, **every section header silently fails to match and the entire file no-ops with nothing
logged anywhere** - indistinguishable from a syntax error, and the single fault behind both the dead
unlock and a missing MOTD. `server/news.py` authors the file as UTF-8 and converts on the way out, also
normalising line endings so a `core.autocrlf` checkout cannot inject a trailing `\r` into every value.

---

## 4. `[Settings]` - applying class-default overrides

```
<GameInfoFilter>-<Class>.<Property>=<Value>
```

Each entry becomes a `TGSNewsSettings {GameInfoFilter, SettingClass, SettingParam, NewSettingValue}`,
applied as a console `SET <Class> <Property> <Value>`. The delimiters, from `FillNewsSettings`: `;`
comment, `-` filter/body, `=` assignment, `.` class/property (split from the end, so `Class'Pkg.Name'`
also works), and an optional `:min,max` version gate on the filter.

Three rules, each of which fails silently or confusingly:

1. **The filter must match the GameInfo that is live when news is applied.** News is applied during
   login, while the entry map's `S9GameInfoEntryEmpty` is current - and that derives from
   `TGGameInfoEntry`, **not** `S9GameInfo`:

   ```
   GameInfo
    └ TGGameInfo
       ├ TGGameInfoEntry → S9GameInfoEntryEmpty    ← live when news applies
       └ S9GameInfo      → S9GameInfoAssault
   ```

   Filtering on `S9GameInfo` matches nothing and the line is skipped with no error and no effect.
   `TGGameInfo` is the common ancestor.
2. **Class names must be bare.** Package-qualifying (`S9Game.S9GameInfoAssault`) adds a dot that breaks
   the class/property split and rejects the whole section.
3. **A malformed line aborts the rest of the section.** Later lines are never read, so a file of
   candidate spellings only ever tests its first line. Change one thing per line.

Diagnostics land in the game's own log
(`Documents\My Games\Section 8 Prejudice - PC\S9Game\Logs\Launch.log`, launch with `-FORCELOGFLUSH` or
the tail is lost when the process hangs on exit):

| Log line | Meaning |
|---|---|
| `Improper News Settings file on line N` | structural - the line does not split into the four fields |
| `Invalid setting filter class X` | filter token did not resolve to a class |
| `Unrecognized class X` / `Unrecognized property X` | class or property token wrong |
| *(silence)* | parsed and applied, **or** the filter did not match the live GameInfo |

Because the same message covers several failures, the fastest way to locate a fault is a synthetic
probe that separates structure from naming - `A-B.C=D` yields `Unrecognized class B` and
`Invalid setting filter class A`, labelling every field at once.

---

## 5. `[MOTD]` - a different parser, and its trap

```
[MOTD]
MOTD_INT=Welcome to the server.
```

The key is `MOTD_` plus the game's language ext with `MOTD_INT` as the fallback (`TRIAL_<lang>` when
`GIsTrialMode` is set); everything right of the first `=` is displayed.

**This section is not parsed like `[Settings]`.** It has no comment handling and no section terminator:
every line from the header onward is scanned, the **first line containing the key wins**, and everything
right of its first `=` becomes the banner. So a comment that merely *mentions* the key is displayed
instead of the real line - a comment reading

```
;Key is "MOTD_" + the game's language ext, with MOTD_INT as the fallback; value is split on '='.
```

renders a banner of `'.`. Keep the block to a single line and never mention the key after the header.

`motd.asp` (served by `server/motd.py`) is **not** the source of this banner despite its name. Proven by
control: changing the text served there to a marker string left the banner unchanged, while the banner's
content was traceable character-for-character to a line in the news file. `motd.py` exists only so those
requests do not fail.

---

## 6. Reference: tooling

- `S9Game/CookedPCFinal/decompress.exe` (Gildor's) unpacks `.u` and `.tgm` packages.
- The cooked `.u` files still contain **full UnrealScript source text** - grep them directly.
- `defaultproperties` are not in that text; they live in the compiled class default object and need a
  package parser.
- Native functions are registered as `{name_ptr, func_ptr}` pairs - the function pointer **follows** the
  name. Reading that backwards yields the previous entry's function, which patches cleanly and changes
  nothing.
