"""ema-tts Stop hook'unu Claude Code ayarlarına ekler ya da kaldırır (idempotent).

Kullanım:
    python hooks/install_claude_hook.py [--settings YOL] [--uninstall]

Hook exec biçiminde (command + args) yazılır: kabuk yoktur, boşluklu yollar tırnak gerektirmez.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

SETTINGS_FILE = Path.home() / ".claude" / "settings.json"
PYTHON = r"C:\Users\emreo\AppData\Local\Programs\Python\Python313\python.exe"
HOOK = r"C:\Users\emreo\Documents\AiProjects\ema-tts\hooks\claude_stop_hook.py"
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
    """Ayarların kopyasını döner; bizim girdimiz kaldırılır, istenirse tek kopya olarak eklenir."""
    settings = json.loads(json.dumps(settings))
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks alanı bir JSON nesnesi olmalı")
    stop = hooks.get("Stop", [])
    if not isinstance(stop, list):
        raise ValueError("hooks.Stop alanı bir liste olmalı")
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
        raise ValueError("kök bir JSON nesnesi olmalı")
    return data


def write(path: Path, settings: dict):
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + BACKUP_SUFFIX))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-ema-tts")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ema-tts Stop hook'unu Claude Code ayarlarına ekler.")
    parser.add_argument("--settings", type=Path, default=SETTINGS_FILE, help="settings.json yolu")
    parser.add_argument("--uninstall", action="store_true", help="hook'u kaldır")
    parser.add_argument("--python", default=PYTHON, help="hook'u çalıştıracak python.exe")
    parser.add_argument("--hook", default=HOOK, help="claude_stop_hook.py yolu")
    args = parser.parse_args(argv)

    try:
        current = read(args.settings)
        updated = apply(current, not args.uninstall, handler(args.python, args.hook))
    except (OSError, ValueError) as exc:
        print(f"{args.settings} okunamadı: {exc}", file=sys.stderr)
        return 1
    if not args.uninstall:
        for path in (args.python, args.hook):
            if not Path(path).exists():
                print(f"uyarı: {path} bulunamadı; hook bu yol oluşana kadar çalışmaz", file=sys.stderr)
    if updated == current:
        print("değişiklik yok: " + ("hook zaten kaldırılmış" if args.uninstall else "hook zaten kurulu"))
        return 0
    write(args.settings, updated)
    print(("hook kaldırıldı: " if args.uninstall else "hook kuruldu: ") + str(args.settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
