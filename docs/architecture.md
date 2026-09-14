# Architecture

Overview of the services this backend runs and the ports they answer on.

## Service ports

XLLN tunnels the game's backend traffic over five XLSP ports, one service family per port:

| Port | Service | Protocol | Purpose |
|------|---------|----------|---------|
| 8901 | GPCM | GameSpy `\final\` KV, server speaks first | account creation (`\newuser\`) |
| 8902 | GPCM | GameSpy `\final\` KV, server speaks first | login (`\login\` to `\lc\2\`) |
| 8903 | AuthService | SOAP over HTTP | login certificate (`LoginUniqueNick`) |
| 8904 | CompetitionService | SOAP over HTTP | ranked match reports (ATLAS) |
| 8905 | Sake | SOAP over HTTP | stats and awards store |

AuthService and Competition ship as HTTPS. The XLLN quick-patch rewrites both URLs to `http://` and
neuters the certificate signature check, which is what lets this server answer them in the clear. Sake
was plain HTTP already.

The HTTP listeners match on URL path alone, so 8903, 8904 and 8905 are interchangeable. The table just
records what the game happens to use. The server opens 8800 as a spare HTTP port for the same reason.

## Components

```
   Section 8 client  (+ XLLN xlive.dll + quick-patch)
        |  XLSP tunnel ports
        v
 +----------------------------------------------------------+
 |  server.transport   TCP listeners + HTTP framing         |
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

## What happens during a session

1. **Login.** The game hits GPCM on 8901 and 8902. `\newuser\` creates the account, `\login\` answers
   with the `\proof\` and a login ticket. `GpcmService` decodes `passenc` and hands out a stable
   `profileid`.
2. **Certificate.** A call to AuthService `LoginUniqueNick` on 8903 returns the placeholder
   certificate. The quick-patch is what makes the client accept it, not a signature.
3. **Read stats.** Sake `SearchForRecords` and `GetMyRecords` on 8905, for `PlayerStats_v6`,
   `S8Level_v6` and `NewsStats_v6`. A fresh player has nothing stored, so `SakeService` answers with
   zeroed rows instead. Either way, this is what unblocks login and fills the Awards and ranked
   screens.
4. **Ranked match.** At match end the host runs the ATLAS flow on 8904: `CheckProfileOnBanList`,
   `CreateSession`, `SetReportIntention`, `SubmitReport`. `screport` decodes the binary blob, each
   player's round XP goes onto their career `Ranked_xp`, and the derived level is mirrored into
   `S8Level_v6`.
5. **Read back.** Next login, the game reads the new totals out of Sake.

## Persistence

One SQLite file, `section8.db`, created on first run.

Sake tables are stored entity-attribute-value rather than as real columns: a `records` row per
`(table, owner, recordid)`, and a `fields` row per typed value. `PlayerStats_v6` alone has around 991
columns, so this keeps the schema out of the code entirely and lets each field's Sake type be learnt
from the client's own `UpdateRecord` writes. A `profiles` table maps each `uniquenick` to a stable,
unique `profileid`.

Records are keyed by owner, and recordid numbering restarts per owner, so two dedicated servers can
each hold their own `ServerStatusTG09_v6` recordid 1 without colliding. That also matches how the game
thinks: to a client, "recordid 1" means "my record".

Since it is all one file, several PCs pointed at the same host share it. A `SearchForRecords` sorted on
`Ranked_xp` then ranks every player who has ever connected.
