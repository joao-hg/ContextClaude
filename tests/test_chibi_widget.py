import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import chibi_widget
from contextclaude import config, lock


class TestChibiWidget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_claude_code_running_true_when_process_present(self):
        def fake_run(*a, **kw):
            return subprocess.CompletedProcess(a, 0, stdout='"claude.exe","1234","Console","1","50 K"\n', stderr="")
        self.assertTrue(chibi_widget.claude_code_running(run=fake_run))

    def test_claude_code_running_false_when_absent(self):
        def fake_run(*a, **kw):
            # Saída real do tasklist filtrado quando nada bate o /FI.
            return subprocess.CompletedProcess(
                a, 0, stdout="INFO: No tasks are running which match the specified criteria.\n", stderr="")
        self.assertFalse(chibi_widget.claude_code_running(run=fake_run))

    def test_claude_code_running_defaults_true_on_tasklist_failure(self):
        def fake_run(*a, **kw):
            raise OSError("tasklist nao encontrado")
        self.assertTrue(chibi_widget.claude_code_running(run=fake_run))

    def test_claude_code_running_filters_by_exact_image_name_with_no_window(self):
        """Finding #1 (foco roubado pelo console do tasklist) e finding #4
        (substring frágil casando qualquer coisa contendo 'claude.exe')."""
        captured = {}

        def fake_run(*a, **kw):
            captured["args"] = a[0]
            captured["kwargs"] = kw
            return subprocess.CompletedProcess(a, 0, stdout='"claude.exe","1234","Console","1","50 K"\n', stderr="")

        self.assertTrue(chibi_widget.claude_code_running(run=fake_run))
        self.assertIn("/FI", captured["args"])
        self.assertIn("IMAGENAME eq claude.exe", captured["args"])
        self.assertEqual(captured["kwargs"].get("creationflags"), subprocess.CREATE_NO_WINDOW)

    def test_claude_code_running_exact_filter_excludes_lookalike_names(self):
        """Com /FI "IMAGENAME eq claude.exe", um processo hipotético
        'xclaude.exe' nunca aparece na saída do tasklist (o Windows já filtra
        por nome exato), então o antigo bug de substring não se repete."""
        def fake_run(*a, **kw):
            # O Windows nunca devolveria xclaude.exe para esse filtro exato;
            # simulamos a saída "sem resultados" que ele de fato devolveria.
            return subprocess.CompletedProcess(
                a, 0, stdout="INFO: No tasks are running which match the specified criteria.\n", stderr="")
        self.assertFalse(chibi_widget.claude_code_running(run=fake_run))

    def test_main_returns_silently_when_sprite_sheet_missing(self):
        def boom_load_frames():
            raise FileNotFoundError("sprites.png")
        chibi_widget.main(load_frames=boom_load_frames)
        handle = lock.acquire(config.chibi_lock_path())
        self.assertIsNotNone(handle)
        lock.release(handle)

    def test_main_returns_immediately_if_lock_already_held(self):
        handle = lock.acquire(config.chibi_lock_path())
        try:
            calls = []
            chibi_widget.main(load_frames=lambda: calls.append(1))
            self.assertEqual(calls, [])
        finally:
            lock.release(handle)


FRAMES = {estado: [f"{estado}-{i}" for i in range(8)]
          for estado in ("idle", "pensando", "digitando", "processando", "feliz", "notificacoes")}


