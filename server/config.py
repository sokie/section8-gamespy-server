"""Runtime configuration: a JSON file (see config.example.json) merged over the defaults below."""

import json
import os

DEFAULTS = {
    # 0.0.0.0 hosts the whole LAN, so several PCs share one leaderboard. 127.0.0.1 is solo.
    "bind_address": "0.0.0.0",
    "db_path": "section8.db",
    "log_path": "section8_gamespy.log",
    "server_challenge": "ABCDEFGHIJ",
    # Served at motd.asp only. The in-game banner comes from the title's news file.
    "motd_message": "Welcome to the Section 8 GameSpy revival server. See you on the battlefield!",
    # XLSP tunnel ports the game dials. gpcm = server-speaks-first presence; http = SOAP routed by path.
    # null listens on each title's LSP ports and on 8800. A map sets the ports instead.
    "ports": None,
    # Per-title values merged over titles.BUILTIN, e.g. {"section8": {"gameids": [1234]}}.
    "titles": {},
    # The title for a request whose gameid or secretKey no title claims.
    "default_unknown_title": "section8",
    # The SDK reads no answer as "available" too, so this responder only shortens the start-up wait.
    "udp_available": True,
    "udp_available_port": 27900,
    # The address XLLN dials for the services, written into xlln-xlsp/*.json by --write-xlsp.
    "xlsp_endpoint_host": "127.0.0.1",
    "portal_enabled": True,
    "portal_port": 8080,
    # Images exported from your own game files. A relative path starts at the server's folder.
    "portal_assets_dir": "portal_assets",
    # Display names on the portal, by GameSpy uniquenick or profileid: {"T8iP...": "StoneCold"}.
    "portal_names": {},
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
        self.explicit_ports = merged["ports"] is not None
        self.ports = {int(p): kind for p, kind in (merged["ports"] or {}).items()}
        self.games = {name.lower(): dict(info) for name, info in merged["games"].items()}
        self.titles = dict(merged.get("titles") or {})
        self.default_unknown_title = merged.get("default_unknown_title", "section8")
        self.udp_available = bool(merged.get("udp_available", True))
        self.udp_available_port = int(merged.get("udp_available_port", 27900))
        self.xlsp_endpoint_host = merged.get("xlsp_endpoint_host", "127.0.0.1")
        self.portal_enabled = bool(merged.get("portal_enabled", True))
        self.portal_port = int(merged.get("portal_port", 8080))
        self.portal_assets_dir = merged.get("portal_assets_dir", "portal_assets")
        self.portal_names = dict(merged.get("portal_names") or {})

    @property
    def portal_assets_path(self) -> str:
        from .titles import ROOT

        d = self.portal_assets_dir
        return d if os.path.isabs(d) else os.path.join(ROOT, d)

    def resolve_ports(self, titles) -> dict[int, str]:
        """Gives the listener map. A configured map wins; otherwise every title's LSP ports plus 8800."""
        if self.explicit_ports:
            return dict(self.ports)
        from .titles import LSP_SERVICES

        ports = {8800: "http"}
        for title in titles:
            for offset, kind, _service in LSP_SERVICES:
                ports[title.lsp_base_port + offset] = kind
        self.ports = ports
        return ports

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
