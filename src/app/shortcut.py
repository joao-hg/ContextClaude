"""Atalhos do ContextClaude (Menu Iniciar e Área de Trabalho)."""
import os
import sys
from pathlib import Path

from contextclaude import config, logutil

ICON = Path(__file__).resolve().parents[2] / "assets" / "app" / "icon.ico"
APP_ID = "ContextClaude.App"  # AppUserModelID: o mesmo no processo (main.py) e no .lnk, para fixar/agrupar


def start_menu_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def desktop_dir() -> Path:
    """Área de Trabalho real (respeita a redireção do OneDrive)."""
    import win32com.client
    d = win32com.client.Dispatch("WScript.Shell").SpecialFolders("Desktop")
    if not d or not Path(d).is_dir():  # Path("") vira "." e o atalho iria para o diretório atual
        raise RuntimeError(f"Área de Trabalho não encontrada: {d!r}")
    return Path(d)


def _set_app_id(path: Path) -> None:
    """Grava System.AppUserModel.ID no .lnk (o WScript.Shell não expõe essa propriedade)."""
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shell
    link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                      shell.IID_IShellLink)
    pf = link.QueryInterface(pythoncom.IID_IPersistFile)
    pf.Load(str(path), 2)  # STGM_READWRITE
    store = link.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
    store.Commit()
    pf.Save(str(path), True)


def create_shortcut(dest_dir: Path | None = None) -> Path:
    import win32com.client
    dest = Path(dest_dir) if dest_dir else start_menu_dir()
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "ContextClaude.lnk"
    root = Path(config.root())
    lnk = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(path))
    lnk.TargetPath = str(Path(sys.executable).with_name("pythonw.exe"))
    lnk.Arguments = f'"{root / "src" / "cc.py"}" app'
    lnk.WorkingDirectory = str(root)
    lnk.IconLocation = str(ICON)
    lnk.save()
    try:  # sem o ID o atalho funciona, só não agrupa com a janela; não deixa a falha derrubar o comando
        _set_app_id(path)
    except Exception as e:
        logutil.log(f"atalho sem AppUserModelID ({path}): {e!r}")
    return path