class TestChibiWidgetPulse(unittest.TestCase):
    """Finding #3: evento_id é escrito mas nunca lido pelo widget, então um
    pulso (feliz/notificacoes) quase nunca é visto antes de outra escrita
    sobrepor 'estado'. Estes testes exercitam a lógica de polling/animação
    pura (sem Tk) que detecta o pulso via pulso_evento_id."""

    def test_pulse_is_detected_even_after_live_estado_already_moved_to_idle(self):
        """Mimetiza worker.py: pipeline escreve 'notificacoes', e milissegundos
        depois o fim do run escreve 'idle' -- por hipótese o widget só faz
        poll depois das duas escritas, então 'estado' já chega como 'idle'.
        Mesmo assim o pulso_estado/pulso_evento_id devem disparar a animação
        do pulso perdido."""
        status_antes = {"estado": "idle", "sessao": None, "evento_id": 0, "ts": "",
                         "pulso_estado": None, "pulso_evento_id": 0}
        state = chibi_widget._initial_state(status_antes)

        # poll só acontece depois que pipeline.py já escreveu notificacoes
        # (evento_id=1, pulso_evento_id=1) e worker.py já escreveu idle por
        # cima (evento_id=2, pulso_estado continua 'notificacoes').
        status_depois = {"estado": "idle", "sessao": "s1", "evento_id": 2, "ts": "",
                          "pulso_estado": "notificacoes", "pulso_evento_id": 1}
        chibi_widget._apply_status(state, status_depois, FRAMES)

        self.assertEqual(state["pulso_estado"], "notificacoes")

    def test_pulse_plays_full_eight_frame_cycle_then_resumes_live_estado(self):
        state = chibi_widget._initial_state(
            {"estado": "idle", "pulso_estado": None, "pulso_evento_id": 0})
        chibi_widget._apply_status(
            state, {"estado": "idle", "pulso_estado": "feliz", "pulso_evento_id": 1}, FRAMES)

        shown = [chibi_widget._advance_frame(state, FRAMES) for _ in range(8)]
        self.assertEqual(shown, FRAMES["feliz"])  # ciclo completo do pulso, em ordem

        # depois do ciclo, volta a seguir o estado "ao vivo" normalmente
        self.assertIsNone(state["pulso_estado"])
        proximo = chibi_widget._advance_frame(state, FRAMES)
        self.assertIn(proximo, FRAMES["idle"])

    def test_live_estado_changes_are_ignored_while_a_pulse_is_playing(self):
        state = chibi_widget._initial_state(
            {"estado": "idle", "pulso_estado": None, "pulso_evento_id": 0})
        chibi_widget._apply_status(
            state, {"estado": "idle", "pulso_estado": "notificacoes", "pulso_evento_id": 1}, FRAMES)
        chibi_widget._advance_frame(state, FRAMES)  # pulso em andamento (1/8)

        # uma nova sessão começa a ser processada enquanto o pulso ainda toca
        chibi_widget._apply_status(
            state, {"estado": "pensando", "pulso_estado": "notificacoes", "pulso_evento_id": 1}, FRAMES)
        self.assertEqual(state["pulso_estado"], "notificacoes")  # pulso não foi interrompido

    def test_pulse_already_seen_before_widget_started_is_not_replayed(self):
        """O pulso_evento_id já presente na primeira leitura (feito antes do
        widget subir) não deve disparar animação -- só pulsos novos a partir
        daí."""
        status_inicial = {"estado": "idle", "pulso_estado": "feliz", "pulso_evento_id": 5}
        state = chibi_widget._initial_state(status_inicial)
        chibi_widget._apply_status(state, dict(status_inicial), FRAMES)
        self.assertIsNone(state["pulso_estado"])


