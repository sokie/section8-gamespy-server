# Stats portal

A stats website and JSON API, served by the server itself on http://localhost:8080/ (other PCs:
`http://<server address>:8080/`). The stats pages read the database through read-only connections, so
they never slow the game services. Only the account page changes data.

| Page | Shows |
|---|---|
| `/` | Totals, the top 10 by XP, servers online |
| `/leaderboard` | Every ranked player, sortable by XP, kills, K/D, time or matches |
| `/players/<profileid>` | Level, career, records, time per mode, badges, feats, weapons, the last 25 matches |
| `/players?q=<name>` | Search by display name, nick or profileid |
| `/servers` | Dedicated servers; offline after 5 minutes without an update |
| `/account` | Sign-in, and the display name and clan tag of each profile the account claimed |
| `/api` | JSON: `/api/games`, `/api/summary`, `/api/leaderboard?sort=&offset=&limit=`, `/api/players?q=`, `/api/players/<id>`, `/api/servers` |

Section 8 (2009) has the same pages under `/s8` and `/api/s8/`. A player of both games has one profile,
and each career page links to the other.

## How the pages work

The pages are static files in [`server/web`](../server/web). The server sends each one as it is, and the
page's script fills it from the JSON API in the browser. Nothing is built or installed: edit a file and
reload the page.

| File | Holds |
|---|---|
| `index.html`, `leaderboard.html`, `player.html`, `search.html`, `servers.html` | One page each: its markup, and a `<template>` for each repeated part (a table row, an award card) |
| `api.html`, `not-found.html` | Pages with no data |
| `partials/*.html` | Markup shared by several pages: the header, the footer, the leaderboard and servers tables. A page pulls one in with `<!--#include file="header.html" -->` |
| `portal.css` | All styles |
| `js/portal.js` | Shared code: the open game, the header, API calls, number and time formats, and `render()`, which fills a template |
| `js/<page>.js` | One script per page |

A script puts every value into the page as text, never as markup, and the pages run only these scripts
(`Content-Security-Policy: script-src 'self'`). The API gives each player, badge, feat and unlock an
`icon` URL: an image from `portal_assets/`, or a drawn stand-in from `/drawn/`.
`python -m tests.smoke_portal --keep` serves test data on http://127.0.0.1:18080/ for trying changes.

## Settings

`portal_enabled` (default `true`) and `portal_port` (`8080`) in `config.json`. The portal binds to
`bind_address`. If the port is taken, the server logs `skip stats portal` and the game services run as
usual. Anyone who reaches the port sees the stats. An account is needed only to claim a profile.

`python -m server.portal --config <config.json> serve` runs the portal on its own, without the game
services.

## Accounts and claimed profiles

A player creates an account on `/account` with a username and a password for the portal only. The
game's GameSpy password is not used, because the game makes it from the profile.

The account then claims the player's game profile with the code from the game:

1. In Section 8: Prejudice, sign in to Prejudice Online, open Dropship, then Stats Portal. The game
   shows a code such as `758B-338A-154D` and writes the code's random part to its profile record.
2. On `/account`, enter the code. The first eight digits name the profile. The last four must match
   the random part that the game wrote last. The game makes a new code each time the screen opens.
3. The claim sets `Profile_LinkCodeUsed`. At its next sign-in, the game shows "Your account has been
   activated" instead of a code.

A profile has one owner. The owner sets its display name and clan tag, and can release it. A clan tag
has up to 3 letters and digits, and `TG` is reserved. The game reads the tag at its next sign-in. To
free a profile whose owner lost their account:

```bash
python -m server.portal release T8iPxxxxxxxx      # or the profileid; safe while the server runs
```

The game then shows a new code at its next sign-in. A code that claimed a profile does not work again.

| Limit | Value |
|---|---|
| Wrong codes for one code of a profile | 5, then the player opens the Stats Portal screen for a new code |
| Wrong codes for one account | 20 an hour |
| Failed sign-ins | 10 per username and 30 per address, in 15 minutes |
| New accounts per address | 5 an hour |

A password is stored as a salted scrypt hash. The session cookie is `HttpOnly` and `SameSite=Strict`,
and it lasts 14 days. The account API refuses a request that is not JSON or that comes from another
site. For a public server, put the portal behind HTTPS, so that passwords do not cross the network as
plain text. Behind a reverse proxy on a private address, such as Traefik, the portal takes the browser's
address from `X-Forwarded-For` and marks the cookie `Secure` when `X-Forwarded-Proto` is `https`.

The account API, under `/api/account`:

| Request | Does |
|---|---|
| `GET /api/account` | The signed-in account and its profiles, or `{"signed_in": false}` |
| `POST /register`, `POST /login` `{"username", "password"}` | Creates an account or signs in, and sets the session cookie |
| `POST /logout` | Ends the session |
| `POST /password` `{"current", "new"}` | Changes the password and ends the other sessions |
| `POST /claim` `{"code"}` | Claims the profile that shows this code |
| `POST /profiles/<profileid>` `{"display_name", "clan_tag"}` | Sets either one; an empty value clears it |
| `POST /profiles/<profileid>/release` | Frees the profile |

## Display names

The game logs in with a generated GameSpy nick (`T8iP…`), not the gamertag. The owner of a claimed
profile sets its name on `/account`. An admin can name any profile, while the server runs if you like:

```bash
python -m server.portal name T8iPxxxxxxxx "Leon"   # by nick
python -m server.portal name 10000001 "Leon"       # by profileid
python -m server.portal name 10000001              # clear it
```

Or list them in `config.json`, keyed by nick (not case sensitive) or profileid. A name in
`config.json` is shown instead of the owner's name:

```json
"portal_names": {"T8iPFFF6FFFF2D5C59B7": "StoneCold"}
```

## Images

No game art ships with the repo. The portal draws a placeholder for each missing image, so it looks
complete without any. To use the games' art, export it from your own copies (UE Viewer for textures,
JPEXS for Scaleform UI) into `portal_assets/`. The portal picks up new files within 10 seconds.

| File | Used for |
|---|---|
| `games/prejudice.jpg`, `games/section8.jpg`, `games/prejudice-logo.png` | Banners and profile headers |
| `levels/<1-100>.png`, `ranks/<1-21>.png` or `ranks/<rank-name>.png` | Rank insignia |
| `badges/B<0-31>.png` or `badges/<name>.png` | Badges |
| `feats/F<0-31>.png` or `feats/<name>.png` | Feats |
| `gear/G<n>.png`, `gear/<weapon>-<mod>.png` or `gear/<weapon>.png` | Weapons and gear |
| `unlocks/<name>.png` | Unlocks |
| `section8/badges/…`, `section8/feats/…`, `section8/gear/…`, `section8/ranks/…` | The 2009 game's art |

PNG, JPG, WebP and GIF work. Names are lowercase with dashes (`badges/infantry-assault.png`) and not
case sensitive. [`portal_assets/mapping.json`](../portal_assets/mapping.json) maps a name to an exported
file (`{"badges/B0": "export/T_Badge_00.png"}`). `python -m server.portal assets` lists every image the
portal looks for, found or missing.

[`portal_assets/unlocks.json`](../portal_assets/unlocks.json) lists the unlocks a profile shows, each
lit when the player reaches its `level` (Prejudice) or `section8_level`. It holds the four veteran items;
add others from the game's unlock screens.
