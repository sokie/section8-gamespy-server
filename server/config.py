"""Runtime configuration: a JSON file (see config.example.json) merged over the defaults below."""

import json
import os

DEFAULTS = {
    # 0.0.0.0 hosts the whole LAN, so several PCs share one leaderboard. 127.0.0.1 is solo.
    "bind_address": "0.0.0.0",
    "db_path": "section8.db",
    "log_path": "section8_gamespy.log",
    "server_challenge": "ABCDEFGHIJ",
    # Served at motd.asp only. The in-game banner comes from news/section8_news.txt.
    "motd_message": "Welcome to the Section 8 GameSpy revival server. See you on the battlefield!",
    # XLSP tunnel ports the game dials. gpcm = server-speaks-first presence; http = SOAP routed by path.
    "ports": {
        "8800": "http",
        "8901": "gpcm",
        "8902": "gpcm",
        "8903": "http",
        "8904": "http",
        "8905": "http",
    },
    # Keyed by the lowercase gamename the game sends on the wire. secret_key and gameid are not read by
    # the GPCM/Auth/Sake path, which accepts the wire secretKey as-is; they are here for a future
    # ServerBrowser or enctypex path, which does need them.
    "games": {
        "tg09pc": {"secret_key": "OGmgyP", "gameid": 3160},  # Section 8: Prejudice
        "section8pc": {"secret_key": "2UMehS", "gameid": None},  # Section 8 base game, gameid unknown
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
        """Per-game config for a wire gamename, case-insensitive. An unknown name gets a null-valued
        default, so callers never KeyError."""
        if gamename:
            info = self.games.get(gamename.lower())
            if info is not None:
                return info
        return {"secret_key": None, "gameid": None}

    @classmethod
    def load(cls, path: str | None):
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return cls(json.load(f))
        return cls({})
