"""TCP listeners and HTTP request framing for the Section 8 GameSpy backend.

Two connection shapes:
  * gpcm  - server-speaks-first: send the \\lc\\1\\ greeting, then stream \\final\\-framed messages.
  * http  - client-speaks-first SOAP: read one full HTTP request, route by URL path to AuthService /
            CompetitionService / Sake, answer, and close (the SDK sends Connection: close).
"""
import socket
import threading

from . import log

# GP presence keep-alive: how often (seconds) the server heartbeats an idle but logged-in GP connection
# so the SDK holds the session open and native GetSecondaryLoginStatus() stays "logged in".
GPCM_KEEPALIVE_INTERVAL = 5.0
_GPCM_KEEPALIVE_FRAME = b"\\ka\\\\final\\"


class RawResponse:
    """A non-SOAP body (the news file), which must not be served as text/xml.

    extra_headers carries the Sake file-server headers the GameSpy SDK requires; without them it
    discards the body and reports the read as failed.
    """

    def __init__(self, body: bytes, content_type: str = "text/plain", extra_headers: dict | None = None):
        self.body = body
        self.content_type = content_type
        self.extra_headers = extra_headers or {}


class HttpRouter:
    def __init__(self, auth, competition, sake, motd, news):
        self._auth = auth
        self._competition = competition
        self._sake = sake
        self._motd = motd
        self._news = news

    def route(self, head: str, body: str):
        request_line = head.split("\r\n", 1)[0]
        soap_action = ""
        for line in head.split("\r\n"):
            if line.lower().startswith("soapaction:"):
                soap_action = line.split(":", 1)[1].strip()
        if "/motd/" in head.lower():
            return self._motd.handle(head, body)
        # SakeFileServer is a plain GET for a file, not SOAP. It must be matched before the Sake
        # StorageServer check below, whose "/sake" substring test would otherwise swallow it and try to
        # parse an empty GET body as an envelope.
        if "/SakeFileServer/" in head or "download.aspx" in head.lower():
            body_bytes, headers = self._news.handle_download(head)
            return RawResponse(body_bytes, "application/octet-stream", headers)
        if "/AuthService/" in head:
            return self._auth.handle(head, body)
        # Section 8's ATLAS/competition SDK posts to /AtlasDataServices/GameConfig.asmx, not the
        # /CompetitionService/ path other GameSpy titles use; route both, and fall back to the
        # competition namespace on the SOAPAction so a new endpoint URL still lands here.
        if ("/CompetitionService/" in head or "/AtlasDataServices/" in head
                or "gamespy.net/competition/" in soap_action):
            return self._competition.handle(head, body)
        if "/SakeStorageServer/" in head or "/sake" in head.lower():
            return self._sake.handle(soap_action, body)
        log.log(f"    [http] *** UNHANDLED PATH *** {request_line} SOAPAction={soap_action!r} "
                f"(no service matched; full request logged above)")
        return ('<?xml version="1.0" encoding="utf-8"?>'
                '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
                '<soap:Body/></soap:Envelope>')


