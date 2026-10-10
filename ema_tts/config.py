"""Settings: defaults + config.json. Each feature uses its own section."""

import copy
import json
import os
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"

DEFAULTS = {
    "voice": {
        "speed": 1.0,
        "volume": 100,
        "seed": None,  # None: random on every reading
        "output_device": None,  # None: system default
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

SPEED_RANGE = (0.25, 4.0)
VOLUME_RANGE = (0, 100)
SAMPLE_RATES = (48000, 24000, 16000, 8000)
MAX_CHARS_RANGE = (50, 1000)
PORT_RANGE = (1024, 65535)
MAX_SEED = 2**31 - 1


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _between(check, low, high):
    return lambda value: check(value) and low <= value <= high, f"a number between {low} and {high}"


# key -> (is the value valid, what a valid value looks like)
RULES = {
    "voice": {
        "speed": _between(_number, *SPEED_RANGE),
        "volume": _between(_number, *VOLUME_RANGE),
        "seed": (lambda v: v is None or (_whole(v) and 0 <= v <= MAX_SEED),
                 f"null or a whole number between 0 and {MAX_SEED}"),
        "output_device": (lambda v: v is None or isinstance(v, str), "null or a device name"),
        "sample_rate": (lambda v: _whole(v) and v in SAMPLE_RATES, "one of " + ", ".join(map(str, SAMPLE_RATES))),
        "max_chars": (lambda v: _whole(v) and MAX_CHARS_RANGE[0] <= v <= MAX_CHARS_RANGE[1],
                      f"a whole number between {MAX_CHARS_RANGE[0]} and {MAX_CHARS_RANGE[1]}"),
        "use_lightning": (lambda v: isinstance(v, bool), "true or false"),
    },
    "hotkeys": {name: (lambda v: isinstance(v, str), "a string") for name in DEFAULTS["hotkeys"]},
    "server": {
        "enabled": (lambda v: isinstance(v, bool), "true or false"),
        "port": (lambda v: _whole(v) and PORT_RANGE[0] <= v <= PORT_RANGE[1],
                 f"a whole number between {PORT_RANGE[0]} and {PORT_RANGE[1]}"),
        "auto_read": (lambda v: isinstance(v, bool), "true or false"),
    },
}


FAIL_CLOSED = {("server", "enabled"), ("server", "auto_read")}  # an invalid value turns these off


def read(path: Path = CONFIG_FILE) -> dict:
    """The raw contents of config.json, {} if it does not exist. Raises OSError or ValueError if unusable."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("the root must be a JSON object")
    return data


def raw_value(section: str, key: str, path: Path = CONFIG_FILE):
    """One value exactly as written in config.json (the default if absent); raises if the file is unusable."""
    values = read(path).get(section, {})
    if not isinstance(values, dict):
        raise ValueError(f"{section} must be a JSON object")
    return values.get(key, DEFAULTS[section][key])


def load(path: Path = CONFIG_FILE, warnings: list[str] | None = None) -> dict:
    """Merges config.json into the defaults section by section.

    Never raises: an unreadable file, a section of the wrong type or an invalid value falls back to the
    defaults, except that agent reading (server.enabled, server.auto_read) is then off, matching the server
    and the hook, which fail closed. Each problem is appended to `warnings` as a user-facing message.
    """
    warnings = [] if warnings is None else warnings
    config = copy.deepcopy(DEFAULTS)
    try:
        user = read(path)
    except (OSError, ValueError) as exc:
        config["server"]["auto_read"] = False
        warnings.append(f"{path.name} could not be read, using the defaults with agent reading off: {exc}")
        return config
    for section, values in user.items():
        defaults = config.get(section)
        if not isinstance(defaults, dict):
            config[section] = values  # unknown sections are kept as they are
            continue
        if not isinstance(values, dict):
            if section == "server":
                defaults["auto_read"] = False
            warnings.append(f"{path.name}: {section} must be a JSON object; using the defaults for it"
                            + (" with agent reading off" if section == "server" else ""))
            continue
        for key, value in values.items():
            rule = RULES.get(section, {}).get(key)
            if rule and not rule[0](value):
                fallback = False if (section, key) in FAIL_CLOSED else defaults[key]
                warnings.append(f"{path.name}: {section}.{key} must be {rule[1]}; using {json.dumps(fallback)}")
                value = fallback
            defaults[key] = value
    return config


def save(config: dict, path: Path = CONFIG_FILE):
    """Writes only values that differ from the defaults, so defaults are not frozen into the file.

    The file is replaced atomically, so a reader never sees half a file.
    """
    changed = {}
    for section, values in config.items():
        defaults = DEFAULTS.get(section)
        if isinstance(values, dict) and isinstance(defaults, dict):
            values = {key: value for key, value in values.items() if defaults.get(key, object()) != value}
            if not values:
                continue
        changed[section] = values
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(changed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:  # Windows: a reader (e.g. the hook) has the file open for a moment
                if attempt == 9:
                    raise
                time.sleep(0.05)
    finally:
        temporary.unlink(missing_ok=True)


def update(section: str, values: dict, path: Path = CONFIG_FILE) -> dict:
    """Changes one section of config.json. Refuses (raises) rather than overwrite a file it cannot read."""
    read(path)
    config = load(path)
    config[section] = {**config[section], **values}
    save(config, path)
    return config
