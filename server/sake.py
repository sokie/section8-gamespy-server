import xml.etree.ElementTree as ET

from . import log, stats
from .codecs import profileid_from_ticket
from .sakefilter import FilterError, compile_filter, referenced_fields

SAKE_NS = "http://gamespy.net/sake"

VALUE_TYPES = (
    "byteValue",
    "shortValue",
    "intValue",
    "int64Value",
    "floatValue",
    "asciiStringValue",
    "unicodeStringValue",
    "booleanValue",
    "dateAndTimeValue",
    "binaryDataValue",
)
VIRTUAL_FIELDS = ("ownerid", "recordid", "row")
NUMERIC_TYPES = ("byteValue", "shortValue", "intValue", "int64Value", "floatValue")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_text(elem, name, default=""):
    for node in elem.iter():
        if _local(node.tag) == name:
            return (node.text or "").strip()
    return default


def _infer_type(field: str) -> str:
    """Gives the Sake type of a field that the client reads before any write."""
    known = stats.sake_type(field)
    if known:
        return known
    if field.endswith("_Date") or "_Date_" in field:
        return "dateAndTimeValue"
    if "ClanTag" in field or "Name" in field or "Gamertag" in field:
        return "asciiStringValue"
    return "intValue"


def _default_value(value_type: str) -> str:
    if value_type == "dateAndTimeValue":
        return stats.SAKE_DATE_ZERO
    if value_type in ("asciiStringValue", "unicodeStringValue", "binaryDataValue"):
        return ""
    if value_type == "booleanValue":
        return "false"
    return "0"


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _envelope(inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
        f"<soap:Body>{inner}</soap:Body></soap:Envelope>"
    )


def _record_value_xml(value_type: str, value: str) -> str:
    return f"<RecordValue><{value_type}><value>{_xml_escape(str(value))}</value></{value_type}></RecordValue>"


def _ints(root, container: str) -> list[int]:
    for node in root.iter():
        if _local(node.tag) == container:
            return [int(t) for t in ((n.text or "").strip() for n in node.iter() if _local(n.tag) == "int") if t]
    return []


def _parse_sort(sort_raw: str) -> list[tuple[str, bool]]:
    keys = []
    for part in (sort_raw or "").split(","):
        bits = part.split()
        if bits:
            keys.append((bits[0], len(bits) > 1 and bits[1].lower() == "desc"))
    return keys


