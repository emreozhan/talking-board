import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from ema_tts import config as cfg
from ema_tts import player as pl
from ema_tts import settings_window as sw
from ema_tts.speech_queue import SpeechQueue

DEVICES = [
    {"name": "Mikrofon", "hostapi": 0, "max_output_channels": 0},
    {"name": "Hoparlör", "hostapi": 0, "max_output_channels": 2},
    {"name": "Kulaklık", "hostapi": 1, "max_output_channels": 2},
    {"name": "Hoparlör", "hostapi": 1, "max_output_channels": 2},
]


class PortAudioError(Exception):
    pass


class FakeStream:
    """sd.OutputStream yerine: hoparlöre gitmez, yazılanları saklar."""

    opened: list["FakeStream"] = []
    on_write = None
    fail_devices = ()

    def __init__(self, samplerate, channels, dtype, device=None):
        if device is not None and device in FakeStream.fail_devices:
            raise PortAudioError("açılamadı")
        self.samplerate, self.device = samplerate, device
        self.writes: list[np.ndarray] = []
        self.closed = False
        FakeStream.opened.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def write(self, data):
        self.writes.append(np.array(data, copy=True))
        if FakeStream.on_write:
            FakeStream.on_write(self)

    def abort(self):
        pass


class FakeTTS:
    def __init__(self, seconds=0.25):
        self.seconds = seconds
        self.calls: list[dict] = []

    def stream(self, text, speed=1.0, seed=None, sample_rate=48000):
        self.calls.append({"text": text, "speed": speed, "seed": seed, "sample_rate": sample_rate})
        yield np.full(int(sample_rate * self.seconds), 0.5, dtype=np.float32)


class FakeAudioTest(unittest.TestCase):
    def setUp(self):
        FakeStream.opened, FakeStream.on_write, FakeStream.fail_devices = [], None, ()
        fake_sd = SimpleNamespace(query_devices=lambda: DEVICES, default=SimpleNamespace(hostapi=0),
                                  OutputStream=FakeStream, PortAudioError=PortAudioError)
        patcher = mock.patch.object(pl, "sd", fake_sd)
        patcher.start()
        self.addCleanup(patcher.stop)


class SaveVoiceTest(unittest.TestCase):
    def test_only_voice_section_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            user = {
                "voice": {"speed": 1.2, "max_chars": 200},
                "hotkeys": {"add_clipboard": "<f9>"},
                "server": {"enabled": True, "port": 5000},
                "extra": [1, 2],
            }
            path.write_text(json.dumps(user), encoding="utf-8")
            sw.save_voice({"speed": 1.5, "volume": 40, "seed": 16, "output_device": "Hoparlör"}, path)
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(saved["hotkeys"]["add_clipboard"], "<f9>")
        self.assertEqual(saved["server"], {"port": 5000})  # varsayılana eşit değerler yazılmaz
        self.assertEqual(saved["extra"], [1, 2])
        voice = saved["voice"]
        self.assertEqual((voice["speed"], voice["volume"], voice["seed"], voice["output_device"]),
                         (1.5, 40, 16, "Hoparlör"))
        self.assertEqual(voice["max_chars"], 200)
        self.assertEqual(set(voice), {"speed", "volume", "seed", "output_device", "max_chars"})

    def test_creates_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            sw.save_voice({"volume": 70}, path)
            self.assertEqual(cfg.load(path)["voice"]["volume"], 70)


class FormParseTest(unittest.TestCase):
    def test_seed_choices(self):
        self.assertIsNone(sw.parse_seed(sw.RANDOM, "123"))
        self.assertEqual(sw.parse_seed("Pes", ""), sw.SEED_PRESETS["Pes"])
        self.assertEqual(sw.parse_seed(sw.CUSTOM, " 42 "), 42)
        for bad in ("", "abc", "-1", str(sw.MAX_SEED + 1)):
            with self.assertRaisesRegex(ValueError, "^voice.seed:"):
                sw.parse_seed(sw.CUSTOM, bad)
        self.assertEqual(sw.seed_choice(None), (sw.RANDOM, ""))
        self.assertEqual(sw.seed_choice(23), ("Tiz", "23"))
        self.assertEqual(sw.seed_choice(99), (sw.CUSTOM, "99"))

    def test_max_chars(self):
        self.assertEqual(sw.parse_max_chars("300"), 300)
        for bad in ("", "49", "1001", "3.5"):
            with self.assertRaisesRegex(ValueError, "^voice.max_chars:"):
                sw.parse_max_chars(bad)


class VolumeTest(unittest.TestCase):
    def test_gain_and_clip(self):
        chunk = np.array([0.5, -0.5, 1.5, -2.0], dtype=np.float32)
        np.testing.assert_allclose(pl.apply_volume(chunk, 100), [0.5, -0.5, 1.0, -1.0])
        np.testing.assert_allclose(pl.apply_volume(chunk, 50), [0.25, -0.25, 0.75, -1.0])
        np.testing.assert_allclose(pl.apply_volume(chunk, 0), [0, 0, 0, 0])
        self.assertEqual(pl.apply_volume(chunk, 80).dtype, np.float32)


