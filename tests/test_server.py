import http.client
import json
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ema_tts import config as cfg
from ema_tts import server as speak_server
from ema_tts import speech_queue as sq
from ema_tts.speech_queue import SpeechQueue


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b"null"), dict(response.getheaders())
    finally:
        conn.close()


def speak(port, payload, content_type="application/json", **headers):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return request(port, "POST", "/speak", body, {"Content-Type": content_type, **headers})


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.queue = SpeechQueue()
        self.auto_read = True
        self.server = speak_server.start(self.queue, 0, lambda: self.auto_read)
        self.addCleanup(self.server.stop)
        self.port = self.server.port

    def texts(self):
        return [item.text for item in self.queue.snapshot()]

    def test_health(self):
        status, body, _ = request(self.port, "GET", "/health")
        self.assertEqual((status, body), (200, {"ok": True}))

    def test_speak_adds_to_queue(self):
        # Turkish text checks UTF-8 handling
        status, body, headers = speak(self.port, {"text": "Merhaba dünya", "source": "claude"})
        self.assertEqual(status, 202)
        self.assertEqual(body, {"id": self.queue.snapshot()[0].id})
        self.assertEqual(self.texts(), ["Merhaba dünya"])
        self.assertEqual(self.queue.snapshot()[0].source, "claude")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_source_is_optional_and_charset_allowed(self):
        status, _, _ = speak(self.port, {"text": "no source"}, "application/json; charset=utf-8")
        self.assertEqual(status, 202)
        self.assertEqual(self.queue.snapshot()[0].source, "agent")  # never mistaken for the clipboard

    def test_duplicate_and_empty_conflict(self):
        speak(self.port, {"text": "same"})
        self.assertEqual(speak(self.port, {"text": " same "})[0], 409)
        status, body, _ = speak(self.port, {"text": "   "})
        self.assertEqual(status, 409)
        self.assertEqual(body["field"], "text")
        self.assertEqual(self.texts(), ["same"])

    def test_bad_requests(self):
        cases = [b"{broken", b"[1, 2]", json.dumps({"text": 5}).encode(), json.dumps({}).encode(),
                 json.dumps({"text": "x", "source": 1}).encode(), "\xff".encode("latin-1")]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(speak(self.port, body)[0], 400)
        self.assertEqual(self.texts(), [])

    def test_wrong_content_type(self):
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            with self.subTest(content_type=content_type):
                status, body, _ = speak(self.port, {"text": "csrf"}, content_type)
                self.assertEqual(status, 415)
                self.assertEqual(body["field"], "Content-Type")
        self.assertEqual(self.texts(), [])

    def test_body_too_large(self):
        status, _, _ = speak(self.port, {"text": "a" * (speak_server.MAX_BODY + 1)})
        self.assertEqual(status, 413)
        self.assertEqual(self.texts(), [])

    def test_browser_and_rebinding_rejected(self):
        self.assertEqual(speak(self.port, {"text": "x"}, Origin="https://evil.example")[0], 403)
        self.assertEqual(speak(self.port, {"text": "x"}, Host=f"evil.example:{self.port}")[0], 403)
        self.assertEqual(self.texts(), [])

    def test_auto_read_off_ignores(self):
        self.auto_read = False
        status, body, _ = speak(self.port, {"text": "do not read"})
        self.assertEqual((status, body), (202, {"ignored": True}))
        self.assertEqual(self.texts(), [])

    def test_auto_read_fails_closed(self):
        for value in ("yes", 1, None, {}):
            with self.subTest(value=value):
                self.auto_read = value
                self.assertEqual(speak(self.port, {"text": f"not read {value}"})[1], {"ignored": True})
        self.assertEqual(self.texts(), [])

    def test_unreadable_setting_fails_closed(self):
        def broken():
            raise ValueError("config.json is half written")

        server = speak_server.start(self.queue, 0, broken)
        self.addCleanup(server.stop)
        with mock.patch("builtins.print") as printed:
            self.assertEqual(speak(server.port, {"text": "secret"})[:2], (202, {"ignored": True}))
        self.assertEqual(self.texts(), [])
        self.assertIn("ignoring agent messages", printed.call_args[0][0])

    def test_broken_config_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            server = speak_server.start(self.queue, 0, lambda: cfg.raw_value("server", "auto_read", path))
            self.addCleanup(server.stop)
            self.assertIn("id", speak(server.port, {"text": "no file: on"})[1])
            with mock.patch("builtins.print"):
                for text in ('{"server": {"auto_read": tr', '{"server": {"auto_read": "true"}}'):
                    path.write_text(text, encoding="utf-8")
                    self.assertEqual(speak(server.port, {"text": text})[1], {"ignored": True})
        self.assertEqual(self.texts(), ["no file: on"])

    def test_text_and_queue_limits(self):
        with mock.patch("builtins.print") as printed:
            status, body, _ = speak(self.port, {"text": "a" * (sq.MAX_TEXT_CHARS + 1), "source": "codex"})
            self.assertEqual((status, body["field"]), (413, "text"))
            self.assertIn("limit is 20000", body["error"])
            self.assertIn("agent message rejected (codex): text is 20001 characters", printed.call_args[0][0])
            for i in range(sq.MAX_ITEMS):
                self.queue.add(f"metin {i}")
            status, body, _ = speak(self.port, {"text": "one too many"})
            self.assertEqual((status, body["field"]), (429, "text"))
        self.assertEqual(len(self.texts()), sq.MAX_ITEMS)

    def test_source_is_cleaned(self):
        source = "evil\r\nqueued #99 (fake log line)\x1b[31m\u202e" + "x" * 100
        with mock.patch("builtins.print") as printed:
            self.assertEqual(speak(self.port, {"text": "metin", "source": source})[0], 202)
        stored = self.queue.snapshot()[0].source
        self.assertEqual(stored, ("evil queued #99 (fake log line) [31m " + "x" * 100)[:speak_server.MAX_SOURCE_CHARS])
        self.assertEqual(len(stored), speak_server.MAX_SOURCE_CHARS)
        logged = printed.call_args[0][0]
        self.assertIn(stored, logged)
        self.assertFalse(any(ch in logged for ch in "\r\n\x1b\u202e"))
        self.assertEqual(speak_server.clean_source("\t\x00\n"), "")
        speak(self.port, {"text": "control only", "source": "\n\x07"})
        self.assertEqual(self.queue.snapshot()[1].source, "agent")

    def test_silent_client_is_disconnected(self):
        with mock.patch.object(speak_server._Handler, "timeout", 0.3), socket.create_connection(
                ("127.0.0.1", self.port), timeout=5) as client:
            client.sendall(b"POST /speak HTTP/1.1\r\nHost: 127.0.0.1\r\n")  # and then nothing
            started = time.monotonic()
            self.assertEqual(client.recv(1024), b"")
            self.assertLess(time.monotonic() - started, 3)

    def test_connection_limit(self):
        with mock.patch.object(speak_server, "MAX_CONNECTIONS", 2), \
                mock.patch.object(speak_server._Handler, "timeout", 1.0), mock.patch("builtins.print") as printed:
            server = speak_server.start(self.queue, 0, lambda: True)
            self.addCleanup(server.stop)
            idle = [socket.create_connection(("127.0.0.1", server.port), timeout=5) for _ in range(2)]
            time.sleep(0.2)
            with socket.create_connection(("127.0.0.1", server.port), timeout=5) as third:
                try:
                    self.assertEqual(third.recv(1024), b"")  # closed straight away
                except ConnectionResetError:
                    pass
            self.assertIn("more than 2 open connections", printed.call_args[0][0])
            for connection in idle:
                connection.close()
            time.sleep(0.3)
            self.assertEqual(speak(server.port, {"text": "slots are free again"})[0], 202)

    def test_unknown_path(self):
        self.assertEqual(request(self.port, "GET", "/speak")[0], 404)
        self.assertEqual(request(self.port, "POST", "/x", b"{}", {"Content-Type": "application/json"})[0], 404)

    def test_busy_port_returns_none(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            self.assertIsNone(speak_server.start(SpeechQueue(), busy.getsockname()[1], lambda: True))
        self.assertIsNone(speak_server.start(SpeechQueue(), self.port, lambda: True))


if __name__ == "__main__":
    unittest.main()