def _sort_value(value, numeric: bool):
    if value is None:
        return (0, 0.0) if numeric else (0, "")
    try:
        return (1, float(value))
    except (TypeError, ValueError):
        return (1, str(value).lower())


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
                if lt in (
                    "SearchForRecords",
                    "CreateRecord",
                    "UpdateRecord",
                    "DeleteRecord",
                    "GetMyRecords",
                    "GetSpecificRecords",
                    "GetRandomRecord",
                    "GetRecordCount",
                    "GetRecordLimit",
                    "RateRecord",
                ):
                    op = lt
                    break
        if not op and soap_action:
            op = soap_action.rsplit("/", 1)[-1].strip('"')
        log.log(f"    [sake] op={op or '(unknown)'}")

        handler = getattr(self, f"_op_{op}", None)
        if handler is None:
            # Answer an unknown call with Success, because the SDK ends the session on any other answer.
            log.log(
                f"    [sake] *** UNHANDLED OP *** {op or '(unknown)'} (returning bare Success; see full request above)"
            )
            return _envelope(f'<{op}Response xmlns="{SAKE_NS}"><{op}Result>Success</{op}Result></{op}Response>')
        return handler(root)

    def _op_SearchForRecords(self, root) -> str:
        table = _find_text(root, "tableid", "PlayerStats_v6")
        sort_raw = _find_text(root, "sort")
        filter_raw = _find_text(root, "filter")
        target_raw = _find_text(root, "targetRecordFilter")
        offset = int(_find_text(root, "offset", "0") or 0)
        max_rows = int(_find_text(root, "max", "1") or 1)
        surrounding = int(_find_text(root, "surrounding", "0") or 0)
        fields = [_local_text(n) for n in root.iter() if _local(n.tag) == "string"]
        owner_ids = _ints(root, "ownerids")

        if self._news is not None and table.lower().startswith("newsstats"):
            rows_xml = self._news_row_xml(fields)
            log.log(
                f"    [sake] SearchForRecords table={table} -> news row "
                f"(Settings_FileID={self._news.file_id()} recordid={self._news.version()})"
            )
            return self._search_response(rows_xml)

        try:
            found = self.search(table, sort_raw, filter_raw, owner_ids, target_raw, surrounding, offset, max_rows)
        except FilterError as e:
            log.log(f"    [sake] SearchForRecords table={table}: unreadable filter ({e}); returning no records")
            return self._search_response("")
        if found:
            rows_xml = "".join(self._row_xml(table, own, rid, fields, row) for own, rid, row in found)
            n = f"{len(found)} record(s)"
        elif owner_ids or target_raw:
            # A player who reads their own missing record gets a zeroed row.
            owner = owner_ids[0] if owner_ids else profileid_from_ticket(_find_text(root, "loginTicket"))
            rows_xml = self._synthetic_row_xml(fields, owner)
            n = "0 stored -> 1 synthetic (zeroed)"
        else:
            rows_xml, n = "", "0 records"
        log.log(
            f"    [sake] SearchForRecords table={table} sort={sort_raw!r} filter={filter_raw!r} "
            f"target={target_raw!r} offset={offset} max={max_rows} owners={owner_ids or '-'} -> {n}"
        )
        return self._search_response(rows_xml)

    def search(
        self, table, sort_raw="", filter_raw="", owner_ids=(), target_raw="", surrounding=0, offset=0, max_rows=1
    ):
        """Returns (owner_id, record_id, row) per match in the requested order. Raises FilterError."""
        sort = _parse_sort(sort_raw)
        predicate = compile_filter(filter_raw)
        target = compile_filter(target_raw)
        wanted = {f for f, _ in sort} | referenced_fields(filter_raw) | referenced_fields(target_raw)
        records = self._store.all_records(table, [f for f in wanted if f.lower() not in VIRTUAL_FIELDS])

        selected = []
        for owner_id, record_id, values in records:
            if owner_ids and owner_id not in owner_ids:
                continue
            get = _getter(owner_id, record_id, values)
            if predicate is not None and not predicate(get):
                continue
            selected.append((owner_id, record_id, get))

        # Stable sorts from the last key to the first give a multi-key sort.
        selected.sort(key=lambda r: (r[0], r[1]))
        for name, descending in reversed(sort):
            numeric = _infer_type(name) in NUMERIC_TYPES
            selected.sort(key=lambda r, n=name, num=numeric: _sort_value(r[2](n), num), reverse=descending)

        ranked = [(owner_id, record_id, i + 1) for i, (owner_id, record_id, _get) in enumerate(selected)]
        if target is not None:
            at = next((i for i, r in enumerate(selected) if target(r[2])), None)
            if at is None:
                return []
            return ranked[max(0, at - surrounding) : at + surrounding + 1]
        return ranked[offset : offset + max(0, max_rows)]

    def _search_response(self, rows_xml: str) -> str:
        values = f"<values>{rows_xml}</values>" if rows_xml else "<values/>"
        return _envelope(
            f'<SearchForRecordsResponse xmlns="{SAKE_NS}">'
            f"<SearchForRecordsResult>Success</SearchForRecordsResult>{values}"
            f"</SearchForRecordsResponse>"
        )

    def _op_GetMyRecords(self, root) -> str:
        table = _find_text(root, "tableid", "PlayerStats_v6")
        owner = profileid_from_ticket(_find_text(root, "loginTicket")) or int(_find_text(root, "ownerid", "0") or 0)
        fields = [_local_text(n) for n in root.iter() if _local(n.tag) == "string"]
        rid = self._store.record_id_for_owner(table, owner)
        rows_xml = self._row_xml(table, owner, rid, fields) if rid is not None else ""
        values = f"<values>{rows_xml}</values>" if rows_xml else "<values/>"
        return _envelope(
            f'<GetMyRecordsResponse xmlns="{SAKE_NS}">'
            f"<GetMyRecordsResult>Success</GetMyRecordsResult>{values}"
            f"</GetMyRecordsResponse>"
        )

    def _op_GetSpecificRecords(self, root) -> str:
        # Record ids restart per owner, so a bare record id names one of the requester's own records.
        table = _find_text(root, "tableid", "PlayerStats_v6")
        owner = profileid_from_ticket(_find_text(root, "loginTicket"))
        fields = [_local_text(n) for n in root.iter() if _local(n.tag) == "string"]
        own = {rid for o, rid, _ in self._store.all_records(table, []) if o == owner}
        rows_xml = "".join(self._row_xml(table, owner, rid, fields) for rid in _ints(root, "recordids") if rid in own)
        values = f"<values>{rows_xml}</values>" if rows_xml else "<values/>"
        return _envelope(
            f'<GetSpecificRecordsResponse xmlns="{SAKE_NS}">'
            f"<GetSpecificRecordsResult>Success</GetSpecificRecordsResult>{values}"
            f"</GetSpecificRecordsResponse>"
        )

    def _op_GetRecordCount(self, root) -> str:
        table = _find_text(root, "tableid", "PlayerStats_v6")
        filter_raw = _find_text(root, "filter")
        try:
            count = len(self.search(table, filter_raw=filter_raw, max_rows=1 << 30))
        except FilterError as e:
            log.log(f"    [sake] GetRecordCount table={table}: unreadable filter ({e})")
            count = 0
        return _envelope(
            f'<GetRecordCountResponse xmlns="{SAKE_NS}">'
            f"<GetRecordCountResult>Success</GetRecordCountResult><count>{count}</count>"
            f"</GetRecordCountResponse>"
        )

    def _op_GetRecordLimit(self, root) -> str:
        return _envelope(
            f'<GetRecordLimitResponse xmlns="{SAKE_NS}">'
            f"<GetRecordLimitResult>Success</GetRecordLimitResult>"
            f"</GetRecordLimitResponse>"
        )

    def _op_CreateRecord(self, root) -> str:
        table = _find_text(root, "tableid", "PlayerStats_v6")
        # CreateRecord carries no ownerid, so take the owner from the loginTicket.
        owner = profileid_from_ticket(_find_text(root, "loginTicket"))
        # A profile has one record per table, so reuse it.
        rid = self._store.record_for_owner(table, owner)
        self._apply_writes(table, owner, rid, root)
        log.log(f"    [sake] CreateRecord table={table} owner={owner} -> recordid={rid}")
        return _envelope(
            f'<CreateRecordResponse xmlns="{SAKE_NS}">'
            f"<CreateRecordResult>Success</CreateRecordResult><recordid>{rid}</recordid>"
            f"</CreateRecordResponse>"
        )

    def _op_UpdateRecord(self, root) -> str:
        table = _find_text(root, "tableid", "PlayerStats_v6")
        rid = int(_find_text(root, "recordid", "0") or 0)
        # A dedicated server updates the synthetic recordid and never calls CreateRecord, so bind the row here.
        owner = profileid_from_ticket(_find_text(root, "loginTicket"))
        if not owner and rid:
            # Without a login ticket, take the owner from the recordid, not owner 0.
            owner = self._store.owner_for_record(table, rid) or 0
        if rid and owner:
            self._store.ensure_record(table, owner, rid)
        self._apply_writes(table, owner, rid, root)
        log.log(f"    [sake] UpdateRecord table={table} recordid={rid}")
        return _envelope(
            f'<UpdateRecordResponse xmlns="{SAKE_NS}">'
            f"<UpdateRecordResult>Success</UpdateRecordResult>"
            f"</UpdateRecordResponse>"
        )

    def _apply_writes(self, table, owner, record_id, root):
        """Stores each RecordField with the Sake type the client sent."""
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
            preview = ", ".join(f"{n}={v}:{t.replace('Value', '')}" for n, t, v in writes)
            log.log(f"    [sake] WROTE table={table} recordid={record_id} {len(writes)} field(s): {preview}")

    def _news_row_xml(self, fields) -> str:
        """recordid is the news version. The game compares it with its cached NewsVersion."""
        cells = []
        for field in fields:
            key = field.lower()
            # The XLAST column is Settings_FileID, but Prejudice asks for News_Settings_FileID.
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

    def _row_xml(self, table, owner, record_id, fields, row=1) -> str:
        if record_id is None:
            return ""
        stored = self._store.get_fields(table, owner, record_id)
        cells = []
        for field in fields:
            if field in stored:
                vtype, value = stored[field]
            elif field == "ownerid":
                vtype, value = "intValue", str(owner or 0)
            elif field == "recordid":
                vtype, value = "intValue", str(record_id)
            elif field == "row":
                vtype, value = "intValue", str(row)
            else:
                vtype = _infer_type(field)
                value = _default_value(vtype)
            cells.append(_record_value_xml(vtype, value))
        return f"<ArrayOfRecordValue>{''.join(cells)}</ArrayOfRecordValue>"


def _getter(owner_id, record_id, values):
    def get(name):
        low = name.lower()
        if low == "ownerid":
            return owner_id
        if low == "recordid":
            return record_id
        v = values.get(name)
        return v[1] if v else None

    return get


def _local_text(node) -> str:
    return (node.text or "").strip()
