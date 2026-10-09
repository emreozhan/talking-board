"""Ayarlar: varsayılanlar + config.json. Her özellik kendi bölümünü kullanır."""

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"

DEFAULTS = {
    "voice": {
        "speed": 1.0,
        "volume": 100,
        "seed": None,  # None: her okumada rastgele
        "output_device": None,  # None: sistem varsayılanı
        "sample_rate": 48000,
        "max_chars": 300,
        "use_lightning": False,
    },
    "hotkeys": {
        "add_clipboard": "<alt>+<shift>+1",
        "pause_resume": "<alt>+<shift>+2",
        "skip": "<alt>+<shift>+3",
        "toggle_panel": "<alt>+<shift>+4",
        "clear": "<alt>+<shift>+5",
    },
    "server": {
        "enabled": True,
        "port": 47821,
        "auto_read": True,
    },
}


def load(path: Path = CONFIG_FILE) -> dict:
    """Varsayılanları config.json ile bölüm bölüm birleştirir."""
    config = copy.deepcopy(DEFAULTS)
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        for section, values in user.items():
            if isinstance(values, dict) and isinstance(config.get(section), dict):
                config[section].update(values)
            else:
                config[section] = values
    return config


def save(config: dict, path: Path = CONFIG_FILE):
    """Yalnızca varsayılandan farklı değerleri yazar; böylece varsayılanlar dosyada donmaz."""
    changed = {}
    for section, values in config.items():
        defaults = DEFAULTS.get(section)
        if isinstance(values, dict) and isinstance(defaults, dict):
            values = {key: value for key, value in values.items() if defaults.get(key, object()) != value}
            if not values:
                continue
        changed[section] = values
    path.write_text(json.dumps(changed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
