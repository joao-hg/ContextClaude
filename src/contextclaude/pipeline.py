import os
import sys
import urllib.error
from datetime import datetime

from . import chibi_status, db, graph, notes, ollama, vectors
from .logutil import log
from .project import project_name
from .redact import redact, redact_deep
from .transcript import read_new

CHUNK_CHARS = 6000
MAX_ATTEMPTS = 5
SUMMARY_MAX = 1400


def _chunks(lines: list[str]) -> list[list[str]]:
    out, cur, size = [], [], 0
    for l in lines:
        if cur and size + len(l) > CHUNK_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(l)
        size += len(l)
    if cur:
        out.append(cur)
    return out


def _aggregate(parts: list[dict]) -> dict:
    agg = {"fatos": [], "decisoes": [], "pendencias": [], "relacoes": [], "resumo": ""}
    for p in parts:
        for k in ("fatos", "decisoes", "pendencias", "relacoes"):
            for item in p[k]:
                if item not in agg[k]:
                    agg[k].append(item)
    agg["resumo"] = " ".join(p["resumo"] for p in parts if p["resumo"])
    return agg


EMPTY_EXTRACTION = {"fatos": [], "decisoes": [], "pendencias": [], "relacoes": [], "resumo": ""}


def _extract_resilient(lines: list[str], extract, sid: str = "") -> dict:
    """Em caso de JSON truncado (saída do modelo maior que num_predict), reparte o
    bloco pela metade (por linha, depois por caractere, para uma única linha gigante)
    até conseguir extrair ou descartar só o trecho problemático, sem perder a sessão."""
    try:
        return extract(lines)
    except ollama.ExtractionError as e:
        if len(lines) > 1:
            mid = len(lines) // 2
            return _aggregate([
                _extract_resilient(lines[:mid], extract, sid),
                _extract_resilient(lines[mid:], extract, sid),
            ])
        text = lines[0] if lines else ""
        if len(text) > 200:
            mid = len(text) // 2
            return _aggregate([
                _extract_resilient([text[:mid]], extract, sid),
                _extract_resilient([text[mid:]], extract, sid),
            ])
        log(f"trecho descartado (não extraído, sid={sid}): {e!r}")
        return EMPTY_EXTRACTION


def _is_down(e: Exception) -> bool:
    """Ollama fora do ar (conexão recusada etc.). HTTPError e timeout com o servidor no ar não contam."""
    if isinstance(e, urllib.error.HTTPError):
        return False
    return isinstance(e, (ConnectionError, urllib.error.URLError))


def _pending(conn, skip: set):
    rows = conn.execute(
        "SELECT * FROM sessions WHERE status IN ('queued','done') AND attempts < ?", (MAX_ATTEMPTS,)
    ).fetchall()
    for row in rows:
        if row["session_id"] in skip:
            continue
        if row["status"] == "queued":
            yield row
        else:  # sessão retomada ou fechada sem processar o final
            path = row["transcript_path"]
            if path and os.path.isfile(path) and os.path.getsize(path) != row["offset"]:
                yield row


def _pending_bytes(row) -> int:
    """Bytes pendentes de uma sessão, para a estimativa de abertura de rodada."""
    path = row["transcript_path"]
    if not path or not os.path.isfile(path):
        return 0
    size = os.path.getsize(path)
    offset = row["offset"]
    if offset <= size:
        return size - offset
    return size  # arquivo reiniciado (offset além do tamanho atual)


def _find_conflicting_decision(project: str, text: str, search=vectors.search,
                                active=None, threshold: float = 0.15) -> int | None:
    try:
        hits = search(text, projeto=project, n=3, kind="decisao")
        candidates = [h for h in hits if h["distance"] <= threshold]
        if not candidates:
            return None
        live = active([h["id"] for h in candidates]) if active else {h["id"] for h in candidates}
        for h in candidates:
            if h["id"] in live:
                return h["id"]
        return None
    except Exception as e:
        log(f"busca de conflito de decisão falhou ({project!r}): {e!r}")
        return None


