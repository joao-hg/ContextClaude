"""Regressões dos achados da revisão da Fase 2."""
import gc
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from contextclaude import config, db, hooks, lock, vectors
from contextclaude.redact import redact

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import cc  # noqa: E402

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "hook.py")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name
        os.environ["CONTEXTCLAUDE_NO_SPAWN"] = "1"

    def tearDown(self):
        os.environ.pop("CONTEXTCLAUDE_NO_SPAWN", None)
        vectors.close()
        gc.collect()
        self.tmp.cleanup()


class TestRedactPerformance(unittest.TestCase):
    def test_redact_is_linear_on_long_blob(self):
        t = time.time()
        redact("A" * 20000)
        self.assertLess(time.time() - t, 1.0)

    def test_redact_still_catches_prefixed_keys(self):
        for text, secret in {"AWS_SECRET_ACCESS_KEY=abcd1234": "abcd1234", "db.password: hunter2": "hunter2"}.items():
            self.assertNotIn(secret, redact(text))


class TestPromptHookCost(Base):
    def test_no_redact_without_marker(self):
        proj = os.path.join(self.tmp.name, "p")
        os.makedirs(proj)
        with mock.patch.object(hooks, "redact") as r:
            hooks.prompt_submit({"session_id": "s", "cwd": proj, "prompt": "x" * 50000}, search=lambda *a, **k: [])
        r.assert_not_called()

    def test_prompt_is_truncated_before_search(self):
        proj = os.path.join(self.tmp.name, "p")
        os.makedirs(proj)
        open(os.path.join(proj, ".contextclaude"), "w").close()
        seen = []
        hooks.prompt_submit({"session_id": "s", "cwd": proj, "prompt": "palavra " * 5000},
                            search=lambda q, **k: seen.append(q) or [])
        self.assertLessEqual(len(seen[0]), config.PROMPT_INPUT_MAX)

    def test_duplicate_texts_are_collapsed(self):
        proj = os.path.join(self.tmp.name, "p")
        os.makedirs(proj)
        open(os.path.join(proj, ".contextclaude"), "w").close()
        hits = [{"id": i, "text": "mesma decisão", "projeto": "p", "tipo": "decisao",
                 "data": "2026-10-29T10:00:00", "distance": 0.2} for i in (1, 2, 3)]
        conn = db.connect()
        conn.executemany("INSERT INTO facts(id, session_id, project, kind, text, created_at) VALUES(?,?,?,?,?,?)",
                         [(h["id"], "s", "p", "decisao", "mesma decisão", h["data"]) for h in hits])
        conn.commit()
        conn.close()
        out = hooks.prompt_submit({"session_id": "s", "cwd": proj, "prompt": "como configurar o banco de dados?"},
                                  search=lambda *a, **k: hits)
        self.assertEqual(out.count("mesma decisão"), 1)


class TestMigrationRace(Base):
    def test_alter_when_column_already_exists_does_not_raise(self):
        conn = db.connect()
        try:
            db._add_indexed_column(conn)  # outro processo já adicionou a coluna
        finally:
            conn.close()


class TestPoisonBatch(Base):
    def setUp(self):
        super().setUp()
        self.conn = db.connect()
        for t in ("bom um", "venenoso", "bom dois"):
            self.conn.execute("INSERT INTO facts(session_id, project, kind, text, created_at) "
                              "VALUES('s','p','fato',?,'2026-10-29T10:00:00')", (t,))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    @staticmethod
    def picky(texts, timeout=60):
        if any("venenoso" in t for t in texts):
            raise ValueError("NaN no embedding")
        return [[1.0, 0.0] for _ in texts]

    def test_bad_item_does_not_block_the_rest(self):
        n = vectors.index_pending(self.conn, self.picky)
        flags = [r[0] for r in self.conn.execute("SELECT indexed FROM facts ORDER BY id")]
        self.assertEqual(flags, [1, 2, 1])
        self.assertEqual(n, 2)

    def test_ollama_down_still_propagates(self):
        def down(texts, timeout=60):
            raise ConnectionError("fora")

        with self.assertRaises(ConnectionError):
            vectors.index_pending(self.conn, down)
        self.assertEqual([r[0] for r in self.conn.execute("SELECT indexed FROM facts")], [0, 0, 0])

    def test_missing_vectors_are_never_marked_indexed(self):
        n = vectors.index_pending(self.conn, lambda texts, timeout=60: [])
        self.assertEqual(n, 0)
        self.assertEqual([r[0] for r in self.conn.execute("SELECT indexed FROM facts ORDER BY id")], [2, 2, 2])


class TestIndexarLock(Base):
    def test_indexar_refuses_while_worker_holds_lock(self):
        handle = lock.acquire()
        out = []
        try:
            with mock.patch.object(vectors, "rebuild", side_effect=AssertionError("não deveria")), \
                    mock.patch.object(cc.ollama, "ensure_running"):
                code = cc.main(["indexar"], out=out.append)
        finally:
            lock.release(handle)
        self.assertEqual(code, 1)
        self.assertIn("worker", out[0])

    def test_buscar_rejects_non_positive_n(self):
        out = []
        with mock.patch.object(vectors, "search", return_value=[]) as s:
            code = cc.main(["buscar", "x", "-n", "0"], out=out.append)
        self.assertEqual(code, 1)
        s.assert_not_called()


class TestHookWatchdog(Base):
    def test_watchdog_exits_zero_with_clean_stdout(self):
        env = {**os.environ, "CONTEXTCLAUDE_WATCHDOG": "0"}
        r = subprocess.run([sys.executable, HOOK, "prompt"], input=b"{}", capture_output=True, env=env, timeout=30)
        self.assertEqual((r.returncode, r.stdout), (0, b""))


if __name__ == "__main__":
    unittest.main()
