"""CompetitionService (GameSpy SC / ATLAS) - the ranked match-report path Section 8 uses at match end.

Flow (SOAP over HTTP, namespace http://gamespy.net/competition/):
  CheckProfileOnBanList -> CreateSession / CreateMatchlessSession -> SetReportIntention -> SubmitReport.
Each web method returns <result>0</result> (SC_RESULT_NO_ERROR) so the client proceeds; CreateSession
hands out csid/ccid which the client echoes back on the later calls.

The match stats are a binary `report` blob appended to the SubmitReport body after an "application/bin\0"
marker (the whole body may be gzip-compressed) - NOT inside the SOAP XML. We extract that blob, persist it
verbatim, and decode it into per-player stats (see screport + _ingest_report). Section 8's report layout
is its own, so kirov's RA3 MatchReport parser does not apply, but the envelope handling, the
application/bin split, and the response shapes here follow kirov/openspy and were confirmed against
S9-Win32-F.exe's parse tables (result/csid/ccid element names).
"""
import gzip
import os
from datetime import datetime

from . import log, screport, statmap

COMP_NS = "http://gamespy.net/competition/"
BIN_MARKER = b"application/bin\x00"


def _envelope(inner: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
            f'<soap:Body>{inner}</soap:Body></soap:Envelope>')


