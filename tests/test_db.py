import os
import sys
import tempfile
import unittest

sys.path.insert(0, "src")
from contextclaude import db


class TestFactStateColumns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp

    def tearDown(self):
        os.environ.pop("CONTEXTCLAUDE_ROOT", None)

    def test_connect_adds_status_and_supersedes_columns(self):
        conn = db.connect()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(facts)")]
        self.assertIn("status", cols)
        self.assertIn("supersedes", cols)

    def test_existing_rows_get_confirmada_default_on_migration(self):
        conn = db.connect()
        conn.execute(
            "INSERT INTO facts(session_id, project, kind, text, created_at) VALUES('s','p','fato','x','2026-01-01')"
        )
        conn.commit()
        conn.close()
        conn2 = db.connect()  # reabre: simula migração rodando contra tabela já populada
        row = conn2.execute("SELECT status, supersedes FROM facts").fetchone()
        self.assertEqual(row["status"], "confirmada")
        self.assertIsNone(row["supersedes"])

    def test_fact_exists_matches_normalized_text(self):
        conn = db.connect()
        conn.execute(
            "INSERT INTO facts(session_id, project, kind, text, created_at) VALUES('s','p','fato','Usa   SQLite','2026-01-01')"
        )
        conn.commit()
        self.assertTrue(db.fact_exists(conn, "p", "fato", "usa sqlite"))
        self.assertFalse(db.fact_exists(conn, "p", "fato", "usa postgres"))
        self.assertFalse(db.fact_exists(conn, "outro-projeto", "fato", "usa sqlite"))

    def test_active_fact_ids_excludes_substituida(self):
        conn = db.connect()
        conn.execute("INSERT INTO facts(session_id, project, kind, text, created_at) VALUES('s','p','decisao','a','2026-01-01')")
        conn.execute("INSERT INTO facts(session_id, project, kind, text, created_at) VALUES('s','p','decisao','b','2026-01-01')")
        conn.commit()
        ids = [r["id"] for r in conn.execute("SELECT id FROM facts ORDER BY id")]
        conn.execute("UPDATE facts SET status='substituida' WHERE id=?", (ids[0],))
        conn.commit()
        self.assertEqual(db.active_fact_ids(conn, ids), {ids[1]})
        self.assertEqual(db.active_fact_ids(conn, []), set())


if __name__ == "__main__":
    unittest.main()
