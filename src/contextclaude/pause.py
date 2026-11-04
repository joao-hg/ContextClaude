"""Pausa do worker: o app grava data/pause só enquanto usa o Ollama; o pipeline espera."""
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime

from . import config

PAUSE_STALE_S = 300  # arquivo mais velho que isto é resto de um app que caiu: ignorado
PAUSE_REFRESH_S = 60  # enquanto o hold dura, o arquivo é renovado neste intervalo (< PAUSE_STALE_S)


@contextmanager
def hold():
    p = config.pause_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(datetime.now().isoformat(), encoding="utf-8")
    except OSError:
        pass
    stop = threading.Event()

    def refresh():
        while not stop.wait(PAUSE_REFRESH_S):
            try:
                os.utime(p, None)
            except OSError:
                pass

    t = threading.Thread(target=refresh, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=2)  # antes do unlink: o refresher não pode recriar/tocar depois
        try:
            p.unlink()
        except OSError:
            pass


def is_paused(now=time.time) -> bool:
    try:
        return now() - config.pause_path().stat().st_mtime < PAUSE_STALE_S
    except OSError:
        return False


def wait(sleep=time.sleep, now=time.time, poll_s: float = 1.0) -> float:
    waited = 0.0
    while is_paused(now):
        sleep(poll_s)
        waited += poll_s
    return waited