class Server:
    def __init__(self, config, gpcm_service, http_router):
        self._config = config
        self._gpcm = gpcm_service
        self._http = http_router
        self._threads = []

    def serve_forever(self):
        for port, kind in sorted(self._config.ports.items()):
            t = threading.Thread(target=self._listen, args=(port, kind), daemon=True)
            t.start()
            self._threads.append(t)
        log.log("=== listeners up; run the game ===")
        # Park the main thread; the listener threads are daemons.
        for t in self._threads:
            t.join()

    def _listen(self, port: int, kind: str):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self._config.bind_address, port))
            s.listen(16)
        except OSError as e:
            log.log(f"(skip {port}: {e})")
            return
        log.log(f"listening {port} ({kind}) on {self._config.bind_address}")
        while True:
            try:
                conn, addr = s.accept()
                threading.Thread(target=self._handle, args=(conn, addr, port, kind), daemon=True).start()
            except OSError as e:
                log.log(f"accept err {port}: {e}")

    def _handle(self, conn, addr, port, kind):
        log.log(f"*** CONNECT port {port} ({kind}) from {addr[0]}:{addr[1]} ***")
        conn.settimeout(120.0)
        try:
            if kind == "gpcm":
                self._handle_gpcm(conn)
            elif kind == "probe":
                self._handle_probe(conn, port)
            else:
                self._handle_http(conn, port)
        except socket.timeout:
            log.log(f"    [{port}] idle timeout")
        except Exception as e:
            log.log(f"    [{port}] error {e}")
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle_probe(self, conn, port):
        # Unknown/undeclared XLSP service: capture whatever the game sends so the protocol can be
        # identified (HTTP SOAP, GameSpy \\..\\final\\, or a binary/server-browser query). Wait a few
        # seconds for a client-speaks-first request; if nothing arrives it is likely server-speaks-first.
        conn.settimeout(6.0)
        buf = b""
        try:
            while len(buf) < 16384:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if len(chunk) < 4096:
                    break
        except socket.timeout:
            pass
        if buf:
            asc = "".join(chr(c) if 32 <= c < 127 else "." for c in buf[:512])
            log.log(f"    [{port}] PROBE captured {len(buf)} bytes (client-speaks-first):\n"
                    f"{buf[:512].hex(' ')}\n{asc}")
        else:
            log.log(f"    [{port}] PROBE no data in 6s (server-speaks-first? needs a greeting)")

    def _handle_gpcm(self, conn):
        gp = self._gpcm.new_connection()
        conn.sendall(gp.greeting())
        conn.settimeout(GPCM_KEEPALIVE_INTERVAL)
        while True:
            try:
                data = conn.recv(8192)
            except socket.timeout:
                # Idle logged-in session: server-initiated keep-alive so the presence connection is not
                # considered dead. This is the heartbeat GameSpy services expect the server to drive.
                if gp.logged_in:
                    try:
                        conn.sendall(_GPCM_KEEPALIVE_FRAME)
                        log.log("    [gpcm] --> ka (server keep-alive)")
                    except OSError:
                        return
                continue
            if not data:
                log.log("    [gpcm] peer closed")
                return
            for resp in gp.feed(data):
                conn.sendall(resp)

    def _handle_http(self, conn, port):
        buf = b""
        while b"\r\n\r\n" not in buf:
            more = conn.recv(8192)
            if not more:
                return
            buf += more
        head_bytes, _, rest = buf.partition(b"\r\n\r\n")
        head = head_bytes.decode("latin-1", "replace")
        clen = 0
        for line in head.split("\r\n"):
            if line.lower().startswith("content-length:"):
                clen = int(line.split(":", 1)[1].strip())
        body = rest
        while len(body) < clen:
            more = conn.recv(8192)
            if not more:
                break
            body += more
        body_text = body.decode("latin-1", "replace")
        # SubmitReport appends a binary report after "application/bin\0"; that blob is captured to
        # reports/ by CompetitionService, so keep it out of the text log. Every other body - including a
        # large multi-field Sake stat write - is logged in full so a capture never silently drops it.
        if b"application/bin\x00" in body:
            log.log(f"    [{port}] <-- HTTP REQUEST:\n{head}\n\n[SC binary report body {len(body)} bytes -> reports/]")
        else:
            log.log(f"    [{port}] <-- HTTP REQUEST:\n{head}\n\n{body_text}")
        routed = self._http.route(head, body_text)
        if isinstance(routed, RawResponse):
            extra = "".join(f"{k}: {v}\r\n" for k, v in routed.extra_headers.items())
            resp_head = (f"HTTP/1.0 200 OK\r\nContent-Type: {routed.content_type}\r\n"
                         f"Content-Length: {len(routed.body)}\r\n{extra}Connection: close\r\n\r\n")
            conn.sendall(resp_head.encode("latin-1", "replace") + routed.body)
            log.log(f"    [{port}] --> HTTP 200 ({len(routed.body)}B {routed.content_type})\n"
                    f"{routed.body.decode('latin-1', 'replace')}")
            return
        resp_body = routed
        resp = ("HTTP/1.0 200 OK\r\nContent-Type: text/xml; charset=utf-8\r\n"
                f"Content-Length: {len(resp_body)}\r\nConnection: close\r\n\r\n{resp_body}")
        conn.sendall(resp.encode("latin-1", "replace"))
        log.log(f"    [{port}] --> HTTP 200 ({len(resp_body)}B)\n{resp_body}")
