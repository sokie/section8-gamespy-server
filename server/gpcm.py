r"""GPCM, the GameSpy Presence Connection Manager: login and account creation.

Server-speaks-first. The account password is learnt from the client's own \newuser\ \passenc\ blob, so
it never has to be known in advance. Each uniquenick gets a stable profileid from the shared store,
which is what makes a cross-PC leaderboard attribute stats to the right player.
"""

import threading

from . import log
from .codecs import login_proof, login_response, login_ticket_for, md5_hex, parse_kv

# Without a keep-alive the SDK drops the presence session, and the title then falls out of the STATS
# connection level that the leaderboard and stats screens require.
KEEPALIVE = "\\ka\\\\final\\"
# The GP SDK treats presence as established only after it receives a buddy list and a block list.
BUDDY_LIST = "\\bdy\\0\\list\\\\final\\"
BLOCK_LIST = "\\blk\\0\\list\\\\final\\"
# GP_LOGIN_BAD_UNIQUENICK. The SDK reports it as "no such account", which is the state the title
# answers with \newuser\.
ERR_BAD_UNIQUENICK = 265


class GpcmService:
    def __init__(self, store, server_challenge: str):
        self._store = store
        self._server_challenge = server_challenge
        self._passwords = {}  # uniquenick -> plaintext password, learnt from \newuser\
        self._pw_lock = threading.Lock()

    def new_connection(self):
        return GpcmConnection(self)

    def password_for(self, uniquenick: str):
        """The plaintext password a \\newuser\\ taught us, or None while the account is still unknown."""
        with self._pw_lock:
            pw = self._passwords.get(uniquenick)
        if pw is None:
            pw = self._store.get_password(uniquenick)
            if pw is not None:
                with self._pw_lock:
                    self._passwords[uniquenick] = pw
        return pw

    def learn_password(self, uniquenick: str, plaintext: str) -> None:
        with self._pw_lock:
            self._passwords[uniquenick] = plaintext
        self._store.set_password(uniquenick, plaintext)


class GpcmConnection:
    def __init__(self, service: GpcmService):
        self._svc = service
        self._buf = ""
        # The transport's keep-alive loop heartbeats only a session that has logged in.
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
        # One-way presence updates the SDK sends after login. They expect no reply.
        if any(
            k in kv
            for k in (
                "updatepro",
                "updateui",
                "status",
                "addbuddy",
                "delbuddy",
                "authadd",
                "addblock",
                "removeblock",
                "bm",
                "pinvite",
                "revoke",
                "getprofileid",
            )
        ):
            return ""
        log.log(f"    [gpcm] (unhandled cmd: {list(kv)[:4]})")
        return ""

    def _getprofile(self, kv: dict) -> str:
        pid = kv.get("profileid") or str(self.profileid)
        nick = self.uniquenick or "player"
        req_id = kv.get("id", "1")
        # Section 8 never sends \getprofile\; this answers it for SDK completeness. sig is opaque to
        # the SDK, so a stable placeholder passes.
        return (
            f"\\pi\\profileid\\{pid}\\nick\\{nick}\\uniquenick\\{nick}\\email\\{nick}@gamespy.local\\sig\\"
            f"00000000000000000000000000000000\\userid\\{pid}\\pid\\0\\lon\\0.000000\\lat\\0.000000"
            f"\\loc\\\\id\\{req_id}\\final\\"
        )

    def _login(self, kv: dict) -> str:
        uniq = kv.get("uniquenick", "player")
        client_challenge = kv.get("challenge", "")
        password = self._svc.password_for(uniq)
        pwh = md5_hex(password) if password is not None else ""
        if password is None or login_response(pwh, uniq, client_challenge, self._svc._server_challenge) != kv.get(
            "response", ""
        ):
            return self._login_refused(kv, uniq, "no password stored" if password is None else "response mismatch")
        profileid = self._svc._store.get_or_create_profile(uniq)
        lt = login_ticket_for(profileid)
        proof = login_proof(pwh, uniq, self._svc._server_challenge, client_challenge)
        self.logged_in = True
        self.profileid = profileid
        self.uniquenick = uniq
        log.log(f"    [gpcm] login uniquenick={uniq} profileid={profileid} lt={lt}")
        lc2 = (
            f"\\lc\\2\\sesskey\\{profileid}\\proof\\{proof}\\userid\\{profileid}\\profileid\\{profileid}"
            f"\\uniquenick\\{uniq}\\lt\\{lt}\\id\\1\\final\\"
        )
        # One write completes the presence handshake, so the SDK latches to "logged in" at once.
        return lc2 + BUDDY_LIST + BLOCK_LIST + KEEPALIVE

    def _login_refused(self, kv: dict, uniquenick: str, reason: str) -> str:
        r"""Refuse a \login\ we cannot prove. Answering it with \lc\2 sends a \proof\ built on the wrong
        secret, which the client rejects by dropping the socket; the error routes it to \newuser\, and
        the password that teaches us lets the next \login\ succeed."""
        log.log(f"    [gpcm] login refused for {uniquenick}: {reason}")
        req_id = kv.get("id", "1")
        return (
            f"\\error\\\\err\\{ERR_BAD_UNIQUENICK}\\fatal\\\\errmsg\\The uniquenick provided was incorrect."
            f"\\id\\{req_id}\\final\\"
        )

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
        # gpiProcessConnect reads \userid\ and \profileid\ out of this reply by name, and fails the
        # registration with GP_PARSE if either is absent. \nur\ itself carries no value.
        return f"\\nur\\\\userid\\{profileid}\\profileid\\{profileid}\\id\\{kv.get('id', '1')}\\final\\"
