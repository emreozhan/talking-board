"""Adds or removes the ema-tts Stop hook in the Claude Code settings (idempotent).

Usage:
    python hooks/install_claude_hook.py [--settings PATH] [--uninstall]

The hook is written in exec form (command + args): no shell, paths with spaces need no quoting.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

SETTINGS_FILE = Path.home() / ".claude" / "settings.json"
# the hook only needs the stdlib, so use the base interpreter: it keeps working if the venv is removed
PYTHON = getattr(sys, "_base_executable", None) or sys.executable
HOOK = str(Path(__file__).resolve().with_name("claude_stop_hook.py"))
MARKER = "claude_stop_hook.py"
BACKUP_SUFFIX = ".bak-ema-tts"


def handler(python: str = PYTHON, hook: str = HOOK) -> dict:
    return {"type": "command", "command": python, "args": [hook], "timeout": 5, "async": True}


def _ours(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    args = entry.get("args") if isinstance(entry.get("args"), list) else []
    return any(MARKER in str(part) for part in [entry.get("command", ""), *args])


def _without_ours(groups: list) -> list:
    result = []
    for group in groups:
        if isinstance(group, dict) and isinstance(group.get("hooks"), list):
            kept = [h for h in group["hooks"] if not _ours(h)]
            if not kept:
                continue
            group = {**group, "hooks": kept}
        result.append(group)
    return result


def apply(settings: dict, install: bool, entry: dict | None = None) -> dict:
    """Returns a copy of the settings with our entry removed and, if requested, added once."""
    settings = json.loads(json.dumps(settings))
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be a JSON object")
    stop = hooks.get("Stop", [])
    if not isinstance(stop, list):
        raise ValueError("hooks.Stop must be a list")
    stop = _without_ours(stop)
    if install:
        stop.append({"hooks": [entry or handler()]})
    if stop:
        hooks["Stop"] = stop
    else:
        hooks.pop("Stop", None)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    return settings


def read(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("the root must be a JSON object")
    return data


def write(path: Path, settings: dict):
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + BACKUP_SUFFIX))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-ema-tts")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Adds the ema-tts Stop hook to the Claude Code settings.")
    parser.add_argument("--settings", type=Path, default=SETTINGS_FILE, help="path to settings.json")
    parser.add_argument("--uninstall", action="store_true", help="remove the hook")
    parser.add_argument("--python", default=PYTHON, help="python.exe that runs the hook")
    parser.add_argument("--hook", default=HOOK, help="path to claude_stop_hook.py")
    args = parser.parse_args(argv)

    try:
        current = read(args.settings)
        updated = apply(current, not args.uninstall, handler(args.python, args.hook))
    except (OSError, ValueError) as exc:
        print(f"could not read {args.settings}: {exc}", file=sys.stderr)
        return 1
    if not args.uninstall:
        for path in (args.python, args.hook):
            if not Path(path).exists():
                print(f"warning: {path} not found; the hook will not run until it exists", file=sys.stderr)
    if updated == current:
        print("no change: " + ("hook already removed" if args.uninstall else "hook already installed"))
        return 0
    write(args.settings, updated)
    print(("hook removed: " if args.uninstall else "hook installed: ") + str(args.settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
