import time
import unittest
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from unittest import mock

from ema_tts import config as cfg
from ema_tts import player as pl
from ema_tts import settings_window as sw
from ema_tts import speech_queue as sq
from ema_tts import ui
from ema_tts.speech_queue import PLAYING, WAITING, QueueFull
from ema_tts.widgets import Debounce, WheelAccumulator, wheel_target

LONG_TEXT = " ".join(f"Cümle {n}: uzun bir metnin parçası, panelde kaydırılarak okunur." for n in range(60))


@dataclass
class Entry:
    """An item as the core will hand it out (speech_queue.Item plus `finished`)."""
    id: int
    text: str
    state: str = WAITING
    added: float = field(default_factory=time.time)
    source: str = ""
    finished: float = 0.0


class FakeQueue:
    """The queue/history API from the contract; records the calls the UI makes."""

    def __init__(self, items=(), history=()):
        self.items, self.past = list(items), list(history)
        self.version = 0
        self.calls = []
        self.replay_result = None
        self.replay_error = None

    def _changed(self):
        self.version += 1

    def snapshot(self):
        return [replace(item) for item in self.items]

    def history(self):
        return [replace(item) for item in self.past]

    def add(self, text, source=""):
        item = Entry(max([i.id for i in self.items + self.past] or [0]) + 1, text, source=source)
        self.items.append(item)
        self._changed()
        return item

    def remove(self, item_id):
        self.calls.append(("remove", item_id))
        self.items = [i for i in self.items if i.id != item_id]
        self._changed()

    def clear(self):
        self.calls.append(("clear",))
        self.items = []
        self._changed()

    def replay(self, item_id):
        self.calls.append(("replay", item_id))
        if self.replay_error:
            raise self.replay_error
        return self.replay_result

    def remove_history(self, item_id):
        self.calls.append(("remove_history", item_id))
        self.past = [i for i in self.past if i.id != item_id]
        self._changed()

    def clear_history(self):
        self.calls.append(("clear_history",))
        self.past = []
        self._changed()


class FakePlayer:
    """The speed API from the contract (0.5–2.0, 0.05 steps) and the status fields the UI reads."""

    def __init__(self, status=pl.READY, paused=False):
        self.status, self._paused = status, paused
        self.speech_error = self.error = ""
        self.voice = dict(cfg.DEFAULTS["voice"])
        self.nudges = []

    @property
    def paused(self):
        return self._paused

    @property
    def speed(self):
        return self.voice["speed"]

    def set_speed(self, value):
        value = round(min(max(round(value / 0.05) * 0.05, 0.5), 2.0), 2)
        self.voice = {**self.voice, "speed": value}
        return value

    def nudge_speed(self, steps):
        self.nudges.append(steps)
        return self.set_speed(self.speed + steps * 0.05)

    def toggle_pause(self):
        self._paused = not self._paused

    def update(self, voice):
        self.voice = {**self.voice, **voice}


def sample_queue():
    now = time.time()
    return FakeQueue(
        items=[Entry(7, "Şu an okunan metin.", PLAYING, now, "claude-code"),
               Entry(8, LONG_TEXT, WAITING, now)],
        history=[Entry(5, "Bitmiş bir metin.", "done", now - 60, "codex", now - 30),
                 Entry(4, LONG_TEXT, "skipped", now - 120, "", now - 90)])


def make_app(queue, player):
    """The real App with fakes, never shown on screen: no layered window, no DWM calls, real config untouched."""
    with mock.patch.object(ui, "LayeredWindow", side_effect=OSError("test")), \
            mock.patch.object(ui.theme, "window_chrome", return_value=False), \
            mock.patch.object(ui.cfg, "load", return_value=cfg.DEFAULTS):
        app = ui.App(queue, player, "Alt+Shift+1", state_file=mock.MagicMock())
    app.root.withdraw()
    return app


def show_off_screen(app):
    """Maps the bubble and the panel far off screen: Tk only delivers synthetic pointer events to mapped windows."""
    app._place_panel = lambda: app.panel.geometry(f"{app.panel_width}x600+-4000+-4000")
    app.root.geometry("+-4000+-3000")
    app.root.deiconify()
    app._place_panel()
    app.panel.deiconify()
    app.root.update()


