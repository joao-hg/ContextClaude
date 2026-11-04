"""Regressões dos achados da revisão final."""
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime
from unittest import mock

from contextclaude import db, hooks, notes, pipeline, transcript
from contextclaude.redact import redact

DATA = {"fatos": ["f"], "decisoes": ["d"], "feitos": [], "relacoes": [], "resumo": "r"}


def now():
    return datetime(2026, 10, 29, 12, 0)


class TestRedactGaps(unittest.TestCase):
    def test_more_secret_formats(self):
        cases = {
            "DB_PASSWORD=hunter2": "hunter2",
            "GITHUB_TOKEN=ghpx12": "ghpx12",
            "AWS_SECRET_ACCESS_KEY=abcd1234": "abcd1234",
            '{"password": "hunter2"}': "hunter2",
            "postgres://u:hunter2@h/db": "hunter2",
            "github_pat_11AAAAAAA0123456789abcd": "11AAAAAAA0123",
            "xoxb-123456789012-abcdefgh": "123456789012",
            "AIza" + "A" * 35: "AAAAAAAAAA",
            "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM.SflKxwRJSMeKKF2QT4": "SflKxwRJ",
            "Authorization: Basic dXNlcjpwYXNzMTIz": "dXNlcjpw",
            "a senha é hunter2": "hunter2",
        }
        for text, secret in cases.items():
            out = redact(text)
            self.assertNotIn(secret, out, text)
            self.assertIn("[REDACTED]", out, text)

    def test_quoted_value_with_space_fully_removed(self):
        self.assertNotIn(' b"', redact('password = "a b"'))

    def test_pem_without_end_is_removed(self):
        self.assertNotIn("AAAA", redact("-----BEGIN PRIVATE KEY-----\nAAAAAAAA"))

    def test_redact_happens_before_truncation_in_transcript(self):
        key = "-----BEGIN PRIVATE KEY-----\n" + "K" * 5000 + "\n-----END PRIVATE KEY-----"
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.jsonl")
            with open(p, "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "user", "message": {"role": "user", "content": key}}) + "\n")
            lines, _ = transcript.read_new(p, 0)
        self.assertNotIn("KKKK", lines[0])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name
        os.environ["CONTEXTCLAUDE_HOME"] = r"C:\Users\João"
        os.environ["CONTEXTCLAUDE_NO_SPAWN"] = "1"
        self.conn = db.connect()
        self.tpath = os.path.join(self.tmp.name, "t.jsonl")

    def tearDown(self):
        os.environ.pop("CONTEXTCLAUDE_NO_SPAWN", None)
        self.conn.close()
        self.tmp.cleanup()

    def transcript(self, path, *texts, mode="w"):
        with open(path, mode, encoding="utf-8", newline="\n") as f:
            for t in texts:
                f.write(json.dumps({"type": "user", "message": {"role": "user", "content": t}}, ensure_ascii=False) + "\n")

    def run_q(self, extract=lambda lines: dict(DATA), merge=lambda old, d: "RESUMO"):
        return pipeline.process_queue(self.conn, extract=extract, merge=merge, now=now)

    def row(self, sid="s1"):
        return self.conn.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()


class TestPipelineFixes(Base):
    def test_merge_runs_after_commit_outside_any_transaction(self):
        """O merge (chamada lenta ao Ollama) roda no fim da rodada, com tudo já commitado:
        não segura o write lock do SQLite."""
        self.transcript(self.tpath, "x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        seen = []

        def merge(old, d):
            other = db.connect()
            seen.append((other.execute("SELECT status FROM sessions").fetchone()[0], self.conn.in_transaction))
            other.close()
            return "R"

        self.run_q(merge=merge)
        self.assertEqual(seen, [("done", False)])
        self.assertEqual(self.row()["status"], "done")  # commitado após as etapas

    def test_model_output_and_old_summary_are_redacted(self):
        self.transcript(self.tpath, "x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        notes.write_summary("app", "velho password=oldsecret1")
        olds = []

        def extract(lines):
            return {**DATA, "decisoes": ["usar password=hunter2"]}

        def merge(old, d):
            olds.append(old)
            return "novo token=abc123456"

        self.run_q(extract=extract, merge=merge)
        self.assertNotIn("oldsecret1", olds[0])
        everything = notes.read_summary("app") + "".join(
            p.read_text(encoding="utf-8") for p in (notes._dir("app") / "Sessões").glob("*.md"))
        self.assertNotIn("hunter2", everything)
        self.assertNotIn("abc123456", everything)
        self.assertNotIn("hunter2", " ".join(r[0] for r in self.conn.execute("SELECT text FROM facts")))

    def test_http_error_counts_attempt_instead_of_blocking(self):
        self.transcript(self.tpath, "x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

        def boom(lines):
            raise urllib.error.HTTPError("u", 500, "err", {}, None)

        self.run_q(extract=boom)
        self.assertEqual((self.row()["status"], self.row()["attempts"]), ("queued", 1))

    def test_one_failing_session_does_not_block_the_queue(self):
        p2 = os.path.join(self.tmp.name, "t2.jsonl")
        self.transcript(self.tpath, "x")
        self.transcript(p2, "y")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        db.enqueue(self.conn, "s2", r"C:\dev\app", p2)
        real = pipeline._save

        def flaky(conn, row, *a, **kw):
            if row["session_id"] == "s1":
                raise PermissionError("bloqueado")
            return real(conn, row, *a, **kw)

        with mock.patch.object(pipeline, "_save", flaky):
            self.run_q()
        self.assertEqual(self.row("s2")["status"], "done")
        self.assertEqual(self.row("s1")["attempts"], 1)

    def test_session_note_failure_keeps_facts_and_finishes_session(self):
        """Fatos no SQLite são a fonte da verdade; a nota se reconstrói com `cc.py vault`.
        Falha ao gravar a nota (ex.: Obsidian segurando o arquivo) não refaz a extração."""
        self.transcript(self.tpath, "x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        with mock.patch.object(notes, "write_session_note", side_effect=PermissionError("bloqueado")):
            self.run_q()
        self.assertEqual(self.row()["status"], "done")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0], 2)

    def test_text_appended_during_run_is_processed_in_same_run(self):
        self.transcript(self.tpath, "um")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        calls = []

        def extract(lines):
            if not calls:
                self.transcript(self.tpath, "dois", mode="a")
            calls.append(lines)
            return dict(DATA)

        self.run_q(extract=extract)
        self.assertEqual(self.row()["offset"], os.path.getsize(self.tpath))
        self.assertEqual(len(calls), 2)

    def test_partial_trailing_line_terminates(self):
        self.transcript(self.tpath, "um")
        with open(self.tpath, "a", encoding="utf-8") as f:
            f.write('{"type":"user","mess')
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        self.run_q()
        self.assertEqual(self.row()["status"], "done")

    def test_transcript_smaller_than_offset_restarts_from_zero(self):
        self.transcript(self.tpath, "um texto bem longo para ter offset grande")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        self.transcript(self.tpath, "novo")
        self.run_q()
        self.assertEqual(self.row()["offset"], os.path.getsize(self.tpath))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM facts WHERE text='d'").fetchone()[0], 1)  # decisão exata não é reinserida


class TestDbFixes(Base):
    def test_enqueue_with_new_transcript_path_resets_progress(self):
        db.enqueue(self.conn, "s1", "c", "a.jsonl")
        self.conn.execute("UPDATE sessions SET status='done', offset=99, attempts=3")
        db.enqueue(self.conn, "s1", "c", "b.jsonl")
        r = self.row()
        self.assertEqual((r["status"], r["offset"], r["attempts"], r["transcript_path"]), ("queued", 0, 0, "b.jsonl"))

    def test_enqueue_alternating_paths_restores_each_offset(self):
        """Sessão retomada em worktree: hooks alternam entre dois JSONL com o mesmo id."""
        db.enqueue(self.conn, "s1", "c", "a.jsonl")
        self.conn.execute("UPDATE sessions SET status='done', offset=500, attempts=2")
        db.enqueue(self.conn, "s1", "c", "b.jsonl")
        self.conn.execute("UPDATE sessions SET offset=70")
        db.enqueue(self.conn, "s1", "c", "a.jsonl")
        r = self.row()
        self.assertEqual((r["transcript_path"], r["offset"]), ("a.jsonl", 500))
        db.enqueue(self.conn, "s1", "c", "b.jsonl")
        self.assertEqual(self.row()["offset"], 70)

    def test_wal_enabled(self):
        self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")


class TestHookFixes(Base):
    def test_enqueue_failure_still_spawns(self):
        spawned = []
        with mock.patch.object(db, "enqueue", side_effect=RuntimeError("locked")):
            out = hooks.session_start({"session_id": "s", "cwd": "C:\\x"}, spawn=lambda: spawned.append(1))
        self.assertEqual((out, spawned), ("", [1]))

    def test_recall_error_still_spawns(self):
        proj = os.path.join(self.tmp.name, "p")
        os.makedirs(proj)
        open(os.path.join(proj, ".contextclaude"), "w").close()
        d = notes._dir("p")
        d.mkdir(parents=True)
        (d / "_resumo.md").write_bytes(b"\xff\xfe\x00bad")
        spawned = []
        out = hooks.session_start({"session_id": "s", "cwd": proj}, spawn=lambda: spawned.append(1))
        self.assertEqual((out, spawned), ("", [1]))
