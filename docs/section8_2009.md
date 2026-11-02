# Section 8 (2009)

The server was built for Prejudice and serves the 2009 game beside it, from the same process and
database. Login, the certificate, news and the stats screen work in the game. Ranked match reports are
not yet tested in the game: look for `UNHANDLED` lines in `section8_gamespy.log`.

Everything below applies to the retail Games for Windows - Live `S8Game-F.exe` (SHA-256 `520670c7…a51bd8`)
with XLLN 1.6.2.1.

## Setup

1. Put XLLN's `xlive.dll` next to `Binaries\S8Game-F.exe`, and start the game once so that XLLN creates
   its `XLiveLessNess` folder (the one holding `xlln-config-1.ini`).
2. Copy `xlln-xlsp\section8.json` into that folder's `xlln-xlsp\`. For a server on another PC, write the
   file with its address: see [player files](hosting.md#player-files).
3. Patch the exe (below), on a copy or in memory with the quick-patch module.
4. Turn off XLLN auto sign-in: `xlive_user_auto_login_p1 = 0` in `xlln-config-1.ini`.
5. Start the server, then the game. At the title screen, sign in from the XLLN window.

The game logs in to GameSpy only when a user signs in after startup. With auto sign-in it signs in before
XLLN's title server is ready, never retries, and Stats and Awards stay unavailable.

## The exe patch

| File offset | Edit | Why |
|---|---|---|
| `0x0164A550` | `https://` → `http://` in the AuthService URL | The server speaks plain HTTP |
| `0x0164A730` | `https://` → `http://` in the Competition URL | Optional: the game already rewrites it at startup |
| `0x00EFDF45` | `neg eax` → `xor eax,eax` | `wsLoginCertIsValid` always succeeds. Only GameSpy's key could sign a real certificate |

Patched SHA-256: `a1a1d433a8b201085c785ead4d6d5537b427e9fe725012cd7002f17af91c00ea`.

## Differences from Prejudice

- **Routing.** The game's gameid is 2566 and its XLLN title id `0x535007E3`. The server also sends to
  Section 8 any request whose gameid no title claims, and logs the first one
  (`[titles] first request with gameid=…`) so that you can add it to `titles.section8.gameids`.
- **Ports.** The same LSP base port as Prejudice, 8900. If the game's `Launch.log` shows
  `returning port NNNN` with other numbers, set `titles.section8.lsp_base_port` to the lowest one minus 1
  and write the XLSP file again.
- **Storage.** The 2009 tables are stored under an `s8:` prefix, so the games never share a table. They
  share accounts: one nick has the same profileid in both.
- **Career.** `PlayerStats_v5`, XP in `Ranked_XP`, 16 gear slots (Prejudice has 60).
- **Veteran unlocks.** Each Section 8 match writes the player's level into Prejudice's
  `S8Level_v6.Ranked_Level`. Prejudice reads it to unlock Reactive Plating and Aegis Armor (level 1),
  Ablative Layering (25) and Explosives Amplifier (50).
- **Ranked minimum.** 2 humans, bots excluded (`RankedRequiredMinHumanPlayers` in `S8Game.u`). The exe
  checks each package's SHA-1, so the package cannot be changed to lower it.

## Stat catalogue

[`catalog/section8.json`](../catalog/section8.json) maps the game's 340 ATLAS report keys to its Sake
columns, with the level table, ranks, and badge, feat and weapon names. The server loads it.
[`stat_catalog_section8.csv`](stat_catalog_section8.csv) lists the same stats in a readable table. Both
are generated from the game's packages:

```bash
python scripts/build_section8_catalog.py <S8Game.u> <unpacked OnlineSubsystemGameSpy.u>
python scripts/build_section8_catalog.py --csv    # only the CSV, from catalog/section8.json
```

Every key has the same number, column and fold as in Prejudice's [stat catalogue](stat_catalog.csv);
only `Ranked_XP` and `Ranked_clantag` differ in case. The build checks this.

Without a catalogue, matches still count: keys are stored raw as `Unmapped_Key<n>`, with the full
history. After adding or updating the catalogue, fold the careers again:

```bash
python scripts/stats_tool.py --title section8 rebuild
```

[`catalog/section8.example.json`](../catalog/section8.example.json) shows the format. Only `stats` is
required: each entry has `key`, `column`, `fold` (`sum`, `max`, `replace` or `date`) and `label`, plus
optional `family`, `sake_type` and `date_of`. Optional top-level fields: `player_table`, `xp_column`,
`level_column`, `level_xp` (cumulative XP per level), `ranks` and `game_modes`.