def close(app):
    for job in app.root.tk.splitlist(app.root.tk.call("after", "info")):
        app.root.after_cancel(job)
    app.root.destroy()


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.jobs = {}
        self._ids = 0

    def __call__(self):
        return self.now

    def after(self, ms, callback):
        self._ids += 1
        self.jobs[self._ids] = (self.now + ms / 1000, callback)
        return self._ids

    def cancel(self, job):
        self.jobs.pop(job, None)

    def advance(self, seconds):
        self.now += seconds
        for job, (due, callback) in sorted(self.jobs.items(), key=lambda kv: kv[1][0]):
            if due <= self.now:
                del self.jobs[job]
                callback()


class WheelTest(unittest.TestCase):
    def test_notches(self):
        wheel = WheelAccumulator(clock=FakeClock())
        self.assertEqual(wheel.feed(120), 1)
        self.assertEqual(wheel.feed(-120), -1)
        self.assertEqual(wheel.feed(360), 3)

    def test_small_deltas_add_up(self):
        wheel = WheelAccumulator(clock=FakeClock())
        self.assertEqual([wheel.feed(40) for _ in range(6)], [0, 0, 1, 0, 0, 1])
        self.assertEqual([wheel.feed(-30) for _ in range(4)], [0, 0, 0, -1])
        self.assertEqual(wheel.feed(100), 0)
        self.assertEqual(wheel.feed(50), 1)  # 150: one notch, 30 kept
        self.assertEqual(wheel.rest, 30)

    def test_direction_change_and_pause_drop_the_remainder(self):
        clock = FakeClock()
        wheel = WheelAccumulator(clock=clock)
        wheel.feed(100)
        self.assertEqual(wheel.feed(-40), 0)  # not 60 left over
        self.assertEqual(wheel.rest, -40)
        clock.now += 2
        self.assertEqual(wheel.feed(-90), 0)  # the -40 from two seconds ago is gone
        self.assertEqual(wheel.rest, -90)

    def test_inner_area_scrolls_until_its_end(self):
        self.assertEqual(wheel_target(0.0, 0.4, -1), "outer")  # at the top, wheel up: the panel
        self.assertEqual(wheel_target(0.0, 0.4, 1), "inner")
        self.assertEqual(wheel_target(0.6, 1.0, 1), "outer")
        self.assertEqual(wheel_target(0.3, 0.7, -2), "inner")
        self.assertEqual(wheel_target(0.0, 1.0, 1), "outer")  # fits: nothing to scroll


class SpeedTextTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(ui.speed_text(1.15), "1.15×")
        self.assertEqual(ui.speed_text(1), "1.00×")
        self.assertEqual(ui.speed_text(0.5), "0.50×")
        self.assertEqual(ui.speed_text(2.0, "max"), "2.00× max")

    def test_preview(self):
        self.assertEqual(ui.preview_text("  a\n\n b  "), "a b")
        preview = ui.preview_text(LONG_TEXT)
        self.assertTrue(preview.endswith("…"))
        self.assertLessEqual(len(preview), ui.PREVIEW_CHARS + 1)


class DebounceTest(unittest.TestCase):
    def test_runs_once_after_the_last_trigger(self):
        clock, calls = FakeClock(), []
        debounce = Debounce(clock.after, clock.cancel, 1000, lambda: calls.append(clock.now))
        for _ in range(5):
            debounce.trigger()
            clock.advance(0.3)  # each change within a second of the previous one
        self.assertEqual(calls, [])
        self.assertTrue(debounce.pending)
        clock.advance(0.6)
        self.assertEqual(len(calls), 0)
        clock.advance(0.1)  # 1.0 s after the last trigger
        self.assertEqual(len(calls), 1)
        self.assertFalse(debounce.pending)
        clock.advance(5)
        self.assertEqual(len(calls), 1)

    def test_flush(self):
        clock, calls = FakeClock(), []
        debounce = Debounce(clock.after, clock.cancel, 1000, lambda: calls.append(1))
        debounce.flush()
        self.assertEqual(calls, [])
        debounce.trigger()
        debounce.flush()
        self.assertEqual(calls, [1])
        clock.advance(2)
        self.assertEqual(calls, [1])


