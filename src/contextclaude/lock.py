import msvcrt
from pathlib import Path

from . import config


def acquire(path=None):
    """Trava exclusiva. Usa config.lock_path() se path for None. Devolve o handle ou None se já travado."""
    p = config.lock_path() if path is None else Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "a+b")
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        return f
    except OSError:
        f.close()
        return None


def release(f) -> None:
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        f.close()
