"""Local HTTP endpoint that lets agents (e.g. the Claude Code hook) add text to the queue."""

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlsplit

from .speech_queue import SpeechQueue

HOST = "127.0.0.1"
MAX_BODY = 256 * 1024
DISCARD_LIMIT = 4 * 1024 * 1024


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows SO_REUSEADDR shares a busy port
    daemon_threads = True

    def __init__(self, port: int, speech_queue: SpeechQueue, auto_read: Callable[[], bool]):
        self.speech_queue = speech_queue
        self.auto_read = auto_read
        super().__init__((HOST, port), _Handler)

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name, self.server_port = HOST, self.server_address[1]  # getfqdn can be slow


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "ema-tts"

    def log_message(self, format, *args):
        pass

    def _reply(self, status: int, body: dict):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str, field: str | None = None):
        body = {"error": message}
        if field:
            body["field"] = field
        self._reply(status, body)

    def _foreign(self) -> bool:
        """Is the request from a browser (Origin) or through another host name (DNS rebinding)?"""
        host = self.headers.get("Host", "")
        allowed = {f"{HOST}:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        return "Origin" in self.headers or host not in allowed

    def do_GET(self):
        if urlsplit(self.path).path == "/health":
            self._reply(200, {"ok": True})
        else:
            self._error(404, "not found")

    def do_POST(self):
        if urlsplit(self.path).path != "/speak":
            self._error(404, "not found")
            return
        if self._foreign():
            self._error(403, "only local clients are accepted")
            return
        if self.headers.get_content_type() != "application/json":
            self._error(415, "Content-Type must be application/json", "Content-Type")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 0:
            self._error(400, "Content-Length header is required", "Content-Length")
            return
        if length > MAX_BODY:
            self.close_connection = True
            self._error(413, f"body must be at most {MAX_BODY} bytes", "Content-Length")
            self._discard(min(length, DISCARD_LIMIT))
            return
        self._speak(self.rfile.read(length))

    def _discard(self, length: int):
        """On Windows a socket closed with an unread body sends RST; keep the client from losing the reply."""
        self.connection.settimeout(1)
        try:
            while length > 0:
                chunk = self.rfile.read(min(length, 64 * 1024))
                if not chunk:
                    break
                length -= len(chunk)
        except OSError:
            pass

    def _speak(self, raw: bytes):
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._error(400, "body must be valid UTF-8 JSON", "body")
            return
        if not isinstance(payload, dict):
            self._error(400, "body must be a JSON object", "body")
            return
        text, source = payload.get("text"), payload.get("source", "")
        if not isinstance(text, str):
            self._error(400, "text must be a string", "text")
            return
        if not isinstance(source, str):
            self._error(400, "source must be a string", "source")
            return
        if not self._auto_read():
            self._reply(202, {"ignored": True})
            return
        item = self.server.speech_queue.add(text, source or "agent")
        if item is None:
            self._error(409, "text is empty or already queued", "text")
            return
        print(f"agent message queued #{item.id} ({item.source}, {len(item.text)} chars)")
        self._reply(202, {"id": item.id})

    def _auto_read(self) -> bool:
        try:
            return bool(self.server.auto_read())
        except Exception as exc:
            print(f"could not read auto_read, assuming on: {exc}")
            return True


class SpeakServer:
    def __init__(self, server: _Server):
        self._server = server
        self.port = server.server_port
        self._thread = threading.Thread(target=server.serve_forever, daemon=True, name="ema-server")
        self._thread.start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


def start(speech_queue: SpeechQueue, port: int, auto_read: Callable[[], bool]) -> SpeakServer | None:
    """Starts the server in a daemon thread. If the port is busy, logs it and returns None."""
    try:
        server = _Server(port, speech_queue, auto_read)
    except OSError as exc:
        print(f"could not start server ({HOST}:{port}): {exc}")
        return None
    print(f"server listening: http://{HOST}:{server.server_port}")
    return SpeakServer(server)
