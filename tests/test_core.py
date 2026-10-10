import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from ema_tts import config as cfg
from ema_tts import speech_queue as sq
from ema_tts import textprep
from ema_tts.speech_queue import PLAYING, SpeechQueue


# Sample texts are Turkish: the TTS model reads only Turkish, and textprep inserts Turkish words.
class CleanTest(unittest.TestCase):
    def test_strips_markdown_and_skips_code(self):
        text = (
            "## Özet\n"
            "- **Kalın** madde\n"
            "- `config.json` dosyasına bak\n"
            "```python\nprint('x')\n```\n"
            "Detay için [belgeye](https://example.com) ve https://huggingface.co bak"
        )
        self.assertEqual(
            textprep.clean(text),
            "Özet. Kalın madde. config.json dosyasına bak. Kod bloğu. "
            "Detay için belgeye ve bağlantı bak.",
        )

    def test_table_rows_become_lists(self):
        text = "| Ad | Değer |\n|---|---|\n| hız | 1.0 |"
        self.assertEqual(textprep.clean(text), "Ad, Değer. hız, 1.0.")

    def test_keeps_snake_case(self):
        self.assertEqual(textprep.clean("use_lightning ayarı"), "use_lightning ayarı.")


class SplitTest(unittest.TestCase):
    def test_packs_sentences_under_limit(self):
        text = "Bir. İki cümle. Üç cümle burada."
        self.assertEqual(textprep.split(text, max_chars=16), ["Bir. İki cümle.", "Üç cümle burada."])

    def test_breaks_overlong_sentence(self):
        sentence = " ".join(["kelime"] * 40) + "."
        segments = textprep.split(sentence, max_chars=50)
        self.assertTrue(all(len(s) <= 50 for s in segments))
        self.assertEqual(" ".join(segments), sentence)

    def test_text_without_spaces_stays_under_limit(self):
        for text in ("a" * 51, "a" * 120, "https://" + "x" * 300):
            with self.subTest(length=len(text)):
                segments = textprep.split(text, max_chars=50)
                self.assertEqual(max(len(s) for s in segments), 50)
                self.assertEqual("".join(segments), text)