class OutputDeviceTest(FakeAudioTest):
    def test_lookup_and_fallback(self):
        self.assertIsNone(pl.find_output_device(None))
        self.assertEqual(pl.find_output_device("Hoparlör"), 1)  # varsayılan sürücüdeki önce
        self.assertEqual(pl.find_output_device("Kulaklık"), 2)
        with mock.patch("builtins.print"):
            self.assertIsNone(pl.find_output_device("Çıkarılmış cihaz"))
        self.assertEqual(pl.output_devices(), ["Hoparlör"])

    def test_unknown_device_plays_on_default(self):
        player = pl.Player(SpeechQueue(), {"output_device": "Çıkarılmış cihaz"})
        player.tts = FakeTTS()
        with mock.patch("builtins.print"):
            player._speak("merhaba", lambda: True)
        self.assertIsNone(FakeStream.opened[0].device)
        self.assertTrue(FakeStream.opened[0].writes)

    def test_device_that_fails_to_open_falls_back(self):
        FakeStream.fail_devices = (1,)
        player = pl.Player(SpeechQueue(), {"output_device": "Hoparlör"})
        player.tts = FakeTTS()
        with mock.patch("builtins.print"):
            player._speak("merhaba", lambda: True)
        self.assertEqual(len(FakeStream.opened), 1)
        self.assertIsNone(FakeStream.opened[0].device)


class LiveSettingsTest(FakeAudioTest):
    def wait_for(self, condition, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return
            time.sleep(0.01)
        self.fail("zaman aşımı")

    def test_next_item_uses_updated_settings(self):
        queue = SpeechQueue()
        tts = FakeTTS()
        player = pl.Player(queue, {"speed": 1.0, "sample_rate": 48000})
        player._load = lambda: tts
        player.start()

        queue.add("birinci")
        self.wait_for(lambda: len(FakeStream.opened) == 1 and FakeStream.opened[0].closed and not queue.snapshot())
        player.update({"speed": 1.5, "seed": 16, "sample_rate": 24000, "volume": 50, "output_device": "Kulaklık"})
        queue.add("ikinci")
        self.wait_for(lambda: len(FakeStream.opened) == 2 and FakeStream.opened[1].closed and not queue.snapshot())

        self.assertEqual(tts.calls[0], {"text": "birinci.", "speed": 1.0, "seed": None, "sample_rate": 48000})
        self.assertEqual(tts.calls[1], {"text": "ikinci.", "speed": 1.5, "seed": 16, "sample_rate": 24000})
        first, second = FakeStream.opened
        self.assertEqual((first.samplerate, first.device), (48000, None))
        self.assertEqual((second.samplerate, second.device), (24000, 2))
        self.assertAlmostEqual(float(np.max(np.concatenate(first.writes))), 0.5)
        self.assertAlmostEqual(float(np.max(np.concatenate(second.writes))), 0.25)

    def test_volume_changes_while_speaking(self):
        player = pl.Player(SpeechQueue(), {"volume": 100})
        player.tts = FakeTTS(seconds=0.25)  # 0.1 sn'lik dilimler: 3 yazım
        FakeStream.on_write = lambda stream: player.update({"volume": 50})
        player._speak("merhaba", lambda: True)
        peaks = [float(np.max(w)) for w in FakeStream.opened[0].writes]
        self.assertEqual(len(peaks), 3)
        self.assertAlmostEqual(peaks[0], 0.5)
        self.assertAlmostEqual(peaks[1], 0.25)
        self.assertAlmostEqual(peaks[2], 0.25)

    def test_preview_bypasses_queue_and_pause(self):
        queue = SpeechQueue()
        tts = FakeTTS()
        player = pl.Player(queue)
        player._load = lambda: tts
        player.toggle_pause()
        player.start()
        player.preview("deneme")
        self.wait_for(lambda: FakeStream.opened and FakeStream.opened[0].closed)
        self.assertEqual(tts.calls[0]["text"], "deneme.")
        self.assertEqual(queue.snapshot(), [])

    def test_new_preview_cuts_running_one(self):
        player = pl.Player(SpeechQueue())
        player.tts = FakeTTS(seconds=1.0)
        player.preview("ilk")
        preview_id, _ = player._take_preview()
        FakeStream.on_write = lambda stream: player.preview("ikinci") if len(stream.writes) == 2 else None
        player._speak("ilk", lambda: player._preview_id == preview_id, pausable=False)
        self.assertEqual(len(FakeStream.opened[0].writes), 2)
        self.assertEqual(player._take_preview()[1], "ikinci")


if __name__ == "__main__":
    unittest.main()
