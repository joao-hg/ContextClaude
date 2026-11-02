import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import cc  # noqa: E402
from contextclaude import db, graph, lock, notes, vectors  # noqa: E402

HIT = {"id": 1, "text": "usa sqlite", "projeto": "app", "tipo": "decisao",
       "data": "2026-10-29T10:00:00", "distance": 0.1234}


class TestCli(unittest.TestCase):
    def run_cli(self, argv, **patch):
        out = []
        with mock.patch.object(vectors, "search", **patch.get("search", {"return_value": []})) as s, \
                mock.patch.object(vectors, "rebuild", **patch.get("rebuild", {"return_value": 0})), \
                mock.patch.object(db, "connect"), \
                mock.patch.object(db, "active_fact_ids", **patch.get("active", {"side_effect": lambda c, ids: set(ids)})):
            code = cc.main(argv, out=out.append)
        return code, out, s

    def test_buscar_excludes_superseded_facts(self):
        hit1 = {**HIT, "id": 1, "text": "decisão antiga"}
        hit2 = {**HIT, "id": 2, "text": "decisão vigente"}
        code, out, _ = self.run_cli(["buscar", "banco"], search={"return_value": [hit1, hit2]},
                                    active={"return_value": {2}})
        self.assertEqual(code, 0)
        self.assertEqual(len(out), 1)
        self.assertIn("decisão vigente", out[0])

    def test_buscar_formats_line_and_passes_options(self):
        code, out, s = self.run_cli(["buscar", "banco", "-p", "app", "-n", "3"], search={"return_value": [HIT]})
        self.assertEqual(code, 0)
        self.assertEqual(out, ["[app/decisao 2026-10-29] usa sqlite (dist 0.12)"])
        args, kwargs = s.call_args
        self.assertEqual(args[0], "banco")
        self.assertEqual((kwargs["projeto"], kwargs["n"]), ("app", 3))

    def test_buscar_nothing_found(self):
        code, out, _ = self.run_cli(["buscar", "x"])
        self.assertEqual((code, out), (0, ["Nada encontrado."]))

    def test_buscar_error_returns_1_with_message(self):
        code, out, _ = self.run_cli(["buscar", "x"], search={"side_effect": ConnectionError("fora")})
        self.assertEqual(code, 1)
        self.assertIn("Falha na busca", out[0])

    def test_indexar_prints_count(self):
        code, out, _ = self.run_cli(["indexar"], rebuild={"return_value": 7})
        self.assertEqual((code, out), (0, ["7 itens indexados."]))

    def test_grafo_prints_edges_or_nada_encontrado(self):
        out = []
        with mock.patch.object(db, "connect"), \
                mock.patch.object(graph, "neighbors", return_value=[
                    {"origem": "A", "relacao": "usa", "destino": "B", "depth": 1}]):
            code = cc.main(["grafo", "A"], out=out.append)
        self.assertEqual((code, out), (0, ["A --usa--> B"]))

        out2 = []
        with mock.patch.object(db, "connect"), \
                mock.patch.object(graph, "neighbors", return_value=[]):
            code2 = cc.main(["grafo", "A"], out=out2.append)
        self.assertEqual((code2, out2), (0, ["Nada encontrado."]))

    def test_grafo_passes_projeto_and_depth_options(self):
        with mock.patch.object(db, "connect"), \
                mock.patch.object(graph, "neighbors", return_value=[]) as n:
            cc.main(["grafo", "A", "-p", "app", "-d", "3"], out=lambda s: None)
        _args, kwargs = n.call_args
        self.assertEqual(kwargs["projeto"], "app")
        self.assertEqual(kwargs["depth"], 3)

    def test_grafo_depth_validation(self):
        out = []
        with mock.patch.object(db, "connect"), \
                mock.patch.object(graph, "neighbors", return_value=[]) as n:
            code = cc.main(["grafo", "A", "-d", "0"], out=out.append)
        self.assertEqual(code, 1)
        self.assertIn("-d deve ser pelo menos 1.", out[0])
        n.assert_not_called()

    def test_grafo_error_returns_1_with_message(self):
        out = []
        with mock.patch.object(db, "connect"), \
                mock.patch.object(graph, "neighbors", side_effect=ConnectionError("fora")):
            code = cc.main(["grafo", "A"], out=out.append)
        self.assertEqual(code, 1)
        self.assertIn("Falha no grafo", out[0])

    def test_vault_rebuilds_notes_for_project(self):
        with mock.patch.object(lock, "acquire", return_value="handle"), \
                mock.patch.object(lock, "release") as rel, \
                mock.patch.object(db, "connect"), \
                mock.patch.object(notes, "rebuild_project", return_value=3):
            out = []
            code = cc.main(["vault", "-p", "p"], out=out.append)
        self.assertEqual(code, 0)
        self.assertEqual(out, ["3 notas reconstruídas."])
        rel.assert_called_once()

    def test_vault_refuses_while_worker_holds_lock(self):
        with mock.patch.object(lock, "acquire", return_value=None):
            out = []
            code = cc.main(["vault", "-p", "p"], out=out.append)
        self.assertEqual(code, 1)
        self.assertIn("O worker está rodando; tente de novo em instantes.", out)


class TestWorkerIndexing(unittest.TestCase):
    def test_index_failure_is_logged_not_raised(self):
        import worker
        from contextclaude import db, lock, ollama, pipeline
        with tempfile.TemporaryDirectory() as d:
            os.environ["CONTEXTCLAUDE_ROOT"] = d
            with mock.patch.object(ollama, "ensure_running"), \
                    mock.patch.object(pipeline, "process_queue"), \
                    mock.patch.object(vectors, "index_pending", side_effect=RuntimeError("boom")), \
                    mock.patch.object(worker, "log") as log:
                worker.main()
            self.assertTrue(any("indexação" in str(c) for c in log.call_args_list))
            vectors.close()


if __name__ == "__main__":
    unittest.main()
