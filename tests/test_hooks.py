import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ema_tts import server as speak_server
from ema_tts.speech_queue import SpeechQueue

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
SYSTEM_PYTHON = Path(r"C:\Users\emreo\AppData\Local\Programs\Python\Python313\python.exe")
PYTHON = str(SYSTEM_PYTHON if SYSTEM_PYTHON.exists() else sys.executable)  # the hook runs on the system Python


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_hook(event, port):
    stdin = event if isinstance(event, bytes) else json.dumps(event).encode("utf-8")
    env = {**os.environ, "EMA_TTS_PORT": str(port)}
    return subprocess.run([PYTHON, "-I", str(HOOKS / "claude_stop_hook.py")], input=stdin,
                          capture_output=True, env=env, timeout=15)


def line(kind, content, **extra):
    return json.dumps({"type": kind, "message": {"role": kind, "content": content}, **extra})


# Agent replies are Turkish here, as in real use; this also covers UTF-8 end to end.
class StopHookTest(unittest.TestCase):
    def setUp(self):
        self.queue = SpeechQueue()
        self.server = speak_server.start(self.queue, 0, lambda: True)
        self.addCleanup(self.server.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.transcript = Path(tmp.name) / "session.jsonl"

    def assert_silent(self, result):
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def texts(self):
        return [item.text for item in self.queue.snapshot()]

    def test_uses_last_assistant_message(self):
        event = {"hook_event_name": "Stop", "stop_hook_active": False,
                 "last_assistant_message": "Bitti, testler geçti. Şimdi ne yapalım?",
                 "transcript_path": str(self.transcript)}
        self.assert_silent(run_hook(event, self.server.port))
        self.assertEqual(self.texts(), ["Bitti, testler geçti. Şimdi ne yapalım?"])

    def test_falls_back_to_transcript(self):
        self.transcript.write_text("\n".join([
            line("user", "old question"),
            line("assistant", [{"type": "text", "text": "old answer"}]),
            line("user", [{"type": "text", "text": "new question"}]),
            line("assistant", [{"type": "thinking", "thinking": "hidden thought"},
                               {"type": "text", "text": "Önce bakıyorum."},
                               {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]),
            line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "file"}]),
            line("assistant", [{"type": "text", "text": "Subagent note"}], isSidechain=True),
            "broken line",
            line("assistant", [{"type": "text", "text": "Sonuç: her şey yolunda."}]),
        ]) + "\n", encoding="utf-8")
        event = {"hook_event_name": "Stop", "stop_hook_active": False,
                 "transcript_path": str(self.transcript)}
        self.assert_silent(run_hook(event, self.server.port))
        self.assertEqual(self.texts(), ["Önce bakıyorum.\n\nSonuç: her şey yolunda."])

    def test_stop_hook_active_does_nothing(self):
        event = {"stop_hook_active": True, "last_assistant_message": "do not read again"}
        self.assert_silent(run_hook(event, self.server.port))
        self.assertEqual(self.texts(), [])

    def test_empty_message_sends_nothing(self):
        self.assert_silent(run_hook({"stop_hook_active": False, "last_assistant_message": "  "},
                                    self.server.port))
        self.assertEqual(self.texts(), [])

    def test_silent_when_server_down_or_input_broken(self):
        port = free_port()
        for stdin in (json.dumps({"last_assistant_message": "nobody listening"}).encode(), b"{broken", b"",
                      json.dumps({"transcript_path": "C:/missing/missing.jsonl"}).encode()):
            with self.subTest(stdin=stdin):
                started = time.monotonic()
                self.assert_silent(run_hook(stdin, port))
                self.assertLess(time.monotonic() - started, 5)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings = Path(tmp.name) / "settings.json"
        self.original = {
            "model": "opus",
            "permissions": {"allow": ["Bash(git status)"]},
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}],
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "check"}]}],
            },
        }
        self.settings.write_text(json.dumps(self.original, indent=2), encoding="utf-8")

    def install(self, *args):
        result = subprocess.run([PYTHON, "-I", str(HOOKS / "install_claude_hook.py"),
                                 "--settings", str(self.settings), *args],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def ours(self, settings):
        return [h for group in settings.get("hooks", {}).get("Stop", []) for h in group["hooks"]
                if "claude_stop_hook.py" in " ".join(h.get("args", []))]

    def test_install_reinstall_uninstall(self):
        first = self.install()
        backup = self.settings.with_name("settings.json.bak-ema-tts")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), self.original)
        self.assertEqual(first["model"], "opus")
        self.assertEqual(first["permissions"], self.original["permissions"])
        self.assertEqual(first["hooks"]["PreToolUse"], self.original["hooks"]["PreToolUse"])
        self.assertEqual(first["hooks"]["Stop"][0], self.original["hooks"]["Stop"][0])
        self.assertEqual(self.ours(first), [{
            "type": "command",
            "command": r"C:\Users\emreo\AppData\Local\Programs\Python\Python313\python.exe",
            "args": [r"C:\Users\emreo\Documents\AiProjects\ema-tts\hooks\claude_stop_hook.py"],
            "timeout": 5,
            "async": True,
        }])

        mtime = self.settings.stat().st_mtime_ns
        self.assertEqual(self.install(), first)
        self.assertEqual(self.settings.stat().st_mtime_ns, mtime)  # not written when nothing changes

        removed = self.install("--uninstall")
        self.assertEqual(removed, self.original)
        self.assertEqual(self.install("--uninstall"), self.original)

    def test_install_into_missing_or_empty_settings(self):
        self.settings.unlink()
        self.assertEqual(len(self.ours(self.install())), 1)
        self.assertEqual(self.install("--uninstall"), {})

    def test_broken_settings_left_untouched(self):
        self.settings.write_text("{broken", encoding="utf-8")
        result = subprocess.run([PYTHON, "-I", str(HOOKS / "install_claude_hook.py"),
                                 "--settings", str(self.settings)], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"settings.json", result.stderr)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
