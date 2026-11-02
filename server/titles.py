"""Per-title profiles, so one backend serves Section 8 (2009) and Section 8: Prejudice at once.

Each title has its GameSpy identifiers, GFWL title id, LSP base port, Sake table namespace and stat catalogue.
"""

from __future__ import annotations

import json
import os
import sys
import threading

from . import log, stats

# (offset from the LSP base port, listener kind, GameSpy service). The game sends each GameSpy host to
# its title's base port plus the offset. Hosts outside this table never reach the server.
LSP_SERVICES = (
    (1, "gpcm", "gpsp"),
    (2, "gpcm", "gpcm"),
    (3, "http", "auth"),
    (4, "http", "competition"),
    (5, "http", "sake"),
    (6, "http", "motd"),
)


def _root_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = _root_dir()
# The most sessions and unknown gameids kept in memory. Both come from the network.
MAX_TRACKED = 4096

BUILTIN = {
    "prejudice": {
        "name": "Section 8: Prejudice",
        "gameids": [3160],
        "secret_keys": ["OGmgyP"],
        "xbox_title_id": 0x544707D4,
        "lsp_base_port": 8900,
        "namespace": "",
        "player_stats_table": "PlayerStats_v6",
        "catalog": "builtin",
        "news_path": os.path.join("news", "section8_news.txt"),
    },
    "section8": {
        "name": "Section 8",
        "gameids": [2566],
        "secret_keys": ["2UMehS"],
        "xbox_title_id": 0x535007E3,
        "lsp_base_port": 8900,
        "namespace": "s8:",
        # None: learn it from the first PlayerStats* table the game reads.
        "player_stats_table": None,
        "catalog": os.path.join("catalog", "section8.json"),
        "news_path": os.path.join("news", "section8_classic_news.txt"),
        # Prejudice reads the Section 8 level from this column to unlock its veteran items.
        "level_link": {"title": "prejudice", "table": "S8Level_v6", "column": "Ranked_Level"},
    },
}


class Title:
    def __init__(self, key: str, data: dict):
        self.key = key
        self.name = data.get("name", key)
        self.gameids = tuple(int(g) for g in data.get("gameids", []) if g is not None and str(g).strip() != "")
        self.secret_keys = tuple(s for s in data.get("secret_keys", []) if s)
        tid = data.get("xbox_title_id")
        self.xbox_title_id = int(tid, 0) if isinstance(tid, str) else tid
        self.lsp_base_port = int(data.get("lsp_base_port", 8900))
        self.namespace = data.get("namespace", "")
        self.configured_player_table = data.get("player_stats_table")
        self.catalog_path = data.get("catalog")
        news = data.get("news_path")
        self.news_path = news if not news or os.path.isabs(news) else os.path.join(ROOT, news)
        self.level_link = data.get("level_link")
        self.catalog = self._load_catalog()

    def _load_catalog(self) -> stats.Catalog:
        path = self.catalog_path
        if path == "builtin":
            return stats.PREJUDICE
        if path:
            full = path if os.path.isabs(path) else os.path.join(ROOT, path)
            if os.path.exists(full):
                try:
                    cat = stats.Catalog.from_json(full)
                    log.log(f"[{self.key}] stat catalogue {full}: {len(cat.by_key)} report keys")
                    return cat
                except Exception as e:  # A broken catalogue must not stop the server.
                    log.log(f"[{self.key}] stat catalogue {full} unreadable ({e}); capturing raw keys")
            else:
                log.log(f"[{self.key}] no stat catalogue at {full}; ranked reports are kept as raw keys")
        return stats.Catalog.empty(self.key)

    def table(self, wire_table: str) -> str:
        """Gives the store's table id for a table name the client sent."""
        # A ':' in the wire name could reach another title's namespace.
        return f"{self.namespace}{wire_table.replace(':', '_')}" if wire_table else wire_table

    def wire_table(self, stored: str) -> str:
        if self.namespace and stored.startswith(self.namespace):
            return stored[len(self.namespace) :]
        return stored

    def matches(self, gameid=None, secret=None) -> bool:
        return (gameid is not None and gameid in self.gameids) or bool(secret and secret in self.secret_keys)

    def __repr__(self):
        return f"<Title {self.key}>"


