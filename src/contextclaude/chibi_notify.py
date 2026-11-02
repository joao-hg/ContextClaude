from pathlib import Path

# Toast nativo do Windows (best-effort), via win32gui.Shell_NotifyIcon.
#
# Assinatura real de NOTIFYICONDATA confirmada na fonte do pywin32 (tag
# b312, igual à versão instalada neste ambiente - `pip show pywin32`):
# https://github.com/mhammond/pywin32/blob/b312/win32/src/win32gui.i
#   (hwnd, id, flags, callback_message, hicon,
#    tip, info, timeout, info_title, info_flags)
# Qualquer import de win32gui/win32con fica dentro das funções: se o
# pywin32 não estiver instalado, só a chamada falha (ImportError), não o
# import do módulo.

_WINDOW_CLASS_NAME = "ContextClaudeChibiNotifyWnd"
_hwnd_cache = None
_icone_adicionado = False
ICON_PATH = Path(__file__).resolve().parents[2] / "assets" / "chibi" / "icon.ico"


def _get_hwnd():
    # HWND oculto, reaproveitado entre chamadas, só para servir de dono do
    # ícone da área de notificação (não precisa de bomba de mensagens).
    global _hwnd_cache
    import win32api
    import win32gui

    if _hwnd_cache is not None:
        return _hwnd_cache

    hinst = win32api.GetModuleHandle(None)
    wc = win32gui.WNDCLASS()
    wc.hInstance = hinst
    wc.lpszClassName = _WINDOW_CLASS_NAME
    wc.lpfnWndProc = {}  # sem handlers: tudo cai no DefWindowProc

    try:
        win32gui.RegisterClass(wc)
    except win32gui.error:
        pass  # classe já registrada por uma chamada anterior

    hwnd = win32gui.CreateWindow(
        _WINDOW_CLASS_NAME,
        "ContextClaude Chibi Notify",
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        hinst,
        None,
    )
    _hwnd_cache = hwnd
    return hwnd


def _load_icon():
    """Ícone do mascote para o tray/toast; cai no ícone padrão do Windows se
    o arquivo (assets/chibi/icon.ico) não existir ou LoadImage falhar."""
    import win32con
    import win32gui

    try:
        return win32gui.LoadImage(
            0,
            str(ICON_PATH),
            win32con.IMAGE_ICON,
            0,
            0,
            win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
        )
    except Exception:
        return win32gui.LoadIcon(0, win32con.IDI_APPLICATION)


def _show_balloon(titulo: str, texto: str) -> None:
    global _icone_adicionado
    import win32con
    import win32gui

    hwnd = _get_hwnd()
    hicon = _load_icon()
    flags = (
        win32gui.NIF_ICON
        | win32gui.NIF_MESSAGE
        | win32gui.NIF_TIP
        | win32gui.NIF_INFO
    )
    # Limites de NOTIFYICONDATA: szTip/szInfoTitle ~64 chars, szInfo ~256.
    nid = (
        hwnd,
        0,
        flags,
        win32con.WM_USER + 20,
        hicon,
        titulo[:63],
        texto[:255],
        10000,
        titulo[:63],
        win32gui.NIIF_INFO,
    )
    mensagem = win32gui.NIM_MODIFY if _icone_adicionado else win32gui.NIM_ADD
    win32gui.Shell_NotifyIcon(mensagem, nid)
    _icone_adicionado = True


def notify(titulo: str, texto: str) -> None:
    # Best-effort: qualquer exceção (pywin32 ausente, sem shell/explorer,
    # erro de API) é engolida. Nunca levanta.
    try:
        _show_balloon(titulo, texto)
    except Exception:
        pass


def _destroy_tray() -> None:
    # I-3: sem NIM_DELETE/DestroyWindow, o ícone fica "fantasma" na área de
    # notificação a cada saída do processo (só some se o usuário passar o
    # mouse por cima). Remove o ícone (se algum dia foi adicionado) e destrói
    # o HWND oculto que o hospeda.
    global _hwnd_cache, _icone_adicionado
    import win32gui

    if _icone_adicionado and _hwnd_cache is not None:
        nid = (_hwnd_cache, 0)
        win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, nid)
    if _hwnd_cache is not None:
        win32gui.DestroyWindow(_hwnd_cache)
    _hwnd_cache = None
    _icone_adicionado = False


def cleanup() -> None:
    # Best-effort, mesmo padrão de notify(): nunca levanta.
    try:
        _destroy_tray()
    except Exception:
        pass
