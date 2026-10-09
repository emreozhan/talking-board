"""Giriş noktası: ayarları okur, oynatıcıyı, kısayolları ve arayüzü başlatır."""

import ctypes
import os
import sys

from . import config as cfg
from . import hotkeys as hk
from . import server as speak_server
from .player import Player
from .speech_queue import SpeechQueue
from .ui import App

# hotkeys bölümündeki eylem -> arayüz olayı
ACTIONS = {
    "add_clipboard": "clipboard",
    "pause_resume": "pause",
    "skip": "skip",
    "toggle_panel": "panel",
    "clear": "clear",
}


def hotkey_label(hotkey: str) -> str:
    if not hotkey:
        return "kapalı"
    try:
        return hk.label(hotkey)
    except ValueError:
        return "geçersiz"


def start_hotkeys(settings: dict, events) -> tuple[hk.Hotkeys, list[str]]:
    """Eylemleri kaydeder; boş dizge eylemi kapatır. Başarısızları kullanıcı mesajı olarak döner."""
    bindings = {name: (settings[name], lambda event=event: events.put(event))
                for name, event in ACTIONS.items() if settings.get(name)}
    hotkeys = hk.Hotkeys(bindings)
    failures = hotkeys.start()
    messages = [f"{message} (hotkeys.{name})" for name, message in failures.items()]
    for message in messages:
        print(f"kısayol kaydedilemedi: {message}")
    return hotkeys, messages


def main():
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass

    config = cfg.load()
    speech_queue = SpeechQueue()
    player = Player(speech_queue, config["voice"])
    app = App(speech_queue, player, hotkey_label(config["hotkeys"].get("add_clipboard", "")),
              cfg.ROOT / "state.json")

    hotkeys, failures = start_hotkeys(config["hotkeys"], app.events)
    app.hotkey_warning = "\n".join(failures)
    player.start()
    server = None
    if config["server"]["enabled"]:
        auto_read = lambda: cfg.load()["server"]["auto_read"]  # menüden değişince yeniden başlatma gerekmesin
        server = speak_server.start(speech_queue, config["server"]["port"], auto_read)
    try:
        app.run()
    finally:
        hotkeys.stop()
        if server:
            server.stop()


if __name__ == "__main__":
    main()