class ContractConstantsTest(unittest.TestCase):
    @unittest.skipUnless(hasattr(sq, "DONE"), "history API of the core is not merged yet")
    def test_history_states_match_the_queue(self):
        self.assertEqual((ui.DONE, ui.SKIPPED), (sq.DONE, sq.SKIPPED))

    @unittest.skipUnless(hasattr(pl, "SPEED_MAX"), "speed API of the core is not merged yet")
    def test_speed_range_matches_the_settings_slider(self):
        self.assertEqual((pl.SPEED_MIN, pl.SPEED_MAX, pl.SPEED_STEP), (sw.SPEED_MIN, sw.SPEED_MAX, sw.SPEED_STEP))


class AppTest(unittest.TestCase):
    def setUp(self):
        self.queue, self.player = sample_queue(), FakePlayer(pl.SPEAKING)
        self.app = make_app(self.queue, self.player)
        self.addCleanup(close, self.app)
        self.app._render_panel()

    def keys_on_canvas(self):
        c = self.app.list_canvas
        return {tag for item in c.find_all() for tag in c.gettags(item) if tag.startswith("body-")}


class ListTest(AppTest):
    def test_queue_and_history_rows(self):
        self.assertEqual(self.keys_on_canvas(), {"body-q7", "body-q8", "body-h5", "body-h4"})
        c = self.app.list_canvas
        texts = {c.itemcget(i, "text") for i in c.find_all() if c.type(i) == "text"}
        self.assertTrue({"HISTORY", "DONE", "SKIPPED", "AGENT · codex", "Clear history", "SPEAKING"} <= texts)

    def test_history_collapses(self):
        self.app.toggle_history()
        self.assertEqual(self.keys_on_canvas(), {"body-q7", "body-q8"})
        self.app.toggle_history()
        self.assertIn("body-h5", self.keys_on_canvas())

    def test_expansion_survives_redraws(self):
        app = self.app
        app.toggle_row("q8")
        app.toggle_row("h4")
        frame, box, _ = app._wells["q8"]
        self.assertEqual(box.get("1.0", "end-1c"), LONG_TEXT)  # full text, not the preview
        self.assertEqual(box.cget("state"), "disabled")
        box.tag_add("sel", "1.0", "1.5")

        self.queue.add("Yeni gelen metin.")  # the queue changes: rows are drawn again
        self.assertTrue(app._render_list(app._status()))
        self.assertEqual(app.expanded, {"q8", "h4"})
        self.assertIs(app._wells["q8"][0], frame)  # the same widget, with its scroll position and selection
        self.assertEqual(box.tag_ranges("sel")[0].string, "1.0")
        windows = [i for i in app.list_canvas.find_all() if app.list_canvas.type(i) == "window"]
        self.assertEqual(len(windows), 2)

    def test_expanded_text_height_is_capped(self):
        self.app.toggle_row("q8")
        window = next(i for i in self.app.list_canvas.find_all() if self.app.list_canvas.type(i) == "window")
        self.assertEqual(int(self.app.list_canvas.itemcget(window, "height")), self.app.px(ui.theme.EXPANDED_MAX))
        self.app.toggle_row("h5")
        short = self.app.list_canvas.find_all()
        heights = sorted(int(self.app.list_canvas.itemcget(i, "height")) for i in short
                         if self.app.list_canvas.type(i) == "window")
        self.assertLess(heights[0], self.app.px(60))

    def test_scroll_position_survives_redraws(self):
        app, c = self.app, self.app.list_canvas
        app.toggle_row("q8")
        app.toggle_row("h4")
        c.yview_moveto(0.3)
        top = c.canvasy(0)
        self.assertGreater(top, 0)
        self.queue.add("Bir metin daha.")
        app._render_list(app._status())
        self.assertEqual(c.canvasy(0), top)

    def test_nothing_changed_means_no_redraw(self):
        app = self.app
        self.assertFalse(app._render_list(app._status()))
        self.player.voice["speed"] = 1.5  # header only
        app._render_panel()
        self.assertFalse(app._render_list(app._status()))
        self.player.status = pl.ERROR  # the playing row says FAILED now
        self.assertTrue(app._render_list(app._status()))

    def test_gone_items_lose_their_expansion(self):
        app = self.app
        app.toggle_row("q8")
        frame = app._wells["q8"][0]
        self.queue.remove(8)
        app._render_list(app._status())
        self.assertEqual(app.expanded, set())
        self.assertEqual(app._wells, {})
        self.assertFalse(frame.winfo_exists())


