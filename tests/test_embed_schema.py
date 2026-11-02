import os
import sqlite3
import tempfile
import unittest

from contextclaude import config, db, embed


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()


class TestSchema(Base):
    def test_connect_adds_indexed_column_to_old_db(self):
        path = config.db_path()
        path.parent.mkdir(parents=True)
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, "
                    "project TEXT, kind TEXT, text TEXT, created_at TEXT)")
        old.commit()
        old.close()
        conn = db.connect()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(facts)")]
        conn.close()
        self.assertIn("indexed", cols)

    def test_new_db_has_indexed_and_injected(self):
        conn = db.connect()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(facts)")]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        self.assertIn("indexed", cols)
        self.assertIn("injected", tables)


class TestEmbed(unittest.TestCase):
    def test_embed_posts_model_and_returns_vectors(self):
        calls = []

        def post(path, body, timeout=300):
            calls.append((path, body, timeout))
            return {"embeddings": [[1.0], [2.0]]}

        out = embed.embed(["a", "b"], timeout=7, post=post)
        self.assertEqual(out, [[1.0], [2.0]])
        path, body, timeout = calls[0]
        self.assertEqual(path, "/api/embed")
        self.assertEqual(body, {"model": config.EMBED_MODEL, "input": ["a", "b"], "keep_alive": "10m"})
        self.assertEqual(timeout, 7)

    def test_embed_empty_list_does_not_post(self):
        def post(*a, **k):
            raise AssertionError("não deveria chamar")

        self.assertEqual(embed.embed([], post=post), [])


if __name__ == "__main__":
    unittest.main()
