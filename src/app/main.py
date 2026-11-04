"""Janela nativa do ContextClaude (pywebview/WebView2)."""
import threading
from pathlib import Path

from app.api import Api
from app.shortcut import APP_ID, ICON
from contextclaude import config, ollama

UI_DIR = Path(__file__).parent / "ui"


def main() -> int:
    try:
        import webview
    except ImportError:
        print("Instale o pywebview: pip install pywebview")
        return 1
    try:  # agrupa a janela sob o próprio ícone na barra de tarefas; só existe no Windows
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass
    api = Api()
    window = webview.create_window("ContextClaude", str(UI_DIR / "index.html"), js_api=api,
                                   width=1280, height=820, min_size=(960, 640), background_color="#F7F7F5")
    api.bind(window)
    threading.Thread(target=ollama.ensure_running, daemon=True).start()
    webview.start(icon=str(ICON))
    try:  # fechar a janela no meio de uma resposta mata as threads sem rodar o finally do hold()
        config.pause_path().unlink(missing_ok=True)
    except OSError:
        pass
    return 0
