import json
import sys
import urllib.error

sys.path.insert(0, "src")
from contextclaude import activity, config, db, pause, pipeline

from tests import test_pipeline as tp


def test_record_writes_json_line_and_recent_newest_first():
    activity.record("um")
    activity.record("dois", "ok")
    out = activity.recent()
    assert [r["texto"] for r in out] == ["dois", "um"]
    assert out[0]["tipo"] == "ok" and out[1]["tipo"] == "info"
    assert len(out[0]["ts"]) == 19


def test_trim_400_to_200():
    p = config.activity_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps({"ts": "t", "texto": str(i), "tipo": "info"}) + "\n" for i in range(400)),
                 encoding="utf-8")
    activity.record("novo")  # 401 linhas
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200
    assert json.loads(lines[-1])["texto"] == "novo"
    assert json.loads(lines[0])["texto"] == "201"


def test_recent_skips_invalid_lines_and_limits():
    p = config.activity_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('lixo\n{"ts":"a","texto":"x","tipo":"info"}\n[1]\n\n{"ts":"b","texto":"y","tipo":"ok"}\n',
                 encoding="utf-8")
    assert [r["texto"] for r in activity.recent()] == ["y", "x"]
    assert [r["texto"] for r in activity.recent(1)] == ["y"]
    assert activity.recent(0) == []


def test_recent_without_file_is_empty():
    assert activity.recent() == []


def test_record_never_raises_when_path_unwritable():
    p = config.activity_path()
    p.mkdir(parents=True)  # diretório no lugar do arquivo: abrir para escrita falha
    activity.record("x")
    assert activity.recent() == []


def test_record_never_raises_when_config_fails(monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(config, "activity_path", boom)
    activity.record("x")


class TestPipelineActivity:
    # reaproveita só os auxiliares de TestPipeline (sem herdar seus testes)
    setup_method = tp.TestPipeline.setUp
    teardown_method = tp.TestPipeline.tearDown
    transcript = tp.TestPipeline.transcript
    extract = tp.TestPipeline.extract
    run_q = tp.TestPipeline.run_q
    status = tp.TestPipeline.status

    def texts(self):
        return [r["texto"] for r in activity.recent(50)]

    def test_round_end_records_ok_line(self):
        self.transcript("decidimos usar sqlite")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        rec = activity.recent(50)
        assert any(r["texto"] == "Rodada concluída: 1 sessões" and r["tipo"] == "ok" for r in rec)
        t = self.texts()
        assert "Lendo sessão de app" in t
        assert "Anotou 1 fatos e 1 decisões (app)" in t
        assert "Resumo de app atualizado" in t
        assert not any("\\" in x or "decidimos" in x for x in t)

    def test_session_failure_records_erro_line(self):
        self.transcript("x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

        def boom(lines):
            raise RuntimeError("falha")

        self.run_q(boom)
        assert any(r["texto"] == "Não consegui processar uma sessão; vou tentar de novo" and r["tipo"] == "erro"
                   for r in activity.recent(50))

    def test_no_reading_line_when_session_has_nothing_new(self):
        self.transcript()  # transcrição vazia: nada a ler
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        assert "Lendo sessão de app" not in self.texts()

    def test_last_attempt_failure_records_give_up_line(self):
        self.transcript("x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.conn.execute("UPDATE sessions SET attempts=?", (pipeline.MAX_ATTEMPTS - 1,))
        self.conn.commit()

        def boom(lines):
            raise RuntimeError("falha")

        self.run_q(boom)
        assert self.status() == "error"
        rec = activity.recent(50)
        assert any(r["texto"] == "Desisti de uma sessão depois de várias tentativas" and r["tipo"] == "erro"
                   for r in rec)
        assert not any("vou tentar de novo" in r["texto"] for r in rec)

    def test_pause_recorded_once_only_when_wait_blocks(self):
        calls = {"n": 0}

        def paused(now=None):
            calls["n"] += 1
            return calls["n"] <= 3

        self.transcript("x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        orig = pause.wait
        import unittest.mock as m
        with m.patch.object(pause, "is_paused", paused), \
             m.patch.object(pause, "wait", lambda: orig(sleep=lambda s: None)):
            pipeline._wait_app("s1")
        assert self.texts().count("Pausado enquanto você usa o app") == 1

    def test_no_pause_line_when_not_paused(self):
        pipeline._wait_app("s1")
        assert "Pausado enquanto você usa o app" not in self.texts()
