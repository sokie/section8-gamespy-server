#!/usr/bin/env python3
"""Checks a running server from the outside, the way the game reaches it.

    python scripts/check_server.py HOST [--portal URL] [--base 8900] [--no-udp]

- GPSP and GPCM (base+1, base+2) must send the \\lc\\1\\ greeting.
- Auth, Competition, Sake and MOTD (base+3 to base+6) must answer GET /motd/motd.asp with HTTP 200.
  The HTTP listeners route by path, so this read-only request works on all four.
- 27900/udp must answer an availability probe with "available".
- --portal URL must answer /api/summary with JSON.

Each check writes one line to the server log. Exits 1 when a check fails.
"""

import argparse
import json
import socket
import sys
import urllib.request

AVAILABLE = b"\xfe\xfd\x09\x00\x00\x00\x00"


def _recv_some(sock, limit=4096) -> bytes:
    buf = b""
    try:
        while len(buf) < limit:
            chunk = sock.recv(limit)
            if not chunk:
                break
            buf += chunk
    except TimeoutError:
        pass
    return buf


def check_gpcm(host: str, port: int, timeout: float) -> tuple[bool, str]:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        greeting = s.recv(512)
    ok = greeting.startswith(b"\\lc\\1\\")
    return ok, greeting[:40].decode("latin-1", "replace")


def check_http(host: str, port: int, timeout: float) -> tuple[bool, str]:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(b"GET /motd/motd.asp HTTP/1.0\r\nHost: check\r\n\r\n")
        resp = _recv_some(s)
    status = resp.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    return status.startswith("HTTP/1.0 200") and b"[MOTD]" in resp, status


def check_udp(host: str, port: int, timeout: float) -> tuple[bool, str]:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(b"\x09\x00\x00\x00\x00tg09pc\x00", (host, port))
        try:
            data, _ = s.recvfrom(64)
        except TimeoutError:
            return False, "no answer"
    return data == AVAILABLE, data.hex(" ")


def check_portal(url: str, timeout: float) -> tuple[bool, str]:
    with urllib.request.urlopen(url.rstrip("/") + "/api/summary", timeout=timeout) as r:
        body = json.loads(r.read().decode("utf-8"))
    return r.status == 200 and isinstance(body, dict), f"HTTP {r.status}, {len(body)} keys"


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host")
    ap.add_argument("--base", type=int, default=8900, help="LSP base port (default 8900)")
    ap.add_argument("--udp-port", type=int, default=27900)
    ap.add_argument("--no-udp", action="store_true", help="skip the availability check")
    ap.add_argument("--portal", help="portal base URL, e.g. https://stats.example.com")
    ap.add_argument("--timeout", type=float, default=5.0)
    args = ap.parse_args(argv)

    checks = [(f"gpcm {args.base + n}", check_gpcm, args.base + n) for n in (1, 2)]
    checks += [(f"http {args.base + n}", check_http, args.base + n) for n in (3, 4, 5, 6)]
    if not args.no_udp:
        checks.append((f"udp  {args.udp_port}", check_udp, args.udp_port))
    failed = 0
    for name, fn, port in checks:
        try:
            ok, detail = fn(args.host, port, args.timeout)
        except OSError as e:
            ok, detail = False, repr(e)
        print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")
        failed += not ok
    if args.portal:
        try:
            ok, detail = check_portal(args.portal, args.timeout)
        except (OSError, ValueError) as e:
            ok, detail = False, repr(e)
        print(f"{'ok  ' if ok else 'FAIL'} portal {args.portal}  {detail}")
        failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
