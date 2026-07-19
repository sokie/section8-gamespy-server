"""Thread-safe logging to stdout and a file; request/response bodies are logged verbatim so a capture
stays greppable."""
import threading
import time

_lock = threading.Lock()
_path = "section8_gamespy.log"


def configure(path: str, truncate: bool = True) -> None:
    global _path
    _path = path
    if truncate:
        open(_path, "w").close()


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    with _lock:
        print(line, flush=True)
        with open(_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
