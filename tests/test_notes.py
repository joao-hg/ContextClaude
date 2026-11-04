import os
import tempfile
import unittest
from datetime import datetime

from contextclaude import config, db, notes

WHEN = datetime(2026, 10, 29, 12, 30)
DATA = {"titulo": "Correção do índice", "resumo": "res", "feitos": ["Corrigimos o índice", 3]}


class TestNotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_session_note_named_by_title_with_summary_and_done_items(self):
        p = notes.write_session_note("app", "s1", WHEN, DATA)
        self.assertEqual(p.name, "2026-10-29 Correção do índice.md")
        text = p.read_text(encoding="utf-8")
        self.assertIn("# Correção do índice", text)
        self.assertIn("## Resumo\nres", text)
        self.assertIn("## O que fizemos\n- Corrigimos o índice\n", text)
        for sec in ("## Decisões", "## Fatos", "## Pendências", "## Relações"):
            self.assertNotIn(sec, text)

    def test_collision_with_other_session_gets_suffix_same_session_overwrites(self):
        a = notes.write_session_note("app", "s1", WHEN, DATA)
        b = notes.write_session_note("app", "s2", WHEN, DATA)
        self.assertEqual(b.name, "2026-10-29 Correção do índice (2).md")
        self.assertEqual(notes.write_session_note("app", "s1", WHEN, DATA), a)
        self.assertEqual(len(list(a.parent.glob("*.md"))), 2)

    def test_title_change_replaces_old_note(self):
        a = notes.write_session_note("app", "s1", WHEN, DATA)
        b = notes.write_session_note("app", "s1", WHEN, dict(DATA, titulo="Outro: título/novo"), old_path=str(a))
        self.assertEqual(b.name, "2026-10-29 Outro título novo.md")
        self.assertFalse(a.exists())

    def test_missing_title_falls_back_to_session_id(self):
        p = notes.write_session_note("app", "abcdef123456", WHEN, {"resumo": "x"})
        self.assertEqual(p.name, "2026-10-29 Sessão abcdef12.md")

    def test_summary_roundtrip(self):
        notes.write_summary("app", "olá")
        self.assertEqual(notes.read_summary("app"), "olá")

    def test_read_summary_missing_returns_none(self):
        self.assertIsNone(notes.read_summary("nada"))

    def test_project_name_with_accent_and_invalid_chars(self):
        p = notes.write_session_note("Programação: x", "s1", WHEN, DATA)
        self.assertTrue(p.exists())
        self.assertIn("Programação_ x", str(p))

    def test_write_entity_note_creates_file_with_wikilinks(self):
        p = notes.write_entity_note("app", "sqlite", "SQLite", [{"origem": "SQLite", "relacao": "usa", "destino": "B"}])
        self.assertEqual(p.name, "sqlite.md")
        self.assertEqual(p.parent.name, "Entidades")
        self.assertEqual(p.parent.parent.name, "app")
        text = p.read_text(encoding="utf-8")
        self.assertIn("# SQLite", text)
        self.assertIn("[[b|B]]", text)

    def test_write_entity_note_empty_relations_shows_placeholder(self):
        p = notes.write_entity_note("app", "sqlite", "SQLite", [])
        self.assertIn("_nada_", p.read_text(encoding="utf-8"))

    def test_write_entity_note_overwrites_instead_of_accumulating(self):
        notes.write_entity_note("app", "sqlite", "SQLite", [{"origem": "SQLite", "relacao": "usa", "destino": "A"}])
        p = notes.write_entity_note("app", "sqlite", "SQLite", [{"origem": "SQLite", "relacao": "usa", "destino": "B"}])
        entities = list(p.parent.glob("*.md"))
        self.assertEqual(len(entities), 1)
        text = entities[0].read_text(encoding="utf-8")
        self.assertIn("[[b|B]]", text)
        self.assertNotIn("[[a|A]]", text)

    def test_write_entity_note_sanitizes_key_for_filesystem(self):
        p = notes.write_entity_note("app", "a/b:c", "a/b:c", [])
        self.assertEqual(p.name, "a_b_c.md")
        self.assertEqual(p.parent.name, "Entidades")

    def test_write_entity_note_relation_links_resolve_to_real_filenames(self):
        p = notes.write_entity_note(
            "app", "src/cc.py", "src/cc.py",
            [{"origem": "src/cc.py", "relacao": "importa", "destino": "C#"}],
        )
        text = p.read_text(encoding="utf-8")
        c_sharp_path = notes.write_entity_note("app", "c#", "C#", [])
        self.assertIn(f"[[{p.stem}|src/cc.py]]", text)
        self.assertIn(f"[[{c_sharp_path.stem}|C#]]", text)

    def test_write_session_from_db_removes_legacy_notes_and_stores_path(self):
        conn = db.connect()
        try:
            db.enqueue(conn, "s1", "C:/x", "t.jsonl")
            legacy = notes._dir("app") / "Sessões" / "2026-10-29-1230.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("---\nprojeto: app\nsessao: s1\n---\n\n## Resumo\nvelho\n\n## Decisões\n- d\n",
                              encoding="utf-8")
            other = legacy.with_name("2026-10-29-1231.md")
            other.write_text("---\nprojeto: app\nsessao: s2\n---\n", encoding="utf-8")
            self.assertEqual(notes.legacy_resumos("app", "s1"), ["velho"])
            p = notes.write_session_from_db(conn, "app", "s1", DATA)
            self.assertFalse(legacy.exists())
            self.assertTrue(other.exists())
            self.assertEqual(conn.execute("SELECT note_path FROM sessions").fetchone()[0], str(p))
        finally:
            conn.close()

    def _context_db(self):
        conn = db.connect()
        conn.execute("INSERT INTO sessions(session_id, project, title, summary, created_at) "
                     "VALUES('s1','p','Correção do índice','Arrumamos o Chroma.','2026-11-01T10:00:00')")
        conn.execute("INSERT INTO facts(session_id, project, kind, text, created_at) VALUES"
                     "('s1','p','decisao','usar sqlite','2026-11-01T10:00:00'),"
                     "('s1','p','fato','o worker roda destacado','2026-11-01T10:00:00')")
        conn.execute("INSERT INTO facts(session_id, project, kind, text, status, created_at) "
                     "VALUES('s1','p','decisao','usar postgres','substituida','2026-11-01T10:00:00')")
        conn.execute("INSERT INTO relations(session_id, project, source, relation, target) "
                     "VALUES('s1','p','worker','usa','SQLite'),('s1','p','hook','usa','sqlite')")
        conn.commit()
        return conn

    def test_context_has_overview_live_decisions_and_recent_sessions(self):
        conn = self._context_db()
        try:
            notes.write_summary("p", "Projeto de memória.")
            text = notes.write_context(conn, "p").read_text(encoding="utf-8")
        finally:
            conn.close()
        self.assertIn("# Contexto do projeto: p", text)
        self.assertIn("Projeto de memória.", text)
        self.assertIn("- usar sqlite", text)
        self.assertNotIn("usar postgres", text)
        self.assertIn("- o worker roda destacado", text)
        self.assertIn("2026-11-01 — **Correção do índice**: Arrumamos o Chroma.", text)
        self.assertRegex(text, r"## Componentes mais citados\n(SQLite|sqlite), ")

    def test_context_is_capped(self):
        conn = self._context_db()
        try:
            for i in range(15):
                conn.execute("INSERT INTO facts(session_id, project, kind, text) VALUES('s1','p','fato',?)",
                             (f"fato {i} " + "x" * 400,))
            conn.commit()
            text = notes.build_context(conn, "p")
        finally:
            conn.close()
        self.assertLessEqual(len(text), notes.CONTEXT_MAX)
        self.assertIn("## Sessões recentes", text)
        self.assertIn("usar sqlite", text)

    def test_write_entity_note_truncates_long_key_for_filename(self):
        long_name = "x" * 300
        p = notes.write_entity_note("app", long_name, long_name, [])
        self.assertTrue(p.exists())
        self.assertLessEqual(len(p.stem), 120)

    def test_write_entity_note_empty_key_falls_back_to_placeholder_name(self):
        p = notes.write_entity_note("app", "", "", [])
        self.assertTrue(p.exists())
        self.assertNotEqual(p.name, ".md")

    def test_write_entity_note_escapes_display_in_frontmatter(self):
        import json
        tricky = "a: b [c] #d"
        p = notes.write_entity_note("app", "weird", tricky, [])
        text = p.read_text(encoding="utf-8")
        self.assertIn(f"entidade: {json.dumps(tricky)}", text)

    def test_rebuild_project_recreates_session_notes_and_entity_pages(self):
        conn = db.connect()
        conn.execute(
            "INSERT INTO facts(session_id, project, kind, text, created_at) VALUES('s1','p','decisao','usar sqlite','2026-01-01T10:00:00')"
        )
        conn.execute(
            "INSERT INTO relations(session_id, project, source, relation, target) VALUES('s1','p','contextclaude','usa','sqlite')"
        )
        conn.commit()
        count = notes.rebuild_project(conn, "p")
        self.assertEqual(count, 1)
        session_files = list((config.vault() / "p" / "Sessões").glob("*.md"))
        self.assertEqual(len(session_files), 1)
        self.assertTrue((config.vault() / "p" / "Entidades" / "sqlite.md").is_file())
        self.assertIn("usar sqlite", notes.context_path("p").read_text(encoding="utf-8"))
        conn.close()

    def test_rebuild_project_uses_session_record(self):
        conn = self._context_db()
        try:
            conn.execute("""UPDATE sessions SET feitos='["Refizemos o índice"]'""")
            conn.commit()
            notes.rebuild_project(conn, "p")
        finally:
            conn.close()
        p = config.vault() / "p" / "Sessões" / "2026-11-01 Correção do índice.md"
        self.assertIn("- Refizemos o índice", p.read_text(encoding="utf-8"))

    def test_rebuild_project_is_idempotent(self):
        conn = db.connect()
        conn.execute(
            "INSERT INTO facts(session_id, project, kind, text, created_at) VALUES('s1','p','decisao','usar sqlite','2026-01-01T10:00:00')"
        )
        conn.commit()
        notes.rebuild_project(conn, "p")
        count = notes.rebuild_project(conn, "p")
        self.assertEqual(count, 1)
        self.assertEqual(len(list((config.vault() / "p" / "Sessões").glob("*.md"))), 1)
        conn.close()

    def test_rebuild_project_preserves_existing_resumo(self):
        conn = db.connect()
        conn.execute(
            "INSERT INTO facts(session_id, project, kind, text, created_at) VALUES('s1','p','decisao','usar sqlite','2026-01-01T10:00:00')"
        )
        conn.commit()
        legacy = config.vault() / "p" / "Sessões" / "2026-01-01-1000.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("---\nprojeto: p\nsessao: s1\n---\n\n## Resumo\nRESUMO REAL LLM\n\n## Fatos\n- f\n",
                          encoding="utf-8")
        notes.rebuild_project(conn, "p")
        files = list((config.vault() / "p" / "Sessões").glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("RESUMO REAL LLM", files[0].read_text(encoding="utf-8"))
        conn.close()
