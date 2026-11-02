from datetime import datetime

from . import config

MAX_BYTES = 200_000


def log(msg: str) -> None:
    try:
        p = config.log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > MAX_BYTES:
            p.replace(p.with_suffix(".old"))
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:
        pass
