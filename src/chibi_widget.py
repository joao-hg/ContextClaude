import math
import os
import subprocess
import sys
import time
import tkinter as tk
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from contextclaude import chibi_gui, chibi_notify, chibi_status, config, db, lock
from contextclaude.logutil import log

POLL_STATUS_MS = 400
POLL_TASKLIST_MS = 7000
BUBBLE_SUMMARY_SECONDS = 6
CLICK_MOVE_THRESHOLD_PX = 4
CLICK_OPEN_DELAY_MS = 250
RODADA_STALE_FLOOR_S = 900  # 15 min


def claude_code_running(run=subprocess.run) -> bool:
    try:
        # Filtro exato de IMAGENAME (não substring sobre a saída inteira do
        # tasklist): evita falso positivo com um hipotético "xclaude.exe" e
        # evita confundir com outro processo que também se chame claude.exe
        # (ex.: Claude Desktop) além do CLI. CREATE_NO_WINDOW evita que o
        # tasklist.exe abra uma janela de console própria e roube o foco da
        # tela a cada poll (o widget é lançado DETACHED/sem console).
        out = run(
            ["tasklist", "/FI", "IMAGENAME eq claude.exe", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
    except Exception:
        return True
    return "claude.exe" in out.lower()


def _initial_state(status: dict) -> dict:
    return {
        "estado": status["estado"], "frame": 0,
        # pulso já visto na primeira leitura não deve ser "replayado" ao
        # ligar o widget — só pulsos novos a partir daqui disparam animação.
        "last_pulso_evento_id": status.get("pulso_evento_id", 0),
        "pulso_estado": None, "pulso_frame": 0,
    }


def _apply_status(state: dict, status: dict, frames: dict) -> None:
    """Atualiza o estado de animação a partir de uma leitura de status.json.

    Detecta um pulso (feliz/notificacoes) pela mudança de pulso_evento_id,
    mesmo que 'estado' já tenha avançado para outra coisa (ex.: idle) antes
    deste poll rodar — é exatamente o caso do worker.py escrevendo 'idle'
    milissegundos depois de um pulso transiente.
    """
    pulso_evento_id = status.get("pulso_evento_id", 0)
    if pulso_evento_id != state["last_pulso_evento_id"]:
        state["last_pulso_evento_id"] = pulso_evento_id
        pulso_estado = status.get("pulso_estado")
        if pulso_estado in frames:
            state["pulso_estado"] = pulso_estado
            state["pulso_frame"] = 0
    if state["pulso_estado"] is None and status["estado"] != state["estado"]:
        state["estado"] = status["estado"]
        state["frame"] = 0


def _advance_frame(state: dict, frames: dict):
    """Avança um tick de animação e devolve o frame (PIL.Image) a mostrar.

    Enquanto houver um pulso pendente, toca seu ciclo completo (8 frames)
    antes de voltar a seguir o estado "ao vivo".
    """
    if state["pulso_estado"] is not None:
        photos = frames[state["pulso_estado"]]
        img = photos[state["pulso_frame"]]
        state["pulso_frame"] += 1
        if state["pulso_frame"] >= len(photos):
            state["pulso_estado"] = None
            state["pulso_frame"] = 0
            state["frame"] = 0
        return img
    photos = frames.get(state["estado"], frames["idle"])
    state["frame"] = (state["frame"] + 1) % len(photos)
    return photos[state["frame"]]


def _initial_bubble_state(status: dict) -> dict:
    return {
        # mesmo truque de _initial_state: um resumo já presente na primeira
        # leitura não deve ser "replayado" como balão ao ligar o widget.
        "last_ultima_rodada_evento_id": (status.get("ultima_rodada") or {}).get("evento_id", 0),
        "mostrar_resumo_ate": None,
    }


def _is_rodada_stale(rodada: dict, elapsed: float) -> bool:
    """I-1: se a escrita final de uma rodada (write_rodada_fim) falhar
    silenciosamente (ex.: status.json ocupado por outro processo), rodada
    ["ativa"] pode ficar True para sempre -- sem isso, o balão de
    countdown/elapsed ficaria travado até a próxima rodada começar. Usa
    3x a estimativa (quando houver) ou um piso de 15min, o que for maior."""
    estimativa_s = rodada.get("estimativa_s")
    cutoff = max(3 * estimativa_s, RODADA_STALE_FLOOR_S) if estimativa_s else RODADA_STALE_FLOOR_S
    return elapsed > cutoff


def _bubble_text(bubble_state: dict, status: dict, now=time.monotonic) -> str | None:
    """Calcula o texto do balão (ou None, se nada deve ser mostrado) e muta
    bubble_state (relógio do resumo pós-rodada).

    now() é usado só para governar a janela de exibição do resumo pós-rodada
    (mostrar_resumo_ate), de propósito baseada em relógio monotônico. O
    countdown de rodada ativa usa datetime.now() contra rodada["inicio"]
    (relógio de parede), não o now() injetado -- são dois relógios
    intencionalmente diferentes para dois propósitos diferentes.
    """
    ultima_rodada = status.get("ultima_rodada")
    if ultima_rodada is not None:
        evento_id = ultima_rodada.get("evento_id", 0)
        if evento_id != bubble_state["last_ultima_rodada_evento_id"]:
            bubble_state["last_ultima_rodada_evento_id"] = evento_id
            bubble_state["mostrar_resumo_ate"] = now() + BUBBLE_SUMMARY_SECONDS

    rodada = status.get("rodada")
    if rodada and rodada.get("ativa"):
        inicio = datetime.fromisoformat(rodada["inicio"])
        elapsed = (datetime.now() - inicio).total_seconds()
        if not _is_rodada_stale(rodada, elapsed):
            sessoes = rodada.get("sessoes")
            estimativa_s = rodada.get("estimativa_s")
            if estimativa_s is not None:
                restante = estimativa_s - elapsed
                if restante <= 0:
                    return f"Anotando · quase lá... ({sessoes} sessões)"
                mm, ss = divmod(int(restante), 60)
                return f"Anotando · ~{mm:02d}:{ss:02d} · {sessoes} sessões"
            return f"Anotando · {int(elapsed)}s · {sessoes} sessões"

    mostrar_resumo_ate = bubble_state.get("mostrar_resumo_ate")
    if mostrar_resumo_ate is not None and now() < mostrar_resumo_ate and ultima_rodada is not None:
        if ultima_rodada.get("status") == "error":
            return ultima_rodada.get("mensagem") or "rodada falhou"
        sessoes = ultima_rodada.get("sessoes", 0)
        fatos = ultima_rodada.get("fatos", 0)
        decisoes = ultima_rodada.get("decisoes", 0)
        return f"{sessoes} sessões, {fatos} fatos, {decisoes} decisões"
    return None


def _initial_notify_state(status: dict) -> dict:
    rodada = status.get("rodada")
    return {
        "rodada_ativa": bool(rodada and rodada.get("ativa")),
        # mesmo truque de "não repetir pulso já visto" de _initial_state.
        "last_evento_id": (status.get("ultima_rodada") or {}).get("evento_id", 0),
    }


def _maybe_notify(notify_state: dict, status: dict, notify=chibi_notify.notify) -> None:
    rodada = status.get("rodada")
    ativa = bool(rodada and rodada.get("ativa"))
    if ativa and not notify_state["rodada_ativa"]:
        notify("ContextClaude", f"Rodada de anotação iniciada ({rodada.get('sessoes')} sessões)")
    notify_state["rodada_ativa"] = ativa

    ultima_rodada = status.get("ultima_rodada")
    if ultima_rodada is not None:
        evento_id = ultima_rodada.get("evento_id", 0)
        if evento_id != notify_state["last_evento_id"]:
            notify_state["last_evento_id"] = evento_id
            if ultima_rodada.get("status") == "error":
                texto = ultima_rodada.get("mensagem") or "rodada falhou"
            else:
                sessoes = ultima_rodada.get("sessoes", 0)
                fatos = ultima_rodada.get("fatos", 0)
                decisoes = ultima_rodada.get("decisoes", 0)
                texto = f"{sessoes} sessões, {fatos} fatos, {decisoes} decisões"
            notify("ContextClaude", texto)


def _is_click_not_drag(start_xy: tuple[int, int], end_xy: tuple[int, int],
                        threshold: float = CLICK_MOVE_THRESHOLD_PX) -> bool:
    dx = end_xy[0] - start_xy[0]
    dy = end_xy[1] - start_xy[1]
    return math.hypot(dx, dy) < threshold


def _drag_screen_xy(event) -> tuple[int, int]:
    """Coordenadas de tela (x_root/y_root) do evento, para comparação de
    clique-vs-arrasto. Coordenadas relativas ao label (event.x/event.y) não
    servem para isso: do_drag reposiciona a janela para manter o cursor no
    mesmo deslocamento relativo, então event.x/event.y no ButtonRelease-1
    voltam perto do valor do ButtonPress-1 mesmo depois de um arrasto real."""
    return event.x_root, event.y_root


def _format_duration(duration_s) -> str:
    if duration_s is None:
        return "?"
    total = max(0, int(round(duration_s)))
    mm, ss = divmod(total, 60)
    if mm:
        return f"{mm}m{ss:02d}s"
    return f"{ss}s"


def _format_round_row(row) -> str:
    status_label = "OK" if row["status"] == "done" else "ERRO"
    duracao = _format_duration(row["duration_s"]) if "duration_s" in row.keys() else "?"
    linha = (f"{row['finished_at']} [{status_label}] {duracao}, {row['sessoes']} sessões, "
             f"{row['fatos']} fatos, {row['decisoes']} decisões")
    if row["status"] == "error" and row["mensagem"]:
        linha += " — " + row["mensagem"]
    return linha


def _run_window(frames, run, read_status) -> None:
    key = chibi_gui.KEY_COLOR_HEX
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.config(bg=key)
    root.wm_attributes("-transparentcolor", key)  # Windows-only Tk attribute
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    cell_w, cell_h = frames["idle"][0].size
    root.geometry(f"{cell_w}x{cell_h}+{screen_w - cell_w - 20}+{screen_h - cell_h - 60}")
    label = tk.Label(root, bd=0, bg=key)
    label.pack()

    # Escotilha de fuga manual: sem taskbar/borda (overrideredirect), o
    # usuário não tem Alt+F4 nem como arrastar a janela por padrão.
    drag = {"x": 0, "y": 0}
    drag_start_screen = {"xy": (0, 0)}

    def start_drag(event):
        drag["x"], drag["y"] = event.x, event.y
        drag_start_screen["xy"] = _drag_screen_xy(event)

    def do_drag(event):
        root.geometry(f"+{root.winfo_x() + event.x - drag['x']}+{root.winfo_y() + event.y - drag['y']}")
        position_bubble()

    def close_widget(_event=None):
        root.destroy()

    pending_click = {"after_id": None}
    history_state = {"toplevel": None}

    def toggle_history_panel():
        pending_click["after_id"] = None
        if history_state["toplevel"] is not None:
            history_state["toplevel"].destroy()
            history_state["toplevel"] = None
            return

        try:
            conn = db.connect()
            try:
                rows = db.recent_rounds(conn, limit=10)
            finally:
                conn.close()
        except Exception:
            rows = []

        panel = tk.Toplevel(root)
        panel.title("Histórico de rodadas — ContextClaude")
        text = tk.Text(panel, width=70, height=15, wrap="word")
        text.pack(fill="both", expand=True)
        conteudo = "\n".join(_format_round_row(r) for r in rows) if rows else "Sem rodadas registradas ainda."
        text.insert("1.0", conteudo)
        text.config(state="disabled")

        def on_close():
            history_state["toplevel"] = None
            panel.destroy()

        panel.protocol("WM_DELETE_WINDOW", on_close)
        history_state["toplevel"] = panel

    def on_release(event):
        if _is_click_not_drag(drag_start_screen["xy"], _drag_screen_xy(event)):
            pending_click["after_id"] = root.after(CLICK_OPEN_DELAY_MS, toggle_history_panel)

    def on_double_click(_event=None):
        if pending_click["after_id"] is not None:
            root.after_cancel(pending_click["after_id"])
            pending_click["after_id"] = None
        close_widget()

    label.bind("<ButtonPress-1>", start_drag)
    label.bind("<B1-Motion>", do_drag)
    label.bind("<ButtonRelease-1>", on_release)
    label.bind("<Double-Button-1>", on_double_click)
    label.bind("<Button-3>", close_widget)

    # Balão de texto: mesmo truque de janela transparente/sem borda/topmost
    # do widget principal, posicionado acima dele.
    bubble_root = tk.Toplevel(root)
    bubble_root.overrideredirect(True)
    bubble_root.attributes("-topmost", True)
    bubble_root.config(bg=key)
    bubble_root.wm_attributes("-transparentcolor", key)  # Windows-only Tk attribute
    bubble_label = tk.Label(bubble_root, bd=4, bg="#ffffe0", fg="#000000",
                             font=("Segoe UI", 9), wraplength=220, justify="left")
    bubble_label.pack()
    bubble_root.withdraw()

    def position_bubble():
        bubble_root.update_idletasks()
        bw = bubble_label.winfo_reqwidth() + 8
        bh = bubble_label.winfo_reqheight() + 8
        bubble_root.geometry(f"{bw}x{bh}+{root.winfo_x()}+{root.winfo_y() - bh - 4}")

    status_inicial = read_status()
    state = _initial_state(status_inicial)
    bubble_state = _initial_bubble_state(status_inicial)
    notify_state = _initial_notify_state(status_inicial)

    def tick_animation():
        from PIL import ImageTk
        img = ImageTk.PhotoImage(_advance_frame(state, frames))
        label.configure(image=img)
        label.image = img
        root.after(POLL_STATUS_MS, tick_animation)

    def poll_status():
        _apply_status(state, read_status(), frames)
        root.after(POLL_STATUS_MS, poll_status)

    def poll_bubble():
        status = read_status()
        _maybe_notify(notify_state, status)
        texto = _bubble_text(bubble_state, status)
        if texto is None:
            bubble_root.withdraw()
        else:
            bubble_label.configure(text=texto)
            position_bubble()
            bubble_root.deiconify()
        root.after(POLL_STATUS_MS, poll_bubble)

    def poll_lifecycle():
        if not claude_code_running():
            root.destroy()
            return
        root.after(POLL_TASKLIST_MS, poll_lifecycle)

    tick_animation()
    poll_status()
    poll_bubble()
    root.after(POLL_TASKLIST_MS, poll_lifecycle)
    try:
        root.mainloop()
    finally:
        # I-3: sem isso, o ícone de bandeja fica "fantasma" (só some se o
        # usuário passar o mouse por cima) a cada saída do processo.
        try:
            chibi_notify.cleanup()
        except Exception:
            pass


def main(acquire=lock.acquire, release=lock.release, load_frames=chibi_gui.load_frames,
         run=subprocess.run, read_status=chibi_status.read_status) -> None:
    handle = acquire(config.chibi_lock_path())
    if handle is None:
        return
    try:
        try:
            frames = load_frames()
        except Exception as e:
            log(f"chibi: cartela de sprites indisponível, bichinho não vai aparecer: {e!r}")
            return
        _run_window(frames, run, read_status)
    finally:
        release(handle)


if __name__ == "__main__":
    main()
