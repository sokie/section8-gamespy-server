"""Entrypoint: `python -m server [config.json]`."""
import sys

from . import log
from .authservice import AuthService
from .competition import CompetitionService
from .config import Config
from .gpcm import GpcmService
from .motd import MotdService
from .persistence import Store
from .sake import SakeService
from .transport import HttpRouter, Server


def main(argv):
    config = Config.load(argv[1] if len(argv) > 1 else None)
    log.configure(config.log_path)
    log.log("=== Section 8 GameSpy backend starting ===")
    log.log(f"bind={config.bind_address} db={config.db_path} ports={sorted(config.ports)}")

    store = Store(config.db_path)
    gpcm = GpcmService(store, config.server_challenge)
    router = HttpRouter(AuthService(store), CompetitionService(store), SakeService(store),
                        MotdService(config.motd_message))
    Server(config, gpcm, router).serve_forever()


if __name__ == "__main__":
    main(sys.argv)
