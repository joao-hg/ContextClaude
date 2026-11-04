import json
import os
import tempfile
import unittest
from unittest import mock

from contextclaude import chibi_status, config


class TestChibiStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_status_default_when_file_missing(self):
        self.assertEqual(chibi_status.read_status(),
                          {"estado": "idle", "sessao": None, "evento_id": 0, "ts": "",
                           "pulso_estado": None, "pulso_evento_id": 0,
                           "rodada": None, "ultima_rodada": None})

    def test_write_rodada_inicio_sets_ativa_block(self):
        chibi_status.write_rodada_inicio("2026-10-31T10:00:00", 42.0, 2)
        out = chibi_status.read_status()
        self.assertEqual(out["rodada"], {"ativa": True, "inicio": "2026-10-31T10:00:00",
                                          "estimativa_s": 42.0, "sessoes": 2})
        self.assertIsNone(out["ultima_rodada"])

    def test_write_rodada_fim_clears_rodada_and_sets_ultima_rodada(self):
        chibi_status.write_rodada_inicio("2026-10-31T10:00:00", 42.0, 2)
        chibi_status.write_rodada_fim("done", 50.0, 2, 3, 1)
        out = chibi_status.read_status()
        self.assertIsNone(out["rodada"])
        self.assertEqual(out["ultima_rodada"],
                          {"evento_id": 1, "status": "done", "duration_s": 50.0,
                           "sessoes": 2, "fatos": 3, "decisoes": 1, "mensagem": None})

    def test_write_rodada_fim_evento_id_increments_across_rounds(self):
        chibi_status.write_rodada_fim("done", 1.0, 1, 0, 0)
        chibi_status.write_rodada_fim("error", 2.0, 0, 0, 0, "Ollama fora do ar")
        out = chibi_status.read_status()
        self.assertEqual(out["ultima_rodada"]["evento_id"], 2)
        self.assertEqual(out["ultima_rodada"]["mensagem"], "Ollama fora do ar")

    def test_write_status_preserves_rodada_fields_across_normal_writes(self):
        chibi_status.write_rodada_inicio("2026-10-31T10:00:00", None, 1)
        chibi_status.write_status("pensando", sessao="s1")
        out = chibi_status.read_status()
        self.assertEqual(out["estado"], "pensando")
        self.assertTrue(out["rodada"]["ativa"])

    def test_write_then_read_roundtrip(self):
        chibi_status.write_status("pensando", sessao="s1")
        out = chibi_status.read_status()
        self.assertEqual((out["estado"], out["sessao"]), ("pensando", "s1"))

    def test_evento_id_increments_on_each_write(self):
        chibi_status.write_status("pensando")
        chibi_status.write_status("digitando")
        self.assertEqual(chibi_status.read_status()["evento_id"], 2)

    def test_pulso_estado_recorded_separately_from_live_estado(self):
        chibi_status.write_status("notificacoes", sessao="s1")
        chibi_status.write_status("idle", sessao="s1")
        out = chibi_status.read_status()
        self.assertEqual(out["estado"], "idle")
        self.assertEqual(out["pulso_estado"], "notificacoes")
        self.assertEqual(out["pulso_evento_id"], 1)

    def test_pulso_evento_id_only_advances_on_pulso_worthy_write(self):
        chibi_status.write_status("feliz")
        chibi_status.write_status("pensando")
        chibi_status.write_status("digitando")
        out = chibi_status.read_status()
        self.assertEqual(out["pulso_estado"], "feliz")
        self.assertEqual(out["pulso_evento_id"], 1)
        self.assertEqual(out["evento_id"], 3)

    def test_write_status_ignores_unknown_estado(self):
        chibi_status.write_status("vmbora")
        self.assertFalse(config.status_path().exists())

    def test_read_status_returns_default_on_corrupted_json(self):
        config.status_path().parent.mkdir(parents=True, exist_ok=True)
        config.status_path().write_text("{nao é json", encoding="utf-8")
        self.assertEqual(chibi_status.read_status()["estado"], "idle")

    def test_write_data_retries_on_permission_error_then_succeeds(self):
        """I-1: status.json pode estar aberto por outro processo (widget faz
        polling 2x por tick de 400ms) no instante do os.replace. Em vez de
        perder a escrita (e deixar rodada.ativa travado em True), tenta de
        novo algumas vezes antes de desistir."""
        calls = {"n": 0}
        real_replace = os.replace

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("em uso por outro processo")
            real_replace(src, dst)

        with mock.patch.object(chibi_status.os, "replace", side_effect=flaky_replace), \
             mock.patch.object(chibi_status.time, "sleep"):
            chibi_status.write_rodada_fim("done", 1.0, 1, 0, 0)

        self.assertEqual(calls["n"], 3)
        self.assertEqual(chibi_status.read_status()["ultima_rodada"]["status"], "done")

    def test_write_data_gives_up_after_max_retries_and_raises(self):
        def always_fails(src, dst):
            raise PermissionError("sempre ocupado")

        with mock.patch.object(chibi_status.os, "replace", side_effect=always_fails), \
             mock.patch.object(chibi_status.time, "sleep"):
            with self.assertRaises(PermissionError):
                chibi_status._write_data({"estado": "idle"})

    def test_module_has_no_pillow_or_tkinter_dependency(self):
        src = (config.root() / ".." / "src" / "contextclaude" / "chibi_status.py")
        import pathlib
        path = pathlib.Path(__file__).resolve().parents[1] / "src" / "contextclaude" / "chibi_status.py"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("PIL", text)
        self.assertNotIn("tkinter", text)