def _save(conn, row, project, data, stamp, find_conflict=None):
    find_conflict = find_conflict or _find_conflicting_decision
    for kind, key in (("fato", "fatos"), ("pendencia", "pendencias")):
        for text in data[key]:
            if isinstance(text, str) and text.strip() and not db.fact_exists(conn, project, kind, text):
                conn.execute(
                    "INSERT INTO facts(session_id, project, kind, text, created_at) VALUES(?,?,?,?,?)",
                    (row["session_id"], project, kind, text, stamp),
                )
    for text in data["decisoes"]:
        if isinstance(text, str) and text.strip() and not db.fact_exists(conn, project, "decisao", text, status="confirmada"):
            conflict_id = find_conflict(project, text)
            if conflict_id is not None:
                conn.execute("UPDATE facts SET status='substituida' WHERE id=?", (conflict_id,))
            conn.execute(
                "INSERT INTO facts(session_id, project, kind, text, status, supersedes, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (row["session_id"], project, "decisao", text, "confirmada", conflict_id, stamp),
            )
    for r in data["relacoes"]:
        if isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("origem", "relacao", "destino")):
            conn.execute(
                "INSERT INTO relations(session_id, project, source, relation, target) VALUES(?,?,?,?,?)",
                (row["session_id"], project, r["origem"], r["relacao"], r["destino"]),
            )


def _resolve_conflicts(conn, project: str, decisoes: list) -> dict:
    """Resolve, para cada decisão nova, o fato ativo que ela substitui — ANTES de abrir
    a transação de escrita. `vectors.search` pode demorar (chamada ao Ollama); rodar aqui
    mantém a janela do write lock do SQLite restrita aos INSERTs/UPDATEs rápidos de `_save`."""
    out = {}
    for text in decisoes:
        if isinstance(text, str) and text.strip() and not db.fact_exists(conn, project, "decisao", text, status="confirmada"):
            out[text] = _find_conflicting_decision(
                project, text, active=lambda ids: db.active_fact_ids(conn, ids)
            )
    return out


def _compute_summary(project: str, data: dict, merge, sid: str) -> str:
    """Calcula o resumo mesclado (chamada lenta ao Ollama) ANTES de abrir a transação de
    escrita — mesmo motivo de `_resolve_conflicts`. Substitui a antiga `_update_summary`,
    que fazia essa mesma chamada DEPOIS do primeiro INSERT de `_save`."""
    old = redact(notes.read_summary(project) or "")
    chibi_status.write_status("pensando", sid)
    try:
        return redact(merge(old, data))
    except Exception as e:
        log(f"resumo não atualizado ({sid}): {e!r}")
        return (old + "\n- " + data["resumo"]).strip()[-SUMMARY_MAX:] if data["resumo"] else old


def _process_one(conn, row, extract, merge, now) -> dict:
    """Devolve {"result": "done"|"idle"|"down", "chars": int, "fatos": int,
    "decisoes": int, "pendencias": int}."""
    sid = row["session_id"]
    project = project_name(conn, row["cwd"] or "")
    offset = row["offset"]
    path = row["transcript_path"]
    if path and os.path.isfile(path) and os.path.getsize(path) < offset:
        offset = 0  # transcrição recomeçou (retomada/compactação)
    lines, new_off = read_new(path, offset)
    when = now()
    stamp = when.isoformat(timespec="seconds")
    conflicts = summary = None
    if lines:
        chibi_status.write_status("pensando", sid)
        try:
            data = redact_deep(_aggregate([_extract_resilient(c, extract, sid) for c in _chunks([redact(l) for l in lines])]))
        except Exception as e:
            if _is_down(e):
                log(f"ollama indisponível ({sid}): {e}")
                chibi_status.write_status("notificacoes", sid)
                return {"result": "down", "chars": 0, "fatos": 0, "decisoes": 0, "pendencias": 0}
            raise
        chibi_status.write_status("processando", sid)
        conflicts = _resolve_conflicts(conn, project, data["decisoes"])
        summary = _compute_summary(project, data, merge, sid)
        chibi_status.write_status("processando", sid)
        _save(conn, row, project, data, stamp, find_conflict=lambda p, t: conflicts.get(t))
    conn.execute(
        "UPDATE sessions SET status='done', offset=?, project=?, attempts=0, processed_at=? WHERE session_id=?",
        (new_off, project, stamp, sid),
    )
    if lines:
        chibi_status.write_status("digitando", sid)
        notes.write_session_note(project, sid, when, data)
        if summary:
            notes.write_summary(project, summary[:SUMMARY_MAX])
        graph.write_entities(conn, sid, project)
        chibi_status.write_status("feliz", sid)
    else:
        chibi_status.write_status("idle", sid)
    conn.commit()
    fatos, decisoes, pendencias = (
        (len(data["fatos"]), len(data["decisoes"]), len(data["pendencias"])) if lines else (0, 0, 0)
    )
    chars = new_off - offset
    return {
        "result": "done" if lines else "idle",
        "chars": chars,
        "fatos": fatos,
        "decisoes": decisoes,
        "pendencias": pendencias,
    }