class TextWheelTest(AppTest):
    def test_wheel_scrolls_the_text_then_the_panel(self):
        app, c = self.app, self.app.list_canvas
        show_off_screen(app)
        app.toggle_row("q8")
        app.root.update()
        _, box, bar = app._wells["q8"]
        self.assertLess(bar.last, 1.0)  # the text's own scrollbar shows without scrolling first
        panel_top = c.canvasy(0)
        box.event_generate("<MouseWheel>", delta=-120)
        app.root.update()
        self.assertGreater(box.yview()[0], 0.0)
        self.assertEqual(c.canvasy(0), panel_top)  # the panel stayed put

        box.yview_moveto(1.0)
        app.root.update()
        box.event_generate("<MouseWheel>", delta=-120)  # at the end of the text: the panel takes over
        app.root.update()
        self.assertGreater(c.canvasy(0), panel_top)

    def test_wheel_elsewhere_scrolls_the_panel(self):
        app, c = self.app, self.app.list_canvas
        show_off_screen(app)
        app.toggle_row("q8")
        app.root.update()
        for _ in range(3):
            c.event_generate("<MouseWheel>", delta=-40)  # high-resolution wheel: three events, one notch
        app.root.update()
        self.assertGreater(c.canvasy(0), 0)


class HistoryActionTest(AppTest):
    def test_replay(self):
        self.queue.replay_result = Entry(9, "Bitmiş bir metin.")
        self.app.replay(5)
        self.assertEqual(self.queue.calls[-1], ("replay", 5))
        self.assertEqual(self.app._flash, "ok")
        self.assertIsNone(self.app._notice)

    def test_replay_refused(self):
        self.queue.replay_result = None
        self.app.replay(5)
        self.assertEqual(self.app._flash, "reject")
        self.assertIn("already in the queue", self.app._notice[0])
        self.queue.replay_error = QueueFull("the queue already holds 100 items")
        self.app.replay(5)
        self.assertEqual(self.app._flash, "reject")
        self.assertIn("100 items", self.app._notice[0])
        self.app._render_panel()
        self.assertIn("100 items", self.app.message_text.cget("text"))

    def test_copy(self):
        with mock.patch.object(self.app.root, "clipboard_clear") as clear, \
                mock.patch.object(self.app.root, "clipboard_append") as append:
            self.app.copy_text(LONG_TEXT)
        clear.assert_called_once()
        append.assert_called_once_with(LONG_TEXT)
        self.assertEqual(self.app._notice[1], "ok")

    def test_notice_expires(self):
        self.app._notify("Copied to the clipboard.")
        self.app._notice = (self.app._notice[0], "ok", time.monotonic() - 1)
        with mock.patch.object(self.app.root, "after"):
            self.app._tick()
        self.assertIsNone(self.app._notice)

    def test_row_buttons_call_the_queue(self):
        """Clicks reach the canvas items through synthetic events on a window placed off screen."""
        app, c = self.app, self.app.list_canvas
        self.queue.replay_result = Entry(9, "x")
        show_off_screen(app)

        def click(tag):
            x0, y0, x1, y1 = c.bbox(tag)
            x, y = (x0 + x1) // 2 - int(c.canvasx(0)), (y0 + y1) // 2 - int(c.canvasy(0))
            c.event_generate("<Motion>", x=x, y=y)
            c.event_generate("<ButtonPress-1>", x=x, y=y)
            c.event_generate("<ButtonRelease-1>", x=x, y=y)
            app.panel.update()

        with mock.patch.object(app, "copy_text") as copy:
            click("copy-h5")
        copy.assert_called_once_with("Bitmiş bir metin.")
        click("replay-h5")
        self.assertIn(("replay", 5), self.queue.calls)
        click("remove-h4")
        self.assertIn(("remove_history", 4), self.queue.calls)
        app._render_panel()
        click("history-clear")
        self.assertIn(("clear_history",), self.queue.calls)
        app._render_panel()
        click("body-q7")
        self.assertEqual(app.expanded, {"q7"})
        click("remove-q7")
        self.assertIn(("remove", 7), self.queue.calls)


