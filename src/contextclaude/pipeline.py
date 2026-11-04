import json
import os
import sys
import urllib.error
from datetime import datetime

from . import activity, chibi_status, db, graph, notes, ollama, pause, vectors
from .logutil import log
from .project import project_name
from .redact import redact, redact_deep
from .transcript import read_new

CHUNK_CHARS = 10000  # prosa ~2,6k tokens; texto denso (~1,5 car./token) ainda cabe em num_ctx - num_predict
MAX_ATTEMPTS = 5
SUMMARY_MAX = 1400
TIMEOUT_SPLIT_MIN = 3000  # timeout num trecho menor que isto = Ollama sobrecarregado: falha (com checkpoint)
SUMMARY_INPUT_ITEMS = 25  # teto de decisões por projeto no merge de fim de rodada
SUMMARY_INPUT_RESUMO = 3000


def _pause_wait() -> float:
    """pause.wait() que registra a pausa uma vez, só se a espera for real."""
    if pause.is_paused():
        activity.record("Pausado enquanto você usa o app")
    return pause.wait()


def _wait_app(sid: str) -> None:
    s = _pause_wait()
    if s > 0:
        log(f"worker pausado {s:.0f}s pelo app ({sid})")


def _chunks(items: list[tuple[str, int]]) -> list[list[tuple[str, int]]]:
    """Agrupa (linha, offset_final) em blocos de até CHUNK_CHARS; o offset do último item
    de cada bloco é o ponto de checkpoint."""
    out, cur, size = [], [], 0
    for item in items:
        l = item[0]
        if cur and size + len(l) > CHUNK_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += len(l)
    if cur:
        out.append(cur)
    return out


LIST_KEYS = ("fatos", "decisoes", "feitos", "relacoes")


def _aggregate(parts: list[dict]) -> dict:
    agg = {k: [] for k in LIST_KEYS}
    for p in parts:
        for k in LIST_KEYS:
            for item in p.get(k, []):
                if item not in agg[k]:
                    agg[k].append(item)
    agg["resumo"] = " ".join(p["resumo"] for p in parts if p.get("resumo"))
    agg["titulo"] = next((p["titulo"] for p in parts if p.get("titulo")), "")
    return agg


EMPTY_EXTRACTION = {"fatos": [], "decisoes": [], "feitos": [], "relacoes": [], "resumo": "", "titulo": ""}

DRAFT_RESUMOS = 12  # teto do rascunho por sessão (material ainda não consolidado pelo LLM)
DRAFT_FEITOS = 40
RECORD_FEITOS = 10
CONSOLIDATE_EVERY = 3  # sessão já com registro: consolida (1 chamada ao LLM) a cada 3 blocos novos


def _cap_resumos(resumos: list[str]) -> list[str]:
    """Mantém até DRAFT_RESUMOS itens juntando os mais antigos num só, em vez de descartá-los."""
    if len(resumos) <= DRAFT_RESUMOS:
        return resumos
    excesso = len(resumos) - DRAFT_RESUMOS + 1
    return [" ".join(resumos[:excesso])] + resumos[excesso:]


def _add_to_draft(draft: dict | None, data: dict) -> dict:
    d = draft or {"titulo": "", "resumos": [], "feitos": []}
    if not d["titulo"] and data.get("titulo"):
        d["titulo"] = data["titulo"]
    if data.get("resumo"):
        d["resumos"] = _cap_resumos(d["resumos"] + [data["resumo"]])
    for f in data.get("feitos", []):
        if isinstance(f, str) and f.strip() and f not in d["feitos"]:
            d["feitos"].append(f)
    d["feitos"] = d["feitos"][-DRAFT_FEITOS:]
    return d


