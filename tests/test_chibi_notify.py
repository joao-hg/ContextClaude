import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, "src")
from contextclaude import chibi_notify


class TestChibiNotify(unittest.TestCase):
    def test_notify_swallows_win32_exception(self):
        with mock.patch.object(chibi_notify, "_show_balloon", side_effect=RuntimeError("sem HWND")):
            chibi_notify.notify("Título", "Texto")  # não deve levantar

    def test_notify_swallows_missing_pywin32(self):
        with mock.patch.object(chibi_notify, "_show_balloon", side_effect=ImportError("no pywin32")):
            chibi_notify.notify("Título", "Texto")  # não deve levantar

    def test_notify_calls_show_balloon_with_title_and_text(self):
        with mock.patch.object(chibi_notify, "_show_balloon") as m:
            chibi_notify.notify("Título", "Texto")
            m.assert_called_once_with("Título", "Texto")

    def test_cleanup_swallows_win32_exception(self):
        with mock.patch.object(chibi_notify, "_destroy_tray", side_effect=RuntimeError("sem HWND")):
            chibi_notify.cleanup()  # não deve levantar

    def test_cleanup_swallows_missing_pywin32(self):
        with mock.patch.object(chibi_notify, "_destroy_tray", side_effect=ImportError("no pywin32")):
            chibi_notify.cleanup()  # não deve levantar

    def test_cleanup_calls_destroy_tray(self):
        with mock.patch.object(chibi_notify, "_destroy_tray") as m:
            chibi_notify.cleanup()
            m.assert_called_once_with()

    def test_load_icon_uses_custom_ico_file(self):
        with mock.patch("win32gui.LoadImage", return_value=42) as m:
            hicon = chibi_notify._load_icon()
        self.assertEqual(hicon, 42)
        args, _kwargs = m.call_args
        self.assertEqual(args[1], str(chibi_notify.ICON_PATH))

    def test_load_icon_falls_back_to_default_on_error(self):
        with mock.patch("win32gui.LoadImage", side_effect=OSError("sem arquivo")):
            with mock.patch("win32gui.LoadIcon", return_value=99) as m2:
                hicon = chibi_notify._load_icon()
        self.assertEqual(hicon, 99)
        m2.assert_called_once()

    def test_show_balloon_uses_custom_icon(self):
        with mock.patch.object(chibi_notify, "_get_hwnd", return_value=123):
            with mock.patch.object(chibi_notify, "_load_icon", return_value=77) as m_icon:
                with mock.patch("win32gui.Shell_NotifyIcon") as m_shell:
                    chibi_notify._show_balloon("Título", "Texto")
        m_icon.assert_called_once_with()
        nid = m_shell.call_args[0][1]
        self.assertEqual(nid[4], 77)  # hicon na posição da tupla NOTIFYICONDATA
