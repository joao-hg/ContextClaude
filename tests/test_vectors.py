import gc
import os
import shutil
import tempfile
import unittest
import zlib

from contextclaude import config, db, vectors

WORDS = ["banco", "dados", "sqlite", "obsidian", "nota", "hook", "ollama", "token"]


def fake_embed(texts, timeout=60):
    out = []
    for t in texts:
        low = t.lower()
        v = [1.0 if w in low else 0.0 for w in WORDS]
        v[0] += 0.01 * (zlib.crc32(t.encode()) % 7)  # evita vetor nulo
        out.append(v if any(v) else [0.001] * len(WORDS))
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        vectors.close()
        gc.collect()
        self.tmp.cleanup()

    def add(self, text, project="app", kind="fato"):
        self.conn.execute(
            "INSERT INTO facts(session_id, project, kind, text, created_at) VALUES('s',?,?,?,'2026-10-29T10:00:00')",
            (project, kind, text))
        self.conn.commit()

    def indexed(self):
        return [r[0] for r in self.conn.execute("SELECT indexed FROM facts ORDER BY id")]


class TestVectors(Base):
    def test_index_pending_marks_indexed(self):
        self.add("usa sqlite como banco")
        self.add("hook do ollama")
        self.assertEqual(vectors.index_pending(self.conn, fake_embed), 2)
        self.assertEqual(self.indexed(), [1, 1])

    def test_index_twice_no_duplicates(self):
        self.add("usa sqlite como banco")
        vectors.index_pending(self.conn, fake_embed)
        self.conn.execute("UPDATE facts SET indexed=0")
        self.conn.commit()
        vectors.index_pending(self.conn, fake_embed)
        self.assertEqual(vectors._collection().count(), 1)

    def test_embed_failure_leaves_pending(self):
        self.add("usa sqlite como banco")

        def boom(texts, timeout=60):
            raise ConnectionError("ollama fora")

        with self.assertRaises(ConnectionError):
            vectors.index_pending(self.conn, boom)
        self.assertEqual(self.indexed(), [0])

    def test_search_returns_closest_first_and_filters_project(self):
        self.add("sqlite banco de dados")
        self.add("nota do obsidian")
        self.add("sqlite banco de dados", project="outro")
        vectors.index_pending(self.conn, fake_embed)
        res = vectors.search("banco sqlite", embed_fn=fake_embed, n=5)
        self.assertEqual(res[0]["text"], "sqlite banco de dados")
        self.assertEqual({"id", "text", "projeto", "tipo", "data", "distance"}, set(res[0]))
        self.assertEqual(res, sorted(res, key=lambda r: r["distance"]))
        only = vectors.search("banco sqlite", projeto="outro", embed_fn=fake_embed)
        self.assertEqual([r["projeto"] for r in only], ["outro"])

    def test_search_filters_by_kind_when_given(self):
        self.add("sqlite banco de dados", kind="decisao")
        self.add("sqlite banco de dados", kind="fato")
        vectors.index_pending(self.conn, fake_embed)
        hits = vectors.search("banco sqlite", embed_fn=fake_embed, kind="decisao", n=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["tipo"], "decisao")

    def test_search_empty_when_no_collection(self):
        self.assertEqual(vectors.search("qualquer", embed_fn=fake_embed), [])

    def test_rebuild_restores_after_deleting_chroma_dir(self):
        self.add("usa sqlite como banco")
        vectors.index_pending(self.conn, fake_embed)
        vectors.close()
        gc.collect()
        shutil.rmtree(config.chroma_path())
        self.assertEqual(vectors.rebuild(self.conn, fake_embed), 1)
        self.assertEqual(len(vectors.search("sqlite banco", embed_fn=fake_embed)), 1)


if __name__ == "__main__":
    unittest.main()
