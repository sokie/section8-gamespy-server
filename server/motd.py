"""GameSpy MOTD/vercheck endpoints - answered so the requests do not fail.

The game GETs http://motd.gamespy.com/motd/{motd,vercheck}.asp?userid=..&gamename=.. through the GameSpy
ghttp layer - a plain HTTP GET to a full URL, unlike the SG-tunnelled SOAP services. Answering them is
purely cosmetic and non-blocking: it does NOT gate sign-in (that is the separate Sake NewsStats read).

NOTE: this is *not* the source of the welcome banner on the online menu, despite the name. That comes
from the `[MOTD]` section of the news file (see server/news.py). Proven by controlled test: changing the
text served here to a marker string left the banner unchanged, while the banner's content was traceable
character-for-character to a line in the news file. Point banner edits at news/section8_news.txt.

The reply keeps the GameSpy INI shape (`[MOTD]` header plus a `MOTD_INT=` line) because that is what the
endpoint is documented to return, and an empty vercheck body reads as "no update required".
"""
from . import log


class MotdService:
    def __init__(self, message: str):
        self._message = message or ""

    def handle(self, head: str, body: str) -> str:
        request_line = head.split("\r\n", 1)[0]
        if "vercheck" in request_line.lower():
            # No client version gate: an empty body reads as "no update required".
            log.log("    [motd] vercheck -> empty (no update)")
            return ""
        # Collapse newlines in the configured text so it stays one INI value on one line.
        text = " ".join(self._message.splitlines()).strip()
        body_out = f"[MOTD]\r\nMOTD_INT={text}\r\n"
        log.log(f"    [motd] motd -> [MOTD] MOTD_INT ({len(text)} chars)")
        return body_out
