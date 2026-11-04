import json
import os
import subprocess
import sys
import tempfile
import unittest

from contextclaude import config, db, hooks

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "hook.py")
LONG = "como configurar o banco de dados do projeto"


def hit(i, text="decisão importante", dist=0.2, tipo="decisao"):
    return {"id": i, "text": text, "projeto": "p", "tipo": tipo, "data": "2026-10-29T10:00:00", "distance": dist}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name
        os.environ["CONTEXTCLAUDE_NO_SPAWN"] = "1"
        self.proj = os.path.join(self.tmp.name, "p")
        os.makedirs(self.proj)
        self.calls = []

    def tearDown(self):
        os.environ.pop("CONTEXTCLAUDE_NO_SPAWN", None)
        self.tmp.cleanup()

    def mark(self):
        open(os.path.join(self.proj, ".contextclaude"), "w").close()

    def seed(self, hits):
        conn = db.connect()
        conn.executemany("INSERT OR IGNORE INTO facts(id, session_id, project, kind, text, created_at) "
                         "VALUES(?,?,?,?,?,?)",
                         [(h["id"], "s", h["projeto"], h["tipo"], h["text"], h["data"]) for h in hits])
        conn.commit()
        conn.close()

    def run_hook(self, hits, prompt=LONG, sid="s1"):
        if isinstance(hits, list):  # recall só devolve fatos existentes (active_fact_ids)
            self.seed(hits)

        def search(query, **kw):
            self.calls.append((query, kw))
            if isinstance(hits, Exception):
                raise hits
            return hits

        return hooks.prompt_submit({"session_id": sid, "cwd": self.proj, "prompt": prompt}, search=search)


class TestPromptSubmit(Base):
    def test_no_marker_is_silent_and_does_not_search(self):
        self.assertEqual(self.run_hook([hit(1)]), "")
        self.assertEqual(self.calls, [])

    def test_short_prompt_is_silent(self):
        self.mark()
        self.assertEqual(self.run_hook([hit(1)], prompt="oi"), "")
        self.assertEqual(self.calls, [])

    def test_secret_in_prompt_is_redacted_before_search(self):
        self.mark()
        self.run_hook([], prompt="use password=hunter2 para conectar no banco")
        self.assertNotIn("hunter2", self.calls[0][0])
        self.assertEqual(self.calls[0][1]["projeto"], "p")
        self.assertEqual(self.calls[0][1]["n"], config.PROMPT_N)
        self.assertEqual(self.calls[0][1]["timeout"], config.PROMPT_EMBED_TIMEOUT)

    def test_distance_filter(self):
        self.mark()
        out = self.run_hook([hit(1, "perto", 0.2), hit(2, "longe", 0.9)])
        self.assertIn("perto", out)
        self.assertNotIn("longe", out)
        self.assertEqual(self.run_hook([hit(3, "longe", 0.9)], sid="s2"), "")

    def test_output_capped_at_800(self):
        self.mark()
        out = self.run_hook([hit(i, "x" * 500) for i in range(1, 4)])
        self.assertLessEqual(len(out), config.PROMPT_MAX_CHARS)
        self.assertTrue(out.startswith("Memória relevante"))

    def test_not_repeated_in_same_session_but_in_another(self):
        self.mark()
        self.assertIn("decisão importante", self.run_hook([hit(1)], sid="s1"))
        self.assertEqual(self.run_hook([hit(1)], sid="s1"), "")
        self.assertIn("decisão importante", self.run_hook([hit(1)], sid="s2"))

    def test_item_cut_by_cap_is_not_marked_injected(self):
        self.mark()
        self.run_hook([hit(1, "a" * 700), hit(2, "b" * 700)], sid="s1")
        again = self.run_hook([hit(1, "a" * 700), hit(2, "b" * 700)], sid="s1")
        self.assertIn("b", again)

    def test_search_failure_is_silent(self):
        self.mark()
        self.assertEqual(self.run_hook(ConnectionError("fora")), "")

    def test_prompt_submit_excludes_superseded_facts(self):
        self.mark()
        conn = db.connect()
        conn.execute("INSERT INTO facts(id, session_id, project, kind, text, status, created_at) "
                     "VALUES(1,'s','p','decisao','decisão antiga','substituida','2026-10-29T10:00:00')")
        conn.execute("INSERT INTO facts(id, session_id, project, kind, text, status, created_at) "
                     "VALUES(2,'s','p','decisao','decisão vigente','confirmada','2026-10-29T10:00:00')")
        conn.commit()
        conn.close()
        out = self.run_hook([hit(1, "decisão antiga"), hit(2, "decisão vigente")])
        self.assertNotIn("decisão antiga", out)
        self.assertIn("decisão vigente", out)


class TestHookScript(Base):
    def test_prompt_without_marker_prints_nothing(self):
        env = {**os.environ, "CONTEXTCLAUDE_ROOT": self.tmp.name}
        payload = json.dumps({"session_id": "s", "cwd": self.proj, "prompt": LONG})
        r = subprocess.run([sys.executable, HOOK, "prompt"], input=payload.encode("utf-8"),
                           capture_output=True, env=env, timeout=30)
        self.assertEqual((r.returncode, r.stdout), (0, b""))


    def test_payload_with_utf8_bom_is_accepted(self):
        self.mark()
        env = {**os.environ, "CONTEXTCLAUDE_ROOT": self.tmp.name}
        payload = b"\xef\xbb\xbf" + json.dumps({"session_id": "s", "cwd": self.proj, "prompt": LONG}).encode("utf-8")
        r = subprocess.run([sys.executable, HOOK, "prompt"], input=payload, capture_output=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0)
        log = config.log_path()
        self.assertFalse(log.exists() and "BOM" in log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
