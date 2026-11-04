import os
import tempfile
import unittest
from unittest import mock

from contextclaude import db, graph, notes


class TestGraph(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name
        os.environ["CONTEXTCLAUDE_HOME"] = r"C:\Users\João"
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def insert(self, session_id, project, source, relation, target):
        self.conn.execute(
            "INSERT INTO relations(session_id, project, source, relation, target) VALUES(?,?,?,?,?)",
            (session_id, project, source, relation, target),
        )
        self.conn.commit()

    def test_normalize_lowercases_and_collapses_whitespace(self):
        self.assertEqual(graph.normalize("  SQLite   Core "), "sqlite core")

    def test_neighbors_depth_1_finds_both_directions(self):
        self.insert("s1", "p1", "A", "usa", "B")
        self.insert("s1", "p1", "A", "contem", "D")
        self.insert("s1", "p1", "C", "depende", "A")

        result = graph.neighbors(self.conn, "A", depth=1)

        self.assertEqual(len(result), 3)
        for item in result:
            self.assertEqual(item["depth"], 1)
        pairs = {(item["origem"], item["relacao"], item["destino"]) for item in result}
        self.assertEqual(pairs, {("A", "usa", "B"), ("A", "contem", "D"), ("C", "depende", "A")})

    def test_neighbors_depth_2_includes_second_hop(self):
        self.insert("s1", "p1", "A", "usa", "B")
        self.insert("s1", "p1", "A", "contem", "D")
        self.insert("s1", "p1", "C", "depende", "A")
        self.insert("s1", "p1", "B", "gera", "D")

        depth1 = graph.neighbors(self.conn, "A", depth=1)
        pairs1 = {(item["origem"], item["relacao"], item["destino"]) for item in depth1}
        self.assertNotIn(("B", "gera", "D"), pairs1)

        depth2 = graph.neighbors(self.conn, "A", depth=2)
        pairs2 = {(item["origem"], item["relacao"], item["destino"]): item for item in depth2}
        self.assertIn(("B", "gera", "D"), pairs2)
        self.assertEqual(pairs2[("B", "gera", "D")]["depth"], 2)

    def test_neighbors_stops_without_duplicating_when_graph_cycles(self):
        self.insert("s1", "p1", "A", "r1", "B")
        self.insert("s1", "p1", "B", "r2", "A")

        result = graph.neighbors(self.conn, "A", depth=3)

        pairs = [(item["origem"], item["relacao"], item["destino"]) for item in result]
        self.assertEqual(len(pairs), 2)
        self.assertEqual(sorted(pairs), sorted([("A", "r1", "B"), ("B", "r2", "A")]))

    def test_neighbors_respects_limit(self):
        self.insert("s1", "p1", "A", "r1", "B")
        self.insert("s1", "p1", "A", "r2", "C")
        self.insert("s1", "p1", "A", "r3", "D")

        result = graph.neighbors(self.conn, "A", depth=1, limit=1)

        self.assertEqual(len(result), 1)

    def test_neighbors_filters_by_project(self):
        self.insert("s1", "p1", "A", "usa", "B")
        self.insert("s2", "p2", "A", "usa", "C")

        result = graph.neighbors(self.conn, "A", projeto="p1", depth=1)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["destino"], "B")

    def test_neighbors_unifies_case_and_whitespace_variants(self):
        self.insert("s1", "p1", "SQLite", "usa", "B")
        self.insert("s2", "p1", " sqlite", "roda_em", "C")

        result = graph.neighbors(self.conn, "SQLITE", depth=1)

        self.assertEqual(len(result), 2)
        relacoes = {item["relacao"] for item in result}
        self.assertEqual(relacoes, {"usa", "roda_em"})

    def test_neighbors_dedupes_same_triple_seen_in_different_sessions(self):
        self.insert("s1", "p1", "cc.py", "usa", "SQLite")
        self.insert("s2", "p1", "cc.py", "usa", "SQLite")

        result = graph.neighbors(self.conn, "cc.py", depth=1)

        self.assertEqual(len(result), 1)

    def test_neighbors_dedupes_relation_case_variants_of_same_triple(self):
        self.insert("s1", "p1", "cc.py", "usa", "SQLite")
        self.insert("s2", "p1", "CC.py", "Usa", "sqlite")

        result = graph.neighbors(self.conn, "cc.py", depth=1)

        self.assertEqual(len(result), 1)

    def test_neighbors_unknown_entity_returns_empty_list(self):
        self.insert("s1", "p1", "A", "usa", "B")

        result = graph.neighbors(self.conn, "fantasma", depth=1)

        self.assertEqual(result, [])

    def test_display_name_prefers_first_seen_original(self):
        self.insert("s1", "p1", "SQLite", "usa", "B")
        self.insert("s2", "p1", "C", "depende", "sqlite")

        self.assertEqual(graph.display_name(self.conn, "sqlite"), "SQLite")

    def test_display_name_falls_back_to_input_when_absent(self):
        self.assertEqual(graph.display_name(self.conn, "fantasma"), "fantasma")

    def test_write_entities_creates_a_page_per_entity_seen_in_session(self):
        self.insert("s1", "app", "A", "usa", "B")

        graph.write_entities(self.conn, "s1", "app")

        a_path = notes._dir("app") / "Entidades" / "a.md"
        b_path = notes._dir("app") / "Entidades" / "b.md"
        self.assertTrue(a_path.exists())
        self.assertTrue(b_path.exists())
        self.assertIn("[[b|B]]", a_path.read_text(encoding="utf-8"))
        self.assertIn("[[a|A]]", b_path.read_text(encoding="utf-8"))

    def test_write_entities_skips_empty_key_without_creating_a_page(self):
        self.insert("s1", "app", "   ", "usa", "RealEntity")

        graph.write_entities(self.conn, "s1", "app")

        entidades_dir = notes._dir("app") / "Entidades"
        self.assertEqual([p.name for p in entidades_dir.glob("*.md")], ["realentity.md"])

    def test_write_entities_long_name_does_not_raise_and_is_truncated(self):
        long_name = "x" * 300
        self.insert("s1", "app", long_name, "usa", "B")

        graph.write_entities(self.conn, "s1", "app")  # não deve levantar

        truncated = notes._dir("app") / "Entidades" / (("x" * 120) + ".md")
        self.assertTrue(truncated.exists())

    def test_write_entities_one_bad_entity_does_not_abort_the_rest(self):
        self.insert("s1", "app", "Good", "usa", "Bad")
        original = notes.write_entity_note

        def flaky(projeto, key, display, relations):
            if key == "bad":
                raise OSError("disco cheio")
            return original(projeto, key, display, relations)

        with mock.patch.object(notes, "write_entity_note", side_effect=flaky):
            graph.write_entities(self.conn, "s1", "app")  # não deve levantar

        self.assertTrue((notes._dir("app") / "Entidades" / "good.md").exists())


if __name__ == "__main__":
    unittest.main()