def _field(raw: bytes, name: str) -> str:
    """Pull a text element out of the raw request, trying the gsc: prefix the SDK uses and the bare name."""
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
        # csid -> ordered list of participant profileids (host first). The SC report blob identifies its
        # players by a local per-match handle (0x10000001, 0x10000002, ...), NOT the Sake profileid, so
        # attribution is by position: report block i belongs to the i-th player that joined this session.
        self._sessions = {}

    def handle(self, head: str, body: str) -> str:
        # The transport decodes the body as latin-1, which is byte-preserving, so re-encoding recovers the
        # exact bytes - needed for the binary report and a possibly gzip-compressed body.
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
            for cand in ("SubmitReport", "SetReportIntention", "CreateMatchlessSession",
                         "CreateSession", "CheckProfileOnBanList"):
                if cand.encode("ascii") in raw:
                    method = cand
                    break

        log.log(f"    [comp] {method or '(unknown)'}")
        handler = getattr(self, f"_op_{method}", None)
        if handler is None:
            # A method we do not implement (e.g. an AtlasDataServices/GameConfig call). Flag it loudly
            # and echo a generic <result>0</result> so the game keeps going and the whole session is
            # captured, rather than tearing down at the first unknown call.
            log.log(f"    [comp] *** UNHANDLED METHOD *** {method or '(unknown)'} "
                    f"(returning generic OK; see full request above)")
            return self._result(method or "Unknown")
        return handler(raw)

    def _result(self, method: str, extra: str = "") -> str:
        return _envelope(
            f'<{method}Response xmlns="{COMP_NS}">'
            f'<{method}Result><result>0</result>{extra}</{method}Result>'
            f'</{method}Response>')

    def _profile_id(self, raw: bytes) -> str:
        # profileid lives inside the certificate element of the request.
        return _field(raw, "profileid") or _field(raw, "userid") or "0"

    # --- session setup: every method just has to succeed so the client keeps going ------------------

    def _op_CheckProfileOnBanList(self, raw: bytes) -> str:
        # The client's response parser requires <result>0</result>, then reads a UserConfig block for
        # ProfileID/PlatformID/IsBanned. IsBanned=0 clears the player. A missing/empty body makes that
        # parser fail, which the game treats as "competition unavailable" and tears the session down.
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
        return self._result("CreateSession", f"<csid>{self._csid}</csid><ccid>{self._profile_id(raw)}</ccid>")

    def _op_CreateMatchlessSession(self, raw: bytes) -> str:
        self._csid += 1
        return self._result("CreateMatchlessSession", f"<csid>{self._csid}</csid><ccid>{self._profile_id(raw)}</ccid>")

    def _op_SetReportIntention(self, raw: bytes) -> str:
        csid = _field(raw, "csid")
        pid = int(self._profile_id(raw) or 0)
        ccid = _field(raw, "ccid") or str(pid)
        # Each participant announces its intention before the match; record join order for attribution.
        participants = self._sessions.setdefault(csid, [])
        if pid and pid not in participants:
            participants.append(pid)
        # SetReportIntention only fires for a match the game has decided IS ranked, so its arrival is the
        # definitive "this match is really ranked" signal (unlike CheckProfileOnBanList, a login ban-check).
        # authoritative=1 marks the host/dedicated-server report that carries every player's stats.
        auth = _field(raw, "authoritative")
        log.log(f"    [comp] *** RANKED CONFIRMED: SetReportIntention csid={csid} pid={pid} ccid={ccid} "
                f"authoritative={auth} participants(join order)={participants} ***")
        return self._result("SetReportIntention", f"<csid>{csid}</csid><ccid>{ccid}</ccid>")

    # --- the actual stats submission ---------------------------------------------------------------

    def _op_SubmitReport(self, raw: bytes) -> str:
        csid = _field(raw, "csid")
        ccid = _field(raw, "ccid")
        gameid = _field(raw, "gameid")
        # The report belongs to the authenticated owner named in the request's certificate - the same
        # profileid GPCM/AuthService issued for this uniquenick, and the one Sake keys PlayerStats on.
        # Attributing by the cert (not the echoed ccid) is what maps a report to the right stored user.
        owner = self._profile_id(raw)
        pos = raw.find(BIN_MARKER)
        report = raw[pos + len(BIN_MARKER):] if pos >= 0 else b""
        log.log(f"    [comp] *** RANKED MATCH: SubmitReport owner={owner} csid={csid} ccid={ccid} "
                f"gameid={gameid} blob={len(report)}B ***")
        self._save_report(gameid, csid, owner, report, raw)
        # Resolve report blocks -> Sake profileids by session join order, host (cert owner) first.
        participants = list(self._sessions.get(csid, []))
        owner_i = int(owner or 0)
        if owner_i and owner_i not in participants:
            participants.insert(0, owner_i)
        self._ingest_report(report, participants)
        return self._result("SubmitReport")

    def _ingest_report(self, blob: bytes, participants: list) -> None:
        """Decode the SC blob and fold each player's per-round result into PlayerStats_v6.

        The report ships the XP EARNED THIS ROUND in keyid 11 (never a total), so career Ranked_xp is a
        running sum (see statmap.XP_DELTA_KEYID). Every other keyid is kept verbatim as Ranked_wkey_<id>.
        The host's report contains every player; block i is attributed to participants[i] (join order),
        since the blob's own player ids are per-match handles, not Sake profileids."""
        if not blob or self._store is None:
            return
        # Attribution-proof dump: every value keyid in wire order (the host's block comes first, carrying
        # keyid 5). Lets us read the raw deltas straight off the log independent of the block split.
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
        # A dedicated server submits every player's stats but plays no round, so it occupies no stat block.
        # It is identifiable by self-publishing a ServerStatusTG09_v6 record; drop such server owners from
        # the join order so report blocks line up with the actual players. A listen host does play and holds
        # no such record, so it stays in position and keeps its block.
        attribution = [p for p in participants
                       if not self._store.record_id_for_owner("ServerStatusTG09_v6", p)] or participants
        log.log(f"    [comp] blocks={len(players)} blobids={[hex(p.profileid) for p in players]} "
                f"participants(join order)={participants} attribution(players)={attribution}")
        for idx, pr in enumerate(players):
            log.log(f"    [comp] block {idx}: blobid=0x{pr.profileid:08x} ft={pr.filetime} "
                    f"keys={dict(sorted(pr.values.items()))}")
        for idx, pr in enumerate(players):
            if not pr.values:
                continue
            if idx >= len(attribution) or not attribution[idx]:
                log.log(f"    [comp] report block {idx} has no known player profileid; skipped")
                continue
            profileid = attribution[idx]
            xp_delta = int(pr.values.get(statmap.XP_DELTA_KEYID, 0))
            record_id, new_xp, applied = self._store.add_xp_delta(
                "PlayerStats_v6", profileid, xp_delta, pr.filetime)
            if not applied:
                log.log(f"    [comp] pid={profileid}: report not newer than stored, skipped (dup/replay)")
                continue
            # Keep every reported keyid verbatim for later analysis (the XP delta excluded - it is summed,
            # not stored per-round). None of these map to a validated Sake column, so no false name is set.
            raw = [(statmap.field_for_keyid(k), "intValue", str(v))
                   for k, v in pr.values.items() if k != statmap.XP_DELTA_KEYID]
            prog = statmap.progression_for_xp(new_xp)
            self._store.set_fields("PlayerStats_v6", profileid, record_id,
                                   raw + [(n, "intValue", str(v)) for n, v in prog.items()])
            # The game reads each player's level from S8Level_v6 too, so mirror the derived level there.
            lrid = (self._store.record_id_for_owner("S8Level_v6", profileid)
                    or self._store.create_record("S8Level_v6", profileid))
            self._store.set_fields("S8Level_v6", profileid, lrid,
                                   [("Ranked_Level", "intValue", str(prog["Ranked_Level"]))])
            log.log(f"    [comp] pid={profileid} -> recordid={record_id}: +{xp_delta} XP (keyid 11) "
                    f"=> total xp={new_xp} level={prog['Ranked_Level']} (+{len(raw)} raw keys)")

    def _save_report(self, gameid: str, csid: str, owner: str, report: bytes, raw: bytes = b"") -> None:
        try:
            os.makedirs(self._report_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            base = os.path.join(self._report_dir, f"report_g{gameid or 'x'}_s{csid or 'x'}_p{owner or 'x'}_{ts}")
            # Save the whole decompressed request (SOAP envelope + the application/bin blob) as well as the
            # isolated blob: reverse-engineering Section 8's report layout needs the surrounding fields too.
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
