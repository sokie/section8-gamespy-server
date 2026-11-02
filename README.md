# Section 8 GameSpy Server

A server emulator for TimeGate's **Section 8** games. It reimplements the GameSpy / ATLAS backend that
the games reach through XLiveLessNess (XLLN), so login, ranked stats, XP, levels and awards work again
after the GameSpy shutdown. Pure Python standard library, one SQLite file, no third-party service.

| Game | `gamename` | GameSpy `gameid` | Status |
|---|---|---|---|
| **Section 8: Prejudice** (2011) | `tg09pc` | 3160 | Login, ranked stats and XP verified in the game |
| **Section 8** (2009) | `section8pc` | 2566 | Login, news and the stats screen work. Ranked match reports are not yet tested in the game |

**What works:** GameSpy login and account creation, ranked match reports folded into lifetime careers
(every ranked stat, badge, feat and weapon), Assault and Skirmish unlocked through the news file, one
server for both games on a LAN or the internet, and a [stats website](docs/stats_portal.md).

**Not served here:** the server browser, leaderboard screens and achievements. They use Games for
Windows - Live, which XLLN provides.

## Getting started

1. **Download the latest release** for your OS from the
   [Releases page](https://github.com/sokie/section8-gamespy-server/releases/latest) and unpack it. Keep
   the folders (`news/`, `catalog/`, `xlln-xlsp/`, `portal_assets/`) next to the executable.
2. **Run `section8-server`**. It listens on TCP 8901–8906 and UDP 27900, serves the stats website on
   http://localhost:8080/, and creates `section8.db` next to itself. To change settings, copy
   `config.example.json` to `config.json`.
3. **Set up the game** on each PC:
   - Put XLLN's `xlive.dll` next to the game executable, and add the Section 8 quick-patch module, which
     switches the game's AuthService and Competition URLs to `http://` and skips the certificate check.
   - Start the game once so that XLLN creates its `XLiveLessNess` folder (the one holding
     `xlln-config-1.ini`). Copy `xlln-xlsp/prejudice.json` (or `section8.json`) into its `xlln-xlsp`
     folder.
   - The shipped files point to `127.0.0.1`. For a server on another machine, write them with its
     address: see [Hosting](docs/hosting.md#player-files).
4. Start the server, then the game.

Section 8 (2009) needs a few extra steps: [docs/section8_2009.md](docs/section8_2009.md).

## Building from source

Python 3.10 or newer. Nothing to install for the server itself.

```bash
git clone https://github.com/sokie/section8-gamespy-server.git
cd section8-gamespy-server
python -m server [config.json]
```

Tests run in-process, with no game:

```bash
python -m tests.smoke            # Prejudice: codecs, Sake, GPCM login, ranked stats
python -m tests.smoke_section8   # Section 8 (2009) and title routing
python -m tests.smoke_portal     # stats website and JSON API
python -m tests.smoke_carry      # scripts/carry_over.py
python -m tests.smoke_container  # Docker entrypoint
```

Lint, type check and a standalone executable need the dev tools:

```bash
pip install -r requirements-dev.txt
ruff format --check . && ruff check .
mypy server --ignore-missing-imports
pyinstaller section8-server.spec    # -> dist/section8-server[.exe]
```

Docker image: `docker build -t section8-gamespy-server .`

Pushing a `v*` tag builds the release archives for Linux, Windows and macOS (Intel and Apple silicon),
and publishes the Docker image `ghcr.io/sokie/section8-gamespy-server`.

## Documentation

| Document | Content |
|---|---|
| [Hosting](docs/hosting.md) | Configuration, ports and firewall, player files, the database, Docker and automatic updates |
| [Dedicated ranked server](docs/dedicated_server.md) | Launch lines, game modes, maps, ranked rules, and the news file that unlocks Assault and Skirmish |
| [Section 8 (2009)](docs/section8_2009.md) | Setup, the exe patch, differences from Prejudice, its stat catalogue |
| [Stats portal](docs/stats_portal.md) | Pages, JSON API, display names and images |
| [Architecture](docs/architecture.md) | Services, session flow, storage, the ATLAS report format, how ranked stats are mapped and folded |
| [Stat catalogue](docs/stat_catalog.csv) | Every Prejudice ranked stat: report key, property id, column, fold and name |
| [Section 8 stat catalogue](docs/stat_catalog_section8.csv) | The same for Section 8 (2009) |

## Contributing

Contributions are welcome, especially protocol captures and Section 8 (2009) testing. Keep the server
small and free of dependencies, and run the tests and `ruff` before opening a pull request.

## License

[GPL-3.0](LICENSE).

## Disclaimer

A fan-made preservation project, not affiliated with or endorsed by TimeGate Studios, GameSpy or any
rights holder. It ships no game code or assets, only a clean-room reimplementation of the network
services. Use it only with games you own.
