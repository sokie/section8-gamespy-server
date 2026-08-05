"""GameSpy "news" delivery - the channel Section 8 uses to unlock its post-launch game modes.

Assault and Skirmish are gated behind an unlock class (S9UnlockAssault / S9UnlockArcade). Those two are
the only game modes carrying an `Unlock` at all, and S9UnlockAssault declares no UnlockID and no
criteria, so nothing a player does locally can ever satisfy it - TimeGate flipped it on server-side for
everyone at once when the community hit the 10-million-kill milestone. The game reaches it like this:

    Sake SearchForRecords on NewsStats_v6   -> Settings_FileID + RecordId
    GET /SakeFileServer/download.aspx?fileid=<Settings_FileID>  -> the file this module serves
    TGGameInfo.IsAvailable() then sees the mode as unlocked

Without it a dedicated server hosting Assault exits during startup with "This game mode is not
available.": the check runs with no local PlayerController, so only a global unlock can satisfy it.

The payload is a sectioned text file ([Settings] / [Localization] / [MOTD], plus [CachedSettings] and
[CachedLocalization] which the game mirrors into the player's profile for offline use). A [Settings]
entry becomes a TGSNewsSettings {GameInfoFilter, SettingClass, SettingParam, NewSettingValue}, which the
game applies as a console `SET <SettingClass> <SettingParam> <NewSettingValue>`.

The same file also carries `[MOTD]`, which is what actually produces the welcome banner on the online
menu - not the motd.asp endpoint in motd.py, despite its name. That section is scanned with no comment
handling and no terminator: the first line containing the key wins, and everything right of its first
'=' becomes the banner, so a comment merely *mentioning* the key gets displayed instead.

The file is read from disk on every request rather than cached, so its syntax can be adjusted and
retested against a running backend without a restart. It is authored as UTF-8 and served as UTF-16LE.
"""
import os
import zlib

from . import log

DEFAULT_NEWS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "news", "section8_news.txt")


class NewsService:
    def __init__(self, news_path: str | None = None):
        self._path = news_path or DEFAULT_NEWS_PATH

    def payload(self) -> bytes:
        """The news file, encoded the way the game expects to receive it.

        The file is kept on disk as plain UTF-8 so it stays human-editable, but it is served as
        UTF-16LE with a BOM: UE3 authors text assets in UTF-16, and the game decodes the download with
        its own GetNewsFileAsStringArray rather than a BOM-sniffing helper. Served as ASCII the section
        headers never match, so every section silently no-ops with no error logged anywhere - which is
        indistinguishable from a syntax mistake. A BOM is correct under either decoder.
        """
        try:
            with open(self._path, "rb") as f:
                raw = f.read()
        except OSError as exc:
            log.log(f"    [news] no news file at {self._path} ({exc}); serving empty payload")
            return b""
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            return raw
        # Normalise line endings rather than trusting the checkout: with core.autocrlf a clone rewrites
        # this file to CRLF, and the trailing \r rides into every parsed value - "None\r" then fails to
        # import as a class reference, and the MOTD picks up a stray carriage return.
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        return b"\xff\xfe" + text.encode("utf-16-le")

    def version(self) -> int:
        """RecordId for the NewsStats row.

        The game only applies news when `Rows[0].RecordId != NewsVersion` (its cached copy), so a
        constant - notably the 0 a synthetic zeroed row returns - makes it skip the file entirely.
        Deriving this from the content means editing the news file re-triggers the apply by itself.
        """
        data = self.payload()
        if not data:
            return 0
        # Keep it inside a signed 32-bit int and away from 0, which reads as "no news".
        return (zlib.crc32(data) & 0x7FFFFFFF) or 1

    def file_id(self) -> int:
        """Settings_FileID handed to the game, which it echoes back as ?fileid=. We serve a single news
        file, so the id is informational - but it must be stable within a session."""
        return self.version()

    def handle_download(self, head: str):
        """Answer a SakeFileServer download. Returns (body, headers).

        The GameSpy SDK validates two response headers before it will hand the bytes to the game, and
        silently reports the read as failed without them - the download looks fine on the wire, the game
        just never parses it. Sake-File-Result is the SAKEFileResult enum, 0 = success.
        """
        request_line = head.split("\r\n", 1)[0]
        data = self.payload()
        file_id = _requested_file_id(request_line) or self.file_id()
        headers = {"Sake-File-Result": "0", "Sake-File-Id": str(file_id)}
        log.log(f"    [news] SakeFileServer download -> {len(data)} bytes "
                f"(Sake-File-Id={file_id}) ({request_line})")
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