class TestChibiWidgetBubble(unittest.TestCase):
    def test_no_bubble_when_no_round_active_and_no_recent_summary(self):
        status = {"rodada": None, "ultima_rodada": None}
        state = chibi_widget._initial_bubble_state(status)
        self.assertIsNone(chibi_widget._bubble_text(state, status))

    def test_bubble_shows_elapsed_time_without_estimate(self):
        inicio = datetime.now().isoformat(timespec="seconds")
        status = {"rodada": {"ativa": True, "inicio": inicio, "estimativa_s": None, "sessoes": 2},
                   "ultima_rodada": None}
        state = chibi_widget._initial_bubble_state(status)
        texto = chibi_widget._bubble_text(state, status)
        self.assertIn("2 sessões", texto)
        self.assertIn("Anotando", texto)
        self.assertNotIn("~", texto)

    def test_bubble_countdown_never_goes_negative_past_estimate(self):
        inicio = (datetime.now() - timedelta(seconds=100)).isoformat(timespec="seconds")
        status = {"rodada": {"ativa": True, "inicio": inicio, "estimativa_s": 10.0, "sessoes": 1},
                   "ultima_rodada": None}
        state = chibi_widget._initial_bubble_state(status)
        texto = chibi_widget._bubble_text(state, status)
        self.assertNotIn("-", texto)

    def test_bubble_shows_summary_for_a_few_seconds_after_round_ends(self):
        status_antes = {"rodada": None, "ultima_rodada": None}
        state = chibi_widget._initial_bubble_state(status_antes)
        status_depois = {"rodada": None, "ultima_rodada": {
            "evento_id": 1, "status": "done", "sessoes": 2, "fatos": 3, "decisoes": 1, "mensagem": None}}
        clock = {"t": 100.0}
        texto = chibi_widget._bubble_text(state, status_depois, now=lambda: clock["t"])
        self.assertIn("2 sessões", texto)  # M-2: resumo inclui contagem de sessões
        self.assertIn("3 fatos", texto)
        clock["t"] += chibi_widget.BUBBLE_SUMMARY_SECONDS + 1
        self.assertIsNone(chibi_widget._bubble_text(state, status_depois, now=lambda: clock["t"]))

    def test_is_rodada_stale_uses_900s_floor_without_estimate(self):
        self.assertFalse(chibi_widget._is_rodada_stale({"estimativa_s": None}, 899))
        self.assertTrue(chibi_widget._is_rodada_stale({"estimativa_s": None}, 901))

    def test_is_rodada_stale_uses_3x_estimate_when_larger_than_floor(self):
        self.assertFalse(chibi_widget._is_rodada_stale({"estimativa_s": 1000}, 2999))
        self.assertTrue(chibi_widget._is_rodada_stale({"estimativa_s": 1000}, 3001))

    def test_bubble_stops_showing_stale_active_round_past_cutoff(self):
        """I-1: se write_rodada_fim falhar silenciosamente, rodada.ativa
        pode ficar True para sempre. Passado o corte de 15min (sem
        estimativa), o balão deixa de mostrar o countdown/elapsed travado."""
        inicio = (datetime.now() - timedelta(seconds=1000)).isoformat(timespec="seconds")
        status = {"rodada": {"ativa": True, "inicio": inicio, "estimativa_s": None, "sessoes": 1},
                   "ultima_rodada": None}
        state = chibi_widget._initial_bubble_state(status)
        self.assertIsNone(chibi_widget._bubble_text(state, status))

    def test_bubble_keeps_showing_active_round_within_estimate_based_cutoff(self):
        inicio = (datetime.now() - timedelta(seconds=1000)).isoformat(timespec="seconds")
        status = {"rodada": {"ativa": True, "inicio": inicio, "estimativa_s": 1000.0, "sessoes": 1},
                   "ultima_rodada": None}
        state = chibi_widget._initial_bubble_state(status)
        self.assertIsNotNone(chibi_widget._bubble_text(state, status))

    def test_bubble_shows_error_message_after_failed_round(self):
        state = chibi_widget._initial_bubble_state({"rodada": None, "ultima_rodada": None})
        status = {"rodada": None, "ultima_rodada": {
            "evento_id": 1, "status": "error", "sessoes": 1, "fatos": 0, "decisoes": 0,
            "mensagem": "Ollama fora do ar"}}
        texto = chibi_widget._bubble_text(state, status)
        self.assertIn("Ollama fora do ar", texto)

    def test_summary_seen_at_startup_is_not_replayed(self):
        status = {"rodada": None, "ultima_rodada": {
            "evento_id": 5, "status": "done", "sessoes": 1, "fatos": 1, "decisoes": 0, "mensagem": None}}
        state = chibi_widget._initial_bubble_state(status)
        self.assertIsNone(chibi_widget._bubble_text(state, dict(status)))