def _fail(conn, row, e) -> None:
    conn.rollback()
    attempts = row["attempts"] + 1
    status = "error" if attempts >= MAX_ATTEMPTS else "queued"
    conn.execute("UPDATE sessions SET status=?, attempts=? WHERE session_id=?", (status, attempts, row["session_id"]))
    conn.commit()
    if status == "error":
        chibi_status.write_status("notificacoes", row["session_id"])
    log(f"sessão falhou ({row['session_id']}, tentativa {attempts}): {e!r}")


def process_queue(conn, extract=ollama.extract, merge=ollama.merge_summary, now=datetime.now) -> int:
    done, failed = 0, set()
    primeiro_batch = list(_pending(conn, failed))
    if not primeiro_batch:
        return 0
    inicio = now()
    chars_pendentes = sum(_pending_bytes(r) for r in primeiro_batch)
    # Sessão enfileirada sem conteúdo novo (ex.: hook SessionStart antes de
    # haver transcrição) não deve abrir uma rodada visível ao usuário: ela
    # fecharia como "feita" com 0 sessões/fatos/decisões, só ruído no toast
    # e no histórico. A fila ainda é processada normalmente abaixo.
    rodada_aberta = chars_pendentes > 0
    throughput = db.round_throughput(conn)
    estimativa_s = chars_pendentes / throughput if throughput else None
    if rodada_aberta:
        chibi_status.write_rodada_inicio(inicio.isoformat(timespec="seconds"), estimativa_s, len(primeiro_batch))
    falhas = []
    chars_total = fatos_total = decisoes_total = pendencias_total = 0
    try:
        while True:
            batch = list(_pending(conn, failed))
            if not batch:
                return done
            progressed = False
            for row in batch:
                try:
                    result = _process_one(conn, row, extract, merge, now)
                except Exception as e:
                    _fail(conn, row, e)
                    failed.add(row["session_id"])
                    status = conn.execute(
                        "SELECT status FROM sessions WHERE session_id=?", (row["session_id"],)
                    ).fetchone()["status"]
                    if status == "error":
                        falhas.append(str(e))
                    continue
                if result["result"] == "down":
                    falhas.append("Ollama fora do ar")
                    return done
                done += 1
                chars_total += result["chars"]
                fatos_total += result["fatos"]
                decisoes_total += result["decisoes"]
                pendencias_total += result["pendencias"]
                if result["result"] == "idle":
                    failed.add(row["session_id"])  # nada novo (ex.: linha parcial): não reexaminar nesta rodada
                else:
                    progressed = True
            if not progressed:
                return done
    finally:
        if rodada_aberta:
            # Uma exceção que escapou do loop (ex.: de _fail, ou da
            # re-consulta de status) ainda deve fechar a rodada como erro,
            # com a mensagem registrada, antes de continuar a propagar.
            exc = sys.exc_info()[1]
            if exc is not None:
                falhas.append(str(exc))
            fim = now()
            duration_s = (fim - inicio).total_seconds()
            status_final = "error" if falhas else "done"
            mensagem = "; ".join(falhas)[:500] if falhas else None
            chibi_status.write_rodada_fim(status_final, duration_s, done, fatos_total, decisoes_total, mensagem)
            try:
                db.insert_round(
                    conn, inicio.isoformat(timespec="seconds"), fim.isoformat(timespec="seconds"), duration_s,
                    done, chars_total, fatos_total, decisoes_total, pendencias_total, status_final, mensagem,
                )
            except Exception as insert_exc:
                # Falha ao persistir o histórico não deve mascarar a exceção
                # original (se houver) nem derrubar process_queue.
                log(f"falha ao gravar rodada em chibi_rounds: {insert_exc!r}")
