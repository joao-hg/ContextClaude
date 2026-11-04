import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, "src")

from contextclaude import config, db, pause, pipeline

DATA = {"fatos": ["f"], "decisoes": [], "feitos": ["x"], "titulo": "T", "relacoes": [], "resumo": "r"}


class TestPause(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)  # registrado antes da conexão: roda depois de fechá-la
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name
        os.environ["CONTEXTCLAUDE_HOME"] = r"C:\Users\João"

    def test_hold_creates_and_removes(self):
        with pause.hold():
            self.assertTrue(config.pause_path().exists())
            self.assertTrue(pause.is_paused())
        self.assertFalse(config.pause_path().exists())
        self.assertFalse(pause.is_paused())

    def test_hold_refreshes_mtime_while_held(self):
        import time
        with mock.patch.object(pause, "PAUSE_REFRESH_S", 0.05):
            with pause.hold():
                path = config.pause_path()
                os.utime(path, (1000, 1000))  # como se estivesse velho
                deadline = time.time() + 3
                while time.time() < deadline and path.stat().st_mtime < 2000:
                    time.sleep(0.02)
                self.assertGreater(path.stat().st_mtime, 2000)
                self.assertTrue(pause.is_paused())
            self.assertFalse(path.exists())
            time.sleep(0.2)
            self.assertFalse(path.exists())  # a thread não recria o arquivo

    def test_wait_returns_when_released(self):
        config.pause_path().parent.mkdir(parents=True, exist_ok=True)
        config.pause_path().write_text("x")
        calls = []

        def fake_sleep(s):
            calls.append(s)
            if len(calls) == 3:
                config.pause_path().unlink()
        waited = pause.wait(sleep=fake_sleep, poll_s=1.0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(waited, 3.0)

    def test_wait_ignores_stale_pause(self):
        config.pause_path().parent.mkdir(parents=True, exist_ok=True)
        config.pause_path().write_text("x")
        mtime = config.pause_path().stat().st_mtime
        self.assertEqual(pause.wait(sleep=self.fail, now=lambda: mtime + 301), 0.0)

    def test_pipeline_waits_before_each_block(self):
        conn = db.connect()
        self.addCleanup(conn.close)
        tpath = os.path.join(self.tmp.name, "t.jsonl")
        with open(tpath, "w", encoding="utf-8", newline="\n") as f:
            for t in ("primeiro bloco", "segundo bloco"):
                f.write(json.dumps({"type": "user", "message": {"role": "user", "content": t}}) + "\n")
        db.enqueue(conn, "s1", r"C:\dev\app", tpath)
        order = []

        def extract(lines):
            order.append("extract")
            return dict(DATA)
        consolidate = mock.Mock(side_effect=lambda c, d: order.append("consolidate") or
                                {"titulo": "T2", "resumo": "R", "feitos": ["a"]})
        with mock.patch.object(pipeline, "CHUNK_CHARS", 10), \
                mock.patch.object(pipeline.pause, "wait", side_effect=lambda: order.append("wait") or 0.0):
            pipeline.process_queue(conn, extract=extract, merge=lambda o, d: "R",
                                   now=lambda: datetime(2026, 10, 29, 12, 0), consolidate=consolidate)
        self.assertEqual(order[:4], ["wait", "extract", "wait", "extract"])
        i = order.index("consolidate")
        self.assertEqual(order[i - 1], "wait")

    def test_flush_summaries_waits_before_llm_work(self):
        order = []
        with mock.patch.object(pipeline.pause, "wait", side_effect=lambda: order.append("wait") or 0.0),                 mock.patch.object(pipeline, "_aggregate", return_value={"decisoes": [], "resumo": ""}),                 mock.patch.object(pipeline, "_compute_summary", side_effect=lambda *a: order.append("llm") or ""):
            pipeline._flush_summaries({"p1": [DATA], "p2": [DATA]}, merge=None)
        self.assertEqual(order, ["wait", "llm", "wait", "llm"])


if __name__ == "__main__":
    unittest.main()