class Titles:
    """The title registry. ``resolve`` maps the identifiers of one request to a Title."""

    LEGACY = "prejudice"

    def __init__(self, overrides: dict | None = None, default_unknown: str = "section8", store=None):
        merged: dict = {k: dict(v) for k, v in BUILTIN.items()}
        for key, data in (overrides or {}).items():
            merged.setdefault(key, {}).update(data or {})
        self._titles = {k: Title(k, v) for k, v in merged.items()}
        self._default_unknown = default_unknown if default_unknown in self._titles else self.LEGACY
        self._store = store
        self._lock = threading.Lock()
        self._learned: dict[str, str] = {}
        self._csid_title: dict[str, str] = {}
        # Unknown gameids already logged, so each one is logged once.
        self._seen_ids: set[int] = set()

    def __iter__(self):
        return iter(self._titles.values())

    def get(self, key: str) -> Title:
        return self._titles[key]

    @property
    def legacy(self) -> Title:
        return self._titles[self.LEGACY]

    def resolve(self, gameid=None, secret=None) -> Title:
        """Finds the title of a request. A request with no identifier is Prejudice. An identifier that no
        title claims goes to the default for unknown titles."""
        try:
            gid = int(gameid) if gameid not in (None, "") else None
        except (TypeError, ValueError):
            gid = None
        for t in self._titles.values():
            if t.matches(gid, secret):
                return t
        if gid is None and not secret:
            return self.legacy
        title = self._titles[self._default_unknown]
        if gid is not None:
            with self._lock:
                first = gid not in self._seen_ids and len(self._seen_ids) < MAX_TRACKED
                if first:
                    self._seen_ids.add(gid)
            if first:
                log.log(
                    f"[titles] first request with gameid={gid} secretKey={secret!r}: "
                    f"serving it as {title.name}. Add the gameid to titles.{title.key}.gameids in config.json "
                    f"to make this explicit."
                )
        return title

    def player_table(self, title: Title) -> str:
        """Gives the stored table id, with its namespace, of a title's player stats table."""
        if title.configured_player_table:
            return title.table(title.configured_player_table)
        if title.catalog.player_table:
            return title.table(title.catalog.player_table)
        with self._lock:
            learned = self._learned.get(title.key)
        if learned is None and self._store is not None:
            learned = self._store.get_meta(f"player_table:{title.key}")
            if learned:
                with self._lock:
                    self._learned[title.key] = learned
        return title.table(learned or "PlayerStats")

    def learn_player_table(self, title: Title, wire_table: str, fields) -> None:
        """Records the first PlayerStats* table a title reads with Ranked_* columns as the table its ranked
        reports fold into. Does nothing when the config or the catalogue names the table."""
        if title.configured_player_table or title.catalog.player_table:
            return
        if not wire_table.lower().startswith("playerstats"):
            return
        if not any(f.startswith("Ranked_") or f in title.catalog.by_column for f in fields):
            return
        with self._lock:
            known = self._learned.get(title.key)
            if known == wire_table:
                return
            self._learned[title.key] = wire_table
        if self._store is not None:
            self._store.set_meta(f"player_table:{title.key}", wire_table)
        log.log(f"[{title.key}] player stats table is {wire_table!r} (learnt from the game's own read)")

    def remember_session(self, csid: str, title: Title) -> None:
        if csid:
            with self._lock:
                self._csid_title[csid] = title.key
                if len(self._csid_title) > MAX_TRACKED:
                    del self._csid_title[next(iter(self._csid_title))]

    def title_for_session(self, csid: str):
        with self._lock:
            key = self._csid_title.get(csid or "")
        return self._titles.get(key) if key else None


def load_titles_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