class TestChibiWidgetNotify(unittest.TestCase):
    def test_notify_fires_once_on_round_start(self):
        calls = []
        status = {"rodada": {"ativa": True, "inicio": "t", "estimativa_s": None, "sessoes": 1},
                   "ultima_rodada": None}
        state = chibi_widget._initial_notify_state({"rodada": None, "ultima_rodada": None})
        chibi_widget._maybe_notify(state, status, notify=lambda t, m: calls.append((t, m)))
        chibi_widget._maybe_notify(state, status, notify=lambda t, m: calls.append((t, m)))
        self.assertEqual(len(calls), 1)

    def test_notify_fires_on_new_ultima_rodada_with_summary_text(self):
        calls = []
        status = {"rodada": None, "ultima_rodada": {
            "evento_id": 1, "status": "done", "sessoes": 2, "fatos": 4, "decisoes": 1, "mensagem": None}}
        state = chibi_widget._initial_notify_state({"rodada": None, "ultima_rodada": None})
        chibi_widget._maybe_notify(state, status, notify=lambda t, m: calls.append((t, m)))
        self.assertEqual(len(calls), 1)
        self.assertIn("2 sessões", calls[0][1])  # M-2: notificação inclui contagem de sessões
        self.assertIn("4", calls[0][1])

    def test_notify_does_not_replay_summary_already_seen_at_startup(self):
        calls = []
        status = {"rodada": None, "ultima_rodada": {
            "evento_id": 3, "status": "done", "sessoes": 1, "fatos": 1, "decisoes": 0, "mensagem": None}}
        state = chibi_widget._initial_notify_state(status)
        chibi_widget._maybe_notify(state, dict(status), notify=lambda t, m: calls.append((t, m)))
        self.assertEqual(calls, [])


class FakeEvent:
    """Mimetiza o bastante de um tk.Event para os testes de arrasto: x/y
    relativos ao label e x_root/y_root (coordenadas de tela)."""

    def __init__(self, x, y, x_root, y_root):
        self.x, self.y, self.x_root, self.y_root = x, y, x_root, y_root


class TestChibiWidgetClick(unittest.TestCase):
    def test_small_movement_counts_as_click(self):
        self.assertTrue(chibi_widget._is_click_not_drag((100, 100), (102, 101)))

    def test_large_movement_counts_as_drag(self):
        self.assertFalse(chibi_widget._is_click_not_drag((100, 100), (130, 100)))

    def test_drag_screen_xy_uses_root_coordinates_not_label_relative(self):
        ev = FakeEvent(x=5, y=5, x_root=500, y_root=300)
        self.assertEqual(chibi_widget._drag_screen_xy(ev), (500, 300))

    def test_real_drag_is_not_misclassified_as_click_when_using_screen_coords(self):
        """C-1: do_drag reposiciona a janela para manter o cursor no mesmo
        deslocamento relativo ao label, então event.x/event.y no release
        voltam perto do valor do press mesmo após um arrasto real -- mas
        event.x_root/event.y_root (coordenadas de tela) não mentem."""
        press = FakeEvent(x=10, y=10, x_root=200, y_root=200)
        release = FakeEvent(x=11, y=9, x_root=350, y_root=260)  # arrasto real de ~170px na tela

        # Coordenadas de tela corretamente classificam como arrasto.
        self.assertFalse(chibi_widget._is_click_not_drag(
            chibi_widget._drag_screen_xy(press), chibi_widget._drag_screen_xy(release)))

        # Demonstra o bug original: coordenadas relativas ao label
        # classificariam esse mesmo arrasto como clique.
        self.assertTrue(chibi_widget._is_click_not_drag((press.x, press.y), (release.x, release.y)))

    def test_format_round_row_marks_done_and_error(self):
        done = {"finished_at": "2026-10-31T10:00:00", "status": "done", "sessoes": 2,
                "fatos": 3, "decisoes": 1, "mensagem": None, "duration_s": 95.0}
        erro = {"finished_at": "2026-10-31T10:05:00", "status": "error", "sessoes": 1,
                "fatos": 0, "decisoes": 0, "mensagem": "Ollama fora do ar", "duration_s": 5.0}
        linha_ok = chibi_widget._format_round_row(done)
        self.assertIn("OK", linha_ok)
        self.assertIn("2 sessões", linha_ok)
        self.assertIn("1m35s", linha_ok)  # duration_s formatada (M-2)
        linha_erro = chibi_widget._format_round_row(erro)
        self.assertIn("ERRO", linha_erro)
        self.assertIn("5s", linha_erro)
        self.assertIn("Ollama fora do ar", linha_erro)

    def test_format_duration_formats_seconds_and_minutes(self):
        self.assertEqual(chibi_widget._format_duration(45), "45s")
        self.assertEqual(chibi_widget._format_duration(95), "1m35s")
        self.assertEqual(chibi_widget._format_duration(None), "?")
