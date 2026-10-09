import contextlib
import ctypes
import io
import queue
import sys
import threading
import unittest

from ema_tts import config as cfg
from ema_tts import hotkeys as hk

WINDOWS = sys.platform == "win32"
# Pratikte hiçbir klavyede olmayan F21–F24 + tüm değiştiriciler: kullanıcıyı etkilemez.
CANDIDATES = [f"<ctrl>+<alt>+<shift>+<win>+<f{n}>" for n in (24, 23, 22, 21)]


def free_hotkey() -> str:
    for hotkey in CANDIDATES:
        combo = hk.parse(hotkey)
        if hk.user32.RegisterHotKey(None, 0xBEEF, combo.modifiers | hk.MOD_NOREPEAT, combo.vk):
            hk.user32.UnregisterHotKey(None, 0xBEEF)
            return hotkey
    raise unittest.SkipTest("serbest test kısayolu bulunamadı")


class ParseTest(unittest.TestCase):
    def test_valid(self):
        cases = {
            "<ctrl>+<shift>+<f9>": (hk.MOD_CONTROL | hk.MOD_SHIFT, 0x78, "Ctrl+Shift+F9"),
            "<win>+<alt>+r": (hk.MOD_WIN | hk.MOD_ALT, ord("R"), "Win+Alt+R"),
            "<alt>+<shift>+1": (hk.MOD_ALT | hk.MOD_SHIFT, ord("1"), "Alt+Shift+1"),
            "<shift>+<alt>+<ctrl>+<space>": (hk.MOD_CONTROL | hk.MOD_ALT | hk.MOD_SHIFT, 0x20,
                                             "Ctrl+Alt+Shift+Space"),
            "<shift>+<f24>": (hk.MOD_SHIFT, 0x87, "Shift+F24"),
            "<ctrl>+<insert>": (hk.MOD_CONTROL, 0x2D, "Ctrl+Insert"),
            "<pause>": (0, 0x13, "Pause"),
            " <CTRL> + A ": (hk.MOD_CONTROL, ord("A"), "Ctrl+A"),
            "ctrl+page_down": (hk.MOD_CONTROL, 0x22, "Ctrl+Page Down"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(tuple(hk.parse(text)), expected)
                self.assertEqual(hk.label(text), expected[2])

    def test_invalid(self):
        cases = {
            "": "geçerli bir kısayol değil",
            "<ctrl>++a": "geçerli bir kısayol değil",
            "<ctrl>+<foo>": "bilinmeyen tuş: foo",
            "<ctrl>+ş": "bilinmeyen tuş: ş",
            "<f25>": "bilinmeyen tuş: f25",
            "<ctrl>+<shift>": "tuş yok",
            "<ctrl>+a+b": "birden fazla tuş",
        }
        for text, message in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as ctx:
                    hk.parse(text)
                self.assertIn(message, str(ctx.exception))

    def test_defaults_are_valid_distinct_and_avoid_altgr(self):
        combos = [hk.parse(value) for value in cfg.DEFAULTS["hotkeys"].values()]
        self.assertEqual(set(cfg.DEFAULTS["hotkeys"]),
                         {"add_clipboard", "pause_resume", "skip", "toggle_panel", "clear"})
        self.assertEqual(len({(c.modifiers, c.vk) for c in combos}), len(combos))
        ctrl_alt = hk.MOD_CONTROL | hk.MOD_ALT
        self.assertFalse(any(c.modifiers & ctrl_alt == ctrl_alt for c in combos))


@unittest.skipUnless(WINDOWS, "RegisterHotKey yalnızca Windows'ta")
class RegisterTest(unittest.TestCase):
    def setUp(self):
        self.hotkey = free_hotkey()
        self.threads = []

    def tearDown(self):
        for thread in self.threads:
            thread.stop()

    def start(self, bindings):
        thread = hk.Hotkeys(bindings)
        self.threads.append(thread)
        return thread, thread.start()

    def test_register_and_unregister_cycle(self):
        thread, failures = self.start({"a": (self.hotkey, lambda: None)})
        self.assertEqual(failures, {})
        self.assertIn("a", thread.ids)
        thread.stop()
        self.assertFalse(thread.is_alive())
        # kayıt kaldırıldıysa aynı kombinasyon yeniden kaydedilebilir
        again, failures = self.start({"a": (self.hotkey, lambda: None)})
        self.assertEqual(failures, {})

    def test_conflict_is_reported(self):
        _first, failures = self.start({"a": (self.hotkey, lambda: None)})
        self.assertEqual(failures, {})
        second, failures = self.start({"b": (self.hotkey, lambda: None)})
        self.assertEqual(list(failures), ["b"])
        self.assertIn("başka bir program tarafından kullanılıyor", failures["b"])
        self.assertIn(hk.label(self.hotkey), failures["b"])
        self.assertNotIn("b", second.ids)
        self.assertTrue(second.is_alive())

    def test_invalid_and_duplicate_do_not_block_others(self):
        thread, failures = self.start({
            "ok": (self.hotkey, lambda: None),
            "bad": ("<ctrl>+<foo>", lambda: None),
            "dup": (self.hotkey.upper(), lambda: None),
        })
        self.assertEqual(set(failures), {"bad", "dup"})
        self.assertIn("bilinmeyen tuş", failures["bad"])
        self.assertIn("başka bir eylemde de tanımlı (ok)", failures["dup"])
        self.assertEqual(list(thread.ids), ["ok"])

    def test_clean_shutdown(self):
        thread, _ = self.start({"a": (self.hotkey, lambda: None)})
        self.assertTrue(thread.is_alive())
        thread.stop(timeout=2)
        self.assertFalse(thread.is_alive())
        thread.stop()  # ikinci kez durdurmak sorun değil
        hk.Hotkeys({}).stop()  # hiç başlamamış thread de

    def test_callback_on_wm_hotkey(self):
        called = threading.Event()
        thread, _ = self.start({"a": (self.hotkey, called.set), "b": ("", lambda: None)})
        # gerçek tuş basmadan: thread'e sahte WM_HOTKEY gönder
        self.assertTrue(hk.user32.PostThreadMessageW(thread.native_id, hk.WM_HOTKEY, thread.ids["a"], 0),
                        ctypes.get_last_error())
        self.assertTrue(called.wait(2))

    def test_callback_error_does_not_kill_thread(self):
        called = threading.Event()

        def boom():
            called.set()
            raise RuntimeError("deneme")

        thread, _ = self.start({"a": (self.hotkey, boom)})
        with contextlib.redirect_stderr(io.StringIO()) as log:
            hk.user32.PostThreadMessageW(thread.native_id, hk.WM_HOTKEY, thread.ids["a"], 0)
            self.assertTrue(called.wait(2))
            thread.join(0.2)
        self.assertIn("RuntimeError: deneme", log.getvalue())
        self.assertTrue(thread.is_alive())

    def test_app_start_hotkeys(self):
        from ema_tts import app

        events = queue.Queue()
        with contextlib.redirect_stdout(io.StringIO()) as log:
            thread, messages = app.start_hotkeys(
                {"add_clipboard": self.hotkey, "skip": "<ctrl>+<foo>", "clear": ""}, events)
        self.assertIn("kısayol kaydedilemedi: ", log.getvalue())
        self.threads.append(thread)
        self.assertEqual(list(thread.ids), ["add_clipboard"])
        self.assertEqual(len(messages), 1)
        self.assertIn("(hotkeys.skip)", messages[0])
        hk.user32.PostThreadMessageW(thread.native_id, hk.WM_HOTKEY, thread.ids["add_clipboard"], 0)
        self.assertEqual(events.get(timeout=2), "clipboard")
        self.assertEqual(app.hotkey_label(""), "kapalı")
        self.assertEqual(app.hotkey_label("<foo>"), "geçersiz")
        self.assertEqual(app.hotkey_label("<alt>+<shift>+1"), "Alt+Shift+1")


if __name__ == "__main__":
    unittest.main()
