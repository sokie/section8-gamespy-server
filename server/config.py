"""Runtime configuration. Loads a JSON file (see config.example.json) and applies defaults so the
server runs out of the box on 0.0.0.0 to host a whole LAN; set bind_address to 127.0.0.1 for solo."""
import json
import os

DEFAULTS = {
    # 0.0.0.0 = reachable by other PCs on the LAN (default, so several machines share one server /
    # leaderboard). Set 127.0.0.1 for solo / local-only (server on the same PC as the game).
    "bind_address": "0.0.0.0",
    "db_path": "section8.db",
    "log_path": "section8_gamespy.log",
    "server_challenge": "ABCDEFGHIJ",
    # The welcome message served for the GameSpy MOTD (motd.asp), once the news URL is redirected here.
    "motd_message": "Welcome to the Section 8 GameSpy revival server. See you on the battlefield!",
    # XLSP tunnel ports Section 8 dials (see README). Each maps to a service family:
    #   gpcm = server-speaks-first GameSpy presence (login / newuser)
    #   http = client-speaks-first SOAP; routed by URL path (AuthService / CompetitionService / Sake)
    "ports": {
        "8800": "http",
        "8901": "gpcm",
        "8902": "gpcm",
        "8903": "http",
        "8904": "http",
        "8905": "http",
    },
    # Per-game GameSpy identifiers, keyed by the lowercase gamename sent on the wire (the GPCM
    # \login\/\newuser\ \gamename\ field). Keeping them per-game lets one server host both Section 8
    # titles at once. NOTE: the secret key is not consumed by the AuthService/GPCM/Sake path (which
    # accepts the wire secretKey as-is); it lives here for correctness and for a future ServerBrowser /
    # enctypex path that does need it. Verify a gamename's secret key from the game binary before
    # relying on it. gameid is likewise informational until a gameid-dependent service is added.
    "games": {
        "tg09pc":     {"secret_key": "OGmgyP", "gameid": 3160},   # Section 8: Prejudice
        "section8pc": {"secret_key": "2UMehS", "gameid": None},   # Section 8 (base game) - gameid TBD
    },
}


class Config:
    def __init__(self, data: dict):
        merged = dict(DEFAULTS)
        merged.update(data or {})
        self.bind_address = merged["bind_address"]
        self.db_path = merged["db_path"]
        self.log_path = merged["log_path"]
        self.server_challenge = merged["server_challenge"]
        self.motd_message = merged["motd_message"]
        self.ports = {int(p): kind for p, kind in merged["ports"].items()}
        self.games = {name.lower(): dict(info) for name, info in merged["games"].items()}

    def game(self, gamename: str | None) -> dict:
        """Per-game GameSpy config (secret_key, gameid) for a wire gamename, case-insensitive. Returns
        a null-valued default when the gamename is unknown so callers never KeyError."""
        if gamename:
            info = self.games.get(gamename.lower())
            if info is not None:
                return info
        return {"secret_key": None, "gameid": None}

    @classmethod
    def load(cls, path: str | None):
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return cls(json.load(f))
        return cls({})
