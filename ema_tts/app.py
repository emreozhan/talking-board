"""Giriş noktası: ayarları okur, oynatıcıyı, kısayolu ve arayüzü başlatır."""

import ctypes
import json
import os
import sys
from pathlib import Path

from pynput import keyboard

from .player import Player
from .speech_queue import SpeechQueue
from .ui import App

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = {
    "hotkey": "<ctrl>+<alt>+<space>",
    "speed": 1.0,
    "sample_rate": 48000,
    "max_chars": 300,
    "use_lightning": False,
}


def load_config() -> dict:
    config = dict(DEFAULTS)
    path = ROOT / "config.json"
    if path.exists():
        config.update(json.loads(path.read_text(encoding="utf-8")))
    return config


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

    config = load_config()
    speech_queue = SpeechQueue()
    player = Player(speech_queue, speed=config["speed"], sample_rate=config["sample_rate"],
                    max_chars=config["max_chars"], use_lightning=config["use_lightning"])
    app = App(speech_queue, player, hotkey_label(config["hotkey"]), ROOT / "state.json")

    hotkeys = listen_hotkey(config["hotkey"], lambda: app.events.put("clipboard"))
    player.start()
    try:
        app.run()
    finally:
        hotkeys.stop()


if __name__ == "__main__":
    main()
