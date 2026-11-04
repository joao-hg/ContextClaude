import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from contextclaude import chibi_status, db, lock, ollama, pipeline, vectors  # noqa: E402
from contextclaude.logutil import log  # noqa: E402


def main() -> None:
    handle = lock.acquire()
    if handle is None:
        return
    conn = None
    try:
        try:
            conn = db.connect()
            ollama.ensure_running()
            pipeline.process_queue(conn)
        except Exception as e:
            log(f"worker falhou: {e!r}")
        try:
            if conn is not None:
                vectors.index_pending(conn)
        except Exception as e:
            log(f"indexação falhou: {e!r}")
        chibi_status.write_status("idle")
    finally:
        if conn is not None:
            conn.close()
        lock.release(handle)


if __name__ == "__main__":
    main()
