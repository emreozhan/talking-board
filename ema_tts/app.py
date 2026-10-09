"""Giriş noktası: ayarları okur, oynatıcıyı, kısayolu ve arayüzü başlatır."""

import ctypes
import os
import sys

from pynput import keyboard

from . import config as cfg
from .player import Player
from .speech_queue import SpeechQueue
from .ui import App


def hotkey_label(hotkey: str) -> str:
    return "+".join(part.strip("<>").capitalize() for part in hotkey.split("+"))


def listen_hotkey(hotkey: str, callback) -> keyboard.Listener:
    """GlobalHotKeys gibi, ama AltGr'yi (Türkçe klavyede sağ Alt) Alt sayar."""
    combo = keyboard.HotKey(keyboard.HotKey.parse(hotkey), callback)

    def normalize(key):
        key = listener.canonical(key)
        return keyboard.Key.alt if key == keyboard.Key.alt_gr else key

    listener = keyboard.Listener(on_press=lambda key: combo.press(normalize(key)),
                                 on_release=lambda key: combo.release(normalize(key)))
    listener.daemon = True
    listener.start()
    return listener


def main():
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass

    config = cfg.load()
    voice = config["voice"]
    speech_queue = SpeechQueue()
    player = Player(speech_queue, speed=voice["speed"], sample_rate=voice["sample_rate"],
                    max_chars=voice["max_chars"], use_lightning=voice["use_lightning"])
    hotkey = config["hotkeys"]["add_clipboard"]
    app = App(speech_queue, player, hotkey_label(hotkey), cfg.ROOT / "state.json")

    hotkeys = listen_hotkey(hotkey, lambda: app.events.put("clipboard"))
    player.start()
    try:
        app.run()
    finally:
        hotkeys.stop()


if __name__ == "__main__":
    main()
