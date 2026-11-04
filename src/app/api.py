"""Api exposta ao JS da janela (pywebview js_api).

O pywebview expõe ao JS todo atributo público e chama os métodos de várias threads:
todo estado interno usa prefixo `_` e cada chamada abre a própria conexão SQLite.
"""
import base64
import functools
import json
import threading
import urllib.error
import uuid
from contextlib import closing
from pathlib import Path

from app import agent, organizer
from contextclaude import activity, appdb, chibi_status, config, db, notes, ollama, pause, vectors
from contextclaude.logutil import log

TEXT_MAX = 4000
MEMORY_SESSIONS = 30
SPRITE_PATH = Path(__file__).resolve().parents[2] / "assets" / "chibi" / "sprites.png"

MSG_BUSY = "Aguarde a resposta anterior."
MSG_EMPTY = "Escreva uma mensagem."
MSG_NO_CONV = "Conversa não encontrada."
MSG_OLLAMA_DOWN = "O Ollama não respondeu. Ele está aberto?"
MSG_EMPTY_ANSWER = "O modelo não devolveu resposta. Tente de novo."
MSG_ASK_FOLDER = "Qual pasta devo organizar? Diga o nome (ex.: Downloads) ou o caminho completo."
MSG_ALREADY = "Este plano já foi executado."
MSG_NO_PLAN = "Plano não encontrado."
MSG_NO_ITEMS = "Nenhum arquivo do plano foi selecionado."
MSG_NO_BATCH = "Lote não encontrado."
MSG_MEMORY = "Consultando a memória…"


