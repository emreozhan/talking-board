"""Entry point: loads settings, starts the player, hotkeys, server and UI."""

import ctypes
import os
import sys
from pathlib import Path

from . import config as cfg
from . import hotkeys as hk
from . import server as speak_server
from .player import Player
from .speech_queue import SpeechQueue, default_history_file
from .ui import App

MODEL_REPO = "canberkkkkkk/ema-lightning"
MODEL_FILES = ("ema.pt", "decoder.pt")  # what EMA() needs; config.json is only a download-counter ping
APP_ID = "EmreOzhan.TalkingBoard"

# action in the hotkeys section -> UI event
ACTIONS = {
    "add_clipboard": "clipboard",
    "pause_resume": "pause",
    "skip": "skip",
    "toggle_panel": "panel",
    "clear": "clear",
}


def hotkey_label(hotkey: str) -> str:
    if not hotkey:
        return "off"
    try:
        return hk.label(hotkey)
    except ValueError:
        return "invalid"


def start_hotkeys(settings: dict, events) -> tuple[hk.Hotkeys, list[str]]:
    """Registers the actions; an empty string disables one. Returns failures as user messages."""
    bindings = {name: (settings[name], lambda event=event: events.put(event))
                for name, event in ACTIONS.items() if settings.get(name)}
    hotkeys = hk.Hotkeys(bindings)
    failures = hotkeys.start()
    messages = [f"{message} (hotkeys.{name})" for name, message in failures.items()]
    for message in messages:
        print(f"hotkey not registered: {message}")
    return hotkeys, messages


def hub_cache(environ=os.environ) -> Path:
    """The Hugging Face hub cache folder, resolved the way huggingface_hub does it."""
    home = environ.get("HF_HOME") or os.path.join(
        environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"), "huggingface")
    cache = environ.get("HF_HUB_CACHE") or environ.get("HUGGINGFACE_HUB_CACHE") or os.path.join(home, "hub")
    return Path(os.path.expandvars(os.path.expanduser(cache)))


def model_cached(environ=os.environ) -> bool:
    model = hub_cache(environ) / ("models--" + MODEL_REPO.replace("/", "--"))
    try:
        snapshot = model / "snapshots" / (model / "refs" / "main").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return all((snapshot / name).is_file() for name in MODEL_FILES)


def offline_if_cached(environ=os.environ) -> bool:
    """Once the weights are cached, keeps the Hub out of start-up (no update checks, no download-counter pings).

    Must run before huggingface_hub is imported: it reads HF_HUB_OFFLINE once, at import time.
    EMA_TTS_ONLINE=1 or an HF_HUB_OFFLINE set by the user leaves the environment alone.
    """
    if environ.get("EMA_TTS_ONLINE", "") not in ("", "0") or "HF_HUB_OFFLINE" in environ:
        return False
    if not model_cached(environ):
        return False
    environ["HF_HUB_OFFLINE"] = "1"
    return True


def main():
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    if offline_if_cached():
        print("model is cached; Hugging Face Hub access is off (set EMA_TTS_ONLINE=1 to allow it)")
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            # own taskbar button and icon instead of python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass

    warnings: list[str] = []
    config = cfg.load(warnings=warnings)
    for warning in warnings:
        print(warning)
    speech_queue = SpeechQueue(default_history_file())
    player = Player(speech_queue, config["voice"])
    app = App(speech_queue, player, hotkey_label(config["hotkeys"].get("add_clipboard", "")),
              cfg.ROOT / "state.json")

    hotkeys, failures = start_hotkeys(config["hotkeys"], app.events)
    app.warnings = warnings + failures
    player.start()
    server = None
    if config["server"]["enabled"]:
        # read on every message, so toggling it from the menu needs no restart; the server fails closed
        auto_read = lambda: cfg.raw_value("server", "auto_read")
        server = speak_server.start(speech_queue, config["server"]["port"], auto_read)
    try:
        app.run()
    finally:
        hotkeys.stop()
        if server:
            server.stop()


if __name__ == "__main__":
    main()
