"""Claude Code Stop hook: sends the agent's last reply to the Talking Board /speak endpoint.

Uses only the stdlib. Always exits 0 and never writes to stdout; it never blocks Claude.
Sends nothing unless server.enabled and server.auto_read in config.json are true (both default to true
when there is no config.json); a config.json that cannot be read also means nothing is sent.
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


def server_settings() -> dict | None:
    """The server section of config.json; {} without a config.json, None if it cannot be read."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    server = config.get("server", {}) if isinstance(config, dict) else None
    return server if isinstance(server, dict) else None


def allowed(server: dict | None) -> bool:
    return server is not None and server.get("enabled", True) is True and server.get("auto_read", True) is True


def port(server: dict) -> int:
    return int(os.environ.get("EMA_TTS_PORT") or server.get("port", DEFAULT_PORT))


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


def send(text: str, server: dict):
    body = json.dumps({"text": text, "source": "claude"}, ensure_ascii=False).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port(server), timeout=TIMEOUT)
    try:
        conn.request("POST", "/speak", body, {"Content-Type": "application/json"})
        conn.getresponse().read()
    finally:
        conn.close()


def main():
    event = json.loads(sys.stdin.buffer.read().decode("utf-8"))  # read stdin fully, so Claude's write never fails
    server = server_settings()
    if not allowed(server) or not isinstance(event, dict) or event.get("stop_hook_active"):
        return
    text = message(event)
    if text:
        send(text, server)


if __name__ == "__main__":
    sys.stdout = sys.stderr = open(os.devnull, "w")
    try:
        main()
    except BaseException:
        pass
    os._exit(0)
