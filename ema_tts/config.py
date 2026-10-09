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
        "add_clipboard": "<ctrl>+<alt>+<space>",
    },
    "server": {},
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
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
