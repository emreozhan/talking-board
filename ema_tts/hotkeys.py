"""Global hotkeys via Win32 RegisterHotKey (ctypes, a thread with its own message loop)."""

import ctypes
import sys
import threading
import traceback
from ctypes import wintypes
from typing import Callable, NamedTuple

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

MODIFIERS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT,
             "win": MOD_WIN, "cmd": MOD_WIN}
MOD_LABELS = [(MOD_WIN, "Win"), (MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift")]

# name: (virtual key code, label)
SPECIAL_KEYS = {
    "space": (0x20, "Space"),
    "enter": (0x0D, "Enter"),
    "tab": (0x09, "Tab"),
    "esc": (0x1B, "Esc"),
    "backspace": (0x08, "Backspace"),
    "insert": (0x2D, "Insert"),
    "delete": (0x2E, "Delete"),
    "home": (0x24, "Home"),
    "end": (0x23, "End"),
    "page_up": (0x21, "Page Up"),
    "page_down": (0x22, "Page Down"),
    "up": (0x26, "Up"),
    "down": (0x28, "Down"),
    "left": (0x25, "Left"),
    "right": (0x27, "Right"),
    "pause": (0x13, "Pause"),
    "print_screen": (0x2C, "Print Screen"),
    "scroll_lock": (0x91, "Scroll Lock"),
    "media_play_pause": (0xB3, "Media Play/Pause"),
    "media_next": (0xB0, "Media Next"),
    "media_previous": (0xB1, "Media Previous"),
    "media_stop": (0xB2, "Media Stop"),
}
SPECIAL_KEYS.update({f"f{n}": (0x70 + n - 1, f"F{n}") for n in range(1, 25)})


class Combo(NamedTuple):
    modifiers: int
    vk: int
    label: str


def parse(hotkey: str) -> Combo:
    """Turns a string like '<ctrl>+<shift>+<f9>' into MOD + VK; raises ValueError on errors."""
    parts = [part.strip().lower() for part in hotkey.split("+")]
    if not hotkey.strip() or any(not part for part in parts):
        raise ValueError(f"'{hotkey}' is not a valid hotkey (e.g. <ctrl>+<shift>+<f9>)")
    modifiers, key = 0, None
    for part in parts:
        name = part[1:-1] if part.startswith("<") and part.endswith(">") else part
        if name in MODIFIERS:
            modifiers |= MODIFIERS[name]
            continue
        if key is not None:
            raise ValueError(f"'{hotkey}' has more than one key: {key[1]} and {name}")
        if name in SPECIAL_KEYS:
            key = SPECIAL_KEYS[name]
        elif len(name) == 1 and (name.isascii() and name.isalnum()):
            key = (ord(name.upper()), name.upper())
        else:
            raise ValueError(f"'{hotkey}' has an unknown key: {name}")
    if key is None:
        raise ValueError(f"'{hotkey}' has no key, only modifiers")
    names = [text for flag, text in MOD_LABELS if modifiers & flag]
    return Combo(modifiers, key[0], "+".join(names + [key[1]]))


def label(hotkey: str) -> str:
    """Display label, e.g. 'Ctrl+Shift+F9'."""
    return parse(hotkey).label


if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                    wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL


class Hotkeys(threading.Thread):
    """Registers hotkeys on its own thread and calls the callback on WM_HOTKEY.

    bindings: {name: (hotkey string, callback)}. start() returns failures as {name: message}.
    """

    def __init__(self, bindings: dict[str, tuple[str, Callable[[], None]]]):
        super().__init__(daemon=True, name="ema-hotkeys")
        self.bindings = bindings
        self.ids: dict[str, int] = {}  # name -> RegisterHotKey id
        self.failures: dict[str, str] = {}
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._ready = threading.Event()

    def start(self) -> dict[str, str]:
        super().start()
        self._ready.wait()
        return dict(self.failures)

    def _register_all(self):
        seen: dict[tuple[int, int], str] = {}
        for next_id, (name, (hotkey, callback)) in enumerate(self.bindings.items(), start=1):
            try:
                combo = parse(hotkey)
            except ValueError as exc:
                self.failures[name] = str(exc)
                continue
            if (combo.modifiers, combo.vk) in seen:
                self.failures[name] = f"{combo.label} is also assigned to another action ({seen[combo.modifiers, combo.vk]})"
                continue
            if not user32.RegisterHotKey(None, next_id, combo.modifiers | MOD_NOREPEAT, combo.vk):
                error = ctypes.get_last_error()
                self.failures[name] = (f"{combo.label} is used by another program"
                                       if error == ERROR_HOTKEY_ALREADY_REGISTERED
                                       else f"{combo.label} could not be registered (Windows error {error})")
                continue
            seen[combo.modifiers, combo.vk] = name
            self.ids[name] = next_id
            self._callbacks[next_id] = callback

    def run(self):
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)  # creates the thread message queue
        try:
            self._register_all()
        finally:
            self._ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    callback = self._callbacks.get(msg.wParam)
                    if callback:
                        try:
                            callback()
                        except Exception:
                            traceback.print_exc()
        finally:
            for hotkey_id in self._callbacks:
                user32.UnregisterHotKey(None, hotkey_id)
            self._callbacks.clear()

    def stop(self, timeout: float = 2.0):
        if self.ident is None:
            return
        if self.is_alive():
            user32.PostThreadMessageW(self.native_id, WM_QUIT, 0, 0)
        self.join(timeout)