class SpeedTest(AppTest):
    def setUp(self):
        super().setUp()
        show_off_screen(self.app)

    def wheel(self, widget, delta):
        widget.event_generate("<MouseWheel>", delta=delta)

    def test_bubble_wheel_while_speaking(self):
        with mock.patch.object(self.app, "_show_badge") as badge:
            self.wheel(self.app.canvas, 120)
            self.wheel(self.app.canvas, 240)
        self.assertEqual(self.player.nudges, [1, 1, 1])  # one call per notch
        self.assertAlmostEqual(self.player.speed, 1.15)
        badge.assert_called_with("1.15×")
        self.assertTrue(self.app._speed_save.pending)

    def test_hi_res_wheel_and_paused(self):
        self.player.status, self.player._paused = pl.READY, True
        with mock.patch.object(self.app, "_show_badge"):
            for _ in range(3):
                self.wheel(self.app.canvas, -40)
        self.assertEqual(self.player.nudges, [-1])
        self.assertAlmostEqual(self.player.speed, 0.95)

    def test_idle_bubble_only_shows_the_speed(self):
        self.player.status = pl.READY
        with mock.patch.object(self.app, "_show_badge") as badge:
            self.wheel(self.app.canvas, 120)
        self.assertEqual(self.player.nudges, [])
        badge.assert_called_once_with("1.00×", active=False)
        self.assertFalse(self.app._speed_save.pending)

    def test_limit(self):
        self.player.set_speed(1.95)
        with mock.patch.object(self.app, "_show_badge") as badge:
            self.wheel(self.app.canvas, 360)
        badge.assert_called_with("2.00× max")
        self.assertEqual(self.player.speed, 2.0)

    def test_chip_wheel_works_when_idle_and_click_resets(self):
        self.player.status = pl.READY
        self.wheel(self.app.speed_chip, -120)
        self.assertAlmostEqual(self.player.speed, 0.95)
        self.assertEqual(self.app.speed_chip.text, "0.95×")
        self.app.speed_chip.invoke()
        self.assertEqual(self.player.speed, 1.0)
        self.assertEqual(self.app.speed_chip.text, "1.00×")

    def test_chip_wheel_does_not_scroll_the_panel(self):
        with mock.patch.object(self.app.list_canvas, "yview_scroll") as scroll:
            self.wheel(self.app.speed_chip, 120)
        scroll.assert_not_called()

    def test_saved_once_after_the_last_change(self):
        clock = FakeClock()
        self.app._speed_save = Debounce(clock.after, clock.cancel, ui.SPEED_SAVE_MS, self.app._save_speed)
        with mock.patch.object(ui.cfg, "update") as update, mock.patch.object(self.app, "_show_badge"):
            for _ in range(4):
                self.wheel(self.app.canvas, 120)
                clock.advance(0.4)
            update.assert_not_called()
            clock.advance(0.6)
        update.assert_called_once_with("voice", {"speed": 1.2})

    def test_save_error_is_shown(self):
        with mock.patch.object(ui.cfg, "update", side_effect=ValueError("bad JSON")):
            self.app._save_speed()
        self.assertTrue(any("voice.speed" in w and "bad JSON" in w for w in self.app.warnings))

    def test_quit_flushes_a_pending_save(self):
        self.app.change_speed(1)
        with mock.patch.object(ui.cfg, "update") as update, mock.patch.object(self.app, "_save_position"), \
                mock.patch.object(self.app.root, "destroy"):
            self.app.quit()
        update.assert_called_once_with("voice", {"speed": 1.05})

    def test_open_settings_window_follows(self):
        window = SimpleNamespace(top=mock.Mock(**{"winfo_exists.return_value": True}), show_speed=mock.Mock())
        with mock.patch.object(sw.SettingsWindow, "instance", window), mock.patch.object(ui.cfg, "update"):
            self.app.change_speed(2)
            window.show_speed.assert_called_with(1.1, False)
            self.app._speed_save.flush()
            window.show_speed.assert_called_with(1.1, True)


class SettingsSyncTest(unittest.TestCase):
    def window(self, speed):
        changed = mock.Mock()
        return SimpleNamespace(saved={"speed": 1.0, "volume": 80}, speed=mock.Mock(**{"get.return_value": speed}),
                               _changed=changed)

    def test_slider_follows(self):
        window = self.window(1.0)
        sw.SettingsWindow.show_speed(window, 1.25)
        window.speed.set.assert_called_once_with(1.25)
        self.assertEqual(window.saved["speed"], 1.0)  # not saved yet: the window may say "unsaved"

    def test_saved_value_is_not_reported_as_unsaved(self):
        window = self.window(1.25)
        sw.SettingsWindow.show_speed(window, 1.25, saved=True)
        self.assertEqual(window.saved, {"speed": 1.25, "volume": 80})
        window.speed.set.assert_not_called()
        window._changed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
