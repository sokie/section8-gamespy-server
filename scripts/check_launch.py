#!/usr/bin/env python3
"""Starts the server the way a user does and checks that it read the config.json beside it.

    python scripts/check_launch.py dist/section8-server[.exe]   the built executable, started from another directory
    python scripts/check_launch.py                              python -m server, started in a folder with config.json

The check puts a config.json that moves every port off its default into a new folder whose name has a space.
With an executable, it copies the executable into that folder and starts it from a different directory. It
passes when GPCM, HTTP and the stats portal answer on the moved ports, the portal serves its bundled pages,
and the database and the log are created in that folder. Exits 1 otherwise, after printing the output.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from check_server import check_gpcm, check_http, check_portal  # noqa: E402

GPCM_PORT, HTTP_PORT, PORTAL_PORT = 18902, 18905, 18181
CONFIG = {
    "bind_address": "127.0.0.1",
    "ports": {str(GPCM_PORT): "gpcm", str(HTTP_PORT): "http"},
    "udp_available": False,
    "portal_port": PORTAL_PORT,
}
STARTUP_SECONDS = 90


def fetch(path: str) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORTAL_PORT}{path}", timeout=5) as r:
        return r.read().decode("utf-8", "replace")


def wait_until_up() -> bool:
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        try:
            if check_portal(f"http://127.0.0.1:{PORTAL_PORT}", 2)[0]:
                return True
        except (OSError, ValueError):
            pass
        time.sleep(1)
    return False


def main(argv) -> int:
    work = tempfile.mkdtemp(prefix="s8-launch-")
    folder = os.path.join(work, "server folder")
    elsewhere = os.path.join(work, "elsewhere")
    os.makedirs(folder)
    os.makedirs(elsewhere)
    with open(os.path.join(folder, "config.json"), "w", encoding="utf-8") as f:
        json.dump(CONFIG, f)

    env = dict(os.environ)
    if len(argv) > 1:
        exe = os.path.join(folder, os.path.basename(argv[1]))
        shutil.copy2(argv[1], exe)
        command, cwd = [exe], elsewhere
        print(f"starting {exe} from {elsewhere}")
    else:
        env["PYTHONPATH"] = ROOT
        command, cwd = [sys.executable, "-m", "server"], folder
        print(f"starting python -m server in {folder}")

    out_path = os.path.join(work, "output.txt")
    results = []
    with open(out_path, "wb") as out:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
        try:
            up = wait_until_up()
            results.append(("portal answers on the port from config.json", up, f"{PORTAL_PORT}"))
            if up:
                ok, detail = check_gpcm("127.0.0.1", GPCM_PORT, 5)
                results.append(("GPCM greets on the port from config.json", ok, detail))
                ok, detail = check_http("127.0.0.1", HTTP_PORT, 5)
                results.append(("HTTP answers on the port from config.json", ok, detail))
                page = fetch("/")
                results.append(("portal page includes its header", 'id="game-switch"' in page, ""))
                script = fetch("/static/js/portal.js")
                results.append(("portal serves its bundled scripts", "export function start" in script, ""))
        finally:
            proc.terminate()
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()
    for name in ("section8.db", "section8_gamespy.log"):
        results.append(
            (
                f"{name} is created beside config.json",
                os.path.exists(os.path.join(folder, name)) and not os.path.exists(os.path.join(elsewhere, name)),
                "",
            )
        )

    failed = 0
    for name, ok, detail in results:
        print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")
        failed += not ok
    if failed:
        with open(out_path, encoding="utf-8", errors="replace") as f:
            print("--- server output ---\n" + f.read())
    shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
