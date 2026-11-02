import gzip
import hashlib
import os
import re
from datetime import datetime

from . import log, screport, stats
from .titles import Titles

COMP_NS = "http://gamespy.net/competition/"
BIN_MARKER = b"application/bin\x00"
PLAYER_STATS = "PlayerStats_v6"
CCID_MARKER = bytes.fromhex("BEEFCAFE")


def minted_profileid(block):
    if block.connection_id[4:8] == CCID_MARKER:
        return block.connection_dword() or None
    return None


def _envelope(inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
        f"<soap:Body>{inner}</soap:Body></soap:Envelope>"
    )


def _field(raw: bytes, name: str) -> str:
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
    def __init__(self, store=None, report_dir="reports", titles=None):
        self._store = store
        self._report_dir = report_dir
        self._csid = 1000
        self._titles = titles or Titles(store=store)

    def _title_of(self, raw: bytes, csid: str = ""):
        # CheckProfileOnBanList spells it <gsc:GameId>; the other calls use <gsc:gameid>.
        gameid = _field(raw, "gameid") or _field(raw, "GameId")
        if gameid:
            return self._titles.resolve(gameid)
        return self._titles.title_for_session(csid) or self._titles.legacy

    def handle(self, head: str, body: str) -> str:
        # The transport decodes the body as latin-1, so encoding it back gives the exact bytes.
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

        log.log(f"    [comp] {method or '(unknown)'} title={self._title_of(raw, _field(raw, 'csid')).key}")
        handler = getattr(self, f"_op_{method}", None)
        if handler is None:
            # Answer an unknown call with success, so the game does not end the session.
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
        return _field(raw, "profileid") or _field(raw, "userid") or "0"

    def _mint_ccid(self, pid: int) -> str:
        # The report names a player only by this id, which the host copies into the roster. The first
        # dword holds the profileid. The marker tells a minted ccid from a raw slot handle.
        return f"{pid & 0xFFFFFFFF:08X}-BEEF-CAFE-0000-000000000000"

    def _op_CheckProfileOnBanList(self, raw: bytes) -> str:
        # Without a UserConfig block the game shows competition as unavailable and ends the session.
        pid = self._profile_id(raw)
        return self._result(
            "CheckProfileOnBanList",
            f"<UserConfig><ProfileID>{pid}</ProfileID><PlatformID>1</PlatformID><IsBanned>0</IsBanned></UserConfig>",
        )

    def _op_CreateSession(self, raw: bytes) -> str:
        self._csid += 1
        host_pid = int(self._profile_id(raw) or 0)
        title = self._title_of(raw)
        self._titles.remember_session(str(self._csid), title)
        log.log(f"    [comp] CreateSession csid={self._csid} host_pid={host_pid} title={title.key}")
        return self._result("CreateSession", f"<csid>{self._csid}</csid><ccid>{self._mint_ccid(host_pid)}</ccid>")

    def _op_CreateMatchlessSession(self, raw: bytes) -> str:
        self._csid += 1
        self._titles.remember_session(str(self._csid), self._title_of(raw))
        return self._result("CreateMatchlessSession", f"<csid>{self._csid}</csid><ccid>{self._profile_id(raw)}</ccid>")

    def _op_SetReportIntention(self, raw: bytes) -> str:
        csid = _field(raw, "csid")
        pid = int(self._profile_id(raw) or 0)
        ccid = self._mint_ccid(pid)
        # The game calls this only for a ranked match. CheckProfileOnBanList also runs at login.
        auth = _field(raw, "authoritative")
        log.log(
            f"    [comp] *** RANKED CONFIRMED: SetReportIntention csid={csid} pid={pid} ccid={ccid} "
            f"authoritative={auth} ***"
        )
        return self._result("SetReportIntention", f"<csid>{csid}</csid><ccid>{ccid}</ccid>")

    def _op_SubmitReport(self, raw: bytes) -> str:
        csid = _field(raw, "csid")
        ccid = _field(raw, "ccid")
        gameid = _field(raw, "gameid")
        owner = int(self._profile_id(raw) or 0)
        pos = raw.find(BIN_MARKER)
        report = raw[pos + len(BIN_MARKER) :] if pos >= 0 else b""
        log.log(
            f"    [comp] *** RANKED MATCH: SubmitReport owner={owner} csid={csid} ccid={ccid} "
            f"gameid={gameid} blob={len(report)}B ***"
        )
        self._save_report(gameid, csid, owner, report, raw)
        self.ingest_report(report, csid, owner, self._title_of(raw, csid))
        return self._result("SubmitReport")

    def ingest_report(self, blob: bytes, csid: str = "", host: int = 0, title=None) -> None:
        """Stores a report as match history and folds each player onto their career. A resent report
        changes nothing."""
        if not blob or self._store is None:
            return
        title = title or self._titles.title_for_session(csid) or self._titles.legacy
        catalog = title.catalog
        player_stats = self._titles.player_table(title)
        try:
            report = screport.parse(blob)
        except screport.ReportError as e:
            log.log(f"    [comp] report decode failed: {e}")
            return
        if not any(p.keys for p in report.players):
            log.log("    [comp] report carried no stat blocks (close/empty report)")
            return
        mode = catalog.game_mode_of([p.keys for p in report.players])
        log.log(
            f"    [comp] report v{report.version} status={report.status} players={len(report.players)} "
            f"mode={catalog.game_modes.get(mode) if mode is not None else None} title={title.key} "
            f"table={player_stats}"
        )
        if not catalog.by_key:
            log.log(
                f"    [comp] {title.name} has no stat catalogue yet: every key is stored as Unmapped_Key<n> and in "
                f"match history. Build catalog/{title.key}.json, then run scripts/stats_tool.py --title {title.key} rebuild"
            )
        with self._store.transaction():
            match_id = self._store.add_match(
                hashlib.sha256(blob).hexdigest(),
                csid,
                host,
                mode,
                report,
                len(report.players),
                title.key,
                catalog.game_modes,
            )
            if match_id is None:
                log.log("    [comp] this report was already applied; skipped (resend)")
                return
            for block in report.players:
                profileid = minted_profileid(block)
                if not profileid or not block.keys:
                    log.log(
                        f"    [comp] block {block.index} (connection {block.connection_id.hex()}): "
                        f"{'no stats' if profileid else 'no minted ccid'}; skipped"
                    )
                    self._store.add_match_player(match_id, block, profileid)
                    continue
                record_id = self._store.record_for_owner(player_stats, profileid)
                career = self._store.get_fields(player_stats, profileid, record_id)
                result = catalog.fold_match(career, block.keys)
                self._store.set_fields(
                    player_stats, profileid, record_id, [(c, t, v) for c, (t, v) in result.updates.items()]
                )
                self._store.add_match_player(match_id, block, profileid, result)
                self._log_player(match_id, profileid, block, result, catalog)
                self._link_level(title, profileid, result)

    def _link_level(self, title, profileid: int, result) -> None:
        """Copies the player's level into the other title's column that level_link names. Both games log
        in with the same uniquenick, so the profileid is the same in both."""
        link = title.level_link
        if not link or result.level is None:
            return
        try:
            target = self._titles.get(link["title"])
        except KeyError:
            return
        table = target.table(link["table"])
        record_id = self._store.record_for_owner(table, profileid)
        self._store.set_fields(table, profileid, record_id, [(link["column"], "intValue", str(result.level))])
        log.log(f"    [comp]   {title.name} level {result.level} -> {target.name} {link['table']}.{link['column']}")

    def _log_player(self, match_id, profileid, block, result, catalog=None) -> None:
        catalog = catalog or stats.PREJUDICE
        reported, awards = [], []
        for key, column, value in result.match_values:
            s = catalog.by_key.get(key)
            if s is not None and s.fold == "date":
                continue
            reported.append(f"{catalog.describe(column)}={value}")
            if s is not None and s.family == "badge" and s.item is not None and s.item in catalog.badges:
                awards.append(f"badge {catalog.badges[s.item][0]} x{value}")
            elif (
                s is not None
                and s.family == "feat"
                and s.item is not None
                and s.item in catalog.feats
                and column.startswith("Ranked_Feats_F")
            ):
                awards.append(f"feat {catalog.feats[s.item][0]} x{value}")
        xp = result.updates.get(catalog.xp_column, (None, "unchanged"))[1]
        log.log(
            f"    [comp] match {match_id} pid={profileid} (slot {block.index}): +{result.xp_gained} XP "
            f"-> {xp}, level {result.level} {catalog.rank_for_level(result.level)}, {len(result.updates)} career fields"
        )
        log.log(f"    [comp]   reported: {'; '.join(reported)}")
        if awards:
            log.log(f"    [comp]   awards: {', '.join(awards)}")
        for note in result.notes:
            log.log(f"    [comp]   note: {note}")

    def _save_report(self, gameid: str, csid: str, owner: int, report: bytes, raw: bytes = b"") -> None:
        try:
            os.makedirs(self._report_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            # gameid and csid come from the network, so the file name keeps only their digits.
            gameid, csid = (re.sub(r"\D", "", v or "") for v in (gameid, csid))
            base = os.path.join(self._report_dir, f"report_g{gameid or 'x'}_s{csid or 'x'}_p{owner or 'x'}_{ts}")
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
