"""Kuyruktaki metinleri EMA Lightning ile üretip hoparlöre akıtan arka plan thread'i."""

import threading
import traceback

import numpy as np
import sounddevice as sd

from . import textprep
from .speech_queue import SpeechQueue

LOADING = "yükleniyor"
READY = "hazır"
SPEAKING = "okunuyor"
PAUSED = "duraklatıldı"
ERROR = "hata"

SLICE_SECONDS = 0.1  # silme/duraklatma tepki süresi


class Player(threading.Thread):
    def __init__(self, queue: SpeechQueue, speed: float = 1.0, sample_rate: int = 48000,
                 max_chars: int = 300, use_lightning: bool = False):
        super().__init__(daemon=True, name="ema-player")
        self.queue = queue
        self.speed = speed
        self.sample_rate = sample_rate
        self.max_chars = max_chars
        self.use_lightning = use_lightning
        self.status = LOADING
        self.error = ""
        self._resume = threading.Event()
        self._resume.set()

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    def toggle_pause(self):
        if self.paused:
            self._resume.set()
        else:
            self._resume.clear()

    def run(self):
        try:
            self.tts = self._load()
        except Exception:
            self.status, self.error = ERROR, traceback.format_exc(limit=1)
            traceback.print_exc()
            return
        self.status = READY
        print("model hazır")

        while True:
            self._resume.wait()
            item = self.queue.take(timeout=0.5)
            if item is None:
                continue
            self.status = SPEAKING
            try:
                self._speak(item.id, item.text)
            except Exception:
                traceback.print_exc()
            finally:
                self.queue.finish(item.id)
                self.status = READY

    def _load(self):
        from ema_lightning import EMA

        tts = EMA()
        if self.use_lightning:
            try:
                tts = tts.lightning()
            except Exception:
                print("lightning() kullanılamadı, normal modda devam ediliyor.")
                traceback.print_exc()
        for _ in tts.stream("Hazır.", sample_rate=self.sample_rate):  # ilk çağrının ısınma gecikmesini burada öde
            pass
        return tts

    def _speak(self, item_id: int, text: str):
        step = int(self.sample_rate * SLICE_SECONDS)
        with sd.OutputStream(samplerate=self.sample_rate, channels=1, dtype="float32") as out:
            for segment in textprep.prepare(text, self.max_chars):
                for chunk in self.tts.stream(segment, speed=self.speed, sample_rate=self.sample_rate):
                    chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
                    for start in range(0, len(chunk), step):
                        if not self._resume.is_set():
                            self.status = PAUSED
                            self._resume.wait()
                            self.status = SPEAKING
                        if not self.queue.contains(item_id):
                            out.abort()
                            return
                        out.write(chunk[start:start + step])
