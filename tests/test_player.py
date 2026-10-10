import contextlib
import io
import itertools
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from ema_tts import player as pl
from ema_tts.speech_queue import DONE, SpeechQueue

RATE = 8000


class FakeStream:
    """Stands in for sd.OutputStream: plays nothing, keeps what was written."""

    opened: list["FakeStream"] = []
    on_write = None
    pace = 0.0  # wall seconds per second of audio; a real device blocks in write() for about 1.0

    def __init__(self, samplerate, channels, dtype, device=None):
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
        time.sleep(len(data) / self.samplerate * FakeStream.pace)
        if FakeStream.on_write:
            FakeStream.on_write(self)

    def abort(self):
        pass


class FakeTTS:
    """stream() only, like a TTS without word times."""

    def __init__(self, seconds=0.25):
        self.seconds = seconds
        self.calls: list[dict] = []

    def stream(self, text, speed=1.0, seed=None, sample_rate=48000):
        self.calls.append({"text": text, "speed": speed})
        yield np.full(int(sample_rate * self.seconds), 0.5, dtype=np.float32)


def wait_for(test, condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return
        time.sleep(0.01)
    test.fail("timed out")


class FakeAudioTest(unittest.TestCase):
    def setUp(self):
        FakeStream.opened, FakeStream.on_write, FakeStream.pace = [], None, 0.05
        fake_sd = SimpleNamespace(query_devices=lambda: [], default=SimpleNamespace(hostapi=0),
                                  OutputStream=FakeStream, PortAudioError=OSError)
        patcher = mock.patch.object(pl, "sd", fake_sd)
        patcher.start()
        self.addCleanup(patcher.stop)


def code(word: str) -> float:
    """The constant sample value a fake word is read with: k1 -> 0.12, k2 -> 0.14, ..."""
    return 0.1 + 0.02 * int(word.strip(".,;:!?")[1:])


def words_of(samples: np.ndarray) -> list[tuple[int, int]]:
    """(word number, samples) runs of the played audio; silence is 0, crossfades round to a neighbour."""
    runs = []
    for value, group in itertools.groupby(np.round((samples - 0.1) / 0.02).astype(int)):
        if value > 0:
            runs.append((int(value), len(list(group))))
    merged = []
    for word, length in runs:
        if merged and merged[-1][0] == word:
            merged[-1] = (word, merged[-1][1] + length)
        else:
            merged.append((word, length))
    return merged


class WordTTS:
    """Reads "k1 k2 ..." as one constant level per word: word_seconds / speed long, gap_seconds / speed of
    silence after each. say() gives the same audio plus the word times, like ema_lightning."""

    def __init__(self, word_seconds=0.2, gap_seconds=0.0, chunk_seconds=0.5, say_delay=0.0):
        self.word_seconds, self.gap_seconds, self.chunk_seconds = word_seconds, gap_seconds, chunk_seconds
        self.say_delay = say_delay
        self.streams: list[tuple[str, float]] = []
        self.says: list[tuple[str, float]] = []
        self.said = threading.Event()
        self.lock = threading.Lock()

    def _render(self, text, speed, sample_rate):
        parts, words, t = [], [], 0.0
        for word in text.split():
            n, gap = int(sample_rate * self.word_seconds / speed), int(sample_rate * self.gap_seconds / speed)
            parts += [np.full(n, code(word), np.float32), np.zeros(gap, np.float32)]
            words.append(SimpleNamespace(text=word, start=round(t, 3), end=round(t + n / sample_rate, 3)))
            t += (n + gap) / sample_rate
        return np.concatenate(parts), tuple(words)

    def stream(self, text, speed=1.0, seed=None, sample_rate=48000):
        with self.lock:
            self.streams.append((text, speed))
        audio, _ = self._render(text, speed, sample_rate)
        size = int(sample_rate * self.chunk_seconds)
        for start in range(0, len(audio), size):
            yield audio[start:start + size]

    def say(self, text, speed=1.0, seed=None, sample_rate=48000):
        with self.lock:
            self.says.append((text, speed))
        time.sleep(self.say_delay)
        audio, words = self._render(text, speed, 48000)
        threading.Timer(0.005, self.said.set).start()  # just after the take has stored the words
        return SimpleNamespace(audio=audio, words=words)

    def timed(self):
        """Waits until the first say() is done, so a speed change lands where the test expects it."""
        self.said.wait(2)
        time.sleep(0.005)


def text(n: int) -> str:
    return " ".join(f"k{i}" for i in range(1, n + 1))


class SpeedSettingTest(unittest.TestCase):
    def test_clamps_and_rounds_to_the_step(self):
        player = pl.Player(SpeechQueue(), {"speed": 1.0, "volume": 40})
        for value, expected in ((1.12, 1.1), (1.13, 1.15), (0.774, 0.75), (0.1, 0.5), (5, 2.0),
                                (float("inf"), 2.0), (float("-inf"), 0.5), (1.15, 1.15), ("1.3", 1.3)):
            with self.subTest(value=value):
                self.assertEqual(player.set_speed(value), expected)
                self.assertEqual(player.speed, expected)
                self.assertEqual(player.voice["speed"], expected)
        self.assertEqual(player.voice["volume"], 40)  # the other settings stay
        with self.assertRaises(ValueError):
            player.set_speed(float("nan"))
        self.assertEqual(player.speed, 1.3)

    def test_nudge(self):
        player = pl.Player(SpeechQueue(), {"speed": 1.0})
        self.assertEqual(player.nudge_speed(1), 1.05)
        self.assertEqual(player.nudge_speed(3), 1.2)
        self.assertEqual(player.nudge_speed(-6), 0.9)
        self.assertEqual(player.nudge_speed(-100), pl.SPEED_MIN)
        self.assertEqual(player.nudge_speed(100), pl.SPEED_MAX)
        player.update({"speed": 3.0})  # a hand-edited config may hold more than the slider allows
        self.assertEqual(player.nudge_speed(-1), pl.SPEED_MAX)

    def test_update_and_speed_do_not_lose_each_other(self):
        player = pl.Player(SpeechQueue(), {"speed": 1.0, "volume": 100})
        player.set_speed(1.5)
        player.update({"volume": 30})
        self.assertEqual((player.speed, player.voice["volume"]), (1.5, 30))

    def test_threads(self):
        player = pl.Player(SpeechQueue(), {"speed": 1.0})
        seen = []

        def nudger():
            for i in range(200):
                seen.append(player.nudge_speed(1 if i % 2 == 0 else -1))

        threads = [threading.Thread(target=nudger) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(player.speed, 1.0)
        self.assertTrue(all(pl.SPEED_MIN <= s <= pl.SPEED_MAX and round(s / pl.SPEED_STEP, 6).is_integer()
                            for s in seen))

    def test_crossfade(self):
        old, new = np.full(80, 0.5, np.float32), np.full(80, -0.5, np.float32)
        mixed = pl._crossfade(old, new)
        self.assertEqual((len(mixed), mixed.dtype), (80, np.float32))
        self.assertAlmostEqual(float(mixed[0]), 0.5)
        self.assertLess(float(np.max(np.abs(np.diff(mixed)))), 0.02)
        self.assertEqual(len(pl._crossfade(old[:10], new)), 80)  # a short old part is padded


class LiveSpeedTest(FakeAudioTest):
    def speak(self, tts, words=12, change_at=None, speed=2.0, pausable=True, alive=lambda: True, rate=RATE,
              timed=True):
        """Reads "k1 .. kN" and sets the speed after write number change_at; returns the written audio."""
        player = pl.Player(SpeechQueue(), {"speed": 1.0, "sample_rate": rate})
        player.tts = tts

        def on_write(stream):
            if len(stream.writes) == change_at:
                if timed:
                    tts.timed()
                player.set_speed(speed)

        FakeStream.on_write = on_write
        player._speak(text(words), alive, pausable)
        self.player = player
        return np.concatenate(FakeStream.opened[0].writes)

    def assert_in_order(self, runs, words=12):
        self.assertEqual([w for w, _ in runs], list(range(1, words + 1)))  # nothing skipped or repeated

    def test_no_change_reads_like_before(self):
        tts = WordTTS()
        audio = self.speak(tts)
        runs = words_of(audio)
        self.assert_in_order(runs)
        self.assertEqual({n for _, n in runs}, {1600})
        self.assertEqual(tts.streams, [(text(12) + ".", 1.0)])
        self.assertEqual({len(w) for w in FakeStream.opened[0].writes[:-1]}, {800})  # 0.1 s slices as before

    def test_change_continues_after_the_current_word_at_the_new_speed(self):
        tts = WordTTS(word_seconds=0.2)
        audio = self.speak(tts, change_at=3)  # 0.3 s: in k2, which ends 0.1 s later
        runs = words_of(audio)
        self.assert_in_order(runs)
        lengths = dict(runs)
        self.assertEqual((lengths[1], lengths[2]), (1600, 1600))
        self.assertTrue(all(lengths[w] == 800 for w in range(4, 13)), runs)  # 0.2 s / 2.0
        self.assertEqual(tts.streams[1], ("k3 k4 k5 k6 k7 k8 k9 k10 k11 k12.", 2.0))
        self.assertEqual(len(audio), 2 * 1600 + 10 * 800)

    def test_long_word_is_read_again_from_its_start(self):
        tts = WordTTS(word_seconds=1.0)
        audio = self.speak(tts, words=4, change_at=2)  # 0.2 s into a one-second word
        runs = words_of(audio)
        self.assert_in_order(runs, words=4)
        self.assertEqual(tts.streams[1][0], "k1 k2 k3 k4.")
        self.assertEqual([n for _, n in runs[1:]], [4000, 4000, 4000])
        self.assertLess(runs[0][1], 8000 + 4000)  # the restart cut the old k1 short

    def test_change_between_words(self):
        tts = WordTTS(word_seconds=0.2, gap_seconds=0.3)
        audio = self.speak(tts, words=6, change_at=3)  # 0.3 s: in the pause after k1
        runs = words_of(audio)
        self.assert_in_order(runs, words=6)
        self.assertEqual(tts.streams[1][0], "k2 k3 k4 k5 k6.")
        fade = int(RATE * pl.FADE_SECONDS)  # the fade from silence rounds partly into k1
        for (_, length), expected in zip(runs, [1600] + [800] * 5):
            self.assertLessEqual(abs(length - expected), fade, runs)

    def test_change_on_the_last_word_applies_to_the_next_segment(self):
        tts = WordTTS(word_seconds=0.2)
        player = pl.Player(SpeechQueue(), {"speed": 1.0, "sample_rate": RATE, "max_chars": 50})
        player.tts = tts
        first = " ".join(f"k{i}" for i in range(1, 9)) + "."
        second = " ".join(f"k{i}" for i in range(9, 17)) + "."
        # write 15 ends at 1.5 s, in k8 (1.4-1.6 s), the last word of the first segment
        FakeStream.on_write = lambda s: (tts.timed(), player.set_speed(1.5)) if len(s.writes) == 15 else None
        player._speak(first + " " + second, lambda: True)
        self.assertEqual(tts.streams, [(first, 1.0), (second, 1.5)])
        runs = words_of(np.concatenate(FakeStream.opened[0].writes))
        self.assert_in_order(runs, words=16)
        self.assertEqual(runs[-1][1], int(RATE * 0.2 / 1.5))

    def test_several_quick_changes_end_at_the_last_speed(self):
        tts = WordTTS(word_seconds=0.2)
        player = pl.Player(SpeechQueue(), {"speed": 1.0, "sample_rate": RATE})
        player.tts = tts

        def wheel(stream):
            if len(stream.writes) == 3:
                tts.timed()
                for _ in range(4):
                    player.nudge_speed(2)  # 1.1, 1.2, 1.3, 1.4 in one go

        FakeStream.on_write = wheel
        player._speak(text(12), lambda: True)
        runs = words_of(np.concatenate(FakeStream.opened[0].writes))
        self.assert_in_order(runs)
        self.assertEqual(tts.streams[-1][1], 1.4)
        self.assertEqual(runs[-1][1], int(RATE * 0.2 / 1.4))

    def test_change_waits_for_word_times(self):
        FakeStream.pace = 0.2
        tts = WordTTS(word_seconds=0.2, say_delay=0.05)
        audio = self.speak(tts, change_at=1, timed=False)
        runs = words_of(audio)
        self.assert_in_order(runs)
        self.assertEqual(runs[-1][1], 800)

    def test_without_word_times_the_speed_waits_for_the_next_segment(self):
        player = pl.Player(SpeechQueue(), {"speed": 1.0, "max_chars": 50})
        player.tts = FakeTTS(seconds=0.5)  # no say()
        FakeStream.on_write = lambda s: player.set_speed(1.5) if len(s.writes) == 1 else None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            player._speak("birinci cümle burada ve uzun sürüyor. ikinci cümle burada.", lambda: True)
        self.assertEqual([c["speed"] for c in player.tts.calls], [1.0, 1.5])
        self.assertIn("word times unavailable", out.getvalue())
        self.assertNotIn("cümle", out.getvalue())

    def test_volume_still_applies_after_a_change(self):
        tts = WordTTS(word_seconds=0.2)
        player = pl.Player(SpeechQueue(), {"speed": 1.0, "sample_rate": RATE, "volume": 100})
        player.tts = tts

        def on_write(stream):
            if len(stream.writes) == 3:
                tts.timed()
                player.set_speed(2.0)
            if len(stream.writes) == 10:
                player.update({"volume": 0})

        FakeStream.on_write = on_write
        player._speak(text(12), lambda: True)
        writes = FakeStream.opened[0].writes
        self.assertGreater(float(np.max(writes[5])), 0)
        self.assertEqual(float(np.max(np.abs(np.concatenate(writes[10:])))), 0.0)

    def test_pause_then_change_then_resume(self):
        tts = WordTTS(word_seconds=0.2)
        player = pl.Player(SpeechQueue(), {"speed": 1.0, "sample_rate": RATE})
        player.tts = tts
        paused = threading.Event()

        def on_write(stream):
            if len(stream.writes) == 3:
                player.toggle_pause()
                paused.set()

        FakeStream.on_write = on_write
        thread = threading.Thread(target=player._speak, args=(text(12), lambda: True))
        thread.start()
        paused.wait(2)
        wait_for(self, lambda: player.status == pl.PAUSED)
        tts.timed()
        player.set_speed(2.0)
        time.sleep(0.1)
        self.assertEqual(len(FakeStream.opened[0].writes), 3)  # nothing played while paused
        player.toggle_pause()
        thread.join(3)
        runs = words_of(np.concatenate(FakeStream.opened[0].writes))
        self.assert_in_order(runs)
        self.assertEqual(runs[-1][1], 800)

    def test_remove_during_a_pending_change_stops_at_once(self):
        tts = WordTTS(word_seconds=1.0)
        alive = threading.Event()
        alive.set()

        def on_write(stream):
            if len(stream.writes) == 2:
                tts.timed()
                self.player.set_speed(0.5)
            if len(stream.writes) == 3:
                alive.clear()

        player = pl.Player(SpeechQueue(), {"speed": 1.0, "sample_rate": RATE})
        player.tts = tts
        self.player = player
        FakeStream.on_write = on_write
        player._speak(text(12), alive.is_set)
        self.assertEqual(len(FakeStream.opened[0].writes), 3)

    def test_preview_follows_the_speed(self):
        tts = WordTTS(word_seconds=0.2)
        audio = self.speak(tts, change_at=3, pausable=False)
        self.assertEqual(words_of(audio)[-1][1], 800)

    def test_idle_change_applies_to_the_next_item(self):
        queue = SpeechQueue()
        tts = WordTTS()
        player = pl.Player(queue, {"speed": 1.0, "sample_rate": RATE})
        player._load = lambda: tts
        player.start()
        wait_for(self, lambda: player.status == pl.READY)
        self.assertEqual(player.set_speed(1.25), 1.25)
        queue.add("k1 k2")
        wait_for(self, lambda: queue.history())
        self.assertEqual(tts.streams, [("k1 k2.", 1.25)])
        self.assertEqual(queue.history()[0].state, DONE)

    def test_stream_error_during_a_change_fails_the_reading(self):
        class Broken(WordTTS):
            def stream(self, text, speed=1.0, seed=None, sample_rate=48000):
                if speed != 1.0:
                    raise RuntimeError("CUDA error: out of memory")
                yield from super().stream(text, speed, seed, sample_rate)

        with self.assertRaisesRegex(RuntimeError, "CUDA error"):
            self.speak(Broken(word_seconds=0.2), change_at=3)


@unittest.skipUnless(os.environ.get("EMA_TTS_SLOW_TESTS") == "1", "set EMA_TTS_SLOW_TESTS=1 to use the real model")
class RealModelSpeedTest(FakeAudioTest):
    """Loads EMA Lightning (a GPU makes it quick) and reads silently into a fake stream that keeps real time."""

    TEXT = ("Bugün hava çok güzel, yarın da yağmur yağacakmış; toplantı öğleden sonra saat 14:30'da başlayacak. "
            "Raporda 1.250 kayıt ve yüzde 15 artış var.")

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        with contextlib.redirect_stdout(io.StringIO()):
            cls.tts = pl.Player(SpeechQueue(), {"speed": 1.0})._load()

    def test_change_is_heard_soon_and_continues_from_the_word(self):
        rate = 48000
        FakeStream.pace = 1.0
        player = pl.Player(SpeechQueue(), {"speed": 1.0, "sample_rate": rate, "seed": 5})
        player.tts = self.tts
        plans, asked, switched, texts = [], {}, {}, []
        plan, crossfade, take = player._plan_change, pl._crossfade, pl._Take

        def spy(take, words, pos, speed, voice):
            change = plan(take, words, pos, speed, voice)
            plans.append((words, change))
            return change

        def mix(old, new):
            switched.setdefault("pos", sum(len(w) for w in FakeStream.opened[0].writes))
            switched.setdefault("time", time.perf_counter())
            return crossfade(old, new)

        class Recorded(take):
            def __init__(self, tts, text, *args):
                texts.append(text)
                super().__init__(tts, text, *args)

        def on_write(stream):
            written = sum(len(w) for w in stream.writes)
            if not asked and written >= 2.5 * rate:
                asked.update(pos=written, time=time.perf_counter())
                player.set_speed(1.5)

        player._plan_change = spy
        FakeStream.on_write = on_write
        with mock.patch.object(pl, "_Take", Recorded), mock.patch.object(pl, "_crossfade", mix):
            player._speak(self.TEXT, lambda: True)
        words, change = plans[0]
        self.assertLess((switched["pos"] - asked["pos"]) / rate, 0.3)  # old audio played after the change
        self.assertLess(switched["time"] - asked["time"], 0.5)  # including any wait for the new audio
        rest = texts[1].split()
        first = len(words) - len(rest)
        self.assertEqual([w.text for w in words[first:]], rest)  # the rest is the tail of the same words
        through = sum(1 for w in words if w.end * rate <= switched["pos"] + 1)  # words played to their end
        self.assertIn(first, (through - 1, through))  # the next word, or a long current one again
