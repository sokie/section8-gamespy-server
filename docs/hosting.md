# Hosting a server

One server holds every career, for both games. Run it on one PC for yourself, on one PC for a LAN, or on
an internet host so that players anywhere share one leaderboard.

## Running

- **Release:** run `section8-server` from the unpacked archive. It reads `config.json`, and writes
  `section8.db` and `section8_gamespy.log`, next to the executable, whatever the start directory.
- **Source:** `python -m server [config.json]`.
- **Docker:** see [Docker](#docker).

With no config the server binds every interface and listens on TCP 8901–8906 (and 8800, a spare HTTP
port), UDP 27900, and 8080 for the [stats portal](stats_portal.md).

## Configuration

Copy `config.example.json` to `config.json` and keep only the keys you change.

| Key | Default | Use |
|---|---|---|
| `bind_address` | `0.0.0.0` | `127.0.0.1` keeps the server to this PC |
| `db_path`, `log_path` | `section8.db`, `section8_gamespy.log` | Relative to the working directory, or to the executable |
| `xlsp_endpoint_host` | `127.0.0.1` | The address written into the [player files](#player-files) |
| `ports` | each title's ports | A map of port to `gpcm` or `http`, to listen elsewhere |
| `titles.section8.lsp_base_port`, `titles.section8.gameids` | `8900`, `[2566]` | See [Section 8 (2009)](section8_2009.md#differences-from-prejudice) |
| `portal_enabled`, `portal_port`, `portal_names` | `true`, `8080`, `{}` | See [stats portal](stats_portal.md) |
| `udp_available`, `udp_available_port` | `true`, `27900` | The GameSpy availability responder |

## Ports and firewall

| Port | Open to | Service |
|---|---|---|
| 8901–8906/tcp | players | GPSP, GPCM, AuthService, CompetitionService, Sake, MOTD |
| 27900/udp | players | Availability check. The 7-byte answer is too small for amplification |
| 8080/tcp | whoever should see the stats portal | Read-only, no login. Behind a reverse proxy, open 80 and 443 instead |
| 8800/tcp | nobody | Spare HTTP listener, no client uses it |

On a VPS, use the provider's network firewall (on Hostinger, the VPS firewall in hPanel). A firewall
inside the VPS such as `ufw` does not stop Docker's published ports, because Docker writes its own
iptables rules.

## Player files

XLLN sends the game's GameSpy traffic to the address in its XLSP service files. The files in
`xlln-xlsp/` point to `127.0.0.1`. For any other server, write them with its address and send them to
the players:

```bash
echo '{"xlsp_endpoint_host": "<server IP or hostname>"}' > xlsp.json
python -m server --write-xlsp xlln-xlsp-online xlsp.json     # or: section8-server --write-xlsp ...
```

Each player copies the file for their game into the `xlln-xlsp` folder inside XLLN's `XLiveLessNess`
folder. XLLN accepts an IPv4 address or a hostname, and resolves a hostname once, at game start, to IPv4.

## The database

- `section8.db` runs in WAL mode, so recent writes live in `section8.db-wal`. Copy or move the `.db`,
  `-wal` and `-shm` files together, and only while the server is stopped.
- A newer server upgrades the database on start and keeps every career.
- Every ranked match is kept in the history tables, and its raw report in `reports/`.

Admin commands, from a source checkout (`--db` picks the file, `--title section8` the 2009 game):

```bash
python scripts/stats_tool.py show <nick>          # rank, totals, records, badges, feats, gear
python scripts/stats_tool.py history <nick>       # the matches behind a career
python scripts/stats_tool.py set-xp <xp> <nick>   # set a player's XP
python scripts/stats_tool.py rebuild [nick]       # fold careers again from match history
python -m server.portal name <nick> "Display Name"   # safe while the server runs
```

The views `player_career` and `player_awards` show careers by stat name in any SQLite browser.

## Docker

Each release is also an image, `ghcr.io/sokie/section8-gamespy-server`, built and boot-tested by
[`docker.yml`](../.github/workflows/docker.yml).

| Git event | Tags pushed |
|---|---|
| tag `v1.2.3` | `1.2.3`, `1.2`, `latest`, `sha-<commit>` |
| pre-release tag `v1.3.0-rc1` | `1.3.0-rc1`, `sha-<commit>` (no `latest`, so no host updates to it) |
| manual run on `main` | `latest`, `sha-<commit>` |
| pull request | nothing (build and boot test only) |

The container keeps everything on the `/data` volume: `section8.db`, `reports/`, `backups/`, the
`.image` marker, and an optional `config.json` merged over the container defaults. The log is the
container output (`docker logs`).

### Running with Traefik and automatic updates

[`docker-compose.yml`](../docker-compose.yml) runs the server and Watchtower on a host with Traefik
on 80 and 443 and a `letsencrypt` certificate resolver (for example the Hostinger "Docker and Traefik"
VPS template). Put the portal's domain in `.env`, then `docker compose up -d`:

```
PORTAL_HOST=stats.example.com
```

Check it from any PC. Each `ok` line is a service players can reach:

```bash
python scripts/check_server.py <host> --portal https://stats.example.com
```

How an update reaches the host:

1. A `vX.Y.Z` tag builds the image and pushes it as `latest`.
2. Watchtower checks `latest` every 5 minutes and recreates the `section8` container with the new image.
   It only touches containers with the label `com.centurylinklabs.watchtower.scope=section8`.
3. On a new image, the container copies the database to `/data/backups/` (newest 10 kept) before the
   server starts. If the copy fails, the container stops instead of upgrading an unbacked database.

Connected players lose their session and sign in again.

**What Watchtower can reach.** It holds the Docker socket, which is root on the host, so the compose
file pins it by digest. Its only traffic is an anonymous `ghcr.io` poll; it has no telemetry and opens
no port. Anyone who can push `latest` (your GitHub account, or this repo's tag and manual workflows)
can deploy to the host. The server container itself runs as a non-root user without the socket.

### Rolling back

Pin a version in `docker-compose.yml` (`image: ghcr.io/sokie/section8-gamespy-server:0.3.0`).
Watchtower leaves a version tag alone. If the newer version upgraded the database, restore its backup:

```bash
docker compose stop section8
docker compose run --rm --entrypoint sh section8 -c \
  'cp /data/backups/<file>.db /data/section8.db && rm -f /data/section8.db-wal /data/section8.db-shm'
docker compose up -d section8
```

Admin commands in the container run in `/data`:

```bash
docker compose exec section8 python -m server.portal name <nick> "Display Name"
docker compose exec section8 python /app/scripts/stats_tool.py --db /data/section8.db show <nick>
```
