import json
import os
import subprocess
import sys
import tempfile
import unittest

from contextclaude import config, db, hooks, notes

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "hook.py")


class TestHooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name
        os.environ["CONTEXTCLAUDE_HOME"] = r"C:\Users\João"
        os.environ["CONTEXTCLAUDE_NO_SPAWN"] = "1"
        self.proj = os.path.join(self.tmp.name, "meuprojeto")
        os.makedirs(self.proj)
        self.spawned = []

    def tearDown(self):
        os.environ.pop("CONTEXTCLAUDE_NO_SPAWN", None)
        self.tmp.cleanup()

    def payload(self, **kw):
        return {"session_id": "s1", "transcript_path": "t.jsonl", "cwd": self.proj, **kw}

    def start(self):
        return hooks.session_start(self.payload(), spawn=lambda: self.spawned.append(1))

    def test_start_without_marker_returns_empty(self):
        notes.write_summary("meuprojeto", "segredo do projeto")
        self.assertEqual(self.start(), "")

    def test_start_with_marker_returns_summary_truncated(self):
        open(os.path.join(self.proj, ".contextclaude"), "w").close()
        notes.write_summary("meuprojeto", "A" * 5000)
        out = self.start()
        self.assertTrue(out.startswith(hooks.RECALL_HEADER))
        self.assertEqual(len(out), len(hooks.RECALL_HEADER) + config.RECALL_MAX_CHARS)

    def test_start_marker_without_summary_returns_empty(self):
        open(os.path.join(self.proj, ".contextclaude"), "w").close()
        self.assertEqual(self.start(), "")

    def test_start_enqueues_current_session(self):
        self.start()
        conn = db.connect()
        row = conn.execute("SELECT status, cwd FROM sessions WHERE session_id='s1'").fetchone()
        conn.close()
        self.assertEqual((row[0], row[1]), ("queued", self.proj))
        self.assertEqual(len(self.spawned), 1)

    def test_start_only_spawns_worker_process(self):
        from unittest import mock
        env = {k: v for k, v in os.environ.items() if k != "CONTEXTCLAUDE_NO_SPAWN"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(hooks.subprocess, "Popen") as popen,
        ):
            hooks.session_start(self.payload())
        self.assertEqual(popen.call_count, 1)
        self.assertTrue(popen.call_args[0][0][1].endswith("worker.py"))
        self.assertFalse(hasattr(hooks, "_spawn_chibi"))

    def test_end_enqueues_and_spawns(self):
        hooks.session_end(self.payload(), spawn=lambda: self.spawned.append(1))
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)
        self.assertEqual(len(self.spawned), 1)

    def run_cli(self, mode, stdin: bytes):
        return subprocess.run([sys.executable, HOOK, mode], input=stdin, capture_output=True, env=os.environ.copy())

    def test_hook_cli_bad_stdin_exits_zero_silent(self):
        for stdin in (b"", b"lixo{", b"[1,2]", b"\xff\xfe"):
            for mode in ("start", "end", "nada"):
                r = self.run_cli(mode, stdin)
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""), (mode, stdin))

    def test_hook_cli_accented_cwd(self):
        acc = os.path.join(self.tmp.name, "Programação")
        os.makedirs(acc)
        open(os.path.join(acc, ".contextclaude"), "w").close()
        notes.write_summary("Programação", "decisão: usar açaí")
        payload = json.dumps({"session_id": "s9", "transcript_path": "x", "cwd": acc}, ensure_ascii=False)
        r = self.run_cli("start", payload.encode("utf-8"))
        self.assertEqual((r.returncode, r.stderr), (0, b""))
        ctx = json.loads(r.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("decisão: usar açaí", ctx)
