"""Sake StorageServer - the stats/awards service (SOAP over HTTP, namespace http://gamespy.net/sake).

SearchForRecords must return a well-formed `SearchForRecordsResult = Success` with a (possibly empty)
`values` array: an empty/placeholder envelope the GameSpy SDK cannot parse makes the client tear the
session down and loop login->logout, so a valid Success envelope (even zero rows for a brand-new player)
is what lets the game get past login into the menu.

CreateRecord / UpdateRecord persist the player's own PlayerStats_v6 record; SearchForRecords and
GetRecordCount read it back (and rank across owners for leaderboards). Field Sake-types are learned from
the client's own UpdateRecord writes and echoed back verbatim, so we never hardcode 991 types.
"""
import xml.etree.ElementTree as ET

from . import log
from .codecs import profileid_from_ticket

SAKE_NS = "http://gamespy.net/sake"

# The typed wrappers a Sake RecordValue can carry (WSDL RecordValue choice).
VALUE_TYPES = (
    "byteValue", "shortValue", "intValue", "int64Value", "floatValue",
    "asciiStringValue", "unicodeStringValue", "booleanValue", "dateAndTimeValue", "binaryDataValue",
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_text(elem, name, default=""):
    for node in elem.iter():
        if _local(node.tag) == name:
            return (node.text or "").strip()
    return default


def _infer_type(field: str) -> str:
    """Fallback Sake type for a field the client reads before it has ever written it. Section 8's
    PlayerStats_v6 is overwhelmingly ints, with `_Date`-suffixed timestamps and a couple of strings."""
    if field.endswith("_Date") or "_Date_" in field:
        return "dateAndTimeValue"
    if "ClanTag" in field or "Locale" in field:
        return "asciiStringValue"
    return "intValue"


def _default_value(value_type: str) -> str:
    if value_type == "dateAndTimeValue":
        return "0001-01-01T00:00:00"
    if value_type in ("asciiStringValue", "unicodeStringValue"):
        return ""
    if value_type == "floatValue":
        return "0"
    if value_type == "booleanValue":
        return "false"
    return "0"


def _envelope(inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
        f'<soap:Body>{inner}</soap:Body></soap:Envelope>'
    )


def _record_value_xml(value_type: str, value: str) -> str:
    return f'<RecordValue><{value_type}><value>{value}</value></{value_type}></RecordValue>'


class SakeService:
    def __init__(self, store, news=None):
        self._store = store
        self._news = news

    def handle(self, soap_action: str, body: str) -> str:
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            log.log(f"    [sake] request parse error: {e}")
            root = None
        op = ""
        if root is not None:
            for node in root.iter():
                lt = _local(node.tag)
                if lt in ("SearchForRecords", "CreateRecord", "UpdateRecord", "DeleteRecord",
                          "GetMyRecords", "GetSpecificRecords", "GetRandomRecord",
                          "GetRecordCount", "GetRecordLimit", "RateRecord"):
                    op = lt
                    break
        if not op and soap_action:
            op = soap_action.rsplit("/", 1)[-1].strip('"')
        log.log(f"    [sake] op={op or '(unknown)'}")

        handler = getattr(self, f"_op_{op}", None)
        if handler is None:
            # Unknown op: a bare Success keeps the SDK from tearing down; refine as ops surface.
            log.log(f"    [sake] *** UNHANDLED OP *** {op or '(unknown)'} "
                    f"(returning bare Success; see full request above)")
            return _envelope(f'<{op}Response xmlns="{SAKE_NS}"><{op}Result>Success</{op}Result></{op}Response>')
        return handler(root)

    # --- reads -------------------------------------------------------------------------------------

    def _op_SearchForRecords(self, root) -> str:
        table = _find_text(root, "tableid", "PlayerStats_v6")
        sort_raw = _find_text(root, "sort")
        offset = int(_find_text(root, "offset", "0") or 0)
        max_rows = int(_find_text(root, "max", "1") or 1)
        fields = [_local_text(n) for n in root.iter() if _local(n.tag) == "string"]
        # `sort` is "Field desc" / "Field asc".
        sort_field, descending = None, True
        if sort_raw:
            bits = sort_raw.split()
            sort_field = bits[0]
            descending = (len(bits) < 2) or bits[1].lower() != "asc"
        owner_ids = [int(t) for t in (_local_text(n) for n in root.iter() if _local(n.tag) == "int") if t]
        owner_ids = owner_ids or None

        # NewsStats is a system-owned table: the game only reads it, and never writes back. It is an
        # index pointing at the real payload, which is fetched separately from SakeFileServer.
        if self._news is not None and table.lower().startswith("newsstats"):
            rows_xml = self._news_row_xml(fields)
            log.log(f"    [sake] SearchForRecords table={table} -> news row "
                    f"(Settings_FileID={self._news.file_id()} recordid={self._news.version()})")
            return _envelope(
                f'<SearchForRecordsResponse xmlns="{SAKE_NS}">'
                f'<SearchForRecordsResult>Success</SearchForRecordsResult><values>{rows_xml}</values>'
                f'</SearchForRecordsResponse>'
            )

        results = self._store.search(table, sort_field, descending, offset, max_rows, owner_ids)
        if results:
            rows_xml = "".join(self._row_xml(table, own, rid, fields) for (own, rid) in results)
            n = f"{len(results)} record(s)"
        else:
            # No stored record yet (brand-new player): return one synthetic row with every requested
            # field present and zeroed in its correct Sake type. This is the "0 stats" a fresh account
            # shows until it writes real records via CreateRecord/UpdateRecord.
            owner = profileid_from_ticket(_find_text(root, "loginTicket"))
            rows_xml = self._synthetic_row_xml(fields, owner)
            n = "0 stored -> 1 synthetic (zeroed)"
        log.log(f"    [sake] SearchForRecords table={table} sort={sort_raw!r} -> {n}")
        values = f"<values>{rows_xml}</values>" if rows_xml else "<values/>"
        return _envelope(
            f'<SearchForRecordsResponse xmlns="{SAKE_NS}">'
            f'<SearchForRecordsResult>Success</SearchForRecordsResult>{values}'
            f'</SearchForRecordsResponse>'
        )

    def _op_GetMyRecords(self, root) -> str:
        table = _find_text(root, "tableid", "PlayerStats_v6")
        owner = int(_find_text(root, "ownerid", "0") or 0)
        fields = [_local_text(n) for n in root.iter() if _local(n.tag) == "string"]
        rid = self._store.record_id_for_owner(table, owner)
        rows_xml = self._row_xml(table, owner, rid, fields) if rid is not None else ""
        values = f"<values>{rows_xml}</values>" if rows_xml else "<values/>"
        return _envelope(
            f'<GetMyRecordsResponse xmlns="{SAKE_NS}">'
            f'<GetMyRecordsResult>Success</GetMyRecordsResult>{values}'
            f'</GetMyRecordsResponse>'
        )

    def _op_GetRecordCount(self, root) -> str:
        table = _find_text(root, "tableid", "PlayerStats_v6")
        count = self._store.record_count(table)
        return _envelope(
            f'<GetRecordCountResponse xmlns="{SAKE_NS}">'
            f'<GetRecordCountResult>Success</GetRecordCountResult><count>{count}</count>'
            f'</GetRecordCountResponse>'
        )

    def _op_GetRecordLimit(self, root) -> str:
        return _envelope(
            f'<GetRecordLimitResponse xmlns="{SAKE_NS}">'
            f'<GetRecordLimitResult>Success</GetRecordLimitResult>'
            f'</GetRecordLimitResponse>'
        )

    # --- writes ------------------------------------------------------------------------------------

    def _op_CreateRecord(self, root) -> str:
        table = _find_text(root, "tableid", "PlayerStats_v6")
        # CreateRecord carries no ownerid; the record belongs to the authenticated session, which the
        # loginTicket identifies (its first 8 hex chars encode the profileid - see codecs.login_ticket_for).
        owner = profileid_from_ticket(_find_text(root, "loginTicket"))
        # Section 8 owns exactly one PlayerStats_v6 record per profile - reuse it rather than piling up rows.
        rid = self._store.record_id_for_owner(table, owner)
        if rid is None:
            rid = self._store.create_record(table, owner)
        self._apply_writes(table, owner, rid, root)
        log.log(f"    [sake] CreateRecord table={table} owner={owner} -> recordid={rid}")
        return _envelope(
            f'<CreateRecordResponse xmlns="{SAKE_NS}">'
            f'<CreateRecordResult>Success</CreateRecordResult><recordid>{rid}</recordid>'
            f'</CreateRecordResponse>'
        )

    def _op_UpdateRecord(self, root) -> str:
        table = _find_text(root, "tableid", "PlayerStats_v6")
        rid = int(_find_text(root, "recordid", "0") or 0)
        # A dedicated server publishing its own status (ServerStatusTG09_v6) goes SearchForRecords ->
        # UpdateRecord on the synthetic recordid we hand back, never CreateRecord. Records are keyed by
        # owner, so (this server, recordid) is its own row - two servers each keep their own recordid 1.
        # Bind it to the owner (from the login ticket) so a later search by ownerid returns the real status.
        owner = profileid_from_ticket(_find_text(root, "loginTicket"))
        if not owner and rid:
            # No login ticket on this update: fall back to the recordid's owner when it is unambiguous,
            # so an owner-keyed write still lands on the right row instead of a phantom owner 0.
            owner = self._store.owner_for_record(table, rid) or 0
        if rid and owner:
            self._store.ensure_record(table, owner, rid)
        self._apply_writes(table, owner, rid, root)
        log.log(f"    [sake] UpdateRecord table={table} recordid={rid}")
        return _envelope(
            f'<UpdateRecordResponse xmlns="{SAKE_NS}">'
            f'<UpdateRecordResult>Success</UpdateRecordResult>'
            f'</UpdateRecordResponse>'
        )

    # --- helpers -----------------------------------------------------------------------------------

    def _apply_writes(self, table, owner, record_id, root):
        """Parse the RecordField list of an UpdateRecord/CreateRecord and persist each typed value,
        learning the Sake type from the client's own wire representation."""
        writes = []
        for rf in root.iter():
            if _local(rf.tag) != "RecordField":
                continue
            name = _find_text(rf, "name")
            value_type, value = None, ""
            for child in rf.iter():
                if _local(child.tag) in VALUE_TYPES:
                    value_type = _local(child.tag)
                    value = _find_text(child, "value")
                    break
            if name and value_type:
                writes.append((name, value_type, value))
        if writes:
            self._store.set_fields(table, owner, record_id, writes)
            # Log every field the game writes (name=value:type) so a capture shows exactly what the
            # client persists - this is how we learn whether the game writes stats/XP/level directly.
            preview = ", ".join(f"{n}={v}:{t.replace('Value', '')}" for n, t, v in writes)
            log.log(f"    [sake] WROTE table={table} recordid={record_id} {len(writes)} field(s): {preview}")

    def _news_row_xml(self, fields) -> str:
        """The single NewsStats row. Settings_FileID names the file to fetch; recordid is the news
        version, which the game compares against its cached NewsVersion and skips the fetch when equal -
        so it must be non-zero and must change whenever the news content does."""
        cells = []
        for field in fields:
            key = field.lower()
            # The XLAST column is Settings_FileID; Prejudice asks for it as News_Settings_FileID.
            if key in ("news_settings_fileid", "settings_fileid"):
                vtype, value = "intValue", str(self._news.file_id())
            elif key in ("recordid", "row"):
                vtype, value = "intValue", str(self._news.version())
            else:
                vtype = _infer_type(field)
                value = _default_value(vtype)
            cells.append(_record_value_xml(vtype, value))
        return f"<ArrayOfRecordValue>{''.join(cells)}</ArrayOfRecordValue>"

    def _synthetic_row_xml(self, fields, owner) -> str:
        """A single row for a player with no stored record: every requested field present, zeroed in
        its correct type. ownerid carries the authenticated profileid; recordid/row are a stable 1."""
        cells = []
        for field in fields:
            if field == "ownerid":
                vtype, value = "intValue", str(owner or 0)
            elif field in ("recordid", "row"):
                vtype, value = "intValue", "1"
            else:
                vtype = _infer_type(field)
                value = _default_value(vtype)
            cells.append(_record_value_xml(vtype, value))
        return f"<ArrayOfRecordValue>{''.join(cells)}</ArrayOfRecordValue>"

    def _row_xml(self, table, owner, record_id, fields) -> str:
        if record_id is None:
            return ""
        stored = self._store.get_fields(table, owner, record_id)
        cells = []
        for field in fields:
            if field in stored:
                vtype, value = stored[field]
            elif field == "ownerid":
                vtype, value = "intValue", str(owner or 0)
            elif field in ("recordid", "row"):
                vtype, value = "intValue", str(record_id)
            else:
                vtype = _infer_type(field)
                value = _default_value(vtype)
            cells.append(_record_value_xml(vtype, value))
        return f"<ArrayOfRecordValue>{''.join(cells)}</ArrayOfRecordValue>"


def _local_text(node) -> str:
    return (node.text or "").strip()
