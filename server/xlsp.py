"""Writes the XLiveLessNess XLSP service files that send a title's GameSpy traffic to this server.

XLLN uses the xlln-xlsp/*.json file whose titleId is the running game's. It redirects TCP connects only.
"""

import json
import os

from .titles import LSP_SERVICES


def service_file(title, host: str = "127.0.0.1") -> dict:
    if title.xbox_title_id is None:
        raise ValueError(f"{title.name} has no xbox_title_id configured")
    services = []
    for offset, _kind, name in LSP_SERVICES:
        port = title.lsp_base_port + offset
        services.append({"service": f"gamespy-{name}", "port": port, "endpoint": f"{host}:{port}"})
    return {"titleId": f"0x{title.xbox_title_id:08X}", "services": services}


def write_service_files(titles, out_dir: str, host: str = "127.0.0.1") -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for title in titles:
        if title.xbox_title_id is None:
            continue
        path = os.path.join(out_dir, f"{title.key}.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(service_file(title, host), f, indent=2)
            f.write("\n")
        written.append(path)
    return written
