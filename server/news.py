"""GameSpy news delivery, the channel that unlocks Assault and Skirmish and sets the in-game MOTD.

    Sake SearchForRecords on NewsStats_v6           -> News_Settings_FileID + RecordId
    GET /SakeFileServer/download.aspx?fileid=<id>   -> the file this module serves

The file is read from disk on every request, so its syntax can be retested against a running backend
with no restart. Format, gating and failure modes: docs/game_modes_and_news.md.
"""

import os
import sys
import zlib

from . import log


def _default_news_path() -> str:
    """A frozen build reads the news file next to the executable, since the file is meant to be
    edited without a rebuild. It is deliberately not bundled into the executable."""
    if getattr(sys, "frozen", False):
        root = os.path.dirname(sys.executable)
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "news", "section8_news.txt")


DEFAULT_NEWS_PATH = _default_news_path()


class NewsService:
    def __init__(self, news_path: str | None = None):
        self._path = news_path or DEFAULT_NEWS_PATH

    def payload(self) -> bytes:
        """The news file as UTF-16LE with a BOM, which is the only encoding the game decodes.

        It is kept on disk as UTF-8 so it stays editable. Served as ASCII, every section header fails
        to match and the whole file no-ops with nothing logged anywhere.
        """
        try:
            with open(self._path, "rb") as f:
                raw = f.read()
        except OSError as exc:
            log.log(f"    [news] no news file at {self._path} ({exc}); serving empty payload")
            return b""
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            return raw
        # core.autocrlf rewrites this file to CRLF on clone, and the trailing \r then rides into every
        # parsed value: "None\r" does not resolve as a class, and the MOTD grows a stray return.
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        return b"\xff\xfe" + text.encode("utf-16-le")

    def version(self) -> int:
        """RecordId for the NewsStats row. The game applies news only when this differs from its cached
        NewsVersion, so deriving it from the content makes an edit re-trigger the apply by itself."""
        data = self.payload()
        if not data:
            return 0
        # Signed 32-bit, and never 0, which the game reads as "no news".
        return (zlib.crc32(data) & 0x7FFFFFFF) or 1

    def file_id(self) -> int:
        """Settings_FileID handed to the game, which echoes it back as ?fileid=. One news file is
        served, so any id works as long as it is stable within a session."""
        return self.version()

    def handle_download(self, head: str):
        """Answer a SakeFileServer download. Returns (body, headers).

        Without both headers the GameSpy SDK reports the read as failed and never parses the body,
        while the download still looks correct on the wire. Sake-File-Result 0 is success.
        """
        request_line = head.split("\r\n", 1)[0]
        data = self.payload()
        file_id = _requested_file_id(request_line) or self.file_id()
        headers = {"Sake-File-Result": "0", "Sake-File-Id": str(file_id)}
        log.log(f"    [news] SakeFileServer download -> {len(data)} bytes (Sake-File-Id={file_id}) ({request_line})")
        return data, headers


def _requested_file_id(request_line: str) -> int | None:
    """Echo back the fileid the game asked for, so its own bookkeeping matches."""
    for part in request_line.partition("?")[2].partition(" ")[0].split("&"):
        key, _, value = part.partition("=")
        if key.lower() == "fileid":
            try:
                return int(value)
            except ValueError:
                return None
    return None
