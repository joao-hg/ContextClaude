import os
import sys
import tempfile
import unittest

sys.path.insert(0, "src")
from contextclaude import db, appdb


class TestAppDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp

    def tearDown(self):
        os.environ.pop("CONTEXTCLAUDE_ROOT", None)

    def test_schema_creates_app_tables(self):
        conn = db.connect()
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"conversations", "messages", "moves"} <= names)

    def test_title_from(self):
        self.assertEqual(appdb.title_from("  Organize   meus Downloads\npor assunto"), "Organize meus Downloads")
        self.assertEqual(appdb.title_from(""), "Nova conversa")
        t = appdb.title_from("palavra " * 30)
        self.assertLessEqual(len(t), 48)
        self.assertTrue(t.endswith("…"))

    def test_conversation_roundtrip_and_search(self):
        conn = db.connect()
        a = appdb.create_conversation(conn, "O que fizemos ontem?")
        appdb.add_message(conn, a, "user", "O que fizemos ontem?")
        b = appdb.create_conversation(conn, "Organizar Downloads")
        appdb.add_message(conn, b, "user", "organize a pasta")
        appdb.add_message(conn, a, "assistant", "Corrigimos o worker.")
        self.assertEqual([c["id"] for c in appdb.list_conversations(conn)], [a, b])
        self.assertEqual([c["id"] for c in appdb.list_conversations(conn, "WORKER")], [a])
        self.assertEqual([m["role"] for m in appdb.get_messages(conn, a)], ["user", "assistant"])

    def test_moves_and_batches(self):
        conn = db.connect()
        m1 = appdb.record_move(conn, "b1", r"C:\x\a.pdf", r"C:\x\Docs\a.pdf", "moved")
        appdb.record_move(conn, "b1", r"C:\x\b.txt", r"C:\x\Docs\b.txt", "failed")
        appdb.set_move_status(conn, m1, "undone")
        [b] = appdb.list_batches(conn)
        self.assertEqual((b["batch_id"], b["folder"], b["moved"], b["undone"], b["failed"]), ("b1", r"C:\x", 0, 1, 1))

    def test_search_is_case_insensitive_for_accents(self):
        conn = db.connect()
        a = appdb.create_conversation(conn, "Organização dos Downloads")
        appdb.add_message(conn, a, "user", "Foi ÓTIMO")
        b = appdb.create_conversation(conn, "outra")
        appdb.add_message(conn, b, "user", "nada")
        self.assertEqual([c["id"] for c in appdb.list_conversations(conn, "ORGANIZAÇÃO")], [a])
        self.assertEqual([c["id"] for c in appdb.list_conversations(conn, "ótimo")], [a])
