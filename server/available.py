"""Answers the GameSpy SDK's UDP availability check (GSIStartAvailableCheck).

The probe is ``09 00 00 00 00 <gamename> 00``. The answer is ``FE FD 09 <status:u32 BE>``, 0 = available.
"""

import socket
import threading

from . import log

AVAILABLE = b"\xfe\xfd\x09\x00\x00\x00\x00"


def parse_probe(data: bytes):
    """Gives the gamename of an availability probe, or None for any other packet."""
    if len(data) >= 6 and data[0] == 0x09 and data[1:5] == b"\x00\x00\x00\x00":
        return data[5:].split(b"\x00", 1)[0].decode("latin-1", "replace")
    return None


class AvailabilityResponder:
    def __init__(self, bind_address: str, port: int = 27900):
        self._addr = (bind_address, port)

    def start(self) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(self._addr)
        except OSError as e:
            log.log(f"(skip udp {self._addr[1]} availability: {e})")
            return False
        threading.Thread(target=self._serve, args=(sock,), daemon=True).start()
        log.log(f"listening {self._addr[1]}/udp (availability) on {self._addr[0]}")
        return True

    def _serve(self, sock):
        while True:
            try:
                data, peer = sock.recvfrom(2048)
            except ConnectionResetError:
                # Windows reports a closed client port on the next receive.
                continue
            except OSError:
                return
            name = parse_probe(data)
            if name is None:
                log.log(
                    f"    [avail] {len(data)}B from {peer[0]}:{peer[1]} is not an availability probe: {data[:32].hex()}"
                )
                continue
            sock.sendto(AVAILABLE, peer)
            log.log(f"    [avail] {name!r} from {peer[0]}:{peer[1]} -> available")