def _safe(fn):
    """Exceção num método público vira {"error": ...} (o JS recebe sempre JSON)."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            log(f"api.{fn.__name__}: {type(e).__name__}: {e}")
            return {"error": f"Algo deu errado: {e}"}
    return wrapper


def _js_event(event: dict) -> str:
    payload = json.dumps(event, ensure_ascii=False)
    # U+2028/U+2029 são válidos em JSON mas quebram literais em motores JS antigos
    payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f"window.ccEvent({payload})"


def _opt_str(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


class Api:
    def __init__(self, emit=None, llm_json=None, llm_stream=None, search=None, run_async: bool = True):
        self._emit_fn = emit
        self._window = None
        self._llm_json = llm_json or ollama.chat_json
        self._llm_stream = llm_stream or ollama.chat_stream
        self._search = search or vectors.search
        self._run_async = run_async
        self._busy_lock = threading.Lock()
        self._busy = False
        self._exec_lock = threading.Lock()  # approve/undo: um de cada vez

    # --- eventos ---

    def bind(self, window) -> None:
        self._window = window
        self._emit_fn = self._emit_window

    def _emit_window(self, event: dict) -> None:
        self._window.evaluate_js(_js_event(event))

    def _emit(self, event: dict) -> None:
        if self._emit_fn is None:
            return
        try:
            self._emit_fn(event)
        except Exception as e:  # janela fechada etc.: não derruba a rodada
            log(f"api.emit: {type(e).__name__}: {e}")

    # --- consultas ---

    @_safe
    def status(self) -> dict:
        st = chibi_status.read_status()
        if st.get("rodada"):
            msg = "Processando memória…"
        elif (st.get("ultima_rodada") or {}).get("status") == "done":
            msg = "Memória atualizada"
        else:
            msg = ""
        return {"ollama": ollama.is_up(), "estado": st.get("estado") or "idle", "mensagem": msg}

    @_safe
    def activity(self, limit=30) -> list:
        return activity.recent(int(limit))

    @_safe
    def models(self) -> dict:
        installed = ollama.list_models()
        return {"models": installed, "default": self._app_model(None, installed)}

    @staticmethod
    def _app_model(model, installed=None) -> str:
        if isinstance(model, str) and model:
            return model
        if installed is None:
            installed = ollama.list_models()
        return config.APP_MODEL if config.APP_MODEL in (installed or []) else config.MODEL

    @_safe
    def conversations(self, query: str = "") -> list:
        with closing(db.connect()) as conn:
            return appdb.list_conversations(conn, query if isinstance(query, str) else "")

    @_safe
    def messages(self, conversation_id: int) -> list:
        with closing(db.connect()) as conn:
            return appdb.get_messages(conn, int(conversation_id))

    @_safe
    def projects(self) -> list:
        with closing(db.connect()) as conn:
            return notes.projects(conn)

    @_safe
    def memory(self, project: str | None = None) -> dict:
        project = _opt_str(project)
        where, args = "title IS NOT NULL AND title != ''", []
        if project:
            where += " AND project = ?"
            args.append(project)
        with closing(db.connect()) as conn:
            rows = conn.execute(
                f"SELECT title, summary, project, created_at FROM sessions WHERE {where} "
                f"ORDER BY created_at DESC LIMIT {MEMORY_SESSIONS}", args).fetchall()
        sessions = [{"title": r["title"], "summary": r["summary"] or "", "project": r["project"] or "",
                     "date": (r["created_at"] or "")[:10]} for r in rows]
        context = ""
        if project:
            path = notes.context_path(project)
            if path.exists():
                context = path.read_text(encoding="utf-8", errors="replace")
        return {"sessions": sessions, "context": context}

    @_safe
    def batches(self) -> list:
        with closing(db.connect()) as conn:
            return appdb.list_batches(conn)

    @_safe
    def chibi_sprite(self) -> str:
        return "data:image/png;base64," + base64.b64encode(SPRITE_PATH.read_bytes()).decode("ascii")

    # --- chat ---

    @_safe
    def send(self, conversation_id, text, model=None, project=None) -> dict:
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            return {"error": MSG_EMPTY}
        text = text[:TEXT_MAX]
        with self._busy_lock:
            if self._busy:
                return {"error": MSG_BUSY}
            self._busy = True
        started = False
        try:
            with closing(db.connect()) as conn:
                if conversation_id is None:
                    cid = appdb.create_conversation(conn, text)
                else:
                    cid = int(conversation_id)
                    if not conn.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone():
                        return {"error": MSG_NO_CONV}
                user_id = appdb.add_message(conn, cid, "user", text)
            rid = uuid.uuid4().hex
            args = (rid, cid, user_id, text, _opt_str(model), _opt_str(project))
            if self._run_async:
                threading.Thread(target=self._run, args=args, daemon=True).start()
            started = True
            if not self._run_async:
                self._run(*args)
            return {"conversation_id": cid, "request_id": rid}
        finally:
            if not started:
                with self._busy_lock:
                    self._busy = False

    def _run(self, rid, cid, user_id, text, model, project) -> None:
        def ev(**kw):
            self._emit({"request_id": rid, "conversation_id": cid, **kw})

        try:
            model = self._app_model(model)  # resolvido uma vez; o mesmo modelo em route, plan e chat
            with pause.hold(), closing(db.connect()) as conn:
                intent = agent.route(text, lambda s, u: self._llm_json(s, u, agent.ROUTE_NUM_PREDICT, model))
                if intent == "organizar":
                    self._organize(conn, ev, cid, text, model)
                    return
                if intent == "memoria":
                    ev(type="status", text=MSG_MEMORY)
                    material = agent.memory_material(conn, text, project, search=self._search)
                    msgs = agent.memory_messages(text, material)
                else:
                    history = [m for m in appdb.get_messages(conn, cid) if m["id"] != user_id]
                    msgs = agent.chat_messages(history, text)
                answer = (self._llm_stream(msgs, lambda piece: ev(type="chunk", text=piece), model) or "").strip()
                if not answer:
                    ev(type="error", text=MSG_EMPTY_ANSWER)
                    return
                self._answer(conn, ev, cid, answer)
        except (urllib.error.URLError, OSError) as e:
            log(f"api._run: {type(e).__name__}: {e}")
            ev(type="error", text=MSG_OLLAMA_DOWN)
        except Exception as e:
            log(f"api._run: {type(e).__name__}: {e}")
            ev(type="error", text=f"Algo deu errado: {e}")
        finally:
            with self._busy_lock:
                self._busy = False

    def _answer(self, conn, ev, cid, content: str) -> None:
        mid = appdb.add_message(conn, cid, "assistant", content)
        ev(type="done", message=appdb.get_message(conn, mid))

    def _organize(self, conn, ev, cid, text, model) -> None:
        folder = agent.find_folder(text)
        if folder is None:
            return self._answer(conn, ev, cid, MSG_ASK_FOLDER)
        try:
            base = organizer.check_folder(folder)
        except organizer.BlockedFolder as e:
            return self._answer(conn, ev, cid, str(e))
        try:
            files, total = organizer.scan(base)
        except OSError:
            return self._answer(conn, ev, cid, f"Não consegui ler a pasta {base}.")
        if not files:
            return self._answer(conn, ev, cid, f"Não há arquivos para organizar em {base}.")

        def progress(done, n):
            ev(type="status", text=f"Classificando {done} de {n} arquivos…")

        progress(0, len(files))
        items = organizer.plan(files, lambda s, u: self._llm_json(s, u, organizer.PLAN_NUM_PREDICT, model), progress)
        content = {"folder": str(base), "items": items, "total": total, "batch_id": None}
        pid = appdb.add_message(conn, cid, "plan", json.dumps(content, ensure_ascii=False))
        ev(type="plan", message=appdb.get_message(conn, pid))
        n = len({i["dest"].split("/")[0] for i in items})
        extra = f" A pasta tem {total} arquivos; esta rodada cobre os primeiros {len(items)}." if total > len(items) else ""
        self._answer(conn, ev, cid, f"Separei os arquivos em {n} {'categoria' if n == 1 else 'categorias'}. "
                                    f"Confira os destinos antes de mover.{extra}")

    # --- organizador ---

    @_safe
    def approve(self, message_id, items) -> dict:
        if not isinstance(items, list):
            return {"error": MSG_NO_ITEMS}
        with self._exec_lock, closing(db.connect()) as conn:
            msg = appdb.get_message(conn, int(message_id))
            if not msg or msg["role"] != "plan":
                return {"error": MSG_NO_PLAN}
            plan = json.loads(msg["content"])
            if plan.get("batch_id"):
                return {"error": MSG_ALREADY}
            allowed = {i.get("name") for i in plan.get("items") or [] if isinstance(i, dict)}
            chosen, seen = [], set()
            for it in items:  # vindos da UI: só nomes do plano gravado; destino validado no organizer
                name = it.get("name") if isinstance(it, dict) else None
                if isinstance(name, str) and name in allowed and name not in seen:
                    seen.add(name)
                    dest = it.get("dest")
                    chosen.append({"name": name, "dest": dest if isinstance(dest, str) else None})
            if not chosen:
                return {"error": MSG_NO_ITEMS}
            try:
                result = organizer.execute(conn, plan["folder"], chosen)
            except organizer.BlockedFolder as e:
                return {"error": str(e)}
            plan.update(batch_id=result["batch_id"], items=chosen, result=result)
            appdb.update_message(conn, msg["id"], json.dumps(plan, ensure_ascii=False))
            return result

    @_safe
    def undo(self, batch_id) -> dict:
        if not isinstance(batch_id, str) or not batch_id:
            return {"error": MSG_NO_BATCH}
        with self._exec_lock, closing(db.connect()) as conn:
            if not appdb.batch_moves(conn, batch_id):
                return {"error": MSG_NO_BATCH}
            result = organizer.undo(conn, batch_id)
            rows = conn.execute("SELECT id, content FROM messages WHERE role='plan' AND instr(content, ?) > 0",
                                (batch_id,)).fetchall()
            for r in rows:
                try:
                    plan = json.loads(r["content"])
                except ValueError:
                    continue
                if isinstance(plan, dict) and plan.get("batch_id") == batch_id:
                    plan["undone"] = True
                    appdb.update_message(conn, r["id"], json.dumps(plan, ensure_ascii=False))
            return result