class QueueTest(unittest.TestCase):
    def test_add_rejects_empty_and_duplicates(self):
        q = SpeechQueue()
        self.assertIsNotNone(q.add("hello"))
        self.assertIsNone(q.add("  hello "))
        self.assertIsNone(q.add("   "))
        self.assertEqual(len(q.snapshot()), 1)

    def test_take_marks_playing_and_remove_cancels(self):
        q = SpeechQueue()
        first = q.add("one")
        q.add("two")
        taken = q.take(timeout=0)
        self.assertEqual(taken.id, first.id)
        self.assertEqual(q.snapshot()[0].state, PLAYING)
        q.remove(first.id)
        self.assertFalse(q.contains(first.id))
        self.assertEqual([i.text for i in q.snapshot()], ["two"])

    def test_source_defaults_to_clipboard_and_survives_snapshot(self):
        q = SpeechQueue()
        q.add("from the clipboard")
        q.add("from an agent", source="claude")
        self.assertEqual([(i.text, i.source) for i in q.snapshot()],
                         [("from the clipboard", ""), ("from an agent", "claude")])
        q.take(timeout=0)
        self.assertEqual(q.snapshot()[0].source, "")

    def test_take_waits_for_add(self):
        q = SpeechQueue()
        threading.Timer(0.05, q.add, args=("late",)).start()
        self.assertEqual(q.take(timeout=2).text, "late")
        self.assertIsNone(SpeechQueue().take(timeout=0.01))

    def test_text_length_limit(self):
        q = SpeechQueue()
        self.assertIsNotNone(q.add("a" * sq.MAX_TEXT_CHARS))
        with self.assertRaisesRegex(sq.TooLong, "20001 characters"):
            q.add("b" * (sq.MAX_TEXT_CHARS + 1))
        self.assertEqual(len(q.snapshot()), 1)

    def test_item_count_limit(self):
        q = SpeechQueue()
        for i in range(sq.MAX_ITEMS):
            q.add(f"metin {i}")
        with self.assertRaisesRegex(sq.QueueFull, "100 items"):
            q.add("one too many")
        self.assertIsNone(q.add("metin 0"))  # duplicates are still reported as such
        q.remove(q.snapshot()[0].id)
        self.assertIsNotNone(q.add("fits again"))

    def test_total_length_limit(self):
        q = SpeechQueue()
        for letter in "abcdefghij":
            q.add(letter * sq.MAX_TEXT_CHARS)  # 10 x 20 000 = the 200 000 limit
        with self.assertRaisesRegex(sq.QueueFull, "200000 characters"):
            q.add("k")
        self.assertEqual(len(q.snapshot()), 10)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "config.json"

    def write(self, data):
        self.path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    def load(self):
        warnings = []
        return cfg.load(self.path, warnings), warnings

    def closed(self):
        """The defaults, with agent reading off."""
        config = json.loads(json.dumps(cfg.DEFAULTS))
        config["server"]["auto_read"] = False
        return config

    def test_broken_or_odd_files_fall_back_to_defaults(self):
        for text in ("{broken", "[1, 2]", "null", ""):
            with self.subTest(text=text):
                self.write(text)
                config, warnings = self.load()
                self.assertEqual(config, self.closed())
                self.assertEqual(len(warnings), 1)
                self.assertTrue(warnings[0].startswith(
                    "config.json could not be read, using the defaults with agent reading off: "))

    def test_section_of_wrong_type_falls_back(self):
        self.write({"voice": None, "server": [1], "hotkeys": {"skip": "<f9>"}, "extra": [1]})
        config, warnings = self.load()
        self.assertEqual(config["voice"], cfg.DEFAULTS["voice"])
        self.assertEqual(config["server"], self.closed()["server"])
        self.assertEqual(config["hotkeys"]["skip"], "<f9>")
        self.assertEqual(config["extra"], [1])
        self.assertEqual(warnings, ["config.json: voice must be a JSON object; using the defaults for it",
                                    "config.json: server must be a JSON object; using the defaults for it "
                                    "with agent reading off"])

    def test_invalid_values_fall_back_one_by_one(self):
        bad = {
            "voice": {"speed": 10, "volume": -1, "seed": -5, "output_device": 3, "sample_rate": 44100,
                      "max_chars": -1, "use_lightning": 1},
            "hotkeys": {"skip": None},
            "server": {"enabled": "yes", "port": 80, "auto_read": 1},
        }
        self.write(bad)
        config, warnings = self.load()
        self.assertEqual((config["voice"], config["hotkeys"]), (cfg.DEFAULTS["voice"], cfg.DEFAULTS["hotkeys"]))
        self.assertEqual(config["server"], {"enabled": False, "port": 47821, "auto_read": False})  # fail closed
        self.assertEqual(len(warnings), 11)
        self.assertIn("config.json: voice.speed must be a number between 0.25 and 4.0; using 1.0", warnings)
        self.assertIn("config.json: voice.max_chars must be a whole number between 50 and 1000; using 300", warnings)
        self.assertIn("config.json: server.port must be a whole number between 1024 and 65535; using 47821",
                      warnings)
        self.assertIn("config.json: server.auto_read must be true or false; using false", warnings)

    def test_booleans_are_not_numbers(self):
        self.write({"voice": {"seed": True, "volume": True, "max_chars": 300.0}})
        config, warnings = self.load()
        self.assertEqual((config["voice"]["seed"], config["voice"]["volume"], config["voice"]["max_chars"]),
                         (None, 100, 300))
        self.assertEqual(len(warnings), 3)

    def test_edge_values_are_kept(self):
        good = {"voice": {"speed": 0.25, "volume": 0, "seed": cfg.MAX_SEED, "output_device": "Kulaklık",
                          "sample_rate": 8000, "max_chars": 1000, "use_lightning": True},
                "server": {"port": 65535, "enabled": False, "auto_read": False},
                "hotkeys": {"skip": ""}}
        self.write(good)
        config, warnings = self.load()
        self.assertEqual(warnings, [])
        for section, values in good.items():
            for key, value in values.items():
                self.assertEqual(config[section][key], value)
        self.write({"voice": {"speed": 4, "seed": 0}, "server": {"port": 1024}})
        self.assertEqual(self.load()[1], [])

    def test_unknown_keys_are_kept(self):
        self.write({"voice": {"future_option": 1}})
        config, warnings = self.load()
        self.assertEqual((config["voice"]["future_option"], warnings), (1, []))

    def test_save_is_atomic(self):
        self.write({"voice": {"speed": 1.5}})
        with mock.patch.object(cfg.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                cfg.save({"voice": {"speed": 2.0}}, self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"voice": {"speed": 1.5}})
        self.assertEqual(os.listdir(self.dir), ["config.json"])  # no temporary file left behind
        cfg.save({"voice": {"speed": 2.0}}, self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"voice": {"speed": 2.0}})
        self.assertEqual(os.listdir(self.dir), ["config.json"])

    def test_readers_never_see_a_partial_file(self):
        stop, partial = threading.Event(), []

        def reader():
            while not stop.is_set():
                try:
                    json.loads(self.path.read_text(encoding="utf-8"))
                except ValueError:
                    partial.append(True)
                except OSError:
                    pass  # Windows: the file is being replaced at this instant
                time.sleep(0.001)  # like the hook and the server: short reads, not a busy loop

        cfg.save({"voice": {"speed": 1.5}}, self.path)
        thread = threading.Thread(target=reader)
        thread.start()
        try:
            for i in range(100):
                cfg.save({"voice": {"output_device": "x" * (i * 50)}}, self.path)
        finally:
            stop.set()
            thread.join()
        self.assertEqual(partial, [])

    def test_raw_value_for_fail_closed_checks(self):
        self.assertIs(cfg.raw_value("server", "auto_read", self.path), True)  # no file: default
        self.write({"server": {"auto_read": False}})
        self.assertIs(cfg.raw_value("server", "auto_read", self.path), False)
        self.write({"server": {"auto_read": "yes"}})
        self.assertEqual(cfg.raw_value("server", "auto_read", self.path), "yes")  # not replaced by the default
        for text in ("{broken", '{"server": []}'):
            self.write(text)
            with self.assertRaises(ValueError):
                cfg.raw_value("server", "auto_read", self.path)

    def test_save_writes_only_changes_and_load_restores_defaults(self):
        config = cfg
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            data = config.load(path)
            data["voice"]["speed"] = 1.5
            data["extra"] = {"a": 1}
            config.save(data, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"voice": {"speed": 1.5}, "extra": {"a": 1}})
            loaded = config.load(path)
            self.assertEqual(loaded["voice"]["speed"], 1.5)
            self.assertEqual(loaded["hotkeys"], config.DEFAULTS["hotkeys"])


if __name__ == "__main__":
    unittest.main()
