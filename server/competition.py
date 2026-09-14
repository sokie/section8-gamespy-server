"""CompetitionService (GameSpy SC / ATLAS), the ranked match-report path the game runs at match end.

Flow (SOAP over HTTP, namespace http://gamespy.net/competition/):
  CheckProfileOnBanList -> CreateSession / CreateMatchlessSession -> SetReportIntention -> SubmitReport.
Every method returns <result>0</result> (SC_RESULT_NO_ERROR) so the client proceeds. CreateSession
hands out the csid and ccid the client echoes back on the later calls.

The match stats are not in the SOAP XML. They are a binary blob appended to the SubmitReport body after
an "application/bin\\0" marker, in a body that may itself be gzip-compressed. See screport for its
layout.
"""

import gzip
import os
import struct
from datetime import datetime

from . import log, screport, statmap

COMP_NS = "http://gamespy.net/competition/"
BIN_MARKER = b"application/bin\x00"


def _envelope(inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
        f"<soap:Body>{inner}</soap:Body></soap:Envelope>"
    )


def _field(raw: bytes, name: str) -> str:
    """Pull a text element out of the raw request, trying the SDK's gsc: prefix and the bare name."""
    for tag in (f"gsc:{name}", name):
        start = f"<{tag}>".encode("ascii")
        end = f"</{tag}>".encode("ascii")
        a = raw.find(start)
        if a >= 0:
            a += len(start)
            b = raw.find(end, a)
            if b >= 0:
                return raw[a:b].decode("ascii", "ignore").strip()
    return ""


