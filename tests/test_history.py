import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ema_tts import speech_queue as sq
from ema_tts.speech_queue import DONE, PLAYING, SKIPPED, WAITING, SpeechQueue


def read_one(q: SpeechQueue, text: str, source: str = ""):
    """Queues the text, takes it as the player does and finishes it."""
    item = q.add(text, source)
    taken = q.take(timeout=0)
    assert taken.id == item.id
    q.finish(item.id)
    return item


def quiet(func, *args):
    """Runs func with stdout captured; returns (result, printed text)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = func(*args)
    return result, out.getvalue()


class HistoryTest(unittest.TestCase):
    def test_finished_item_is_done_and_newest_first(self):
        q = SpeechQueue()
        read_one(q, "birinci")
        read_one(q, "ikinci", "claude")
        history = q.history()
        self.assertEqual([(i.text, i.state, i.source) for i in history],
                         [("ikinci", DONE, "claude"), ("birinci", DONE, "")])
        self.assertTrue(all(i.finished >= i.added > 0 for i in history))
        self.assertEqual(q.snapshot(), [])

    def test_removing_the_playing_item_is_skipped_but_waiting_items_are_not_kept(self):
        q = SpeechQueue()
        playing = q.add("okunuyor")
        waiting = q.add("bekliyor")
        q.take(timeout=0)
        q.remove(waiting.id)
        self.assertEqual(q.history(), [])
        q.remove(playing.id)
        self.assertEqual([(i.text, i.state) for i in q.history()], [("okunuyor", SKIPPED)])

    def test_clear_keeps_only_the_playing_item(self):
        q = SpeechQueue()
        q.add("okunuyor")
        q.add("bekliyor")
        q.clear()
        self.assertEqual(q.history(), [])  # nothing was being read
        q.add("okunuyor")
        q.add("bekliyor")
        q.take(timeout=0)
        q.clear()
        self.assertEqual([(i.text, i.state) for i in q.history()], [("okunuyor", SKIPPED)])
        self.assertEqual(q.snapshot(), [])

    def test_failed_reading_does_not_touch_history(self):
        q = SpeechQueue()
        item = q.add("hatalı")
        q.take(timeout=0)  # the player fails and does not call finish
        self.assertEqual(q.history(), [])
        self.assertEqual([(i.id, i.state) for i in q.snapshot()], [(item.id, PLAYING)])

    def test_finish_after_remove_is_ignored(self):
        q = SpeechQueue()
        item = q.add("metin")
        q.take(timeout=0)
        q.remove(item.id)
        q.finish(item.id)
        self.assertEqual([(i.text, i.state) for i in q.history()], [("metin", SKIPPED)])

    def test_size_limit(self):
        q = SpeechQueue()
        for i in range(sq.HISTORY_SIZE + 3):
            read_one(q, f"metin {i}")
        self.assertEqual([i.text for i in q.history()],
                         [f"metin {i}" for i in range(sq.HISTORY_SIZE + 2, 2, -1)])

    def test_same_text_read_again_moves_to_top(self):
        q = SpeechQueue()
        read_one(q, "a")
        read_one(q, "b")
        read_one(q, "a")
        self.assertEqual([i.text for i in q.history()], ["a", "b"])

    def test_history_does_not_count_towards_queue_limits(self):
        q = SpeechQueue()
        for letter in "abcdefghij":
            read_one(q, letter * sq.MAX_TEXT_CHARS)
        for letter in "abcdefghij":  # the queue is empty again, so the full total fits
            self.assertIsNotNone(q.add(letter * sq.MAX_TEXT_CHARS))
        self.assertEqual(len(q.history()), 10)

    def test_history_returns_copies(self):
        q = SpeechQueue()
        read_one(q, "metin")
        q.history()[0].text = "değişti"
        self.assertEqual(q.history()[0].text, "metin")

    def test_remove_and_clear_history(self):
        q = SpeechQueue()
        read_one(q, "a")
        read_one(q, "b")
        first = q.history()[1]
        before = q.version
        q.remove_history(first.id)
        self.assertEqual([i.text for i in q.history()], ["b"])
        self.assertGreater(q.version, before)
        before = q.version
        q.remove_history(12345)  # unknown id: no change
        self.assertEqual(q.version, before)
        q.clear_history()
        self.assertEqual(q.history(), [])
        self.assertGreater(q.version, before)

    def test_every_history_change_bumps_version(self):
        q = SpeechQueue()
        item = q.add("metin")
        q.take(timeout=0)
        before = q.version
        q.finish(item.id)
        self.assertGreater(q.version, before)

    def test_replay_queues_a_new_waiting_item_with_the_same_source(self):
        q = SpeechQueue()
        read_one(q, "tekrar", "codex")
        old = q.history()[0]
        new = q.replay(old.id)
        self.assertNotEqual(new.id, old.id)
        self.assertEqual([(i.id, i.text, i.source, i.state) for i in q.snapshot()],
                         [(new.id, "tekrar", "codex", WAITING)])
        self.assertEqual([i.id for i in q.history()], [old.id])  # the history entry stays
        self.assertIsNone(q.replay(old.id))  # already queued: a duplicate
        self.assertIsNone(q.replay(98765))  # unknown id

    def test_replay_follows_queue_limits(self):
        q = SpeechQueue()
        read_one(q, "geçmiş")
        old = q.history()[0]
        for i in range(sq.MAX_ITEMS):
            q.add(f"metin {i}")
        with self.assertRaises(sq.QueueFull):
            q.replay(old.id)
        q.clear()
        for letter in "abcdefghij":
            q.add(letter * sq.MAX_TEXT_CHARS)
        with self.assertRaisesRegex(sq.QueueFull, "200000 characters"):
            q.replay(old.id)

    def test_threads(self):
        q = SpeechQueue()

        def reader(n):
            for i in range(50):
                item = q.add(f"metin {n} {i}")
                if item:
                    q.take(timeout=1)
                    q.finish(item.id)
                q.history()

        threads = [threading.Thread(target=reader, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        history = q.history()
        self.assertEqual(len(history), sq.HISTORY_SIZE)
        self.assertEqual(len({i.id for i in history}), sq.HISTORY_SIZE)


class HistoryFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "talking-board" / "history.json"

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["items"]

    def new_queue(self):
        return quiet(SpeechQueue, self.path)

    def test_default_location(self):
        with mock.patch.dict(os.environ, {"APPDATA": str(self.dir)}):
            self.assertEqual(sq.default_history_file(), self.dir / "talking-board" / "history.json")
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(Path, "home", return_value=self.dir):
            self.assertEqual(sq.default_history_file(), self.dir / ".talking-board" / "history.json")

    def test_round_trip(self):
        q, log = self.new_queue()
        self.assertIn("not found", log)
        read_one(q, "birinci", "claude")
        item = q.add("ikinci")
        q.take(timeout=0)
        q.remove(item.id)
        self.assertEqual([(e["text"], e["state"], e["source"]) for e in self.stored()],
                         [("ikinci", SKIPPED, ""), ("birinci", DONE, "claude")])
        saved = q.history()

        loaded, log = self.new_queue()
        self.assertEqual(log, "")
        history = loaded.history()
        self.assertEqual([(i.text, i.state, i.source, i.finished, i.added) for i in history],
                         [(i.text, i.state, i.source, i.finished, i.added) for i in saved])
        new = loaded.add("yeni")  # loaded ids never clash with queue ids
        self.assertNotIn(new.id, {i.id for i in history})
        self.assertEqual(loaded.replay(history[1].id).source, "claude")

    def test_text_never_goes_to_the_log(self):
        q, _ = self.new_queue()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(sq.os, "replace", side_effect=PermissionError(13, "denied")):
            read_one(q, "gizli metin")
        self.assertIn("could not be written", out.getvalue())
        self.assertNotIn("gizli", out.getvalue())

    def test_size_limit_on_disk_and_on_load(self):
        q, _ = self.new_queue()
        for i in range(sq.HISTORY_SIZE + 2):
            read_one(q, f"metin {i}")
        self.assertEqual(len(self.stored()), sq.HISTORY_SIZE)
        entries = [{"text": f"metin {i}", "state": DONE, "source": "", "finished": 1.0, "added": 1.0}
                   for i in range(15)]
        self.path.write_text(json.dumps({"items": entries}), encoding="utf-8")
        loaded, _ = self.new_queue()
        self.assertEqual([i.text for i in loaded.history()], [f"metin {i}" for i in range(10)])

    def test_corrupt_or_odd_files_start_empty(self):
        self.path.parent.mkdir(parents=True)
        for content in ("{broken", "[1, 2]", "null", "", '{"items": 5}', b"\xff\xfe\x00"):
            with self.subTest(content=content):
                if isinstance(content, bytes):
                    self.path.write_bytes(content)
                else:
                    self.path.write_text(content, encoding="utf-8")
                q, log = self.new_queue()
                self.assertEqual(q.history(), [])
                self.assertEqual(len(log.strip().splitlines()), 1)
                self.assertIn("starting with an empty history", log)

    def test_invalid_entries_are_dropped(self):
        self.path.parent.mkdir(parents=True)
        good = {"text": "iyi", "state": SKIPPED, "source": "codex", "finished": 2.0, "added": 1.0}
        bad = [5, {"text": ""}, {"text": "x", "state": WAITING}, {"text": "x", "state": DONE, "finished": "dün"},
               {"text": "x" * (sq.MAX_TEXT_CHARS + 1), "state": DONE}, {"text": "x", "state": DONE, "source": 1}]
        self.path.write_text(json.dumps({"items": [bad[0], good, *bad[1:]]}), encoding="utf-8")
        q, log = self.new_queue()
        self.assertEqual([(i.text, i.state, i.source, i.finished) for i in q.history()],
                         [("iyi", SKIPPED, "codex", 2.0)])
        self.assertIn("6 invalid entries ignored", log)

    def test_unreadable_file_starts_empty(self):
        with mock.patch.object(Path, "read_text", side_effect=PermissionError(13, "denied")):
            q, log = self.new_queue()
        self.assertEqual(q.history(), [])
        self.assertIn("could not be read (PermissionError)", log)

    def test_write_error_is_logged_not_raised(self):
        q, _ = self.new_queue()
        read_one(q, "kalıcı")
        with mock.patch.object(sq.os, "replace", side_effect=OSError(28, "disk full")):
            _, log = quiet(read_one, q, "yazılamadı")
        self.assertIn("could not be written", log)
        self.assertEqual([i.text for i in q.history()], ["yazılamadı", "kalıcı"])  # memory is still right
        self.assertEqual([e["text"] for e in self.stored()], ["kalıcı"])
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["history.json"])  # no temp left
        q.clear_history()  # the next change writes everything again
        self.assertEqual(self.stored(), [])

    def test_unwritable_folder_is_logged(self):
        blocker = self.dir / "file"
        blocker.write_text("x", encoding="utf-8")
        q, _ = quiet(SpeechQueue, blocker / "history.json")
        _, log = quiet(read_one, q, "metin")
        self.assertIn("could not be written", log)
        self.assertEqual(len(q.history()), 1)

    def test_remove_and_clear_history_reach_the_file(self):
        q, _ = self.new_queue()
        read_one(q, "a")
        read_one(q, "b")
        q.remove_history(q.history()[1].id)
        self.assertEqual([e["text"] for e in self.stored()], ["b"])
        q.clear_history()
        self.assertEqual(self.stored(), [])
        loaded, _ = self.new_queue()
        self.assertEqual(loaded.history(), [])

    def test_writes_from_threads_end_with_the_latest_history(self):
        q, _ = self.new_queue()

        def reader(n):
            for i in range(20):
                item = q.add(f"metin {n} {i}")
                if item and q.take(timeout=1):
                    q.finish(item.id)

        threads = [threading.Thread(target=reader, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([e["text"] for e in self.stored()], [i.text for i in q.history()])

    def test_none_is_memory_only(self):
        with mock.patch.object(Path, "write_text") as write, mock.patch.object(Path, "read_text") as read:
            q = SpeechQueue()
            read_one(q, "metin")
            q.clear_history()
        write.assert_not_called()
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
