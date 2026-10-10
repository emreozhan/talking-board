"""Background thread that synthesizes queued text with EMA Lightning and streams it to the speakers."""

import math
import threading
import traceback

import numpy as np
import sounddevice as sd

from . import config as cfg
from . import textprep
from .speech_queue import SpeechQueue

LOADING = "loading"
READY = "ready"
SPEAKING = "speaking"
PAUSED = "paused"
ERROR = "error"

SLICE_SECONDS = 0.1  # reaction time for remove/pause/volume
FINE_SLICE_SECONDS = 0.02  # while a speed change waits for its moment
FADE_SECONDS = 0.01  # crossfade where the reading switches to a new speed
WORD_WAIT_SECONDS = 0.25  # a speed change lets the current word end if it ends this soon, else reads it again

SPEED_MIN = 0.5
SPEED_MAX = 2.0
SPEED_STEP = 0.05


def apply_volume(chunk: np.ndarray, volume: float) -> np.ndarray:
    """0–100 % software gain; overflowing samples are clipped."""
    return np.clip(chunk * (volume / 100.0), -1.0, 1.0).astype(np.float32, copy=False)


def _outputs(all_hostapis: bool = True):
    """Output devices [(index, name, on default host API)]; default host API (MME on Windows) first."""
    devices = sd.query_devices()
    hostapi = sd.default.hostapi
    found = [(i, d["name"], d["hostapi"] == hostapi) for i, d in enumerate(devices)
             if d["max_output_channels"] > 0]
    found.sort(key=lambda entry: not entry[2])
    return found if all_hostapis else [entry for entry in found if entry[2]]


def output_devices() -> list[str]:
    """Unique output device names of the default host API, for the dropdown."""
    names = []
    for _, name, _ in _outputs(all_hostapis=False):
        if name not in names:
            names.append(name)
    return names


class DeviceError(Exception):
    """The chosen output device is missing or cannot be opened. Never replaced by the system default:
    text meant for headphones must not end up on the speakers."""


def find_output_device(name: str | None) -> int | None:
    """Maps a device name to its index; None (system default) only if no device is chosen."""
    if not name:
        return None
    try:
        return next(i for i, device_name, _ in _outputs() if device_name == name)
    except StopIteration:
        raise DeviceError(f"Output device '{name}' not found") from None
    except Exception as exc:
        raise DeviceError(f"Output devices could not be listed ({exc})") from exc


def _crossfade(old: np.ndarray, new: np.ndarray) -> np.ndarray:
    """Linear crossfade over len(new) samples; a short old part is padded with silence."""
    old = np.pad(old, (0, max(0, len(new) - len(old))))[:len(new)]
    ramp = np.linspace(0.0, 1.0, len(new), endpoint=False, dtype=np.float32)
    return (old * (1.0 - ramp) + new * ramp).astype(np.float32)


class _Take:
    """One text read at one speed, synthesized on its own thread.

    The audio comes from stream(), so the first sound is as fast as before. Once the first chunk is in, say()
    of the same text and speed runs alongside only for its word times: they come from the frame plan, which
    depends on the text and the speed but not on the seed, so they match the streamed audio.
    """

    def __init__(self, tts, text: str, speed: float, seed, rate: int):
        self.speed = speed
        self.audio = np.zeros(0, dtype=np.float32)
        self.ended = False
        self._error = None
        self._words = None  # ema_lightning Words once say() is done
        self._words_error = None
        self._closed = False
        self._cond = threading.Condition()
        threading.Thread(target=self._stream, args=(tts, text, seed, rate), daemon=True, name="ema-take").start()

    def _stream(self, tts, text, seed, rate):
        timing = False
        try:
            chunks = tts.stream(text, speed=self.speed, seed=seed, sample_rate=rate)
            try:
                for chunk in chunks:
                    chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
                    with self._cond:
                        if self._closed:
                            return
                        self.audio = np.concatenate((self.audio, chunk))
                        self._cond.notify_all()
                    if not timing:
                        timing = True
                        threading.Thread(target=self._time_words, args=(tts, text, seed), daemon=True,
                                         name="ema-words").start()
            finally:
                chunks.close()  # a dropped take stops the model's work on it
        except Exception as exc:
            with self._cond:
                self._error = exc
        finally:
            with self._cond:
                self.ended = True
                if not timing:
                    self._words_error = RuntimeError("no audio")
                self._cond.notify_all()

    def _time_words(self, tts, text, seed):
        try:
            words = tuple(tts.say(text, speed=self.speed, seed=seed).words)
        except Exception as exc:
            with self._cond:
                self._words_error = exc
            return
        with self._cond:
            self._words = words

    def ready(self, end: int) -> bool:
        with self._cond:
            return len(self.audio) >= end or self.ended

    def wait(self, end: int, alive) -> bool:
        """Waits until audio[:end] exists or the take ended; False if the reading was cancelled meanwhile."""
        with self._cond:
            while len(self.audio) < end and not self.ended:
                self._cond.wait(SLICE_SECONDS)
                if not alive():
                    return False
            if self._error is not None:
                raise self._error
            return True

    def words(self):
        """The word times; None while say() still runs, or the exception that stopped it."""
        with self._cond:
            return self._words if self._words is not None else self._words_error

    def close(self):
        with self._cond:
            self._closed = True