class CompetitionService:
    def __init__(self, store=None, report_dir="reports"):
        self._store = store
        self._report_dir = report_dir
        self._csid = 1000
        # csid -> participant profileids in join order, host first. This is the fallback attribution
        # for a report whose roster does not carry a minted ccid (see _mint_ccid).
        self._sessions = {}

    def handle(self, head: str, body: str) -> str:
        # The transport decodes the body as latin-1, which is byte-preserving, so this recovers the
        # exact bytes the binary report and the gzip check need.
        raw = body.encode("latin-1")
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except Exception as e:
                log.log(f"    [comp] gzip decompress failed: {e}")

        method = ""
        for line in head.split("\r\n"):
            if line.lower().startswith("soapaction:"):
                method = line.split(":", 1)[1].strip().strip('"').rsplit("/", 1)[-1]
        if not method:
            for cand in (
                "SubmitReport",
                "SetReportIntention",
                "CreateMatchlessSession",
                "CreateSession",
                "CheckProfileOnBanList",
            ):
                if cand.encode("ascii") in raw:
                    method = cand
                    break

        log.log(f"    [comp] {method or '(unknown)'}")
        handler = getattr(self, f"_op_{method}", None)
        if handler is None:
            # A generic <result>0</result> keeps the game going, so the rest of the session is still
            # captured instead of tearing down at the first unknown call.
            log.log(
                f"    [comp] *** UNHANDLED METHOD *** {method or '(unknown)'} "
                f"(returning generic OK; see full request above)"
            )
            return self._result(method or "Unknown")
        return handler(raw)

    def _result(self, method: str, extra: str = "") -> str:
        return _envelope(
            f'<{method}Response xmlns="{COMP_NS}">'
            f"<{method}Result><result>0</result>{extra}</{method}Result>"
            f"</{method}Response>"
        )

    def _profile_id(self, raw: bytes) -> str:
        # profileid lives inside the certificate element of the request.
        return _field(raw, "profileid") or _field(raw, "userid") or "0"

    def _mint_ccid(self, pid: int) -> str:
        # The report blob carries no real profileid, only this connection GUID, which the backend
        # assigns and the host round-trips verbatim into each roster slot. Encoding the profileid in
        # the leading dword therefore makes attribution independent of block order and player count.
        # The rest is a fixed marker, so the value is recognisable in a capture.
        return f"{pid & 0xFFFFFFFF:08X}-BEEF-CAFE-0000-000000000000"

    # --- session setup: every method only has to succeed, so the client keeps going ----------------

    def _op_CheckProfileOnBanList(self, raw: bytes) -> str:
        # The client parser needs a UserConfig block after the result. An empty body fails it, which the
        # game reads as "competition unavailable" and tears the session down.
        pid = self._profile_id(raw)
        return self._result(
            "CheckProfileOnBanList",
            f"<UserConfig><ProfileID>{pid}</ProfileID><PlatformID>1</PlatformID><IsBanned>0</IsBanned></UserConfig>",
        )

    def _op_CreateSession(self, raw: bytes) -> str:
        self._csid += 1
        # The session creator (the host) is participant 0 of this match.
        host_pid = int(self._profile_id(raw) or 0)
        self._sessions[str(self._csid)] = [host_pid]
        log.log(f"    [comp] CreateSession csid={self._csid} host_pid={host_pid}")
        return self._result("CreateSession", f"<csid>{self._csid}</csid><ccid>{self._mint_ccid(host_pid)}</ccid>")

    def _op_CreateMatchlessSession(self, raw: bytes) -> str:
        self._csid += 1
        return self._result("CreateMatchlessSession", f"<csid>{self._csid}</csid><ccid>{self._profile_id(raw)}</ccid>")

    def _op_SetReportIntention(self, raw: bytes) -> str:
        csid = _field(raw, "csid")
        pid = int(self._profile_id(raw) or 0)
        ccid = self._mint_ccid(pid)
        participants = self._sessions.setdefault(csid, [])
        if pid and pid not in participants:
            participants.append(pid)
        # This method fires only for a match the game has already decided is ranked, so its arrival is
        # the definitive ranked signal. CheckProfileOnBanList is not: that one also runs at login.
        auth = _field(raw, "authoritative")
        log.log(
            f"    [comp] *** RANKED CONFIRMED: SetReportIntention csid={csid} pid={pid} ccid={ccid} "
            f"authoritative={auth} participants(join order)={participants} ***"
        )
        return self._result("SetReportIntention", f"<csid>{csid}</csid><ccid>{ccid}</ccid>")

    # --- the actual stats submission ---------------------------------------------------------------

    def _op_SubmitReport(self, raw: bytes) -> str:
        csid = _field(raw, "csid")
        ccid = _field(raw, "ccid")
        gameid = _field(raw, "gameid")
        # Take the owner from the request's certificate, not the echoed ccid: the certificate carries
        # the profileid GPCM issued, which is the one Sake keys PlayerStats on.
        owner = self._profile_id(raw)
        pos = raw.find(BIN_MARKER)
        report = raw[pos + len(BIN_MARKER) :] if pos >= 0 else b""
        log.log(
            f"    [comp] *** RANKED MATCH: SubmitReport owner={owner} csid={csid} ccid={ccid} "
            f"gameid={gameid} blob={len(report)}B ***"
        )
        self._save_report(gameid, csid, owner, report, raw)
        participants = list(self._sessions.get(csid, []))
        owner_i = int(owner or 0)
        if owner_i and owner_i not in participants:
            # The cert owner is the host, so it leads the join order when CreateSession missed it.
            participants.insert(0, owner_i)
        self._ingest_report(report, participants)
        return self._result("SubmitReport")

    def _ingest_report(self, blob: bytes, participants: list) -> None:
        """Decode the SC blob and fold each player's per-round result into PlayerStats_v6.

        The host's report carries every player. Career Ranked_xp is a running sum of the per-round XP
        delta (statmap.XP_DELTA_KEYID); every other keyid is kept verbatim as Ranked_wkey_<id>.
        """
        if not blob or self._store is None:
            return
        # Dump every value keyid in wire order, so the raw deltas can be read off the log without
        # trusting the per-player block split.
        ordered = " ".join(f"{k}={v}" for _, k, kind, v in screport.raw_entries(blob) if kind == "i32")
        log.log(f"    [comp] STREAM (wire order, i32 only): {ordered}")
        try:
            players = screport.parse(blob)
        except Exception as e:
            log.log(f"    [comp] report decode failed: {e}")
            return
        if not players:
            log.log("    [comp] report carried no stat blocks (close/empty report)")
            return
        # A dedicated server submits every player's stats but plays no round, so it fills no stat block
        # and has to leave the join order. It is the participant that publishes a ServerStatusTG09_v6
        # record; a listen host does play, holds no such record, and so keeps its block.
        attribution = [
            p for p in participants if not self._store.record_id_for_owner("ServerStatusTG09_v6", p)
        ] or participants
        log.log(
            f"    [comp] blocks={len(players)} blobids={[hex(p.profileid) for p in players]} "
            f"participants(join order)={participants} attribution(players)={attribution}"
        )
        # A roster GUID that starts with a profileid and the BEEF-CAFE marker means the host echoed our
        # minted ccid. A 0x10000000+slot handle instead means it ignored it.
        if len(blob) >= 0x44:
            for i in range(struct.unpack_from(">H", blob, 0x20)[0]):
                rec = blob[0x44 + i * 20 : 0x44 + i * 20 + 20]
                log.log(
                    f"    [comp] roster[{i}] connGUID={rec[:16].hex(' ')} team={struct.unpack_from('>I', rec, 16)[0]}"
                )
        for idx, pr in enumerate(players):
            log.log(
                f"    [comp] block {idx}: blobid=0x{pr.profileid:08x} ft={pr.filetime} "
                f"keys={dict(sorted(pr.values.items()))}"
            )
        # Attribute by the roster GUID, which screport reads as pr.profileid, because _mint_ccid encoded
        # the real profileid there. Fall back to join order only for a slot carrying a raw local handle.
        participant_set = {p for p in participants if p}
        for idx, pr in enumerate(players):
            if not pr.values:
                continue
            if pr.profileid in participant_set:
                profileid, how = pr.profileid, "ccid"
            elif idx < len(attribution) and attribution[idx]:
                profileid, how = attribution[idx], "positional"
            else:
                log.log(f"    [comp] report block {idx} has no resolvable profileid; skipped")
                continue
            xp_delta = int(pr.values.get(statmap.XP_DELTA_KEYID, 0))
            record_id, new_xp, applied = self._store.add_xp_delta("PlayerStats_v6", profileid, xp_delta, pr.filetime)
            if not applied:
                log.log(f"    [comp] pid={profileid}: report not newer than stored, skipped (dup/replay)")
                continue
            # The XP delta is summed rather than stored per-round, so it is the one key left out here.
            raw = [
                (statmap.field_for_keyid(k), "intValue", str(v))
                for k, v in pr.values.items()
                if k != statmap.XP_DELTA_KEYID
            ]
            prog = statmap.progression_for_xp(new_xp)
            self._store.set_fields(
                "PlayerStats_v6", profileid, record_id, raw + [(n, "intValue", str(v)) for n, v in prog.items()]
            )
            # The game reads the level from S8Level_v6 as well, so mirror it there.
            lrid = self._store.record_id_for_owner("S8Level_v6", profileid) or self._store.create_record(
                "S8Level_v6", profileid
            )
            self._store.set_fields(
                "S8Level_v6", profileid, lrid, [("Ranked_Level", "intValue", str(prog["Ranked_Level"]))]
            )
            log.log(
                f"    [comp] pid={profileid} (via {how}) -> recordid={record_id}: +{xp_delta} XP "
                f"(keyid 11) => total xp={new_xp} level={prog['Ranked_Level']} (+{len(raw)} raw keys)"
            )

    def _save_report(self, gameid: str, csid: str, owner: str, report: bytes, raw: bytes = b"") -> None:
        try:
            os.makedirs(self._report_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            base = os.path.join(self._report_dir, f"report_g{gameid or 'x'}_s{csid or 'x'}_p{owner or 'x'}_{ts}")
            # Save the whole decompressed request as well as the isolated blob, because decoding the
            # report layout needs the surrounding SOAP fields too.
            if raw:
                with open(base + ".request", "wb") as f:
                    f.write(raw)
            if report:
                with open(base + ".bin", "wb") as f:
                    f.write(report)
                log.log(f"    [comp] saved blob -> {base}.bin ({len(report)}B) + full .request")
            else:
                log.log(f"    [comp] SubmitReport had no application/bin blob; saved full .request -> {base}.request")
        except Exception as e:
            log.log(f"    [comp] failed to save report: {e}")
