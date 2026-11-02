import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime
from unittest import mock

from contextclaude import chibi_status, db, lock, notes, pipeline
from contextclaude.ollama import ExtractionError

DATA = {"fatos": ["f"], "decisoes": ["usar sqlite"], "pendencias": ["p"],
        "relacoes": [{"origem": "A", "relacao": "usa", "destino": "B"}], "resumo": "r"}


def now():
    return datetime(2026, 10, 29, 12, 0)


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name
        os.environ["CONTEXTCLAUDE_HOME"] = r"C:\Users\João"
        self.conn = db.connect()
        self.tpath = os.path.join(self.tmp.name, "t.jsonl")
        self.captured = []

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def transcript(self, *texts, mode="w"):
        with open(self.tpath, mode, encoding="utf-8", newline="\n") as f:
            for t in texts:
                f.write(json.dumps({"type": "user", "message": {"role": "user", "content": t}}, ensure_ascii=False) + "\n")

    def extract(self, lines):
        self.captured.extend(lines)
        return dict(DATA)

    def run_q(self, extract=None):
        return pipeline.process_queue(self.conn, extract=extract or self.extract, merge=lambda old, d: "RESUMO", now=now)

    def status(self):
        return self.conn.execute("SELECT status FROM sessions").fetchone()[0]

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_session_processed_creates_note_and_rows(self):
        self.transcript("decidimos usar sqlite")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.assertEqual(self.run_q(), 1)
        self.assertEqual(len(list((notes._dir("app") / "Sessões").glob("*.md"))), 1)
        self.assertEqual(notes.read_summary("app"), "RESUMO")
        self.assertEqual(self.count("facts"), 3)
        self.assertEqual(self.count("relations"), 1)

    def test_process_one_runs_merge_and_conflict_search_before_first_write(self):
        """I2: o resumo (merge, chamada lenta ao Ollama) e a busca de conflito de decisão
        (vectors.search) devem rodar ANTES do primeiro INSERT/UPDATE de _save, para que a
        janela do write lock do SQLite fique restrita aos INSERTs/UPDATEs rápidos."""
        self.transcript("decidimos usar sqlite")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        order = []
        orig_save = pipeline._save

        def tracking_save(*a, **kw):
            order.append("save")  # _save's first conn.execute opens the write transaction
            return orig_save(*a, **kw)

        def find_conflict(*a, **kw):
            order.append("search")
            return None

        def merge(old, data):
            order.append("merge")
            return "RESUMO"

        with mock.patch.object(pipeline, "_find_conflicting_decision", find_conflict), \
             mock.patch.object(pipeline, "_save", tracking_save):
            pipeline.process_queue(self.conn, extract=self.extract, merge=merge, now=now)

        self.assertEqual(order, ["search", "merge", "save"])

    def test_unrecoverable_line_is_discarded_not_retried_forever(self):
        """Uma linha que nunca produz JSON válido (mesmo sozinha) é descartada: a
        sessão fica 'done' sem fatos, em vez de ficar travada em erro permanente
        (bug anterior: uma linha ruim perdia a sessão inteira, para sempre)."""
        self.transcript("x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

        def boom(lines):
            raise ExtractionError("ruim")

        self.assertEqual(self.run_q(boom), 1)
        self.assertEqual(self.status(), "done")
        self.assertEqual(self.count("facts"), 0)

    def test_one_bad_chunk_does_not_discard_other_chunks(self):
        """Uma linha irrecuperável em meio a um transcript maior não deve apagar
        os fatos já extraídos com sucesso das outras linhas."""
        self.transcript("boa", "ruim")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

        def picky(lines):
            if "ruim" in lines:
                raise ExtractionError("sempre inválido para esta linha")
            return dict(DATA)

        self.assertEqual(self.run_q(picky), 1)
        self.assertEqual(self.status(), "done")
        self.assertEqual(self.count("facts"), 3)

    def test_truncated_json_retries_with_smaller_chunks(self):
        self.transcript("um", "dois", "tres", "quatro")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

        def flaky(lines):
            if len(lines) > 1:
                raise ExtractionError("JSON inválido: Unterminated string")
            return dict(DATA)

        self.assertEqual(self.run_q(flaky), 1)
        self.assertEqual(self.status(), "done")
        self.assertEqual(self.count("facts"), 3)
        self.assertEqual(self.count("relations"), 1)

    def test_ollama_down_does_not_burn_attempts(self):
        self.transcript("x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

        def down(lines):
            raise urllib.error.URLError("fora")

        self.run_q(down)
        row = self.conn.execute("SELECT status, attempts FROM sessions").fetchone()
        self.assertEqual((row[0], row[1]), ("queued", 0))

    def test_empty_transcript_marks_done_without_note(self):
        self.transcript()
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        self.assertEqual(self.status(), "done")
        self.assertFalse((notes._dir("app") / "Sessões").exists())

    def test_secret_never_reaches_extract(self):
        self.transcript("minha chave sk-ant-api03-AAAAAAAAAAAAAAAAAAAA")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        self.assertTrue(self.captured)
        self.assertNotIn("sk-ant", "".join(self.captured))

    def test_second_run_does_not_duplicate_but_picks_up_new_text(self):
        self.transcript("um")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        self.run_q()
        self.assertEqual(self.count("facts"), 3)
        self.transcript("dois", mode="a")
        self.run_q()
        # fato "f", pendencia "p" e decisao "usar sqlite" são duplicatas exatas (não reinseridas)
        self.assertEqual(self.count("facts"), 3)

    def test_home_cwd_goes_to_geral(self):
        self.transcript("x")
        db.enqueue(self.conn, "s1", r"C:\Users\João", self.tpath)
        self.run_q()
        self.assertTrue((notes._dir("_geral") / "Sessões").exists())

    def test_process_one_writes_entity_pages_for_relations_seen(self):
        self.transcript("x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        self.assertTrue((notes._dir("app") / "Entidades" / "a.md").exists())
        self.assertTrue((notes._dir("app") / "Entidades" / "b.md").exists())

    def test_failure_in_graph_write_entities_does_not_advance_offset(self):
        self.transcript("decidimos usar sqlite")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        def boom(*a, **kw):
            raise RuntimeError("falha simulada")
        with mock.patch("contextclaude.pipeline.graph.write_entities", side_effect=boom):
            self.run_q()
        row = self.conn.execute("SELECT status, offset FROM sessions").fetchone()
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["offset"], 0)  # nada consumido: a sessão inteira será reprocessada
        self.assertEqual(self.count("facts"), 0)  # fatos da tentativa falha não ficaram presos
        # segunda rodada, sem a falha: desta vez tudo deve completar, incluindo o grafo
        self.run_q()
        row = self.conn.execute("SELECT status, offset FROM sessions").fetchone()
        self.assertEqual(row["status"], "done")
        self.assertGreater(row["offset"], 0)
        self.assertEqual(self.count("facts"), 3)  # DATA fixture produz 1 fato + 1 decisao + 1 pendencia

    def test_worker_lock_blocks_second_instance(self):
        first = lock.acquire()
        try:
            self.assertIsNotNone(first)
            self.assertIsNone(lock.acquire())
        finally:
            lock.release(first)
        again = lock.acquire()
        self.assertIsNotNone(again)
        lock.release(again)

    def test_lock_acquire_with_custom_path_is_independent_of_default(self):
        custom = os.path.join(self.tmp.name, "chibi.lock")
        default_handle = lock.acquire()
        try:
            custom_handle = lock.acquire(custom)
            self.assertIsNotNone(custom_handle)
            lock.release(custom_handle)
        finally:
            lock.release(default_handle)

    def test_successful_session_pulses_feliz_status(self):
        self.transcript("decidimos usar sqlite")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        self.assertEqual(chibi_status.read_status()["estado"], "feliz")

    def test_idle_session_sets_status_idle(self):
        self.transcript()
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        self.assertEqual(chibi_status.read_status()["estado"], "idle")

    def test_pipeline_reports_pensando_digitando_feliz_in_order(self):
        seen = []
        orig = pipeline.chibi_status.write_status
        pipeline.chibi_status.write_status = lambda estado, sessao=None, **kw: seen.append(estado)
        try:
            self.transcript("decidimos usar sqlite")
            db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
            self.run_q()
        finally:
            pipeline.chibi_status.write_status = orig
        self.assertIn("pensando", seen)
        self.assertIn("digitando", seen)
        self.assertEqual(seen[-1], "feliz")

    def test_ollama_down_pulses_notificacoes_status(self):
        self.transcript("x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

        def down(lines):
            raise urllib.error.URLError("fora")

        self.run_q(down)
        self.assertEqual(chibi_status.read_status()["estado"], "notificacoes")

    def test_fail_at_max_attempts_pulses_notificacoes_status(self):
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        for _ in range(pipeline.MAX_ATTEMPTS):
            row = self.conn.execute("SELECT * FROM sessions WHERE session_id='s1'").fetchone()
            pipeline._fail(self.conn, row, RuntimeError("falha"))
        self.assertEqual(self.status(), "error")
        self.assertEqual(chibi_status.read_status()["estado"], "notificacoes")

    def test_notificacoes_pulse_survives_end_of_run_idle_write(self):
        """worker.py escreve 'idle' logo após o pipeline terminar (em
        milissegundos). O pulso transiente de notificacoes some do campo
        'estado' ao vivo, mas deve continuar visível via pulso_estado/
        pulso_evento_id para um poller que só olhar o arquivo depois."""
        self.transcript("x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

        def down(lines):
            raise urllib.error.URLError("fora")

        self.run_q(down)
        pulso_evento_id_antes = chibi_status.read_status()["pulso_evento_id"]
        chibi_status.write_status("idle")  # simula o fim de worker.main()
        out = chibi_status.read_status()
        self.assertEqual(out["estado"], "idle")
        self.assertEqual(out["pulso_estado"], "notificacoes")
        self.assertEqual(out["pulso_evento_id"], pulso_evento_id_antes)

    def test_empty_queue_does_not_open_a_round(self):
        self.run_q()
        out = chibi_status.read_status()
        self.assertIsNone(out["rodada"])
        self.assertIsNone(out["ultima_rodada"])

    def test_successful_round_closes_done_with_summary_in_status_and_db(self):
        self.transcript("decidimos usar sqlite")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        out = chibi_status.read_status()["ultima_rodada"]
        self.assertEqual((out["status"], out["sessoes"], out["fatos"], out["decisoes"]), ("done", 1, 1, 1))
        row = db.recent_rounds(self.conn, limit=1)[0]
        self.assertEqual(row["chars_total"], os.path.getsize(self.tpath))

    def test_down_closes_round_as_error_with_message(self):
        self.transcript("x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

        def down(lines):
            raise urllib.error.URLError("fora")

        self.run_q(down)
        out = chibi_status.read_status()["ultima_rodada"]
        self.assertEqual(out["status"], "error")

    def test_partial_failure_within_round_closes_as_error_with_real_chars_total(self):
        self.transcript("boa")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        bad_path = os.path.join(self.tmp.name, "bad.jsonl")
        with open(bad_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "ruim"}}, ensure_ascii=False) + "\n")
        db.enqueue(self.conn, "s2", r"C:\dev\app2", bad_path)
        self.conn.execute("UPDATE sessions SET attempts=? WHERE session_id='s2'", (pipeline.MAX_ATTEMPTS - 1,))
        self.conn.commit()

        def picky(lines):
            if any("ruim" in l for l in lines):
                raise RuntimeError("falha de verdade")
            return dict(DATA)

        self.run_q(picky)
        out = chibi_status.read_status()["ultima_rodada"]
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["sessoes"], 1)  # só s1 concluiu
        row = db.recent_rounds(self.conn, limit=1)[0]
        self.assertEqual(row["chars_total"], os.path.getsize(self.tpath))  # não inclui bad_path

    def test_resumed_session_requiring_second_internal_pass_is_still_one_round(self):
        self.transcript("um")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.conn.execute("UPDATE sessions SET status='queued' WHERE session_id='s1'")
        self.conn.commit()
        self.transcript("um", "dois", mode="a")
        db.enqueue(self.conn, "s2", r"C:\dev\app", os.path.join(self.tmp.name, "t2.jsonl"))
        with open(os.path.join(self.tmp.name, "t2.jsonl"), "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "tres"}}, ensure_ascii=False) + "\n")
        self.run_q()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM chibi_rounds").fetchone()[0], 1)

    def test_unexpected_exception_mid_processing_still_closes_the_round(self):
        self.transcript("decidimos usar sqlite")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        orig = pipeline.graph.write_entities
        pipeline.graph.write_entities = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.run_q()
        finally:
            pipeline.graph.write_entities = orig
        self.assertIsNotNone(chibi_status.read_status()["ultima_rodada"])

    def test_session_with_no_pending_bytes_does_not_open_a_round(self):
        """I-2: sessão enfileirada sem conteúdo novo (ex.: hook SessionStart
        antes de haver transcrição) não deve abrir rodada visível -- só
        ruído no toast e no histórico. A fila ainda processa normalmente."""
        self.transcript()  # arquivo de 0 bytes: nada pendente
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        antes = chibi_status.read_status()

        self.assertEqual(self.run_q(), 1)

        depois = chibi_status.read_status()
        self.assertEqual(depois["rodada"], antes["rodada"])
        self.assertEqual(depois["ultima_rodada"], antes["ultima_rodada"])
        self.assertEqual(self.status(), "done")  # fila processada normalmente
        self.assertEqual(self.count("chibi_rounds"), 0)

    def test_exception_escaping_the_loop_still_closes_round_as_error_and_propagates(self):
        """M-1: uma exceção que escapa do loop principal (não a que o except
        por sessão já trata, mas uma que vem de dentro dele, ex.: _fail) deve
        fechar a rodada como erro com a mensagem registrada, e continuar
        propagando para quem chamou process_queue (não pode ser engolida)."""
        self.transcript("boa")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

        def picky(lines):
            raise RuntimeError("falha de verdade")

        orig_fail = pipeline._fail

        def exploding_fail(conn, row, e):
            raise RuntimeError("fail explodiu")

        pipeline._fail = exploding_fail
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.run_q(picky)
        finally:
            pipeline._fail = orig_fail
        self.assertIn("fail explodiu", str(ctx.exception))

        out = chibi_status.read_status()["ultima_rodada"]
        self.assertEqual(out["status"], "error")
        self.assertIn("fail explodiu", out["mensagem"])
        row = db.recent_rounds(self.conn, limit=1)[0]
        self.assertEqual(row["status"], "error")

    def test_insert_round_failure_does_not_crash_the_happy_path(self):
        """M-1: falha ao gravar em chibi_rounds não pode derrubar
        process_queue nem impedir o write_rodada_fim de acontecer."""
        self.transcript("decidimos usar sqlite")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

        orig_insert = pipeline.db.insert_round

        def exploding_insert(*a, **kw):
            raise RuntimeError("insert falhou")

        pipeline.db.insert_round = exploding_insert
        try:
            self.assertEqual(self.run_q(), 1)  # não levanta
        finally:
            pipeline.db.insert_round = orig_insert
        out = chibi_status.read_status()["ultima_rodada"]
        self.assertEqual(out["status"], "done")  # write_rodada_fim ainda ocorreu

    def test_insert_round_failure_does_not_mask_the_original_exception(self):
        """M-1: se tanto o loop quanto o db.insert_round falharem, a exceção
        original (do loop) deve ser a que propaga, não a do insert."""
        self.transcript("boa")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

        def picky(lines):
            raise RuntimeError("falha de verdade")

        orig_fail = pipeline._fail
        orig_insert = pipeline.db.insert_round

        def exploding_fail(conn, row, e):
            raise RuntimeError("fail explodiu")

        def exploding_insert(*a, **kw):
            raise RuntimeError("insert falhou")

        pipeline._fail = exploding_fail
        pipeline.db.insert_round = exploding_insert
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.run_q(picky)
        finally:
            pipeline._fail = orig_fail
            pipeline.db.insert_round = orig_insert
        self.assertIn("fail explodiu", str(ctx.exception))

    def test_save_skips_exact_duplicate_fato_text(self):
        row = {"session_id": "s1"}
        data = {"fatos": ["usa sqlite"], "decisoes": [], "pendencias": [], "relacoes": [], "resumo": ""}
        pipeline._save(self.conn, row, "p", data, "2026-01-01T00:00:00")
        pipeline._save(self.conn, row, "p", data, "2026-01-01T00:00:01")
        self.conn.commit()
        self.assertEqual(self.count("facts"), 1)  # não duplicou

    def test_save_supersedes_conflicting_earlier_decision(self):
        row = {"session_id": "s1"}
        data = {"fatos": [], "decisoes": ["usar sqlite"], "pendencias": [], "relacoes": [], "resumo": ""}
        pipeline._save(self.conn, row, "p", data, "2026-01-01T00:00:00", find_conflict=lambda project, text: None)
        old_id = self.conn.execute("SELECT id FROM facts WHERE kind='decisao'").fetchone()["id"]

        row2 = {"session_id": "s2"}
        data2 = {"fatos": [], "decisoes": ["usar postgres"], "pendencias": [], "relacoes": [], "resumo": ""}
        pipeline._save(self.conn, row2, "p", data2, "2026-01-02T00:00:00",
                       find_conflict=lambda project, text: old_id)
        self.conn.commit()

        old = self.conn.execute("SELECT status FROM facts WHERE id=?", (old_id,)).fetchone()
        self.assertEqual(old["status"], "substituida")
        new = self.conn.execute("SELECT status, supersedes FROM facts WHERE text='usar postgres'").fetchone()
        self.assertEqual(new["status"], "confirmada")
        self.assertEqual(new["supersedes"], old_id)

    def test_find_conflicting_decision_returns_none_on_search_failure(self):
        def boom(*a, **kw):
            raise ConnectionError("chroma indisponível")
        result = pipeline._find_conflicting_decision("p", "usar sqlite", search=boom)
        self.assertIsNone(result)

    def test_find_conflicting_decision_respects_threshold(self):
        near = lambda *a, **kw: [{"id": 7, "distance": 0.10}, {"id": 8, "distance": 0.30}]
        far = lambda *a, **kw: [{"id": 9, "distance": 0.40}]
        self.assertEqual(pipeline._find_conflicting_decision("p", "usar sqlite", search=near), 7)
        self.assertIsNone(pipeline._find_conflicting_decision("p", "usar sqlite", search=far))

    def test_find_conflicting_decision_skips_already_superseded_fact_in_chain(self):
        """I1: A foi substituída por B; C chega como paráfrase de A (hit mais próximo
        no Chroma é A, já 'substituida' no SQLite). Não deve devolver A como conflito."""
        self.conn.execute(
            "INSERT INTO facts(id, session_id, project, kind, text, status, created_at) "
            "VALUES(1,'s1','p','decisao','usar sqlite','substituida','2026-01-01T00:00:00')"
        )
        self.conn.execute(
            "INSERT INTO facts(id, session_id, project, kind, text, status, supersedes, created_at) "
            "VALUES(2,'s1','p','decisao','vamos usar sqlite','confirmada',1,'2026-01-01T00:00:01')"
        )
        self.conn.commit()
        search = lambda *a, **kw: [{"id": 1, "distance": 0.05}]
        result = pipeline._find_conflicting_decision(
            "p", "usar sqlite mesmo", search=search,
            active=lambda ids: db.active_fact_ids(self.conn, ids),
        )
        self.assertIsNone(result)

    def test_find_conflicting_decision_skips_already_superseded_fact_on_reversion(self):
        """I1: A foi substituída por B; o texto original de A chega de novo (hit exato
        é A). Não deve devolver A (já morta) como conflito a ser re-substituída."""
        self.conn.execute(
            "INSERT INTO facts(id, session_id, project, kind, text, status, created_at) "
            "VALUES(1,'s1','p','decisao','usar sqlite','substituida','2026-01-01T00:00:00')"
        )
        self.conn.execute(
            "INSERT INTO facts(id, session_id, project, kind, text, status, supersedes, created_at) "
            "VALUES(2,'s1','p','decisao','usar postgres','confirmada',1,'2026-01-01T00:00:01')"
        )
        self.conn.commit()
        search = lambda *a, **kw: [{"id": 1, "distance": 0.0}]
        result = pipeline._find_conflicting_decision(
            "p", "usar sqlite", search=search,
            active=lambda ids: db.active_fact_ids(self.conn, ids),
        )
        self.assertIsNone(result)

    def test_save_skips_find_conflict_for_blank_decision_text(self):
        calls = []
        row = {"session_id": "s1"}
        data = {"fatos": [], "decisoes": ["   ", "usar sqlite"], "pendencias": [], "relacoes": [], "resumo": ""}
        pipeline._save(self.conn, row, "p", data, "2026-01-01T00:00:00",
                       find_conflict=lambda project, text: calls.append(text) or None)
        self.assertEqual(calls, ["usar sqlite"])  # a entrada em branco nunca chega ao find_conflict

    def test_save_does_not_duplicate_or_self_supersede_on_retry(self):
        row = {"session_id": "s1"}
        data = {"fatos": [], "decisoes": ["usar sqlite"], "pendencias": [], "relacoes": [], "resumo": ""}
        pipeline._save(self.conn, row, "p", data, "2026-01-01T00:00:00", find_conflict=lambda project, text: None)
        pipeline._save(self.conn, row, "p", data, "2026-01-01T00:00:01", find_conflict=lambda project, text: None)
        self.conn.commit()
        self.assertEqual(self.count("facts"), 1)

    def test_save_reinstates_decision_identical_to_a_superseded_one(self):
        row = {"session_id": "s1"}
        data = {"fatos": [], "decisoes": ["usar sqlite"], "pendencias": [], "relacoes": [], "resumo": ""}
        pipeline._save(self.conn, row, "p", data, "2026-01-01T00:00:00", find_conflict=lambda project, text: None)
        old_id = self.conn.execute("SELECT id FROM facts WHERE kind='decisao'").fetchone()["id"]
        self.conn.execute("UPDATE facts SET status='substituida' WHERE id=?", (old_id,))
        self.conn.commit()

        row2 = {"session_id": "s2"}
        pipeline._save(self.conn, row2, "p", data, "2026-01-02T00:00:00", find_conflict=lambda project, text: None)
        self.conn.commit()

        rows = self.conn.execute(
            "SELECT status FROM facts WHERE kind='decisao' AND text='usar sqlite' AND status='confirmada'"
        ).fetchall()
        self.assertEqual(len(rows), 1)  # a decisão revertida volta a ter uma linha ativa
