"""GameSpy motd.asp and vercheck endpoints, answered so the requests do not fail. Neither gates sign-in.

This is not the source of the welcome banner on the online menu, despite the name. That comes from the
`[MOTD]` section of the news file, so point banner edits at news/section8_news.txt.
"""

from . import log


class MotdService:
    def __init__(self, message: str):
        self._message = message or ""

    def handle(self, head: str, body: str) -> str:
        request_line = head.split("\r\n", 1)[0]
        if "vercheck" in request_line.lower():
            log.log("    [motd] vercheck -> empty (no update required)")
            return ""
        # An INI value must stay on one line.
        text = " ".join(self._message.splitlines()).strip()
        body_out = f"[MOTD]\r\nMOTD_INT={text}\r\n"
        log.log(f"    [motd] motd -> [MOTD] MOTD_INT ({len(text)} chars)")
        return body_out