def _load_json(text, default):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _session_record(conn, sid: str, project: str, consolidate) -> dict | None:
    """Registro da sessão para a nota (titulo/resumo/feitos). Com `consolidate`, funde o
    registro atual com o rascunho via LLM e grava; sem ele (rodada interrompida) ou se o LLM
    falhar, devolve uma junção simples para a nota e mantém o rascunho para a próxima rodada."""
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()
    if row is None:
        return None
    current = notes.session_record(row)
    draft = _load_json(row["draft"], None)
    if not draft:
        return current
    record = None
    if current is None:
        # 1ª consolidação de sessão já processada no formato antigo: o resumo das notas antigas
        # entra no material (essas notas são apagadas quando a nota nova é gravada)
        legado = [r for r in notes.legacy_resumos(project, sid) if r not in draft["resumos"]]
        draft = dict(draft, resumos=_cap_resumos(legado + draft["resumos"]))
    if consolidate is not None and current is not None and len(draft["resumos"]) < CONSOLIDATE_EVERY:
        consolidate = None  # pouco material novo: junção simples na nota, rascunho fica para depois
    if consolidate is not None:
        if current is None and len(draft["resumos"]) <= 1:
            # sessão curta (um bloco): o próprio rascunho já é o registro, sem outra chamada
            record = {"titulo": draft["titulo"], "resumo": " ".join(draft["resumos"]),
                      "feitos": draft["feitos"][:RECORD_FEITOS]}
        else:
            try:
                _wait_app(sid)
                chibi_status.write_status("digitando", sid)
                record = redact_deep(consolidate(current or {"titulo": "", "resumo": "", "feitos": []}, draft))
                record["feitos"] = record["feitos"][:RECORD_FEITOS]
            except Exception as e:
                log(f"registro da sessão não consolidado ({sid}): {e!r}")
    if record is not None and record.get("titulo"):
        conn.execute("UPDATE sessions SET title=?, summary=?, feitos=?, draft=NULL WHERE session_id=?",
                     (record["titulo"], record["resumo"], json.dumps(record["feitos"], ensure_ascii=False), sid))
        conn.commit()
        return record
    base = current or {"titulo": "", "resumo": "", "feitos": []}
    return {
        "titulo": base["titulo"] or draft["titulo"],
        "resumo": " ".join([base["resumo"]] + draft["resumos"]).strip(),
        "feitos": list(dict.fromkeys(base["feitos"] + draft["feitos"])),
    }


