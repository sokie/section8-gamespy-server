import struct

HEADER_SIZE = 0x44
KEY_INT32, KEY_INT16, KEY_BYTE, KEY_STRING, KEY_FLOAT, KEY_INT64 = range(6)


class ReportError(ValueError):
    pass


class PlayerBlock:
    __slots__ = ("index", "connection_id", "team", "result", "keys")

    def __init__(self, index, connection_id, team, result):
        self.index = index
        self.connection_id: bytes = connection_id
        self.team = team
        self.result = result
        self.keys: dict[int, tuple[int, object]] = {}

    def connection_dword(self) -> int:
        return int(struct.unpack_from(">I", self.connection_id, 0)[0])


class Report:
    __slots__ = ("protocol", "version", "checksum", "status", "flags", "players", "game_keys", "team_keys")

    def __init__(self):
        self.protocol = 0
        self.version = 0
        self.checksum = b""
        self.status = 0
        self.flags = 0
        self.players: list[PlayerBlock] = []
        self.game_keys: dict = {}
        self.team_keys: list[dict] = []


def _length_prefix(b: bytes, o: int):
    value = shift = 0
    while True:
        if o >= len(b):
            raise ReportError("string length runs past the end of the report")
        byte = b[o]
        o += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, o


def _read_keys(b: bytes, o: int, count: int):
    keys = {}
    for _ in range(count):
        key, typ = struct.unpack_from(">HH", b, o)
        o += 4
        if typ == KEY_INT32:
            value = struct.unpack_from(">i", b, o)[0]
            o += 4
        elif typ == KEY_INT16:
            value = struct.unpack_from(">h", b, o)[0]
            o += 2
        elif typ == KEY_BYTE:
            value = b[o]
            o += 1
        elif typ == KEY_STRING:
            n, o = _length_prefix(b, o)
            value = b[o : o + n].decode("utf-8", "replace")
            o += n
        elif typ == KEY_FLOAT:
            value = struct.unpack_from(">f", b, o)[0]
            o += 4
        elif typ == KEY_INT64:
            value = struct.unpack_from(">q", b, o)[0]
            o += 8
        else:
            raise ReportError(f"unknown key type {typ} for key {key}")
        keys[key] = (typ, value)
    return keys, o


def parse(blob: bytes) -> Report:
    """Raises ReportError for a blob that does not parse."""
    if len(blob) < HEADER_SIZE:
        raise ReportError(f"report is {len(blob)} bytes, shorter than its header")
    r = Report()
    r.protocol, r.version = struct.unpack_from(">II", blob, 0)
    r.checksum = blob[8:24]
    r.status, r.flags = struct.unpack_from(">II", blob, 0x18)
    players, teams, game_keys, _player_keys, _team_keys, _ = struct.unpack_from(">6H", blob, 0x20)
    roster_len, auth_len, results_len, game_len, player_len, team_len = struct.unpack_from(">6I", blob, 0x2C)
    # Single-player reports can end with two zero bytes after the sections, so only require the sections to fit.
    if HEADER_SIZE + roster_len + auth_len + results_len + game_len + player_len + team_len > len(blob):
        raise ReportError("section lengths run past the end of the report")
    if roster_len != 20 * players:
        raise ReportError(f"roster is {roster_len} bytes for {players} players")

    try:
        o = HEADER_SIZE
        roster = []
        for _ in range(players):
            roster.append((blob[o : o + 16], struct.unpack_from(">I", blob, o + 16)[0]))
            o += 20
        o += auth_len
        results = list(struct.unpack_from(f">{results_len // 4}I", blob, o))
        o += results_len

        end = o + game_len
        r.game_keys, o = _read_keys(blob, o, game_keys)
        if o != end:
            raise ReportError("game section length does not match its keys")

        end = o + player_len
        for i, (connection_id, team) in enumerate(roster):
            (count,) = struct.unpack_from(">H", blob, o)
            block = PlayerBlock(i, connection_id, team, results[i] if i < len(results) else None)
            block.keys, o = _read_keys(blob, o + 2, count)
            r.players.append(block)
        if o != end:
            raise ReportError("player section length does not match its keys")

        for _ in range(teams):
            (count,) = struct.unpack_from(">H", blob, o)
            keys, o = _read_keys(blob, o + 2, count)
            r.team_keys.append(keys)
    except struct.error as e:
        raise ReportError(f"report is truncated: {e}") from e
    return r
