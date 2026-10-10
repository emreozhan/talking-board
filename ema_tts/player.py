"""Background thread that synthesizes queued text with EMA Lightning and streams it to the speakers."""

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


def find_output_device(name: str | None) -> int | None:
    """Maps a device name to its index; None (system default) if the name is empty or missing."""
    if not name:
        return None
    try:
        return next(i for i, device_name, _ in _outputs() if device_name == name)
    except StopIteration:
        print(f"output device not found: {name!r}, using the system default")
    except Exception:
        traceback.print_exc()
    return None


class Player(threading.Thread):
    def __init__(self, queue: SpeechQueue, voice: dict | None = None):
        super().__init__(daemon=True, name="ema-player")
        self.queue = queue
        self.voice = {**cfg.DEFAULTS["voice"], **(voice or {})}  # only ever replaced as a whole
        self.lightning = bool(self.voice["use_lightning"])  # value at load time; changing it needs a restart
        self.status = LOADING
        self.error = ""
        self._resume = threading.Event()
        self._resume.set()
        self._lock = threading.Lock()
        self._preview = None
        self._preview_id = 0

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    def toggle_pause(self):
        if self.paused:
            self._resume.set()
        else:
            self._resume.clear()

    def update(self, voice: dict):
        """New voice settings apply from the next item; volume applies immediately."""
        self.voice = {**self.voice, **voice}

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
            try:
                self._play(item.text, lambda: self.queue.contains(item.id))
            finally:
                self.queue.finish(item.id)

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
        return tts

    def _play(self, text: str, alive, pausable: bool = True):
        self.status = SPEAKING
        try:
            self._speak(text, alive, pausable)
        except Exception:
            traceback.print_exc()
        finally:
            self.status = READY

    def _open_stream(self, sample_rate: int, device_name: str | None):
        device = find_output_device(device_name)
        try:
            return sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32", device=device)
        except sd.PortAudioError:
            if device is None:
                raise
            print(f"could not open {device_name!r}, using the system default")
            return sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32")

    def _speak(self, text: str, alive, pausable: bool = True):
        voice = self.voice  # fixed for the item; volume is re-read on every slice
        rate = voice["sample_rate"]
        step = int(rate * SLICE_SECONDS)
        with self._open_stream(rate, voice["output_device"]) as out:
            for segment in textprep.prepare(text, voice["max_chars"]):
                for chunk in self.tts.stream(segment, speed=voice["speed"], seed=voice["seed"], sample_rate=rate):
                    chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
                    for start in range(0, len(chunk), step):
                        if pausable and not self._resume.is_set():
                            self.status = PAUSED
                            self._resume.wait()
                            self.status = SPEAKING
                        if not alive():
                            out.abort()
                            return
                        out.write(apply_volume(chunk[start:start + step], self.voice["volume"]))
