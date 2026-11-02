"""Images for the stats portal: game art, rank insignia, badge, feat, gear and unlock icons.

No game art ships with the repo, and a missing image is drawn. docs/stats_portal.md lists the file names.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from urllib.parse import quote

from . import statdefs

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
RESCAN_SECONDS = 10


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def _unlock(item) -> dict | None:
    """Gives a checked copy of one unlocks.json entry, or None when a value has the wrong type."""
    if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
        return None
    out = {"name": item["name"]}
    try:
        for k in ("level", "section8_level"):
            if item.get(k) is not None:
                out[k] = int(item[k])
    except (TypeError, ValueError):
        return None
    for k in ("category", "description", "image"):
        if item.get(k) is not None:
            if not isinstance(item[k], str):
                return None
            out[k] = item[k]
    return out


class Assets:
    def __init__(self, root: str):
        self.root = os.path.realpath(root)
        self._lock = threading.Lock()
        self._index: dict[str, str] = {}  # "dir/stem" in lowercase -> path relative to the root
        self._mapping: dict[str, str] = {}
        self._unlocks: list[dict] = []
        self._scanned = 0.0

    def _scan(self) -> None:
        index: dict[str, str] = {}
        mapping: dict[str, str] = {}
        unlocks: list[dict] = []
        if os.path.isdir(self.root):
            for dirpath, _dirs, files in os.walk(self.root):
                for name in files:
                    stem, ext = os.path.splitext(name)
                    if ext.lower() not in IMAGE_TYPES:
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, name), self.root).replace(os.sep, "/")
                    key = (os.path.dirname(rel) + "/" + stem).lstrip("/").lower()
                    index.setdefault(key, rel)
            mapping = {k.lower(): v for k, v in self._json("mapping.json", {}).items() if isinstance(v, str)}
            data = self._json("unlocks.json", {})
            items = data.get("unlocks", [])
            unlocks = [u for u in map(_unlock, items if isinstance(items, list) else []) if u]
        with self._lock:
            self._index, self._mapping, self._unlocks = index, mapping, unlocks
            self._scanned = time.time()

    def _json(self, name: str, default):
        path = os.path.join(self.root, name)
        if not os.path.exists(path):
            return default
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, type(default)) else default
        except (OSError, ValueError):
            return default

    def _fresh(self) -> None:
        if time.time() - self._scanned > RESCAN_SECONDS:
            self._scan()

    def find(self, *keys: str) -> str | None:
        """Gives the URL of the first key that has an image, else None."""
        self._fresh()
        with self._lock:
            for key in keys:
                k = key.lower()
                rel = self._mapping.get(k)
                if rel and self._safe(rel):
                    return "/assets/" + quote(rel.replace(os.sep, "/"))
                rel = self._index.get(k)
                if rel:
                    return "/assets/" + quote(rel)
        return None

    def unlocks(self) -> list[dict]:
        self._fresh()
        with self._lock:
            return list(self._unlocks)

    def _safe(self, rel: str) -> str | None:
        full = os.path.realpath(os.path.join(self.root, rel))
        if not full.startswith(self.root + os.sep):
            return None
        if os.path.splitext(full)[1].lower() not in IMAGE_TYPES or not os.path.isfile(full):
            return None
        return full

    def read(self, rel: str):
        """Gives (bytes, content type) of an image inside the folder, or None. Never reads outside the folder."""
        full = self._safe(rel)
        if not full:
            return None
        with open(full, "rb") as f:
            return f.read(), IMAGE_TYPES[os.path.splitext(full)[1].lower()]

    def game(self, key: str):
        return self.find(f"games/{key}", f"games/{key}-banner")

    def game_logo(self, key: str):
        return self.find(f"games/{key}-logo")

    # Section 8's own images are under section8/. A rank icon falls back to the shared ranks/.
    @staticmethod
    def _p(game: str) -> str:
        return "" if game in ("", "prejudice") else f"{game}/"

    def rank(self, level: int, game: str = "prejudice"):
        ordinal, name = rank_ordinal(level)
        keys = [f"levels/{level}", f"ranks/{ordinal}", f"ranks/{slug(name)}"]
        p = self._p(game)
        return self.find(*([p + k for k in keys] if p else []), *keys)

    def badge(self, i: int, name: str, game: str = "prejudice"):
        p = self._p(game)
        return self.find(f"{p}badges/b{i}", f"{p}badges/{i}", f"{p}badges/{slug(name)}")

    def feat(self, i: int, name: str, game: str = "prejudice"):
        p = self._p(game)
        return self.find(f"{p}feats/f{i}", f"{p}feats/{i}", f"{p}feats/{slug(name)}")

    def gear(self, i: int, weapon: str, mod: str, game: str = "prejudice"):
        p = self._p(game)
        keys = [f"{p}gear/g{i}", f"{p}gear/{i}"]
        if mod:
            keys.append(f"{p}gear/{slug(weapon)}-{slug(mod)}")
        return self.find(*keys, f"{p}gear/{slug(weapon)}")

    def unlock(self, item: dict):
        keys = [item["image"]] if item.get("image") else []
        found = None
        if keys:
            self._fresh()
            with self._lock:
                if self._safe(keys[0]):
                    found = "/assets/" + quote(keys[0].replace(os.sep, "/"))
        return found or self.find(f"unlocks/{slug(item['name'])}")

    def checklist(self, games) -> list[tuple[str, str | None]]:
        """Gives (what, url or None) for every image the portal can use."""
        out = [(f"games/{g.key}  ({g.name} banner)", self.game(g.key)) for g in games]
        out += [(f"games/{g.key}-logo  (optional)", self.game_logo(g.key)) for g in games]
        for n, (level, name) in enumerate(statdefs.RANKS, 1):
            out.append((f"ranks/{n}  or  ranks/{slug(name)}  ({name}, level {level}+)", self.rank(level)))
        for g in games:
            p, cat = self._p(g.key), g.catalog
            out += [
                (f"{p}badges/B{i}  or  {p}badges/{slug(b[0])}", self.badge(i, b[0], g.key))
                for i, b in sorted(cat.badges.items())
            ]
            out += [
                (f"{p}feats/F{i}  or  {p}feats/{slug(f[0])}", self.feat(i, f[0], g.key))
                for i, f in sorted(cat.feats.items())
            ]
            for i, item in sorted(cat.gear.items()):
                w, m = (list(item) + ["", ""])[:2]
                names = (
                    f"{p}gear/G{i}  or  " + (f"{p}gear/{slug(w)}-{slug(m)}  or  " if m else "") + f"{p}gear/{slug(w)}"
                )
                out.append((names, self.gear(i, w, m, g.key)))
        out += [(f"unlocks/{slug(u['name'])}", self.unlock(u)) for u in self.unlocks()]
        return out


def rank_ordinal(level: int) -> tuple[int, str]:
    ordinal, name = 1, statdefs.RANKS[0][1]
    for n, (needed, rank) in enumerate(statdefs.RANKS, 1):
        if needed > (level or 0):
            break
        ordinal, name = n, rank
    return ordinal, name


PALETTE = ("#f5b229", "#4fb3e8", "#5fd38d", "#e8735f", "#b48cf2", "#e5c35a", "#53c7c0", "#e889b8")


def colour(text: str) -> str:
    return PALETTE[int(hashlib.md5(text.encode()).hexdigest(), 16) % len(PALETTE)]


def initials(name: str) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w]
    return ("".join(w[0] for w in words[:2]) or "?").upper()


def _svg(body: str, size: int) -> str:
    # The page always shows the name next to the icon, so a screen reader skips the icon.
    return (
        f'<svg class="ico" width="{size}" height="{size}" viewBox="0 0 64 64" aria-hidden="true" '
        f'xmlns="http://www.w3.org/2000/svg">{body}</svg>'
    )


def emblem(kind: str, name: str, category: str, size: int = 44) -> str:
    """Draws a stand-in icon: a hexagon for a badge, a shield for a feat, a rounded square for an unlock."""
    c = colour(category or kind)
    text = initials(name)
    if kind == "badge":
        shape = '<path d="M32 3 57 17.5v29L32 61 7 46.5v-29z" fill="#18212c" stroke="{c}" stroke-width="3"/>'
    elif kind == "feat":
        shape = '<path d="M32 4 56 12v18c0 16-10 26-24 31C18 56 8 46 8 30V12z" fill="#18212c" stroke="{c}" stroke-width="3"/>'
    else:
        shape = '<rect x="6" y="6" width="52" height="52" rx="10" fill="#18212c" stroke="{c}" stroke-width="3"/>'
    body = shape.format(c=c) + (
        f'<text x="32" y="{39 if kind != "feat" else 37}" text-anchor="middle" font-family="system-ui,sans-serif" '
        f'font-size="19" font-weight="800" fill="{c}">{text}</text>'
    )
    return _svg(body, size)


def insignia(level: int, size: int = 44) -> str:
    """Draws a stand-in insignia for the rank tier: chevrons and rockers, bars, leaves or stars."""
    n, name = rank_ordinal(level)
    gold, silver, line = "#f5b229", "#cfd8dc", "#0b0f14"
    parts = ['<rect x="2" y="2" width="60" height="60" rx="12" fill="#18212c" stroke="#243142" stroke-width="2"/>']

    def chevrons(count, rockers, mark=None):
        cs, rs = (6, 4.5) if mark else (7, 5) if rockers >= 2 else (8, 6)
        gap = 12 if mark else 6
        total = (count - 1) * cs + 10 + ((gap + (rockers - 1) * rs + 7) if rockers else 0)
        top = (64 - total) / 2
        for i in range(count):
            y = top + i * cs
            parts.append(
                f'<path d="M15 {y + 10:.1f} 32 {y:.1f} 49 {y + 10:.1f}" fill="none" stroke="{gold}" stroke-width="4.5" stroke-linejoin="round"/>'
            )
        base = top + (count - 1) * cs + 10 + gap
        for i in range(rockers):
            y = base + i * rs
            parts.append(
                f'<path d="M15 {y:.1f} Q32 {y + 7:.1f} 49 {y:.1f}" fill="none" stroke="{gold}" stroke-width="3.5"/>'
            )
        if mark:
            parts.append(mark(32, base - gap / 2 + 1))

    def star(cx, cy, r, fill):
        pts = []
        for k in range(10):
            rr = r if k % 2 == 0 else r * 0.45
            a = math.pi / 2 + k * math.pi / 5
            pts.append(f"{cx + rr * math.cos(a):.1f},{cy - rr * math.sin(a):.1f}")
        return f'<polygon points="{" ".join(pts)}" fill="{fill}"/>'

    if n == 1:
        parts.append(f'<circle cx="32" cy="32" r="9" fill="none" stroke="{gold}" stroke-width="4"/>')
    elif n <= 8:
        table = {2: (1, 0), 3: (1, 1), 4: (2, 0), 5: (3, 0), 6: (3, 1), 7: (3, 2), 8: (3, 3)}
        chevrons(*table[n])
    elif n <= 11:
        mark = {
            9: lambda x, y: (
                f'<rect x="{x - 4}" y="{y - 4:.1f}" width="8" height="8" transform="rotate(45 {x} {y:.1f})" fill="{silver}"/>'
            ),
            10: lambda x, y: star(x, y, 6, silver),
            11: lambda x, y: (
                star(x, y, 5, silver)
                + f'<circle cx="{x}" cy="{y:.1f}" r="7.5" fill="none" stroke="{silver}" stroke-width="1.5"/>'
            ),
        }[n]
        chevrons(3, 3, mark)
    elif n <= 14:
        bars = 2 if n == 14 else 1
        fill = gold if n == 12 else silver
        for i in range(bars):
            x = 32 - (bars * 10 + (bars - 1) * 6) / 2 + i * 16
            parts.append(f'<rect x="{x:.0f}" y="16" width="10" height="32" rx="2" fill="{fill}" stroke="{line}"/>')
    elif n <= 17:
        fill = gold if n == 15 else silver
        leaf = f'<path d="M32 12C44 22 46 36 32 52 18 36 20 22 32 12z" fill="{fill}"/><path d="M32 18v34" stroke="{line}" stroke-width="2"/>'
        if n == 17:
            leaf = (
                f'<path d="M10 28 32 20 54 28 44 32 32 50 20 32z" fill="{silver}"/>'
                f'<circle cx="32" cy="27" r="4" fill="{line}"/>'
            )
        parts.append(leaf)
    else:
        count = n - 17
        r = {1: 13, 2: 11, 3: 8.5, 4: 7}[count]
        for i in range(count):
            cx = 32 - (count - 1) * (r * 1.9) / 2 + i * r * 1.9
            parts.append(star(cx, 32, r, silver))
    return _svg("".join(parts), size)
