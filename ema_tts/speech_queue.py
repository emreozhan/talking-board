"""Seslendirme kuyruğu. Kısayol, arayüz ve oynatıcı thread'leri arasında paylaşılır."""

import itertools
import threading
import time
from dataclasses import dataclass, field

WAITING = "bekliyor"
PLAYING = "okunuyor"


@dataclass
class Item:
    id: int
    text: str
    state: str = WAITING
    added: float = field(default_factory=time.time)


class SpeechQueue:
    def __init__(self):
        self._items: list[Item] = []
        self._ids = itertools.count(1)
        self._cond = threading.Condition()
        self.version = 0  # arayüz değişikliği bu sayaçtan anlar

    def _changed(self):
        self.version += 1
        self._cond.notify_all()

    def add(self, text: str) -> Item | None:
        """Metni sona ekler. Boşsa ya da kuyrukta zaten varsa None döner."""
        text = text.strip()
        if not text:
            return None
        with self._cond:
            if any(item.text == text for item in self._items):
                return None
            item = Item(next(self._ids), text)
            self._items.append(item)
            self._changed()
            return item

    def remove(self, item_id: int):
        with self._cond:
            self._items = [item for item in self._items if item.id != item_id]
            self._changed()

    def clear(self):
        with self._cond:
            self._items.clear()
            self._changed()

    def contains(self, item_id: int) -> bool:
        with self._cond:
            return any(item.id == item_id for item in self._items)

    def snapshot(self) -> list[Item]:
        with self._cond:
            return [Item(i.id, i.text, i.state, i.added) for i in self._items]

    def take(self, timeout: float | None = None) -> Item | None:
        """Sıradaki öğeyi okunuyor olarak işaretleyip döner; kuyruk boşsa bekler."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._items, timeout):
                return None
            item = self._items[0]
            item.state = PLAYING
            self._changed()
            return item

    def finish(self, item_id: int):
        self.remove(item_id)
