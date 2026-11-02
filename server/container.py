"""Docker entrypoint: `python -m server.container`. `--health` runs the image's health check instead.

Before the server starts it
  * merges /data/config.json, if the volume has one, over the container defaults,
  * backs up the database when the image differs from the image that ran last. The server migrates the
    database on start, so an automatic update has to be reversible,
  * runs from the data directory, so the match reports that CompetitionService saves to reports/ persist.
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import urllib.request

DATA_DIR = "/data"
KEEP_BACKUPS = 10
# The merged config. `docker exec` tools take it too: python -m server.portal --config <this> name ...
CONFIG_PATH = os.path.join(tempfile.gettempdir(), "section8-config.json")
_IDENTITY_KEYS = ("SECTION8_IMAGE_VERSION", "SECTION8_IMAGE_REVISION", "SECTION8_IMAGE_BUILD")


def image_identity(env=None) -> str:
    """Version, commit and CI build of the running image, from the build args the Dockerfile stores."""
    env = os.environ if env is None else env
    return " ".join(env.get(k) or "?" for k in _IDENTITY_KEYS)


def container_config(data_dir: str) -> dict:
    cfg = {
        "bind_address": "0.0.0.0",
        "db_path": os.path.join(data_dir, "section8.db"),
        # stdout already holds every line, and Docker rotates it. The file would grow for the whole run.
        "log_path": os.devnull,
    }
    override = os.path.join(data_dir, "config.json")
    if os.path.exists(override):
        with open(override, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def prune_backups(backup_dir: str, keep: int) -> list[str]:
    """Deletes all but the newest `keep` backups. The names sort by time. Returns the deleted paths."""
    names = sorted(n for n in os.listdir(backup_dir) if n.startswith("section8-") and n.endswith(".db"))
    doomed = [os.path.join(backup_dir, n) for n in names[: max(len(names) - keep, 0)]]
    for path in doomed:
        os.remove(path)
    return doomed


def backup_if_image_changed(db_path: str, data_dir: str, identity: str, keep: int = KEEP_BACKUPS, now=None):
    """Copies the database to <data_dir>/backups when `identity` is not the image that ran last.

    Returns the backup path, or None when the image is the same or there is no database yet. Raises when
    the copy fails, so the new image does not migrate a database that has no copy.
    """
    marker = os.path.join(data_dir, ".image")
    previous = None
    if os.path.exists(marker):
        with open(marker, encoding="utf-8") as f:
            previous = f.read().strip()
    backup = None
    if previous != identity and os.path.exists(db_path):
        backup_dir = os.path.join(data_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now))
        label = re.sub(r"[^A-Za-z0-9._-]", "_", (previous or "unknown").split(" ")[0])
        backup = os.path.join(backup_dir, f"section8-{stamp}-{label}.db")
        partial = backup + ".partial"
        src = sqlite3.connect(db_path)
        try:
            # VACUUM INTO reads the committed WAL pages too and writes one rollback-mode file, so the copy
            # is complete on its own and never grows -wal or -shm files.
            src.execute("VACUUM INTO ?", (partial,))
        finally:
            src.close()
        os.replace(partial, backup)
        prune_backups(backup_dir, keep)
    with open(marker + ".tmp", "w", encoding="utf-8") as f:
        f.write(identity + "\n")
    os.replace(marker + ".tmp", marker)
    return backup


def health(config_path: str = CONFIG_PATH) -> int:
    """0 when the portal answers. With the portal off, a running process is enough."""
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except OSError:
        return 1
    if not cfg.get("portal_enabled", True):
        return 0
    url = f"http://127.0.0.1:{int(cfg.get('portal_port', 8080))}/api/summary"
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            return 0 if r.status == 200 else 1
    except OSError:
        return 1


def main(argv) -> int:
    if "--health" in argv:
        return health()
    data_dir = os.environ.get("SECTION8_DATA_DIR", DATA_DIR)
    identity = image_identity()
    print(f"section8-gamespy-server image {identity}", flush=True)
    cfg = container_config(data_dir)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    keep = int(os.environ.get("SECTION8_KEEP_BACKUPS", KEEP_BACKUPS))
    backup = backup_if_image_changed(cfg["db_path"], data_dir, identity, keep)
    if backup:
        print(f"new image: database backed up to {backup}", flush=True)
    os.chdir(data_dir)
    from .__main__ import main as server_main

    try:
        server_main(["server", CONFIG_PATH])
    except KeyboardInterrupt:
        # The image's STOPSIGNAL is SIGINT, so `docker stop` ends here, and the exit code is 0.
        print("stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
