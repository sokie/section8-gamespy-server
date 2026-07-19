"""GameSpy MOTD ("message of the day") - the welcome banner Section 8 shows on the online menu.

The game GETs http://motd.gamespy.com/motd/{motd,vercheck}.asp?userid=..&gamename=.. through the GameSpy
ghttp layer - a plain HTTP GET to a full URL, unlike the SG-tunnelled SOAP services. Redirecting those two
URLs to this backend (patch the host in the exe to 127.0.0.1:<httpport>) lets us answer them. This is
purely cosmetic and non-blocking: it does NOT gate sign-in (that is the separate Sake NewsStats read).

The reply is a GameSpy INI blob the game parses (S9-Win32-F.exe FUN_015a0080): it finds the `[MOTD]`
line, then the `MOTD_<lang>` key (lang comes from Engine.Engine/Language, "INT" for this install), splits
on `=` and displays the value. An optional per-line version filter (`min-max:` style, tokens - : ,) is
skipped when absent, so a bare `MOTD_INT=<text>` always shows. Returning the raw text with no `[MOTD]`
header is why the banner previously fell back to "...not available".
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
