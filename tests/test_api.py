import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, "src")
from app.api import Api
from contextclaude import config, db


def _nosearch(*a, **k):
    return []


class TestApi(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.root = self.base / "root"
        self.home = self.base / "home"
        (self.root / "data").mkdir(parents=True)
        self.home.mkdir()
        os.environ["CONTEXTCLAUDE_ROOT"] = str(self.root)
        os.environ["CONTEXTCLAUDE_HOME"] = str(self.home)
        self.addCleanup(shutil.rmtree, self.base, True)
        self.addCleanup(os.environ.pop, "CONTEXTCLAUDE_ROOT", None)
        self.addCleanup(os.environ.pop, "CONTEXTCLAUDE_HOME", None)
        self.events = []
        from contextclaude import ollama
        orig_list = ollama.list_models
        ollama.list_models = lambda *a, **k: ["qwen3:4b"]
        self.addCleanup(setattr, ollama, "list_models", orig_list)

    def fail(self, *a, **k):
        raise AssertionError("LLM não deveria ser chamado")

    def fake_stream(self, answer):
        def stream(messages, on_chunk, model=None):
            for piece in answer.split(" "):
                on_chunk(piece + " ")
            return answer
        return stream

    def _organize(self, api_kwargs=None):
        d = self.home / "Downloads"
        d.mkdir(parents=True)
        (d / "contrato.pdf").write_text("x")
        (d / "foto.jpg").write_text("y")
        api = Api(emit=self.events.append, llm_json=lambda s, u, n, m: {"contrato.pdf": "Documentos/Contratos",
                                                                         "foto.jpg": "Imagens"},
                  llm_stream=self.fail, search=_nosearch, run_async=False, **(api_kwargs or {}))
        api.send(None, "organize meus downloads")
        plan_msg = next(e["message"] for e in self.events if e["type"] == "plan")
        return api, d, plan_msg, json.loads(plan_msg["content"])

    # --- testes do brief ---

    def test_send_memory_streams_and_saves(self):
        api = Api(emit=self.events.append, llm_json=self.fail, llm_stream=self.fake_stream("Ontem corrigimos o worker."),
                  search=_nosearch, run_async=False)
        r = api.send(None, "o que fizemos ontem?")
        types = [e["type"] for e in self.events]
        self.assertIn("chunk", types)
        self.assertEqual(types[-1], "done")
        msgs = api.messages(r["conversation_id"])
        self.assertEqual([(m["role"], m["content"]) for m in msgs],
                         [("user", "o que fizemos ontem?"), ("assistant", "Ontem corrigimos o worker.")])
        self.assertEqual(api.conversations()[0]["title"], "o que fizemos ontem?")
        self.assertFalse(config.pause_path().exists())
        for e in self.events:
            self.assertEqual((e["request_id"], e["conversation_id"]), (r["request_id"], r["conversation_id"]))

    def test_send_while_busy_is_rejected(self):
        api = Api(emit=self.events.append, run_async=False)
        api._busy = True
        self.assertEqual(api.send(None, "oi"), {"error": "Aguarde a resposta anterior."})
        self.assertEqual(api.conversations(), [])

    def test_organize_plan_approve_undo(self):
        d = self.home / "Downloads"
        d.mkdir(parents=True)
        (d / "contrato.pdf").write_text("x")
        api = Api(emit=self.events.append, llm_json=lambda s, u, n, m: {"contrato.pdf": "Documentos/Contratos"},
                  llm_stream=self.fail, search=_nosearch, run_async=False)
        api.send(None, "organize meus downloads")
        plan_msg = next(e["message"] for e in self.events if e["type"] == "plan")
        plano = json.loads(plan_msg["content"])
        self.assertEqual(plano["items"][0]["dest"], "Documentos/Contratos")
        res = api.approve(plan_msg["id"], plano["items"])
        self.assertTrue((d / "Documentos" / "Contratos" / "contrato.pdf").exists())
        self.assertEqual(api.approve(plan_msg["id"], plano["items"]), {"error": "Este plano já foi executado."})
        self.assertEqual(api.undo(res["batch_id"])["restored"], 1)
        self.assertTrue((d / "contrato.pdf").exists())

    def test_organize_blocked_folder_answers_reason(self):
        api = Api(emit=self.events.append, llm_json=self.fail, llm_stream=self.fail, search=_nosearch, run_async=False)
        api.send(None, r'organize a pasta "C:\Windows"')
        self.assertIn("bloqueada", self.events[-1]["message"]["content"].lower())

    def test_ollama_down_emits_error(self):
        def down(*a, **k):
            raise urllib.error.URLError("refused")
        api = Api(emit=self.events.append, llm_json=down, llm_stream=down, search=_nosearch, run_async=False)
        api.send(None, "conte uma piada")
        self.assertEqual((self.events[-1]["type"], self.events[-1]["text"]), ("error", "O Ollama não respondeu. Ele está aberto?"))
        self.assertEqual([m["role"] for m in api.messages(api.conversations()[0]["id"])], ["user"])

    # --- regras adicionais ---

    def test_empty_stream_emits_error_and_saves_nothing(self):
        api = Api(emit=self.events.append, llm_json=lambda *a: {"intencao": "conversa"},
                  llm_stream=lambda messages, on_chunk, model=None: "", search=_nosearch, run_async=False)
        r = api.send(None, "conte uma piada")
        self.assertEqual(self.events[-1]["type"], "error")
        self.assertTrue(self.events[-1]["text"])
        self.assertEqual([m["role"] for m in api.messages(r["conversation_id"])], ["user"])

    def test_route_and_plan_use_num_predict_and_model(self):
        from app import agent, organizer
        calls = []

        def llm(s, u, n, m):
            calls.append((n, m))
            return {"intencao": "conversa"}
        api = Api(emit=self.events.append, llm_json=llm, llm_stream=self.fake_stream("oi"),
                  search=_nosearch, run_async=False)
        api.send(None, "conte uma piada", model="qwen3:8b")
        self.assertEqual(calls, [(agent.ROUTE_NUM_PREDICT, "qwen3:8b")])
        d = self.home / "Downloads"
        d.mkdir(parents=True)
        (d / "a.pdf").write_text("x")
        api.send(None, "organize meus downloads", model="qwen3:8b")
        self.assertEqual(calls[-1], (organizer.PLAN_NUM_PREDICT, "qwen3:8b"))

    def _installed(self, names):
        from contextclaude import ollama
        orig = ollama.list_models
        ollama.list_models = lambda *a, **k: names
        self.addCleanup(setattr, ollama, "list_models", orig)

    def test_models_default_is_app_model_when_installed(self):
        api = Api(emit=self.events.append, run_async=False)
        self._installed(["qwen3:4b", config.APP_MODEL])
        self.assertEqual(api.models()["default"], config.APP_MODEL)
        self._installed(["qwen3:4b"])
        self.assertEqual(api.models()["default"], config.MODEL)
        self._installed([])
        self.assertEqual(api.models()["default"], config.MODEL)

    def test_models_cold_start_then_ollama_up(self):
        """Contrato usado pelo loadModels do app.js: com Ollama fora, lista vazia e default=MODEL;
        quando sobe, o default passa a APP_MODEL (o JS só preserva a escolha anterior se havia lista)."""
        api = Api(emit=self.events.append, run_async=False)
        self._installed([])
        self.assertEqual(api.models(), {"models": [], "default": config.MODEL})
        self._installed(["qwen3:4b", config.APP_MODEL])
        res = api.models()
        self.assertEqual(res["models"], ["qwen3:4b", config.APP_MODEL])
        self.assertEqual(res["default"], config.APP_MODEL)

    def test_send_without_model_passes_resolved_model_everywhere(self):
        self._installed([config.APP_MODEL])
        seen = []

        def llm(s, u, n, m):
            seen.append(m)
            return {"intencao": "conversa"}

        def stream(messages, on_chunk, model=None):
            seen.append(model)
            return "oi"
        api = Api(emit=self.events.append, llm_json=llm, llm_stream=stream, search=_nosearch, run_async=False)
        api.send(None, "conte uma piada")
        d = self.home / "Downloads"
        d.mkdir(parents=True)
        (d / "a.pdf").write_text("x")
        api.send(None, "organize meus downloads")
        self.assertTrue(seen)
        self.assertEqual(set(seen), {config.APP_MODEL})
        self.assertGreaterEqual(len(seen), 3)

    def test_explicit_model_wins_even_if_app_model_installed(self):
        self._installed([config.APP_MODEL])
        seen = []

        def llm(s, u, n, m):
            seen.append(m)
            return {"intencao": "conversa"}
        api = Api(emit=self.events.append, llm_json=llm, llm_stream=self.fake_stream("oi"),
                  search=_nosearch, run_async=False)
        api.send(None, "conte uma piada", model="qwen3:8b")
        self.assertEqual(seen, ["qwen3:8b"])

    def test_empty_model_falls_through_to_default(self):
        self._installed([config.APP_MODEL])
        seen = []

        def llm(s, u, n, m):
            seen.append(m)
            return {"intencao": "conversa"}
        api = Api(emit=self.events.append, llm_json=llm, llm_stream=self.fake_stream("oi"),
                  search=_nosearch, run_async=False)
        api.send(None, "conte uma piada", model="")
        self.assertEqual(seen, [config.APP_MODEL])

    def test_send_falls_back_to_config_model_when_app_model_missing(self):
        self._installed([])
        seen = []

        def llm(s, u, n, m):
            seen.append(m)
            return {"intencao": "conversa"}
        api = Api(emit=self.events.append, llm_json=llm, llm_stream=self.fake_stream("oi"),
                  search=_nosearch, run_async=False)
        api.send(None, "conte uma piada")
        self.assertEqual(seen, [config.MODEL])

    def test_conversation_history_reaches_chat(self):
        seen = []

        def stream(messages, on_chunk, model=None):
            seen.append(messages)
            on_chunk("ok")
            return "ok"
        api = Api(emit=self.events.append, llm_json=lambda *a: {"intencao": "conversa"}, llm_stream=stream,
                  search=_nosearch, run_async=False)
        r = api.send(None, "primeira pergunta")
        api.send(r["conversation_id"], "segunda pergunta")
        self.assertEqual([m["content"] for m in seen[1][1:]], ["primeira pergunta", "ok", "segunda pergunta"])

    def test_send_validation(self):
        api = Api(emit=self.events.append, llm_json=self.fail, llm_stream=self.fail, run_async=False)
        self.assertIn("error", api.send(None, "   "))
        self.assertIn("error", api.send(9999, "oi"))
        self.assertEqual(api.conversations(), [])
        self.assertFalse(api._busy)

    def test_send_truncates_long_text(self):
        api = Api(emit=self.events.append, llm_json=lambda *a: {"intencao": "conversa"},
                  llm_stream=self.fake_stream("ok"), search=_nosearch, run_async=False)
        r = api.send(None, "x" * 5000)
        self.assertEqual(len(api.messages(r["conversation_id"])[0]["content"]), 4000)

    def test_ask_folder_when_not_found(self):
        api = Api(emit=self.events.append, llm_json=self.fail, llm_stream=self.fail, search=_nosearch, run_async=False)
        api.send(None, "organize minha pasta de coisas")
        self.assertEqual(self.events[-1]["type"], "done")
        self.assertEqual(self.events[-1]["message"]["content"],
                         "Qual pasta devo organizar? Diga o nome (ex.: Downloads) ou o caminho completo.")

    def test_empty_folder_answer(self):
        (self.home / "Downloads").mkdir(parents=True)
        api = Api(emit=self.events.append, llm_json=self.fail, llm_stream=self.fail, search=_nosearch, run_async=False)
        api.send(None, "organize meus downloads")
        self.assertTrue(self.events[-1]["message"]["content"].startswith("Não há arquivos para organizar em "))

    def test_plan_message_shape_and_summary(self):
        api, d, plan_msg, plano = self._organize()
        self.assertEqual(plano["folder"], str(d.resolve()))
        self.assertEqual((plano["total"], plano["batch_id"]), (2, None))
        self.assertEqual(self.events[-1]["type"], "done")
        self.assertEqual(self.events[-1]["message"]["content"],
                         "Separei os arquivos em 2 categorias. Confira os destinos antes de mover.")
        self.assertTrue(any(e["type"] == "status" and "Classificando" in e["text"] for e in self.events))
        roles = [m["role"] for m in api.messages(self.events[-1]["conversation_id"])]
        self.assertEqual(roles, ["user", "plan", "assistant"])

    def test_truncated_scan_shows_real_total(self):
        from unittest import mock
        from app import organizer
        d = self.home / "Downloads"
        d.mkdir(parents=True)
        files = [{"name": f"f{i}.txt", "ext": "txt"} for i in range(3)]
        api = Api(emit=self.events.append, llm_json=lambda s, u, n, m: {}, llm_stream=self.fail,
                  search=_nosearch, run_async=False)
        with mock.patch.object(organizer, "scan", return_value=(files, 350)):
            api.send(None, "organize meus downloads")
        plano = json.loads(next(e["message"] for e in self.events if e["type"] == "plan")["content"])
        self.assertEqual(plano["total"], 350)
        self.assertIn("350", self.events[-1]["message"]["content"])

    def test_approve_drops_names_not_in_plan_and_uses_stored_folder(self):
        api, d, plan_msg, plano = self._organize()
        outside = self.base / "outra"
        outside.mkdir()
        (outside / "segredo.txt").write_text("s")
        (d / "intruso.txt").write_text("i")
        items = [{"name": "contrato.pdf", "dest": "Meus/Contratos", "folder": str(outside)},
                 {"name": "intruso.txt", "dest": "X"},
                 {"name": "segredo.txt", "dest": "X"}]
        res = api.approve(plan_msg["id"], items)
        self.assertEqual([m["name"] for m in res["moved"]], ["contrato.pdf"])
        self.assertTrue((d / "Meus" / "Contratos" / "contrato.pdf").exists())
        self.assertTrue((d / "intruso.txt").exists())
        self.assertTrue((d / "foto.jpg").exists())
        self.assertTrue((outside / "segredo.txt").exists())
        saved = json.loads(api.messages(self.events[-1]["conversation_id"])[1]["content"])
        self.assertEqual(saved["batch_id"], res["batch_id"])
        self.assertEqual([i["name"] for i in saved["items"]], ["contrato.pdf"])
        self.assertEqual(saved["result"]["moved"][0]["name"], "contrato.pdf")

    def test_approve_rejects_escaping_dest(self):
        api, d, plan_msg, plano = self._organize()
        res = api.approve(plan_msg["id"], [{"name": "contrato.pdf", "dest": "../../fora"}])
        self.assertEqual(res["moved"], [])
        self.assertEqual(len(res["failed"]), 1)
        self.assertTrue((d / "contrato.pdf").exists())

    def test_approve_invalid_inputs(self):
        api, d, plan_msg, plano = self._organize()
        self.assertIn("error", api.approve(999999, plano["items"]))
        user_msg_id = api.messages(self.events[-1]["conversation_id"])[0]["id"]
        self.assertIn("error", api.approve(user_msg_id, plano["items"]))
        self.assertIn("error", api.approve(plan_msg["id"], "nada"))
        self.assertIn("error", api.approve(plan_msg["id"], [{"name": "nao-esta.txt", "dest": "X"}]))
        self.assertIsNone(json.loads(api.messages(self.events[-1]["conversation_id"])[1]["content"])["batch_id"])

    def test_concurrent_approve_executes_once(self):
        from app import organizer
        api, d, plan_msg, plano = self._organize()
        real = organizer.execute
        gate = threading.Event()
        calls = []

        def slow_execute(*a, **k):
            calls.append(1)
            gate.wait(2)
            return real(*a, **k)
        organizer.execute = slow_execute
        self.addCleanup(setattr, organizer, "execute", real)
        results = [None, None]

        def run(i):
            results[i] = api.approve(plan_msg["id"], plano["items"])
        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        threading.Event().wait(0.2)
        gate.set()
        for t in threads:
            t.join(5)
        self.assertEqual(len(calls), 1)
        errors = [r for r in results if r == {"error": "Este plano já foi executado."}]
        oks = [r for r in results if "batch_id" in r]
        self.assertEqual((len(errors), len(oks)), (1, 1))
        self.assertEqual(len(oks[0]["moved"]), 2)

    def test_undo_marks_plan_and_unknown_batch(self):
        api, d, plan_msg, plano = self._organize()
        res = api.approve(plan_msg["id"], plano["items"])
        self.assertEqual(api.undo(res["batch_id"])["restored"], 2)
        saved = json.loads(api.messages(self.events[-1]["conversation_id"])[1]["content"])
        self.assertTrue(saved["undone"])
        self.assertEqual(api.batches()[0]["batch_id"], res["batch_id"])
        self.assertIn("error", api.undo("nao-existe"))

    def test_status_models_projects_memory_sprite(self):
        api = Api(emit=self.events.append, run_async=False)
        from contextclaude import chibi_status, ollama
        orig = (ollama.is_up, ollama.list_models)
        ollama.is_up = lambda *a, **k: False
        ollama.list_models = lambda *a, **k: ["qwen3:4b"]
        self.addCleanup(setattr, ollama, "is_up", orig[0])
        self.addCleanup(setattr, ollama, "list_models", orig[1])
        self.assertEqual(api.status(), {"ollama": False, "estado": "idle", "mensagem": ""})
        chibi_status.write_rodada_inicio("2026-11-02T10:00:00", None, 1)
        self.assertEqual(api.status()["mensagem"], "Processando memória…")
        chibi_status.write_rodada_fim("done", 1.0, 1, 0, 0)
        self.assertEqual(api.status()["mensagem"], "Memória atualizada")
        self.assertEqual(api.models(), {"models": ["qwen3:4b"], "default": config.MODEL})

        conn = db.connect()
        conn.execute("INSERT INTO sessions(session_id, project, created_at, title, summary) VALUES"
                     "('s1','Proj','2026-11-01T10:00:00','Antiga','r1'),"
                     "('s2','Proj','2026-11-02T10:00:00','Nova','r2'),"
                     "('s3','Outro','2026-11-02T11:00:00','',NULL)")
        conn.commit()
        conn.close()
        self.assertEqual(api.projects(), ["Outro", "Proj"])
        from contextclaude import notes
        notes.context_path("Proj").parent.mkdir(parents=True, exist_ok=True)
        notes.context_path("Proj").write_text("# Contexto", encoding="utf-8")
        mem = api.memory("Proj")
        self.assertEqual([s["title"] for s in mem["sessions"]], ["Nova", "Antiga"])
        self.assertEqual(mem["sessions"][0], {"title": "Nova", "summary": "r2", "project": "Proj", "date": "2026-11-02"})
        self.assertEqual(mem["context"], "# Contexto")
        self.assertEqual(api.memory()["context"], "")
        self.assertTrue(api.chibi_sprite().startswith("data:image/png;base64,"))

    def test_bind_emits_via_evaluate_js(self):
        calls = []

        class Win:
            def evaluate_js(self, code):
                calls.append(code)
        api = Api(llm_json=lambda *a: {"intencao": "conversa"}, llm_stream=self.fake_stream("olá\u2028"),
                  search=_nosearch, run_async=False)
        api.bind(Win())
        api.send(None, "conte uma piada")
        self.assertTrue(calls)
        self.assertTrue(all(c.startswith("window.ccEvent(") and c.endswith(")") for c in calls))
        self.assertNotIn("\u2028", "".join(calls))
        last = json.loads(calls[-1][len("window.ccEvent("):-1])
        self.assertEqual(last["type"], "done")

    def test_public_surface_has_no_state(self):
        api = Api(emit=self.events.append, run_async=False)
        public = [k for k in vars(api) if not k.startswith("_")]
        self.assertEqual(public, [])

    def test_async_run_releases_busy(self):
        done = threading.Event()

        def emit(e):
            self.events.append(e)
            if e["type"] in ("done", "error"):
                done.set()
        api = Api(emit=emit, llm_json=lambda *a: {"intencao": "conversa"}, llm_stream=self.fake_stream("ok"),
                  search=_nosearch)
        r = api.send(None, "conte uma piada")
        self.assertIn("request_id", r)
        self.assertTrue(done.wait(5))
        for _ in range(50):
            if not api._busy:
                break
            threading.Event().wait(0.05)
        self.assertFalse(api._busy)


if __name__ == "__main__":
    unittest.main()


class TestApiActivity(unittest.TestCase):
    def test_activity_returns_recent_newest_first(self):
        import tempfile
        from contextclaude import activity
        with tempfile.TemporaryDirectory() as d:
            os.environ["CONTEXTCLAUDE_ROOT"] = d
            self.addCleanup(os.environ.pop, "CONTEXTCLAUDE_ROOT", None)
            activity.record("a")
            activity.record("b", "ok")
            api = Api(run_async=False)
            self.assertEqual([r["texto"] for r in api.activity()], ["b", "a"])
            self.assertEqual(len(api.activity(1)), 1)
