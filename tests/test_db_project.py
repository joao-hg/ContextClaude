import os
import tempfile
import unittest

from contextclaude import db, project


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name
        os.environ["CONTEXTCLAUDE_HOME"] = r"C:\Users\João"
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class TestProject(Base):
    def test_project_name_home_is_geral(self):
        self.assertEqual(project.project_name(self.conn, r"C:\Users\João"), "_geral")

    def test_project_name_basename(self):
        self.assertEqual(project.project_name(self.conn, r"C:\dev\app"), "app")

    def test_project_name_same_basename_gets_hash_suffix(self):
        first = project.project_name(self.conn, r"C:\dev\app")
        second = project.project_name(self.conn, r"D:\outro\app")
        self.assertEqual(first, "app")
        self.assertRegex(second, r"^app-[0-9a-f]{6}$")
        self.assertEqual(project.project_name(self.conn, r"D:\outro\app"), second)

    def test_project_name_accented(self):
        self.assertEqual(project.project_name(self.conn, r"C:\Users\João\Programação"), "Programação")


class TestDb(Base):
    def test_enqueue_idempotent(self):
        db.enqueue(self.conn, "s1", "c", "t")
        db.enqueue(self.conn, "s1", "c", "t")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)

    def test_enqueue_does_not_downgrade_done(self):
        db.enqueue(self.conn, "s1", "c", "t")
        self.conn.execute("UPDATE sessions SET status='done'")
        db.enqueue(self.conn, "s1", "c", "t")
        self.assertEqual(self.conn.execute("SELECT status FROM sessions").fetchone()[0], "done")

    def test_reset_session_requeues_errored_session(self):
        db.enqueue(self.conn, "s1", "c", "t")
        self.conn.execute("UPDATE sessions SET status='error', attempts=5")
        self.assertTrue(db.reset_session(self.conn, "s1"))
        row = self.conn.execute("SELECT status, attempts FROM sessions WHERE session_id='s1'").fetchone()
        self.assertEqual((row["status"], row["attempts"]), ("queued", 0))

    def test_reset_session_missing_returns_false(self):
        self.assertFalse(db.reset_session(self.conn, "inexistente"))


class TestChibiRounds(Base):
    def test_insert_and_recent_rounds_returns_newest_first(self):
        db.insert_round(self.conn, "2026-10-31T10:00:00", "2026-10-31T10:01:00", 60.0,
                         2, 1000, 3, 1, 0, "done")
        db.insert_round(self.conn, "2026-10-31T10:02:00", "2026-10-31T10:02:30", 30.0,
                         1, 500, 1, 0, 0, "done")
        rows = db.recent_rounds(self.conn, limit=10)
        self.assertEqual([r["sessoes"] for r in rows], [1, 2])

    def test_round_throughput_none_when_no_done_rounds(self):
        self.assertIsNone(db.round_throughput(self.conn))

    def test_round_throughput_is_aggregate_sum_not_average_of_ratios(self):
        # média das taxas: (100/1 + 5000/100) / 2 = 75.0
        # agregada: (100+5000)/(1+100) = 50.49...
        db.insert_round(self.conn, "t1", "t1", 1.0, 1, 100, 0, 0, 0, "done")
        db.insert_round(self.conn, "t2", "t2", 100.0, 1, 5000, 0, 0, 0, "done")
        self.assertAlmostEqual(db.round_throughput(self.conn), 5100 / 101, places=3)

    def test_round_throughput_ignores_error_rounds(self):
        db.insert_round(self.conn, "t1", "t1", 1.0, 1, 100, 0, 0, 0, "error", "falhou")
        self.assertIsNone(db.round_throughput(self.conn))

    def test_insert_round_prunes_to_last_50(self):
        for i in range(55):
            db.insert_round(self.conn, str(i), str(i), 1.0, 1, 10, 0, 0, 0, "done")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM chibi_rounds").fetchone()[0], 50)
