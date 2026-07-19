r"""GPCM - GameSpy Presence Connection Manager (login / account creation).

Server-speaks-first: on connect we send \lc\1\challenge\...\ and the client answers with \login\ or
\newuser\. We learn the account password from the client's own \newuser\ \passenc\ blob (so we never
need to know it in advance), then answer \login\ with a real \proof\ and a login ticket the game will
carry to Sake. Each distinct uniquenick gets a stable, unique profileid from the shared store, which is
what makes cross-PC leaderboards attribute stats to the right player.
"""
import threading

from . import log
from .codecs import parse_kv, md5_hex, login_proof, login_ticket_for

# Server keep-alive frame. The GP presence connection is what native GetSecondaryLoginStatus() reads;
# the SDK drops the session (and the title's connection-status manager falls out of the STATS level the
# leaderboards/stats screens require) unless the connection is kept alive.
KEEPALIVE = "\\ka\\\\final\\"
# Post-login handshake the GP SDK expects before it treats presence as fully established: the (empty)
# buddy list and block list.
BUDDY_LIST = "\\bdy\\0\\list\\\\final\\"
BLOCK_LIST = "\\blk\\0\\list\\\\final\\"


class GpcmService:
    def __init__(self, store, server_challenge: str):
        self._store = store
        self._server_challenge = server_challenge
        self._passwords = {}          # uniquenick -> plaintext password, learned from \newuser\
        self._pw_lock = threading.Lock()

    def new_connection(self):
        return GpcmConnection(self)

    def password_md5(self, uniquenick: str) -> str:
        with self._pw_lock:
            pw = self._passwords.get(uniquenick)
        if pw is None:
            # Not learned this run: fall back to the password persisted from a prior launch's \newuser\.
            # The game DOES verify the server \proof\, so a first \login\ (which races ahead of this
            # launch's \newuser\) is rejected unless we already know the password from before.
            pw = self._store.get_password(uniquenick)
            if pw is not None:
                with self._pw_lock:
                    self._passwords[uniquenick] = pw
        return md5_hex(pw if pw is not None else "")

    def learn_password(self, uniquenick: str, plaintext: str) -> None:
        with self._pw_lock:
            self._passwords[uniquenick] = plaintext
        # Persist so the first \login\ of a later launch (or after a server restart) proves correctly.
        self._store.set_password(uniquenick, plaintext)


class GpcmConnection:
    def __init__(self, service: GpcmService):
        self._svc = service
        self._buf = ""
        # Read by the transport's keep-alive loop: only heartbeat a session that has actually logged in.
        self.logged_in = False
        self.profileid = 0
        self.uniquenick = ""

    def greeting(self) -> bytes:
        return (f"\\lc\\1\\challenge\\{self._svc._server_challenge}\\id\\1\\final\\").encode()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data.decode("latin-1", "replace")
        out = []
        while "\\final\\" in self._buf:
            frame, self._buf = self._buf.split("\\final\\", 1)
            frame += "\\final\\"
            kv = parse_kv(frame)
            asc = "".join(c if 32 <= ord(c) < 127 else "." for c in frame)
            log.log(f"    [gpcm] <-- {asc}")
            resp = self._dispatch(kv)
            if resp:
                out.append(resp.encode())
                log.log(f"    [gpcm] --> {resp}")
        return out

    def _dispatch(self, kv: dict) -> str:
        if "login" in kv:
            return self._login(kv)
        if "newuser" in kv:
            return self._newuser(kv)
        if "ka" in kv:
            return KEEPALIVE
        if "getprofile" in kv:
            return self._getprofile(kv)
        if "logout" in kv:
            self.logged_in = False
            log.log("    [gpcm] client logout")
            return ""
        # One-way presence/profile updates the SDK sends after login. They expect no reply, but must be
        # accepted (not logged as errors) so the session is treated as a healthy, established connection.
        if any(k in kv for k in ("updatepro", "updateui", "status", "addbuddy", "delbuddy", "authadd",
                                 "addblock", "removeblock", "bm", "pinvite", "revoke", "getprofileid")):
            return ""
        log.log(f"    [gpcm] (unhandled cmd: {list(kv)[:4]})")
        return ""

    def _getprofile(self, kv: dict) -> str:
        pid = kv.get("profileid") or str(self.profileid)
        nick = self.uniquenick or "player"
        # Minimal \pi\ (profile info) reply. sig is an opaque field the SDK stores; a stable placeholder
        # is accepted. Not exercised by Section 8's own flow, but handled for SDK completeness.
        return ("\\pi\\profileid\\%s\\nick\\%s\\uniquenick\\%s\\email\\%s@gamespy.local\\sig\\"
                "00000000000000000000000000000000\\userid\\%s\\pid\\0\\lon\\0.000000\\lat\\0.000000"
                "\\loc\\\\id\\%s\\final\\"
                % (pid, nick, nick, nick, pid, kv.get("id", "1")))

    def _login(self, kv: dict) -> str:
        uniq = kv.get("uniquenick", "player")
        client_challenge = kv.get("challenge", "")
        profileid = self._svc._store.get_or_create_profile(uniq)
        lt = login_ticket_for(profileid)
        pwh = self._svc.password_md5(uniq)
        proof = login_proof(pwh, uniq, self._svc._server_challenge, client_challenge)
        self.logged_in = True
        self.profileid = profileid
        self.uniquenick = uniq
        log.log(f"    [gpcm] login uniquenick={uniq} profileid={profileid} lt={lt}")
        lc2 = ("\\lc\\2\\sesskey\\%d\\proof\\%s\\userid\\%d\\profileid\\%d\\uniquenick\\%s\\lt\\%s\\id\\1\\final\\"
               % (profileid, proof, profileid, profileid, uniq, lt))
        # Complete the presence handshake in the same write: login confirm, empty buddy + block lists,
        # then an immediate keep-alive. This is what lets native GetSecondaryLoginStatus() latch to
        # "logged in" so the connection-status manager can reach the STATS level (7) the stats UI needs.
        return lc2 + BUDDY_LIST + BLOCK_LIST + KEEPALIVE

    def _newuser(self, kv: dict) -> str:
        uniq = kv.get("uniquenick") or kv.get("nick", "player")
        penc = kv.get("passenc", "")
        if penc:
            try:
                from .codecs import password_decode
                pw = password_decode(penc)
                self._svc.learn_password(uniq, pw)
                log.log(f"    [gpcm] newuser learned password for {uniq}: {pw!r}")
            except Exception as e:
                log.log(f"    [gpcm] newuser passenc decode failed: {e}")
        profileid = self._svc._store.get_or_create_profile(uniq)
        return "\\nur\\%d\\pid\\%d\\id\\1\\final\\" % (profileid, profileid)
