"""Entrypoint: `python -m server [config.json]`.

With no path given, the server reads config.json from the working directory when it is there. The
executable runs from its own folder (run.py), so that is the config.json next to it.

`python -m server --write-xlsp DIR [config.json]` writes the XLLN service files into DIR instead.
"""

import os
import sys

from . import log, portal
from .authservice import AuthService
from .available import AvailabilityResponder
from .competition import CompetitionService
from .config import Config
from .gpcm import GpcmService
from .motd import MotdService
from .news import NewsService
from .persistence import Store
from .portal_data import games_for
from .sake import SakeService
from .titles import Titles
from .transport import HttpRouter, Server
from .xlsp import write_service_files


def build(config):
    """Creates the services and connects them. Returns (titles, gpcm, router, store)."""
    store = Store(config.db_path)
    titles = Titles(config.titles, config.default_unknown_title, store=store)
    news = {t.key: NewsService(t.news_path) for t in titles}
    gpcm = GpcmService(store, config.server_challenge)
    router = HttpRouter(
        AuthService(store),
        CompetitionService(store, titles=titles),
        SakeService(store, news, titles=titles),
        MotdService(config.motd_message),
        news,
        titles=titles,
    )
    return titles, gpcm, router, store


DEFAULT_CONFIG = "config.json"


def load_config(args):
    """Gives (config, the path it was read from, or None for the built-in defaults)."""
    path = args[0] if args else DEFAULT_CONFIG
    found = path if os.path.exists(path) else None
    return Config.load(found), found


def main(argv):
    args = list(argv[1:])
    xlsp_dir = None
    if "--write-xlsp" in args:
        i = args.index("--write-xlsp")
        xlsp_dir = args[i + 1] if i + 1 < len(args) else "xlln-xlsp"
        del args[i : i + 2]
    config, config_path = load_config(args)

    if xlsp_dir is not None:
        titles = Titles(config.titles, config.default_unknown_title)
        for path in write_service_files(titles, xlsp_dir, config.xlsp_endpoint_host):
            print(f"wrote {path}")
        return

    log.configure(config.log_path)
    log.log("=== Section 8 GameSpy backend starting ===")
    if config_path:
        log.log(f"config {os.path.abspath(config_path)}")
    elif args:
        log.log(f"config {args[0]} not found; using the defaults")
    else:
        log.log(f"no {DEFAULT_CONFIG} in {os.getcwd()}; using the defaults")
    titles, gpcm, router, _store = build(config)
    config.resolve_ports(titles)
    for t in titles:
        tid = f"0x{t.xbox_title_id:08X}" if t.xbox_title_id is not None else "-"
        log.log(
            f"title {t.key}: {t.name} | GFWL title id {tid} | LSP base {t.lsp_base_port} | "
            f"Sake namespace {t.namespace or '(none)'} | catalogue {len(t.catalog.by_key)} keys"
        )
    log.log(f"bind={config.bind_address} db={config.db_path} ports={sorted(config.ports)}")
    if config.udp_available:
        AvailabilityResponder(config.bind_address, config.udp_available_port).start()
    if config.portal_enabled:
        portal.start(
            config.db_path,
            config.bind_address,
            config.portal_port,
            games_for(titles),
            config.portal_assets_path,
            config.portal_names,
        )
    Server(config, gpcm, router).serve_forever()


if __name__ == "__main__":
    main(sys.argv)
