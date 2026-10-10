"""Claude Code Stop hook: sends the agent's last reply to the ema-tts /speak endpoint.

Uses only the stdlib. Always exits 0 and never writes to stdout; it never blocks Claude.
Port lookup order: EMA_TTS_PORT environment variable, server.port in config.json, default 47821.
"""

import http.client
import json
import os
import sys
from pathlib import Path

DEFAULT_PORT = 47821
TIMEOUT = 1.0
CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"
SKIPPED_BLOCKS = {"tool_use", "tool_result", "thinking", "redacted_thinking"}


def port() -> int:
    value = os.environ.get("EMA_TTS_PORT")
    if not value:
        try:
            value = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))["server"]["port"]
        except (OSError, ValueError, KeyError, TypeError):
            value = DEFAULT_PORT
    return int(value)


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"]


def _is_user_prompt(entry: dict) -> bool:
    """Is this a real user prompt? 'user' entries carrying tool_result do not count."""
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and not any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def from_transcript(path: str) -> str:
    """Joins the assistant text blocks after the last user prompt."""
    parts: list[str] = []
    with open(path, encoding="utf-8") as lines:
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("isSidechain"):
                continue
            if _is_user_prompt(entry):
                parts = []
            elif entry.get("type") == "assistant":
                parts += _texts((entry.get("message") or {}).get("content"))
    return "\n\n".join(p.strip() for p in parts if p.strip())


def message(event: dict) -> str:
    text = event.get("last_assistant_message")
    if isinstance(text, str):
        return text.strip()
    path = event.get("transcript_path")
    return from_transcript(os.path.expanduser(path)) if path else ""


def send(text: str):
    body = json.dumps({"text": text, "source": "claude"}, ensure_ascii=False).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port(), timeout=TIMEOUT)
    try:
        conn.request("POST", "/speak", body, {"Content-Type": "application/json"})
        conn.getresponse().read()
    finally:
        conn.close()


def main():
    event = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    if not isinstance(event, dict) or event.get("stop_hook_active"):
        return
    text = message(event)
    if text:
        send(text)


if __name__ == "__main__":
    sys.stdout = sys.stderr = open(os.devnull, "w")
    try:
        main()
    except BaseException:
        pass
    os._exit(0)
