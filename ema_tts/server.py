"""Yerel HTTP uç noktası: agent'ların (ör. Claude Code hook'u) metni kuyruğa eklemesi için."""

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
    allow_reuse_address = False  # Windows'ta SO_REUSEADDR meşgul portu paylaştırır
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
        self.server_name, self.server_port = HOST, self.server_address[1]  # getfqdn yavaş olabilir


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
        """Tarayıcıdan (Origin) ya da başka bir ad üzerinden (DNS rebinding) gelen istek mi?"""
        host = self.headers.get("Host", "")
        allowed = {f"{HOST}:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        return "Origin" in self.headers or host not in allowed

    def do_GET(self):
        if urlsplit(self.path).path == "/health":
            self._reply(200, {"ok": True})
        else:
            self._error(404, "bulunamadı")

    def do_POST(self):
        if urlsplit(self.path).path != "/speak":
            self._error(404, "bulunamadı")
            return
        if self._foreign():
            self._error(403, "yalnızca yerel istemciler kabul edilir")
            return
        if self.headers.get_content_type() != "application/json":
            self._error(415, "Content-Type application/json olmalı", "Content-Type")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 0:
            self._error(400, "Content-Length başlığı gerekli", "Content-Length")
            return
        if length > MAX_BODY:
            self.close_connection = True
            self._error(413, f"gövde en fazla {MAX_BODY} bayt olabilir", "Content-Length")
            self._discard(min(length, DISCARD_LIMIT))
            return
        self._speak(self.rfile.read(length))

    def _discard(self, length: int):
        """Okunmamış gövdeyle kapatılan soket Windows'ta RST yollar; istemci yanıtı kaybetmesin."""
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
            self._error(400, "gövde geçerli UTF-8 JSON olmalı", "body")
            return
        if not isinstance(payload, dict):
            self._error(400, "gövde bir JSON nesnesi olmalı", "body")
            return
        text, source = payload.get("text"), payload.get("source", "")
        if not isinstance(text, str):
            self._error(400, "text alanı metin olmalı", "text")
            return
        if not isinstance(source, str):
            self._error(400, "source alanı metin olmalı", "source")
            return
        if not self._auto_read():
            self._reply(202, {"ignored": True})
            return
        item = self.server.speech_queue.add(text)
        if item is None:
            self._error(409, "metin boş ya da zaten kuyrukta", "text")
            return
        print(f"agent mesajı kuyruğa eklendi #{item.id} ({source or 'bilinmiyor'}, {len(item.text)} karakter)")
        self._reply(202, {"id": item.id})

    def _auto_read(self) -> bool:
        try:
            return bool(self.server.auto_read())
        except Exception as exc:
            print(f"auto_read okunamadı, açık sayıldı: {exc}")
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
    """Sunucuyu daemon thread'de başlatır. Port meşgulse log'a yazar ve None döner."""
    try:
        server = _Server(port, speech_queue, auto_read)
    except OSError as exc:
        print(f"sunucu başlatılamadı ({HOST}:{port}): {exc}")
        return None
    print(f"sunucu dinliyor: http://{HOST}:{server.server_port}")
    return SpeakServer(server)
