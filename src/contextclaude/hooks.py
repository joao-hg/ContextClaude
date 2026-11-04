import os
import subprocess
import sys
from pathlib import Path

from . import config, db, notes
from .logutil import log
from .project import project_name
from .redact import redact

MARKER = ".contextclaude"
RECALL_HEADER = "Memória do projeto (ContextClaude, resumo local de sessões anteriores):\n"
HOOK_DB_TIMEOUT = 2  # o hook tem timeout de 5 s no settings.json


def _spawn_worker() -> None:
    if os.environ.get("CONTEXTCLAUDE_NO_SPAWN"):
        return
    worker = Path(__file__).resolve().parents[1] / "worker.py"
    flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
    subprocess.Popen(
        [sys.executable, str(worker)], creationflags=flags, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _enqueue(payload: dict) -> None:
    sid = payload.get("session_id")
    if not sid:
        return
    conn = db.connect(timeout=HOOK_DB_TIMEOUT)
    try:
        db.enqueue(conn, sid, payload.get("cwd") or "", payload.get("transcript_path") or "")
    finally:
        conn.close()


def _recall(cwd: str) -> str:
    if not cwd or not os.path.isfile(os.path.join(cwd, MARKER)):
        return ""
    conn = db.connect(timeout=HOOK_DB_TIMEOUT)
    try:
        summary = notes.read_summary(project_name(conn, cwd))
    finally:
        conn.close()
    if summary and summary.strip():
        return RECALL_HEADER + summary.strip()[: config.RECALL_MAX_CHARS]
    return ""


PROMPT_HEADER = "Memória relevante (ContextClaude):\n"


def prompt_submit(payload: dict, search=None) -> str:
    """Recall por prompt, só em projetos com marcador. Qualquer falha devolve ''."""
    try:
        cwd = payload.get("cwd") or ""
        sid = payload.get("session_id") or ""
        if not sid or not cwd or not os.path.isfile(os.path.join(cwd, MARKER)):
            return ""
        prompt = redact((payload.get("prompt") or "")[: config.PROMPT_INPUT_MAX]).strip()
        if len(prompt) < config.PROMPT_MIN_CHARS:
            return ""
        if search is None:
            from . import ollama, vectors
            if not ollama.is_up():  # falha rápida: o connect recusado no Windows demora
                return ""
            search = vectors.search
        conn = db.connect(timeout=HOOK_DB_TIMEOUT)
        try:
            hits = search(prompt, projeto=project_name(conn, cwd), n=config.PROMPT_N,
                          timeout=config.PROMPT_EMBED_TIMEOUT)
            active = db.active_fact_ids(conn, [h["id"] for h in hits])
            seen = {r[0] for r in conn.execute("SELECT fact_id FROM injected WHERE session_id=?", (sid,))}
            out, used, texts = PROMPT_HEADER, [], set()
            for h in hits:
                if h["id"] not in active or h["distance"] > config.PROMPT_MAX_DISTANCE or h["id"] in seen:
                    continue
                key = " ".join(h["text"].lower().split())
                if key in texts:  # fatos duplicados (reextração após compactação) não repetem
                    continue
                texts.add(key)
                line = f"- [{h['tipo']} {h['data'][:10]}] {h['text']}\n"
                room = config.PROMPT_MAX_CHARS - len(out)
                if len(line) > room:
                    if used or room < 20:
                        break
                    line = line[:room]
                out += line
                used.append(h["id"])
            if not used:
                return ""
            conn.executemany("INSERT OR IGNORE INTO injected(session_id, fact_id) VALUES(?,?)",
                             [(sid, i) for i in used])
            conn.commit()
            return out.rstrip("\n")
        finally:
            conn.close()
    except Exception as e:
        try:
            log(f"prompt falhou: {e!r}")
        except Exception:
            pass
        return ""


def session_start(payload: dict, spawn=_spawn_worker) -> str:
    recall = ""
    try:
        _enqueue(payload)
    except Exception as e:
        log(f"enqueue falhou no start: {e!r}")
    try:
        recall = _recall(payload.get("cwd") or "")
    except Exception as e:
        log(f"recall falhou: {e!r}")
    spawn()
    return recall


def session_end(payload: dict, spawn=_spawn_worker) -> None:
    try:
        _enqueue(payload)
    except Exception as e:
        log(f"enqueue falhou no end: {e!r}")
    spawn()
