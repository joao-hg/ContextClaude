import os
import tempfile
import unittest
from datetime import datetime

from contextclaude import config, db, notes

WHEN = datetime(2026, 10, 29, 12, 30)
DATA = {"fatos": ["f1"], "decisoes": ["d1"], "pendencias": ["p1"],
        "relacoes": [{"origem": "SQLite", "relacao": "guarda", "destino": "fila"}, "lixo"], "resumo": "res"}


class TestNotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_session_note_has_wikilinks_and_sections(self):
        text = notes.write_session_note("app", "s1", WHEN, DATA).read_text(encoding="utf-8")
        self.assertIn("[[sqlite|SQLite]]", text)
        self.assertIn("[[fila|fila]]", text)
        for sec in ("## Resumo", "## Decisões", "## Fatos", "## Pendências", "## Relações"):
            self.assertIn(sec, text)

    def test_collision_gets_suffix(self):
        a = notes.write_session_note("app", "s1", WHEN, DATA)
        b = notes.write_session_note("app", "s2", WHEN, DATA)
        self.assertEqual(b.name, a.stem + "-2.md")

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

    def test_session_note_relation_links_resolve_to_entity_filenames(self):
        data = {"relacoes": [{"origem": "src/cc.py", "relacao": "importa", "destino": "C#"}]}
        p = notes.write_session_note("app", "s1", WHEN, data)
        text = p.read_text(encoding="utf-8")
        origem_page = notes.write_entity_note("app", "src/cc.py", "src/cc.py", [])
        destino_page = notes.write_entity_note("app", "c#", "C#", [])
        self.assertIn(f"[[{origem_page.stem}|src/cc.py]]", text)
        self.assertIn(f"[[{destino_page.stem}|C#]]", text)

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
        self.assertIn("usar sqlite", session_files[0].read_text(encoding="utf-8"))
        self.assertTrue((config.vault() / "p" / "Entidades" / "sqlite.md").is_file())
        conn.close()

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
        notes.write_session_note("p", "s1", datetime(2026, 1, 1, 10, 0), {"resumo": "RESUMO REAL LLM"})
        notes.rebuild_project(conn, "p")
        files = list((config.vault() / "p" / "Sessões").glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("RESUMO REAL LLM", files[0].read_text(encoding="utf-8"))
        conn.close()
