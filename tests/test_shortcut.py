import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

try:
    import win32com.client
except ImportError:  # pragma: no cover
    win32com = None


class TestShortcut(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_start_menu_dir_uses_appdata(self):
        from app import shortcut
        with mock.patch.dict(os.environ, {"APPDATA": r"C:\X\Roaming"}):
            self.assertEqual(shortcut.start_menu_dir(),
                             Path(r"C:\X\Roaming") / "Microsoft" / "Windows" / "Start Menu" / "Programs")

    @unittest.skipIf(win32com is None, "pywin32 indisponível")
    def test_create_shortcut_in_given_dir(self):
        from app import shortcut
        p = shortcut.create_shortcut(Path(self.tmp))
        self.assertEqual(p.name, "ContextClaude.lnk")
        self.assertTrue(p.exists())
        lnk = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(p))
        self.assertTrue(lnk.TargetPath.lower().endswith("pythonw.exe"))
        self.assertIn("cc.py", lnk.Arguments)
        self.assertTrue(lnk.Arguments.endswith(" app"))

    def test_icon_points_to_existing_ico_with_256(self):
        from PIL import Image
        from app import main as appmain, shortcut
        for ico in (appmain.ICON, shortcut.ICON):
            self.assertTrue(Path(ico).exists())
            self.assertEqual(Path(ico).parts[-3:], ("assets", "app", "icon.ico"))
        self.assertEqual(sorted(Image.open(appmain.ICON).info["sizes"]),
                         [(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])

    @unittest.skipIf(win32com is None, "pywin32 indisponível")
    def test_desktop_dir_is_existing_dir(self):
        from app import shortcut
        self.assertTrue(shortcut.desktop_dir().is_dir())

    @unittest.skipIf(win32com is None, "pywin32 indisponível")
    def test_shortcut_has_app_user_model_id(self):
        from win32com.propsys import propsys, pscon
        from app import shortcut
        p = shortcut.create_shortcut(Path(self.tmp))
        store = propsys.SHGetPropertyStoreFromParsingName(str(p))
        self.assertEqual(store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue(), shortcut.APP_ID)
        self.assertEqual(shortcut.APP_ID, "ContextClaude.App")

    @unittest.skipIf(win32com is None, "pywin32 indisponível")
    def test_shortcut_still_created_if_aumid_fails(self):
        from app import shortcut
        with mock.patch.object(shortcut, "_set_app_id", side_effect=OSError("falhou")):
            p = shortcut.create_shortcut(Path(self.tmp))
        self.assertTrue(p.exists())
        lnk = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(p))
        self.assertTrue(lnk.Arguments.endswith(" app"))

    def test_desktop_dir_rejects_empty_or_missing(self):
        from app import shortcut
        for bad in ("", os.path.join(self.tmp, "nao-existe")):
            shell = mock.Mock()
            shell.SpecialFolders.return_value = bad
            fake = types.SimpleNamespace(client=types.SimpleNamespace(Dispatch=mock.Mock(return_value=shell)))
            with mock.patch.dict(sys.modules, {"win32com": fake, "win32com.client": fake.client}):
                with self.assertRaises(RuntimeError):
                    shortcut.desktop_dir()

    @unittest.skipIf(win32com is None, "pywin32 indisponível")
    def test_shortcut_uses_app_icon(self):
        from app import shortcut
        p = shortcut.create_shortcut(Path(self.tmp))
        lnk = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(p))
        self.assertTrue(lnk.IconLocation.split(",")[0].endswith(os.path.join("assets", "app", "icon.ico")))


class TestMain(unittest.TestCase):
    def setUp(self):
        """Não altera o AUMID do processo de teste de verdade (M7)."""
        import ctypes
        windll = getattr(ctypes, "windll", None)
        self.set_aumid = mock.Mock()
        if windll is not None:
            p = mock.patch.object(windll.shell32, "SetCurrentProcessExplicitAppUserModelID", self.set_aumid)
            p.start()
            self.addCleanup(p.stop)

    @unittest.skipIf(sys.platform != "win32", "só Windows")
    def test_main_sets_app_user_model_id_from_shortcut(self):
        from app import main as appmain, shortcut
        fake = types.SimpleNamespace(create_window=mock.Mock(return_value="WIN"), start=mock.Mock())
        with mock.patch.dict(sys.modules, {"webview": fake}), mock.patch.object(appmain, "Api"), \
                mock.patch.object(appmain.ollama, "ensure_running"):
            self.assertEqual(appmain.main(), 0)
        self.set_aumid.assert_called_once_with(shortcut.APP_ID)

    def test_main_creates_window_and_starts(self):
        from app import main as appmain
        fake = types.SimpleNamespace(create_window=mock.Mock(return_value="WIN"), start=mock.Mock())
        with mock.patch.dict(sys.modules, {"webview": fake}), \
                mock.patch.object(appmain, "Api") as api_cls, \
                mock.patch.object(appmain.ollama, "ensure_running"):
            self.assertEqual(appmain.main(), 0)
        api = api_cls.return_value
        fake.create_window.assert_called_once_with(
            "ContextClaude", str(appmain.UI_DIR / "index.html"), js_api=api, width=1280, height=820,
            min_size=(960, 640), background_color="#F7F7F5")
        api.bind.assert_called_once_with("WIN")
        fake.start.assert_called_once()
        icon = fake.start.call_args.kwargs["icon"]
        self.assertTrue(icon.endswith("icon.ico") and Path(icon).exists())

    def test_main_removes_leftover_pause_file(self):
        from app import main as appmain
        from contextclaude import config
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CONTEXTCLAUDE_ROOT": tmp}):
            pf = config.pause_path()
            pf.parent.mkdir(parents=True, exist_ok=True)
            fake = types.SimpleNamespace(create_window=mock.Mock(return_value="WIN"),
                                         start=mock.Mock(side_effect=lambda **kw: pf.write_text("x")))
            with mock.patch.dict(sys.modules, {"webview": fake}), mock.patch.object(appmain, "Api"), \
                    mock.patch.object(appmain.ollama, "ensure_running"):
                self.assertEqual(appmain.main(), 0)
            self.assertFalse(pf.exists())

    def test_main_without_pywebview(self):
        from app import main as appmain
        out = []
        with mock.patch.dict(sys.modules, {"webview": None}), mock.patch("builtins.print", out.append):
            self.assertEqual(appmain.main(), 1)
        self.assertEqual(out, ["Instale o pywebview: pip install pywebview"])


if __name__ == "__main__":
    unittest.main()
