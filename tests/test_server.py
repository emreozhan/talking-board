import http.client
import json
import socket
import unittest

from ema_tts import server as speak_server
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
