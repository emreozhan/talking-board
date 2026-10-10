"""Speech queue shared by the hotkey, UI, server and player threads, plus the history of read items."""

import dataclasses
import itertools
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

WAITING = "waiting"
PLAYING = "playing"
DONE = "done"
SKIPPED = "skipped"

MAX_TEXT_CHARS = 20_000
MAX_ITEMS = 100
MAX_TOTAL_CHARS = 200_000
HISTORY_SIZE = 10


class Rejected(Exception):
    """The text was not queued because of a limit; the message says which."""


class TooLong(Rejected):
    pass


class QueueFull(Rejected):
    pass


@dataclass
class Item:
    id: int
    text: str
    state: str = WAITING
    added: float = field(default_factory=time.time)
    source: str = ""  # "" = clipboard; otherwise the agent name sent to /speak
    finished: float = 0.0  # when it left the queue for the history


def default_history_file() -> Path:
    appdata = os.environ.get("APPDATA")
    folder = Path(appdata) / "talking-board" if appdata else Path.home() / ".talking-board"
    return folder / "history.json"


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _history_entry(entry) -> dict | None:
    """The stored fields of one history entry, or None if they are not valid."""
    if not isinstance(entry, dict):
        return None
    text, source, state = entry.get("text"), entry.get("source", ""), entry.get("state")
    finished, added = entry.get("finished", 0.0), entry.get("added", 0.0)
    if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS
            or not isinstance(source, str) or state not in (DONE, SKIPPED)
            or not _number(finished) or not _number(added)):
        return None
    return {"text": text, "source": source, "state": state, "finished": float(finished), "added": float(added)}


class SpeechQueue:
    def __init__(self, history_file: Path | None = None):
        """history_file=None keeps the history in memory only (tests)."""
        self._items: list[Item] = []
        self._history: list[Item] = []  # newest first
        self._ids = itertools.count(1)
        self._cond = threading.Condition()
        self.version = 0  # the UI watches this counter for changes
        self._history_file = Path(history_file) if history_file is not None else None
        self._save_lock = threading.Lock()
        self._history_seq = 0  # bumped on every history change
        self._saved_seq = 0  # last change written to the file
        if self._history_file is not None:
            self._history = [Item(next(self._ids), **entry) for entry in self._load_history()]

    def _changed(self):
        self.version += 1
        self._cond.notify_all()

    # --- history file ---

    def _load_history(self) -> list[dict]:
        path = self._history_file
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(f"history file {path} not found; starting with an empty history")
            return []
        except (OSError, ValueError) as exc:
            print(f"history file {path} could not be read ({type(exc).__name__}); starting with an empty history")
            return []
        stored = data.get("items") if isinstance(data, dict) else None
        if not isinstance(stored, list):
            print(f"history file {path} is not a history list; starting with an empty history")
            return []
        entries = [entry for entry in map(_history_entry, stored) if entry is not None]
        if len(entries) < len(stored):
            print(f"history file {path}: {len(stored) - len(entries)} invalid entries ignored")
        return entries[:HISTORY_SIZE]

    def _history_state(self):
        """Call with the lock held after a history change: (change number, what to store)."""
        self._history_seq += 1
        return self._history_seq, [{"text": i.text, "source": i.source, "state": i.state,
                                    "finished": i.finished, "added": i.added} for i in self._history]

    def _save_history(self, state):
        """Writes the history atomically; called without the queue lock, errors are only logged."""
        if self._history_file is None:
            return
        seq, entries = state
        path = self._history_file
        with self._save_lock:
            if seq <= self._saved_seq:
                return  # a newer history is already on disk
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps({"items": entries}, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
                os.replace(temporary, path)
                self._saved_seq = seq
            except OSError as exc:
                print(f"history file {path} could not be written ({type(exc).__name__}: {exc.strerror or ''})")
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _to_history(self, item: Item, state: str):
        """Call with the lock held. The same text read again moves to the top instead of appearing twice."""
        item.state, item.finished = state, time.time()
        self._history = [item] + [old for old in self._history if old.text != item.text][:HISTORY_SIZE - 1]

    # --- queue ---

    def add(self, text: str, source: str = "") -> Item | None:
        """Appends the text. Returns None if it is empty or already queued; raises Rejected over a limit."""
        text = text.strip()
        if not text:
            return None
        if len(text) > MAX_TEXT_CHARS:
            raise TooLong(f"text is {len(text)} characters, the limit is {MAX_TEXT_CHARS}")
        with self._cond:
            if any(item.text == text for item in self._items):
                return None
            if len(self._items) >= MAX_ITEMS:
                raise QueueFull(f"the queue already holds {MAX_ITEMS} items")
            if sum(len(item.text) for item in self._items) + len(text) > MAX_TOTAL_CHARS:
                raise QueueFull(f"the queue would exceed {MAX_TOTAL_CHARS} characters")
            item = Item(next(self._ids), text, source=source)
            self._items.append(item)
            self._changed()
            return item

    def remove(self, item_id: int):
        """Drops the item; one being read goes to the history as skipped."""
        state = None
        with self._cond:
            for item in self._items:
                if item.id == item_id and item.state == PLAYING:
                    self._to_history(item, SKIPPED)
                    state = self._history_state()
            self._items = [item for item in self._items if item.id != item_id]
            self._changed()
        if state:
            self._save_history(state)

    def clear(self):
        state = None
        with self._cond:
            for item in self._items:
                if item.state == PLAYING:
                    self._to_history(item, SKIPPED)
                    state = self._history_state()
            self._items.clear()
            self._changed()
        if state:
            self._save_history(state)

    def contains(self, item_id: int) -> bool:
        with self._cond:
            return any(item.id == item_id for item in self._items)

    def snapshot(self) -> list[Item]:
        with self._cond:
            return [dataclasses.replace(i) for i in self._items]

    def take(self, timeout: float | None = None) -> Item | None:
        """Marks the next item as playing and returns it; waits while the queue is empty."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._items, timeout):
                return None
            item = self._items[0]
            item.state = PLAYING
            self._changed()
            return item

    def finish(self, item_id: int):
        """The item was read to the end: it moves to the history as done."""
        state = None
        with self._cond:
            for item in self._items:
                if item.id == item_id:
                    self._items.remove(item)
                    self._to_history(item, DONE)
                    state = self._history_state()
                    self._changed()
                    break
        if state:
            self._save_history(state)

    # --- history ---

    def history(self) -> list[Item]:
        """Copies of the last read items, newest first."""
        with self._cond:
            return [dataclasses.replace(i) for i in self._history]

    def remove_history(self, item_id: int):
        with self._cond:
            if not any(item.id == item_id for item in self._history):
                return
            self._history = [item for item in self._history if item.id != item_id]
            state = self._history_state()
            self._changed()
        self._save_history(state)

    def clear_history(self):
        with self._cond:
            self._history = []
            state = self._history_state()
            self._changed()
        self._save_history(state)

    def replay(self, item_id: int) -> Item | None:
        """Queues a history item's text again; same results and exceptions as add(). None if the id is unknown."""
        with self._cond:
            old = next((item for item in self._history if item.id == item_id), None)
            if old is None:
                return None
            return self.add(old.text, old.source)
