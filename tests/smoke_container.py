"""Checks the Docker entrypoint's config merge and its database backup.  python -m tests.smoke_container"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile

from server import container

failures = 0


def check(name, cond):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name)
    failures += not cond


data = os.path.join(tempfile.gettempdir(), "s8_container")
shutil.rmtree(data, ignore_errors=True)
os.makedirs(data)
backups = os.path.join(data, "backups")

check(
    "identity names version, commit and build",
    container.image_identity(
        {"SECTION8_IMAGE_VERSION": "0.4.0", "SECTION8_IMAGE_REVISION": "abc", "SECTION8_IMAGE_BUILD": "7-1"}
    )
    == "0.4.0 abc 7-1",
)
check("a missing build arg reads as ?", container.image_identity({}) == "? ? ?")

cfg = container.container_config(data)
check("defaults bind every interface", cfg["bind_address"] == "0.0.0.0")
check("database on the volume", cfg["db_path"] == os.path.join(data, "section8.db"))
with open(os.path.join(data, "config.json"), "w", encoding="utf-8") as f:
    json.dump({"portal_port": 9090, "portal_names": {"T8iP": "Leon"}}, f)
cfg = container.container_config(data)
check("volume config.json merges over the defaults", cfg["portal_port"] == 9090 and cfg["bind_address"] == "0.0.0.0")
os.remove(os.path.join(data, "config.json"))

db = cfg["db_path"]
check("first start without a database: no backup", container.backup_if_image_changed(db, data, "0.3.0 a 1") is None)

conn = sqlite3.connect(db)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("CREATE TABLE t(v)")
conn.execute("INSERT INTO t VALUES ('career')")
conn.commit()  # left open, so the row is still in the WAL file when the backup runs

check("same image again: no backup", container.backup_if_image_changed(db, data, "0.3.0 a 1") is None)
first = container.backup_if_image_changed(db, data, "0.4.0 b 2", now=1_700_000_000)
check("new image: backup made", first is not None and os.path.exists(first))
check("backup is named after the image it replaces", first is not None and first.endswith("-0.3.0.db"))
if first:
    copy = sqlite3.connect(first)
    check("backup holds the committed WAL row", copy.execute("SELECT v FROM t").fetchall() == [("career",)])
    copy.close()
check("no partial file left", not any(n.endswith(".partial") for n in os.listdir(backups)))
check("restart of the new image: no backup", container.backup_if_image_changed(db, data, "0.4.0 b 2") is None)
conn.close()

for i in range(5):
    container.backup_if_image_changed(db, data, f"0.5.{i} c {i}", keep=3, now=1_700_000_100 + i)
kept = sorted(os.listdir(backups))
check("only the newest backups are kept", len(kept) == 3 and kept[-1].endswith("-0.5.3.db"))
mode = sqlite3.connect(os.path.join(backups, kept[-1]))
check("a backup is one rollback-mode file", mode.execute("PRAGMA journal_mode").fetchone() == ("delete",))
mode.close()
check("opening a backup leaves no -wal or -shm file", sorted(os.listdir(backups)) == kept)

print("ALL CONTAINER TESTS PASSED" if not failures else f"{failures} CONTAINER TEST(S) FAILED")
sys.exit(1 if failures else 0)