def _extract_resilient(lines: list[str], extract, sid: str = "", split_on_timeout: bool = True) -> dict:
    """Em caso de JSON truncado (saída do modelo maior que num_predict) ou timeout, reparte o
    bloco pela metade (por linha, depois por caractere, para uma única linha gigante)
    até conseguir extrair ou descartar só o trecho problemático, sem perder a sessão.
    Timeout reparte uma vez só (a geração domina o custo e não encolhe com a entrada); timeout
    de novo, ou num trecho já pequeno, propaga: o Ollama está sobrecarregado, não é o trecho."""
    try:
        return extract(lines)
    except (ollama.ExtractionError, TimeoutError) as e:
        if isinstance(e, TimeoutError):
            if not split_on_timeout or sum(len(l) for l in lines) < TIMEOUT_SPLIT_MIN:
                raise
            split_on_timeout = False
        if len(lines) > 1:
            mid = len(lines) // 2
            return _aggregate([
                _extract_resilient(lines[:mid], extract, sid, split_on_timeout),
                _extract_resilient(lines[mid:], extract, sid, split_on_timeout),
            ])
        text = lines[0] if lines else ""
        if len(text) > 200:
            mid = len(text) // 2
            return _aggregate([
                _extract_resilient([text[:mid]], extract, sid, split_on_timeout),
                _extract_resilient([text[mid:]], extract, sid, split_on_timeout),
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
    for text in data["fatos"]:
        if isinstance(text, str) and text.strip() and not db.fact_exists(conn, project, "fato", text):
            conn.execute(
                "INSERT INTO facts(session_id, project, kind, text, created_at) VALUES(?,?,?,?,?)",
                (row["session_id"], project, "fato", text, stamp),
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


def _compute_summary(project: str, data: dict, merge) -> str:
    """Mescla o resumo vivo do projeto (chamada lenta ao Ollama). Roda fora de qualquer
    transação: os fatos já foram commitados bloco a bloco."""
    old = redact(notes.read_summary(project) or "")
    try:
        return redact(merge(old, data))
    except Exception as e:
        log(f"resumo não atualizado ({project!r}): {e!r}")
        if not data["resumo"]:
            return old
        # mantém o resumo antigo inteiro; só o trecho novo é cortado
        room = SUMMARY_MAX - len(old) - 3 if old else SUMMARY_MAX
        novo = data["resumo"][-room:] if room > 0 else ""
        return (old + "\n- " + novo).strip() if novo else old


def _flush_summaries(pending: dict, merge, conn=None) -> None:
    """Uma mesclagem por projeto por rodada (antes era uma por sessão); depois regrava o
    `_contexto.md` do projeto (sem LLM)."""
    if pending:
        chibi_status.write_status("pensando")
    for project, parts in pending.items():
        _pause_wait()
        try:
            agg = _aggregate(parts)
            slim = {
                "fatos": [], "relacoes": [],
                "decisoes": agg["decisoes"][-SUMMARY_INPUT_ITEMS:],
                "resumo": agg["resumo"][-SUMMARY_INPUT_RESUMO:],
            }
            summary = _compute_summary(project, slim, merge)
            if summary:
                notes.write_summary(project, summary[:SUMMARY_MAX])
                activity.record(f"Resumo de {project} atualizado")
        except Exception as e:
            log(f"falha ao gravar resumo do projeto {project!r}: {e!r}")
        if conn is not None:
            try:
                notes.write_context(conn, project)
            except Exception as e:
                log(f"falha ao gravar contexto do projeto {project!r}: {e!r}")
    if pending:
        chibi_status.write_status("feliz")
    pending.clear()


def _counts(parts: list[dict]) -> tuple[int, int]:
    return sum(len(p["fatos"]) for p in parts), sum(len(p["decisoes"]) for p in parts)


def _process_one(conn, row, extract, now, pending_summaries: dict, consolidate=None) -> dict:
    """Processa a sessão bloco a bloco, com checkpoint: cada bloco extraído grava fatos,
    relações, páginas de entidade e offset numa transação própria. Falha no bloco N não
    descarta os anteriores; a próxima tentativa recomeça do bloco N. O resumo do projeto
    fica para o fim da rodada (`_flush_summaries`). O material de título/resumo/feitos vai
    para `sessions.draft` no mesmo checkpoint e é consolidado pelo LLM no fim da sessão.
    Devolve {"result": "done"|"idle"|"down", "chars", "fatos", "decisoes"}."""
    sid = row["session_id"]
    project = project_name(conn, row["cwd"] or "")
    offset = row["offset"]
    path = row["transcript_path"]
    if path and os.path.isfile(path) and os.path.getsize(path) < offset:
        offset = 0  # transcrição recomeçou (retomada/compactação)
    items, new_off = read_new(path, offset, with_pos=True)
    when = now()
    stamp = when.isoformat(timespec="seconds")
    if items:
        activity.record(f"Lendo sessão de {project}")
    parts: list[dict] = []
    done_off = offset
    draft = _load_json(row["draft"], None)
    finished = False  # só consolida (chamada ao LLM) se a sessão não parou por erro/queda

    def result(kind: str) -> dict:
        f, d = _counts(parts)
        return {"result": kind, "chars": done_off - offset, "fatos": f, "decisoes": d}

    try:
        for chunk in _chunks([(redact(l), pos) for l, pos in items]):
            _wait_app(sid)
            chibi_status.write_status("pensando", sid)
            try:
                data = redact_deep(_extract_resilient([l for l, _ in chunk], extract, sid))
            except Exception as e:
                if _is_down(e):
                    log(f"ollama indisponível ({sid}): {e}")
                    chibi_status.write_status("notificacoes", sid)
                    return result("down")
                raise
            chibi_status.write_status("processando", sid)
            conflicts = _resolve_conflicts(conn, project, data["decisoes"])
            _save(conn, row, project, data, stamp, find_conflict=lambda p, t: conflicts.get(t))
            # `AND transcript_path=?`: se o hook trocou o JSONL da sessão no meio (retomada em
            # worktree), não grava o offset deste arquivo sobre o do outro. Os fatos ficam.
            draft = _add_to_draft(draft, data)
            conn.execute("UPDATE sessions SET draft=? WHERE session_id=?",
                         (json.dumps(draft, ensure_ascii=False), sid))
            moved = conn.execute(
                "UPDATE sessions SET offset=?, project=?, processed_at=? WHERE session_id=? AND transcript_path=?",
                (chunk[-1][1], project, stamp, sid, path),
            ).rowcount == 0
            conn.commit()
            done_off = chunk[-1][1]
            parts.append(data)
            nf, nd = _counts([data])
            activity.record(f"Anotou {nf} fatos e {nd} decisões ({project})")
            if moved:
                break
        else:
            conn.execute(
                "UPDATE sessions SET status='done', offset=?, project=?, attempts=0, processed_at=? "
                "WHERE session_id=? AND transcript_path=?",
                (new_off, project, stamp, sid, path),
            )
            conn.commit()
            done_off = new_off
        finished = True
        if parts:
            chibi_status.write_status("digitando", sid)
    finally:
        if not finished:
            conn.rollback()  # descarta escrita parcial do bloco que falhou (os anteriores já têm commit)
        if parts:  # nota e páginas com o que já foi gravado, mesmo se um bloco posterior falhou
            try:
                graph.write_entities(conn, sid, project)
            except Exception as e:
                log(f"páginas de entidade não gravadas ({sid}): {e!r}")
            agg = _aggregate(parts)
            try:
                record = _session_record(
                    conn, sid, project, (consolidate or ollama.consolidate_session) if finished else None)
                if record is not None:
                    notes.write_session_from_db(conn, project, sid, record)
                    activity.record(f"Notas de {project} atualizadas")
            except Exception as e:
                log(f"nota da sessão não gravada ({sid}): {e!r}")
            pending_summaries.setdefault(project, []).append(agg)
    chibi_status.write_status("feliz" if parts else "idle", sid)
    return result("done" if items else "idle")


def _fail(conn, row, e) -> None:
    conn.rollback()
    cur = conn.execute("SELECT offset FROM sessions WHERE session_id=?", (row["session_id"],)).fetchone()
    # houve progresso (blocos com checkpoint): falha intermitente, não conta como tentativa perdida
    attempts = 1 if cur is not None and cur["offset"] != row["offset"] else row["attempts"] + 1
    status = "error" if attempts >= MAX_ATTEMPTS else "queued"
    conn.execute("UPDATE sessions SET status=?, attempts=? WHERE session_id=?", (status, attempts, row["session_id"]))
    conn.commit()
    if status == "error":
        chibi_status.write_status("notificacoes", row["session_id"])
        activity.record("Desisti de uma sessão depois de várias tentativas", "erro")
    else:
        activity.record("Não consegui processar uma sessão; vou tentar de novo", "erro")
    log(f"sessão falhou ({row['session_id']}, tentativa {attempts}): {e!r}")


def process_queue(conn, extract=ollama.extract, merge=ollama.merge_summary, now=datetime.now,
                  consolidate=None) -> int:
    done, failed = 0, set()
    summaries: dict = {}  # projeto -> extrações da rodada, mescladas uma vez no fim
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
    chars_total = fatos_total = decisoes_total = 0
    try:
        while True:
            batch = list(_pending(conn, failed))
            if not batch:
                return done
            progressed = False
            for row in batch:
                try:
                    result = _process_one(conn, row, extract, now, summaries, consolidate)
                except Exception as e:
                    _fail(conn, row, e)
                    failed.add(row["session_id"])
                    status = conn.execute(
                        "SELECT status FROM sessions WHERE session_id=?", (row["session_id"],)
                    ).fetchone()["status"]
                    if status == "error":
                        falhas.append(str(e))
                    continue
                chars_total += result["chars"]
                fatos_total += result["fatos"]
                decisoes_total += result["decisoes"]
                if result["result"] == "down":
                    falhas.append("Ollama fora do ar")
                    return done
                done += 1
                if result["result"] == "idle":
                    failed.add(row["session_id"])  # nada novo (ex.: linha parcial): não reexaminar nesta rodada
                else:
                    progressed = True
            if not progressed:
                return done
    finally:
        _flush_summaries(summaries, merge, conn)
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
            if status_final == "done":
                activity.record(f"Rodada concluída: {done} sessões", "ok")
            try:
                db.insert_round(
                    conn, inicio.isoformat(timespec="seconds"), fim.isoformat(timespec="seconds"), duration_s,
                    done, chars_total, fatos_total, decisoes_total, 0, status_final, mensagem,
                )
            except Exception as insert_exc:
                # Falha ao persistir o histórico não deve mascarar a exceção
                # original (se houver) nem derrubar process_queue.
                log(f"falha ao gravar rodada em chibi_rounds: {insert_exc!r}")


def rebuild_project_summary(conn, project: str, merge=None) -> str:
    """Refaz o `_resumo.md` do zero a partir dos registros das sessões e das decisões vigentes
    (1 chamada ao LLM). Descarta o texto antigo, que pode ter pendências ou lixo do modelo."""
    merge = merge or ollama.merge_summary
    dec = [r["text"] for r in conn.execute(
        "SELECT text FROM facts WHERE project=? AND kind='decisao' AND status!='substituida' "
        "ORDER BY id DESC LIMIT ?", (project, SUMMARY_INPUT_ITEMS))][::-1]
    resumos = " ".join(f"{r['title']}: {r['summary']}" for r in conn.execute(
        "SELECT title, summary FROM sessions WHERE project=? AND title IS NOT NULL ORDER BY created_at",
        (project,)))
    text = redact(merge("", {"decisoes": dec, "resumo": resumos[-SUMMARY_INPUT_RESUMO:]})).strip()
    if text:
        notes.write_summary(project, text[:SUMMARY_MAX])
    return text


def backfill_records(conn, project: str | None = None, todas: bool = False, consolidate=None,
                     merge=None, out=print) -> int:
    """Refaz título/resumo/feitos das sessões já processadas a partir do que está guardado
    (fatos, decisões e o `## Resumo` das notas antigas), sem reler transcrições: uma chamada
    ao LLM por sessão. Regrava a nota no formato novo e o `_contexto.md` de cada projeto."""
    consolidate = consolidate or ollama.consolidate_session
    q = ("SELECT DISTINCT f.session_id, f.project FROM facts f JOIN sessions s "
         "ON s.session_id=f.session_id WHERE f.project IS NOT NULL")
    args: tuple = ()
    if not todas:
        q += " AND s.title IS NULL"
    if project:
        q += " AND f.project=?"
        args = (project,)
    pares = conn.execute(q + " ORDER BY f.project, f.session_id", args).fetchall()
    feitas, projetos, seguidas = 0, set(), 0
    for i, r in enumerate(pares, 1):
        sid, proj = r["session_id"], r["project"]
        textos = {k: [x["text"] for x in conn.execute(
            "SELECT text FROM facts WHERE session_id=? AND project=? AND kind=? AND status!='substituida' "
            "ORDER BY id LIMIT 15", (sid, proj, k))] for k in ("decisao", "fato")}
        material = {"titulo": "", "resumos": _cap_resumos(notes.legacy_resumos(proj, sid)), "feitos": [],
                    "decisoes": textos["decisao"], "fatos": textos["fato"]}
        try:
            rec = redact_deep(consolidate({"titulo": "", "resumo": "", "feitos": []}, material))
        except Exception as e:
            out(f"[{i}/{len(pares)}] {proj} {sid[:8]}: falhou ({e!r})")
            seguidas += 1
            if _is_down(e) or seguidas >= 3:
                out("Interrompido: Ollama fora do ar ou falhando seguidamente. Rode de novo depois.")
                break
            continue
        seguidas = 0
        rec["feitos"] = rec["feitos"][:RECORD_FEITOS]
        conn.execute("UPDATE sessions SET title=?, summary=?, feitos=?, project=COALESCE(project, ?) "
                     "WHERE session_id=?",
                     (rec["titulo"], rec["resumo"], json.dumps(rec["feitos"], ensure_ascii=False), proj, sid))
        conn.commit()
        notes.write_session_from_db(conn, proj, sid, rec)
        projetos.add(proj)
        feitas += 1
        out(f"[{i}/{len(pares)}] {proj}: {rec['titulo']}")
    for proj in sorted(projetos):
        try:
            rebuild_project_summary(conn, proj, merge)
            out(f"resumo do projeto refeito: {proj}")
        except Exception as e:
            out(f"resumo do projeto {proj} não refeito ({e!r})")
        notes.write_context(conn, proj)
    return feitas
