"""Decoder for Section 8's GameSpy SC (ATLAS) match-report blob - the binary appended to a
`competition/SubmitReport` after the `application/bin\\0` marker. This is the ONLY place ranked stats
enter the backend: the game never writes PlayerStats_v6 itself (it only reads it), so the host's
authoritative report is the source of truth for every player's cumulative stats.

Wire format (big-endian), reverse-engineered from S9-Win32-F.exe's SC serializer:

  header  0x00  u32 = 2
          0x04  u32 = 6            (SC protocol version)
          0x08  16-byte per-report GUID
          0x18  u32 = 0
          0x1c  u32 = 1
          0x20  u16 nplayers
          0x24  u32 total key count
          0x3c  u32 stat-stream byte length
  players 0x44  nplayers x 20 bytes: { profileid u32, 0, 8-byte session handle, 0 }
  stream        a flat run of typed key/value entries, grouped into one block per player (in the
                same order as the player records). Each entry is:
                    keyid  u16
                    type   u16   ( 0=int32(4B)  4=float32(4B)  5=int64(8B)  3=string )
                    value  by type
                A type-5 int64 whose value is a Windows FILETIME is a per-datum "record time"
                (all such values in a player's block are that player's report timestamp); the
                block's FILETIME differs between players, which is what separates the blocks.
"""
import struct

_TW = {0x0000: 4, 0x0004: 4, 0x0005: 8}          # value width by wire type
_FT_LO, _FT_HI = 0x01D0000000000000, 0x01E0000000000000   # plausible 2020s FILETIME window


def _u16(b, o): return struct.unpack_from(">H", b, o)[0]
def _u32(b, o): return struct.unpack_from(">I", b, o)[0]
def _i32(b, o): return struct.unpack_from(">i", b, o)[0]
def _i64(b, o): return struct.unpack_from(">q", b, o)[0]


def _is_filetime(value: int) -> bool:
    return _FT_LO < (value & 0xFFFFFFFFFFFFFFFF) < _FT_HI


class PlayerReport:
    __slots__ = ("profileid", "filetime", "values")

    def __init__(self, profileid):
        self.profileid = profileid
        self.filetime = 0                 # this player's report FILETIME (raw ticks), 0 if none
        self.values = {}                  # keyid -> int32 value (the actual stats)


def raw_entries(blob: bytes):
    """Every typed entry in wire order as (offset, keyid, kind, value), kind in {"i32","f32","ft"}.
    Attribution-independent view of the whole stat stream - used to log a report without trusting the
    per-player block split (which the FILETIME heuristic can get wrong)."""
    out = []
    if len(blob) < 0x44 or _u32(blob, 0) != 2:
        return out
    nplayers = _u16(blob, 0x20)
    o, n = 0x44 + nplayers * 20, len(blob)
    while o + 4 <= n:
        keyid = _u16(blob, o)
        typ = _u16(blob, o + 2)
        w = _TW.get(typ)
        if w is None or keyid == 0 or o + 4 + w > n:
            o += 2
            continue
        if typ == 0x0005:
            raw = _i64(blob, o + 4)
            out.append((o, keyid, "ft" if _is_filetime(raw) else "i64", raw))
        elif typ == 0x0004:
            out.append((o, keyid, "f32", struct.unpack_from(">f", blob, o + 4)[0]))
        else:
            out.append((o, keyid, "i32", _i32(blob, o + 4)))
        o += 4 + w
    return out


def parse(blob: bytes):
    """Decode a SubmitReport blob into a list of PlayerReport. Empty/close reports (0 keys) yield []."""
    if len(blob) < 0x44 or _u32(blob, 0) != 2:
        return []
    nplayers = _u16(blob, 0x20)
    total_keys = _u32(blob, 0x24)
    if nplayers <= 0 or total_keys == 0:
        return []
    profileids = [_u32(blob, 0x44 + i * 20) for i in range(nplayers)]

    # Split the flat stream into one block per player. Each player's block is the SAME fixed keyid
    # template emitted once, so the first keyid that repeats within the current block marks the start of
    # the next player's block. This is robust where the earlier FILETIME-split was not: the per-datum
    # FILETIMEs differ *within* a block (verified against in-game ground truth - a player's XP/kills were
    # being scattered across two blocks), so a boundary can't be inferred from the timestamps.
    blocks, cur, seen = [], [], set()
    for off, keyid, kind, val in raw_entries(blob):
        if keyid in seen:
            blocks.append(cur)
            cur, seen = [], set()
        cur.append((keyid, kind, val))
        seen.add(keyid)
    if cur:
        blocks.append(cur)

    reports = []
    for idx, profileid in enumerate(profileids):
        pr = PlayerReport(profileid)
        if idx < len(blocks):
            for keyid, kind, val in blocks[idx]:
                if kind == "ft":                 # timestamps are metadata; keep the latest as the block time
                    if val > pr.filetime:
                        pr.filetime = val
                elif kind == "i32":
                    pr.values[keyid] = val
        reports.append(pr)
    return reports