class _Change:
    """A pending switch to a new take: not before sample `earliest` of the old take, and at sample `latest`
    at the latest (waiting for the new take there if needed)."""

    def __init__(self, take: _Take, earliest: int, latest: int):
        self.take, self.earliest, self.latest = take, earliest, max(latest, earliest)


class Player(threading.Thread):
    def __init__(self, queue: SpeechQueue, voice: dict | None = None):
        super().__init__(daemon=True, name="ema-player")
        self.queue = queue
        self.voice = {**cfg.DEFAULTS["voice"], **(voice or {})}  # only ever replaced as a whole
        self.lightning = bool(self.voice["use_lightning"])  # value at load time; changing it needs a restart
        self.status = LOADING
        self.error = ""  # why the model failed to load
        self.speech_error = ""  # why the last reading failed; reading stays paused until Resume
        self._resume = threading.Event()
        self._resume.set()
        self._lock = threading.Lock()
        self._preview = None
        self._preview_id = 0

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    def toggle_pause(self):
        """Resume also clears a reading error, so the failed item is tried again."""
        with self._lock:
            if self.paused:
                if self.speech_error:
                    self.speech_error, self.status = "", READY
                self._resume.set()
            else:
                self._resume.clear()

    def _fail(self, message: str):
        with self._lock:
            self._resume.clear()
            self.speech_error, self.status = f"{message} — reading paused", ERROR
        print(self.speech_error)

    def update(self, voice: dict):
        """New voice settings apply from the next item; volume and speed apply immediately."""
        with self._lock:
            self.voice = {**self.voice, **voice}

    @property
    def speed(self) -> float:
        return self.voice["speed"]

    def set_speed(self, value: float) -> float:
        """Clamps and rounds the speed and applies it at once, also in the middle of a reading. Not saved."""
        with self._lock:
            return self._store_speed(value)

    def nudge_speed(self, steps: int) -> float:
        with self._lock:
            return self._store_speed(self.speed + steps * SPEED_STEP)

    def _store_speed(self, value: float) -> float:
        value = float(value)
        if math.isnan(value):
            raise ValueError("speed must be a number")
        value = min(max(value, SPEED_MIN), SPEED_MAX)
        value = round(round(value / SPEED_STEP) * SPEED_STEP, 2)
        self.voice = {**self.voice, "speed": value}
        return value

    def preview(self, text: str):
        """Reads text ahead of the next item without queueing it. A new preview cuts the previous one."""
        with self._lock:
            self._preview_id += 1
            self._preview = (self._preview_id, text)

    def _take_preview(self):
        with self._lock:
            job, self._preview = self._preview, None
            return job

    def run(self):
        try:
            self.tts = self._load()
        except Exception:
            self.status, self.error = ERROR, traceback.format_exc(limit=1)
            traceback.print_exc()
            return
        self.status = READY
        print("model ready")

        while True:
            job = self._take_preview()
            if job is not None:
                preview_id, text = job
                self._play(text, lambda: self._preview_id == preview_id, pausable=False)
                continue
            if not self._resume.wait(timeout=0.2):
                continue
            item = self.queue.take(timeout=0.2)
            if item is None:
                continue
            if self._play(item.text, lambda: self.queue.contains(item.id)):
                self.queue.finish(item.id)  # a failed item stays first in the queue for Resume

    def _load(self):
        from ema_lightning import EMA

        tts = EMA()
        if self.lightning:
            try:
                tts = tts.lightning()
            except Exception:
                print("lightning() unavailable, continuing in normal mode.")
                traceback.print_exc()
        # pay the first-call warm-up cost here; the text is Turkish because the model only reads Turkish
        for _ in tts.stream("Hazır.", sample_rate=self.voice["sample_rate"]):
            pass
        tts.say("Hazır.")  # speed changes take their word times from say()
        return tts

    def _play(self, text: str, alive, pausable: bool = True) -> bool:
        """Reads the text; on failure pauses reading and keeps the reason in speech_error."""
        self.status = SPEAKING
        try:
            self._speak(text, alive, pausable)
        except DeviceError as exc:
            self._fail(str(exc))
            return False
        except Exception as exc:
            traceback.print_exc()
            self._fail(f"Speech error: {(str(exc).strip().splitlines() or [type(exc).__name__])[0]}")
            return False
        self.status = READY
        return True

    def _open_stream(self, sample_rate: int, device_name: str | None):
        device = find_output_device(device_name)
        try:
            return sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32", device=device)
        except sd.PortAudioError as exc:
            if device is None:
                raise
            raise DeviceError(f"Output device '{device_name}' could not be opened ({exc})") from exc

    def _speak(self, text: str, alive, pausable: bool = True):
        voice = self.voice  # fixed for the item, except speed (live) and volume (re-read on every slice)
        with self._open_stream(voice["sample_rate"], voice["output_device"]) as out:
            for segment in textprep.prepare(text, voice["max_chars"]):
                if not self._speak_segment(out, segment, voice, alive, pausable):
                    out.abort()
                    return

    def _speak_segment(self, out, segment: str, voice: dict, alive, pausable: bool) -> bool:
        """Plays one segment; a speed change switches to the rest of it read at the new speed.
        False if the reading was cancelled."""
        rate = voice["sample_rate"]
        step, fine, fade = int(rate * SLICE_SECONDS), int(rate * FINE_SLICE_SECONDS), int(rate * FADE_SECONDS)
        take = _Take(self.tts, segment, self.speed, voice["seed"], rate)
        change, planned, pos = None, take.speed, 0  # planned: the speed the rest of the segment will have
        try:
            while True:
                if pausable and not self._resume.is_set():
                    self.status = PAUSED
                    while not self._resume.wait(SLICE_SECONDS) and alive():
                        pass  # removing the paused item ends it without waiting for Resume
                    self.status = SPEAKING
                if not alive():
                    return False
                speed = self.speed
                if speed != planned:
                    words = take.words()
                    if words is not None:  # else say() is still timing the words: try again on the next slice
                        if change:
                            change.take.close()
                        change, planned = self._plan_change(take, words, pos, speed, voice), speed
                if change and pos >= change.earliest and (pos >= change.latest or change.take.ready(fade)):
                    if not change.take.wait(fade, alive) or not take.wait(pos + fade, alive):
                        return False
                    head = change.take.audio[:fade]
                    out.write(apply_volume(_crossfade(take.audio[pos:pos + fade], head), self.voice["volume"]))
                    take.close()
                    take, change, pos = change.take, None, len(head)
                    continue
                if change:
                    end = min(pos + fine, change.earliest if pos < change.earliest else change.latest)
                else:
                    end = pos + (step if speed == planned else fine)
                if not take.wait(end, alive):
                    return False
                chunk = take.audio[pos:end]
                if not len(chunk):
                    if change is None:
                        return True
                    change.earliest = change.latest = pos  # the old take ran out early: switch now
                    continue
                out.write(apply_volume(chunk, self.voice["volume"]))
                pos += len(chunk)
        finally:
            take.close()
            if change:
                change.take.close()

    def _plan_change(self, take: _Take, words, pos: int, speed: float, voice: dict) -> _Change | None:
        """Where to leave the take for one that reads the rest at the new speed; None if no word is left."""
        if isinstance(words, Exception):
            print(f"word times unavailable ({type(words).__name__}); the new speed applies from the next segment")
            return None
        rate = voice["sample_rate"]
        now = pos / rate
        index = next((i for i, word in enumerate(words) if word.end > now), None)
        if index is None:
            return None
        word = words[index]
        if word.start > now:  # between words: switch once the new take is ready, before the next word starts
            first, earliest, latest = index, pos, round(word.start * rate)
        elif word.end - now <= WORD_WAIT_SECONDS:  # let the word end, go on with the next one
            first, earliest = index + 1, round(word.end * rate)
            latest = earliest
        else:  # long word: read it again at the new speed as soon as the new take is ready
            first, earliest, latest = index, pos, round(word.end * rate)
        if first >= len(words):
            return None
        rest = " ".join(w.text for w in words[first:])
        return _Change(_Take(self.tts, rest, speed, voice["seed"], rate), earliest, latest)
