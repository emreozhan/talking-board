"""Floating bubble and queue panel (tkinter)."""

import json
import queue as stdqueue
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import art, theme
from . import config as cfg
from . import player as pl
from .speech_queue import PLAYING, Rejected, SpeechQueue
from .widgets import Button, Chip, Debounce, LayeredWindow, ThinScrollbar, WheelAccumulator, wheel_target

APP_NAME = "Talking Board"
TRANSPARENT = "#ff00fe"  # colour key, only used when layered windows are unavailable
PREVIEW_CHARS = 160
FRAME_MS = 40  # while animating
IDLE_MS = 100
SPEED_SAVE_MS = 1000  # config.json is written this long after the last speed change
SPEED_BADGE_MS = 1200
NOTICE_SECONDS = 4.0
TEXT_LINES_PER_NOTCH = 3
# history states, as in speech_queue (DONE, SKIPPED)
DONE, SKIPPED = "done", "skipped"
HISTORY_MARKS = {DONE: ("DONE", theme.MINT_TEXT), SKIPPED: ("SKIPPED", theme.MUTED)}
STATUS_LABELS = {pl.LOADING: "Loading model", pl.READY: "Ready", pl.SPEAKING: "Speaking",
                 pl.PAUSED: "Paused", pl.ERROR: "Error"}
STATUS_COLORS = {pl.LOADING: theme.MUTED, pl.READY: theme.PAPER, pl.SPEAKING: theme.SAFFRON,
                 pl.PAUSED: theme.SAFFRON, pl.ERROR: theme.POMEGRANATE_TEXT}


def speed_text(value: float, limit: str | None = None) -> str:
    """"1.15×"; limit ("max" or "min") is added when the wheel hit the end of the range."""
    return f"{value:.2f}×" + (f" {limit}" if limit else "")


def preview_text(text: str) -> str:
    preview = " ".join(text.split())
    return preview if len(preview) <= PREVIEW_CHARS else preview[:PREVIEW_CHARS].rstrip() + "…"


class App:
    def __init__(self, speech_queue: SpeechQueue, player: pl.Player, hotkey_label: str, state_file: Path):
        self.queue = speech_queue
        self.player = player
        self.hotkey_label = hotkey_label
        self.warnings: list[str] = []  # config.json problems, hotkeys that could not be registered
        self.state_file = state_file
        self.events: stdqueue.Queue[str] = stdqueue.Queue()  # requests from other threads

        self.root = tk.Tk()
        theme.pick_icon_font(self.root)
        self.scale = self.root.winfo_fpixels("1i") / 96
        self.size = round(theme.BUBBLE * self.scale)
        self._seen = None
        self._flash = None
        self._flash_start = 0.0
        self._start = time.monotonic()
        self._hover = False
        self._drag = None
        self.expanded: set[str] = set()  # row keys: "q<id>" for the queue, "h<id>" for history
        self.history_open = True
        self._wells: dict[str, tuple] = {}  # row key -> (frame, text, scrollbar) of an expanded row
        self._list_key = None  # what the list was last drawn from; unchanged means no redraw
        self._bound: list[tuple[str, str]] = []
        self._notice = None  # (text, kind, until)
        self._scroll_wheel = WheelAccumulator()
        self._speed_wheel = WheelAccumulator()
        self._speed_save = Debounce(self.root.after, self.root.after_cancel, SPEED_SAVE_MS, self._save_speed)
        self.badge = None
        self._badge_job = None
        theme.set_icon(self.root)

        self._build_bubble()
        self._build_panel()
        self._build_menu()
        self.root.after(IDLE_MS, self._tick)

    # ---------- helpers ----------
    def px(self, value: float) -> int:
        return round(value * self.scale)

    def font(self, size: int, weight: str = "normal", family: str = theme.BODY):
        return (family, size, weight)

    def _status(self) -> str:
        if self.player.paused and self.player.status != pl.ERROR:
            return pl.PAUSED
        return self.player.status

    def _load_position(self):
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return int(data["x"]), int(data["y"])
        except (OSError, ValueError, KeyError, TypeError):
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            return sw - self.size - self.px(24), sh - self.size - self.px(96)

    def _save_position(self):
        data = {"x": self.root.winfo_x(), "y": self.root.winfo_y()}
        try:
            self.state_file.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    # ---------- bubble ----------
    def _build_bubble(self):
        root = self.root
        root.title(APP_NAME)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        x, y = self._load_position()
        root.geometry(f"{self.size}x{self.size}+{x}+{y}")

        self.canvas = tk.Canvas(root, width=self.size, height=self.size, bg=TRANSPARENT,
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_menu)
        self.canvas.bind("<Enter>", lambda _e: self._set_hover(True))
        self.canvas.bind("<Leave>", lambda _e: self._set_hover(False))
        self.canvas.bind("<MouseWheel>", self._on_bubble_wheel)
        self.layered = None
        if sys.platform == "win32":
            try:
                self.layered = LayeredWindow(root)
            except (OSError, AttributeError, ValueError) as error:
                print(f"layered window unavailable, using a colour key: {error}")
        if self.layered is None:
            self._use_color_key()
        self._draw_bubble()

    def _use_color_key(self):
        self.layered = None
        self.root.configure(bg=TRANSPARENT)
        self.root.attributes("-transparentcolor", TRANSPARENT)

    def _set_hover(self, value: bool):
        self._hover = value
        self._draw_bubble()

    def _draw_bubble(self):
        age = time.monotonic() - self._flash_start
        image = art.logo(self.size, self._status(), time.monotonic() - self._start, self._flash, age,
                         len(self.queue.snapshot()), self._hover)
        if self.layered is not None and self.layered.show(image):
            return
        if self.layered is not None:
            print("UpdateLayeredWindow failed, using a colour key")
            self._use_color_key()
        # colour key fallback: no partial transparency, so drop the shadow and flatten the edge
        flat = Image.alpha_composite(Image.new("RGBA", image.size, art.rgb(theme.NIGHT)), image)
        keyed = Image.new("RGBA", image.size, art.rgb(TRANSPARENT))
        keyed.paste(flat, mask=image.getchannel("A").point(lambda a: 255 if a >= 140 else 0))
        from PIL import ImageTk

        self._bubble_photo = ImageTk.PhotoImage(keyed, master=self.root)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._bubble_photo, anchor="nw")

    def _on_press(self, event):
        self._drag = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y(), False)

    def _on_drag(self, event):
        if not self._drag:
            return
        sx, sy, wx, wy, moved = self._drag
        dx, dy = event.x_root - sx, event.y_root - sy
        if moved or abs(dx) + abs(dy) > self.px(4):
            self._drag = (sx, sy, wx, wy, True)
            self.root.geometry(f"+{wx + dx}+{wy + dy}")
            if self.panel.winfo_viewable():
                self._place_panel()

    def _on_release(self, _event):
        moved = self._drag and self._drag[4]
        self._drag = None
        if moved:
            self._save_position()
        else:
            self.toggle_panel()

    # ---------- context menu ----------
    def _build_menu(self):
        self.menu = tk.Menu(self.root, **theme.menu_options(self.font(9)))
        self.menu.add_command(label="Show queue", command=self.toggle_panel)
        self.menu.add_command(label="Add from clipboard", command=self.add_from_clipboard)
        self.menu.add_command(label="Pause / Resume", command=self.toggle_pause)
        self.auto_read = tk.BooleanVar(value=cfg.load()["server"]["auto_read"])
        self.menu.add_checkbutton(label="Read agent messages", variable=self.auto_read,
                                  command=self._save_auto_read)
        self.menu.add_command(label="Settings…", command=self.open_settings)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.quit)

    def _save_auto_read(self):
        try:
            cfg.update("server", {"auto_read": self.auto_read.get()})
        except (OSError, ValueError) as error:
            message = f"Could not save \"Read agent messages\" to config.json: {error}"
            print(message)
            self.auto_read.set(not self.auto_read.get())
            self.warn(message)

    def warn(self, message: str):
        if message not in self.warnings:
            self.warnings.append(message)
        self._seen = None  # redraw the panel

    def _on_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    # ---------- panel ----------
    def _build_panel(self):
        px, bg = self.px, theme.NIGHT
        self.panel = tk.Toplevel(self.root, bg=bg)
        self.panel.withdraw()
        self.panel.overrideredirect(True)
        self.panel.attributes("-topmost", True)
        self.panel.bind("<Escape>", lambda _e: self.hide_panel())
        self.panel_width = px(theme.PANEL_WIDTH)
        if not theme.window_chrome(self.panel):
            self.panel.configure(highlightthickness=1, highlightbackground=theme.LINE)

        self.signal = tk.Frame(self.panel, bg=theme.LINE, height=px(2))  # lights up while speaking
        self.signal.pack(fill="x")

        header = tk.Frame(self.panel, bg=bg)
        header.pack(fill="x", padx=(px(theme.S4), px(theme.S2)), pady=(px(theme.S3), 0))
        self.mark = tk.Label(header, bg=bg, bd=0)
        self.mark.pack(side="left")
        tk.Label(header, text="Speech queue", bg=bg, fg=theme.PAPER,
                 font=self.font(12, family=theme.DISPLAY)).pack(side="left", padx=(px(theme.S2), 0))
        self._tag(header, "AI VOICE").pack(side="left", padx=(px(theme.S2), 0))
        Button(header, "", self.hide_panel, px=px, kind="ghost", icon=theme.ICON_CLOSE, height=28,
               square=True, takefocus=False).pack(side="right")
        Button(header, "", self.open_settings, px=px, kind="ghost", icon=theme.ICON_SETTINGS, height=28,
               square=True, takefocus=False).pack(side="right")
        # reading speed: the wheel changes it, a click goes back to 1.00×
        self.speed_chip = Chip(header, speed_text(1.0), self.reset_speed, px=px, on_wheel=self._on_chip_wheel,
                               width_text=speed_text(0.0))
        self.speed_chip.pack(side="right", padx=(0, px(theme.S1)))

        status = tk.Frame(self.panel, bg=bg)
        status.pack(fill="x", padx=px(theme.S4), pady=(px(theme.S1), 0))
        self.state_label = tk.Label(status, bg=bg, font=self.font(9, family=theme.DISPLAY))
        self.state_label.pack(side="left")
        self.count_label = tk.Label(status, bg=bg, fg=theme.MUTED, font=self.font(9))
        self.count_label.pack(side="left", padx=(px(6), 0))
        self._keycap(status, self.hotkey_label).pack(side="right")
        tk.Label(status, text="add", bg=bg, fg=theme.FAINT, font=self.font(8)).pack(side="right", padx=(0, px(6)))

        self.message = tk.Frame(self.panel, bg=bg)
        self.message_icon = tk.Label(self.message, bg=bg, font=(theme.ICONS, 9))
        self.message_icon.pack(side="left", anchor="n", pady=(px(2), 0))
        self.message_text = tk.Label(self.message, bg=bg, font=self.font(9), justify="left", anchor="w",
                                     wraplength=self.panel_width - px(56))
        self.message_text.pack(side="left", fill="x", padx=(px(6), 0))

        self.actions = tk.Frame(self.panel, bg=bg)
        self.actions.pack(fill="x", padx=px(theme.S4), pady=(px(theme.S3), px(theme.S3)))
        self.pause_button = Button(self.actions, "Pause", self.toggle_pause, px=px, kind="primary",
                                   icon=theme.ICON_PAUSE, takefocus=False)
        self.pause_button.pack(side="left")
        Button(self.actions, "Add from clipboard", self.add_from_clipboard, px=px, icon=theme.ICON_PASTE,
               takefocus=False).pack(side="left", padx=(px(theme.S2), 0))
        Button(self.actions, "Clear all", self.queue.clear, px=px, kind="danger", icon=theme.ICON_CLEAR,
               takefocus=False).pack(side="right")

        tk.Frame(self.panel, bg=theme.LINE, height=1).pack(fill="x")

        body = tk.Frame(self.panel, bg=bg)
        body.pack(fill="both", expand=True, padx=(px(theme.S4), px(theme.S1)), pady=(px(theme.S3), px(theme.S4)))
        self.list_width = self.panel_width - px(theme.S4) - px(theme.S1) - px(10) - px(theme.S1)
        self.list_canvas = tk.Canvas(body, bg=bg, highlightthickness=0, bd=0, width=self.list_width)
        scrollbar = ThinScrollbar(body, self.list_canvas.yview, px=px)
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y", padx=(px(theme.S1), 0))
        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.panel.bind("<MouseWheel>", self._on_panel_wheel)

    def _keycap(self, parent, text):
        px = self.px
        font = (theme.MONO, 8)
        w = tkfont.Font(root=self.root, font=font).measure(text) + px(12)
        h = px(20)
        canvas = tk.Canvas(parent, bg=parent["bg"], highlightthickness=0, bd=0, width=w, height=h)
        image = art.photo(canvas, ("keycap", w, h), lambda: art.rounded(w, h, px(theme.RADIUS_SM), theme.SURFACE,
                                                                         theme.LINE, px(1)))
        canvas.create_image(0, 0, image=image, anchor="nw")
        canvas.create_text(w / 2, h / 2, text=text, font=font, fill=theme.MUTED)
        return canvas

    def _tag(self, parent, text):
        """Small outlined label in the title row; says that the voice is synthetic."""
        px = self.px
        font = (theme.LABEL, 7)
        w = tkfont.Font(root=self.root, font=font).measure(text) + px(10)
        h = px(16)
        canvas = tk.Canvas(parent, bg=parent["bg"], highlightthickness=0, bd=0, width=w, height=h)
        image = art.photo(canvas, ("tag", w, h), lambda: art.rounded(w, h, px(theme.RADIUS_SM), theme.NIGHT,
                                                                     theme.LINE, px(1)))
        canvas.create_image(0, 0, image=image, anchor="nw")
        canvas.create_text(w / 2, h / 2, text=text, font=font, fill=theme.MUTED)
        return canvas

    def _button(self, parent, text, command, kind="secondary", icon=None):
        return Button(parent, text, command, px=self.px, kind=kind, icon=icon)

    def _render_panel(self):
        status = self._status()
        items = self.queue.snapshot()
        self.signal.configure(bg=theme.SAFFRON if status == pl.SPEAKING else theme.LINE)
        size = self.px(22)
        self.mark.configure(image=art.photo(self.panel, ("mark", size, status),
                                            lambda: art.logo(size, status, mark=True)))
        self.state_label.configure(text=STATUS_LABELS.get(status, status).upper(),
                                   fg=STATUS_COLORS.get(status, theme.PAPER))
        self.count_label.configure(text=f"·  {len(items)} item" + ("" if len(items) == 1 else "s"))
        speed = self.player.speed
        self.speed_chip.set(speed_text(speed), accent=abs(speed - 1.0) > 1e-6)

        lines = []
        if self.player.speech_error:
            lines.append(self.player.speech_error)
        elif self.player.status == pl.ERROR:
            lines.append(f"Model failed to load: {self.player.error.strip().splitlines()[-1]}")
        lines += self.warnings
        notice = self._notice
        if lines or notice:
            if lines:
                color = theme.POMEGRANATE_TEXT if self.player.status == pl.ERROR else theme.AMBER_TEXT
                icon = theme.ICON_WARNING
                lines += [notice[0]] if notice else []
            else:
                ok = notice[1] == "ok"
                color, icon = (theme.MINT_TEXT, theme.ICON_OK) if ok else (theme.AMBER_TEXT, theme.ICON_WARNING)
                lines = [notice[0]]
            self.message_icon.configure(text=icon, fg=color)
            self.message_text.configure(text="\n".join(lines), fg=color)
            self.message.pack(fill="x", padx=self.px(theme.S4), pady=(self.px(theme.S2), 0),
                              before=self.actions)
        else:
            self.message.pack_forget()
        paused = self.player.paused
        self.pause_button.set("Resume" if paused else "Pause", theme.ICON_PLAY if paused else theme.ICON_PAUSE)

        self._render_list(status, items)
        self._place_panel()

    def _render_list(self, status, items=None) -> bool:
        """Redraws the queue and history rows if anything they show changed. Keeps the scroll position,
        the expanded rows and their text widgets (with their own scroll position and selection)."""
        items = self.queue.snapshot() if items is None else items
        history = self.queue.history()
        keys = {f"q{item.id}" for item in items} | {f"h{item.id}" for item in history}
        self.expanded &= keys
        for key in [key for key in self._wells if key not in self.expanded]:
            self._wells.pop(key)[0].destroy()
        playing = any(item.state == PLAYING for item in items)
        signature = (tuple((item.id, item.state) for item in items), tuple((item.id, item.state) for item in history),
                     frozenset(self.expanded), self.history_open, status if playing else None)
        if signature == self._list_key:
            return False
        self._list_key = signature

        c = self.list_canvas
        top = c.canvasy(0)
        content = self._render_rows(items, history, status)
        list_h = min(max(content, self.px(60)), self._list_max())
        total = max(content, list_h)
        c.configure(height=list_h, scrollregion=(0, 0, self.list_width, total))
        c.yview_moveto(top / total)
        return True

    def _list_max(self) -> int:
        return min(self.px(theme.LIST_MAX), int(self.root.winfo_screenheight() * 0.55))

    def _bind(self, tag, sequence, handler):
        self.list_canvas.tag_bind(tag, sequence, handler)
        self._bound.append((tag, sequence))

    def _render_rows(self, items, history, status) -> int:
        """Draws the queue, then the history, on the list canvas and returns the content height."""
        px, c, width = self.px, self.list_canvas, self.list_width
        c.delete("all")
        for tag, sequence in self._bound:  # bindings outlive the items; drop them with the rows
            c.tag_unbind(tag, sequence)
        self._bound = []
        if not items:
            label = self.hotkey_label
            hint = ("Copy text and press " + label + ".\nAgent replies show up here too."
                    if label not in ("off", "invalid")
                    else "Copy text, then right-click the bubble\nand choose Add from clipboard.")
            c.create_text(width / 2, px(24), text=theme.ICON_SPEAKER, font=(theme.ICONS, 18), fill=theme.FAINT)
            c.create_text(width / 2, px(56), text="Queue is empty", font=self.font(11, family=theme.DISPLAY),
                          fill=theme.PAPER)
            c.create_text(width / 2, px(86), text=hint, font=self.font(9), fill=theme.MUTED, justify="center")
            y = px(112)
        else:
            y = 0
            for item in items:
                y += self._render_row(item, y, width, status) + px(theme.S2)
            y -= px(theme.S2)
        if history:
            y += px(theme.S4)
            y += self._render_history_header(y, width, len(history))
            if self.history_open:
                y += px(theme.S2)
                for item in history:
                    y += self._render_row(item, y, width, status, history=True) + px(theme.S2)
                y -= px(theme.S2)
        return y

    def _render_history_header(self, y, width, count) -> int:
        px, c = self.px, self.list_canvas
        h = px(24)
        cy = y + h / 2
        toggle, clear = "history-toggle", "history-clear"
        area = c.create_rectangle(0, y, width, y + h, fill=theme.NIGHT, width=0, tags=toggle)
        chevron = c.create_text(px(6), cy + 1, font=(theme.ICONS, 7), fill=theme.MUTED, tags=toggle,
                                text=theme.ICON_CHEVRON if self.history_open else theme.ICON_CHEVRON_RIGHT)
        label = c.create_text(px(18), cy, text="HISTORY", font=self.font(8, family=theme.DISPLAY),
                              fill=theme.SAFFRON, anchor="w", tags=toggle)
        number = c.create_text(c.bbox(label)[2] + px(6), cy, text=str(count), font=(theme.MONO, 8),
                               fill=theme.FAINT, anchor="w", tags=toggle)
        clear_text = c.create_text(width - px(4), cy, text="Clear history", font=self.font(8), fill=theme.MUTED,
                                   anchor="e", tags=clear)
        x0, x1 = c.bbox(number)[2] + px(theme.S2), c.bbox(clear_text)[0] - px(theme.S3)
        c.create_line(x0, cy, max(x0, x1), cy, fill=theme.LINE, tags=toggle)
        c.coords(area, 0, y, x1, y + h)
        self._bind(toggle, "<Enter>", lambda _e: (c.itemconfigure(chevron, fill=theme.PAPER),
                                                  c.configure(cursor="hand2")))
        self._bind(toggle, "<Leave>", lambda _e: (c.itemconfigure(chevron, fill=theme.MUTED), c.configure(cursor="")))
        self._bind(toggle, "<ButtonRelease-1>", lambda _e: self.toggle_history())
        self._bind(clear, "<Enter>", lambda _e: (c.itemconfigure(clear_text, fill=theme.POMEGRANATE_TEXT),
                                                 c.configure(cursor="hand2")))
        self._bind(clear, "<Leave>", lambda _e: (c.itemconfigure(clear_text, fill=theme.MUTED),
                                                 c.configure(cursor="")))
        self._bind(clear, "<ButtonRelease-1>", lambda _e: self.clear_history())
        return h

    def _render_row(self, item, y, width, status, history=False) -> int:
        px, c = self.px, self.list_canvas
        pad = px(theme.S3)
        key = f"{'h' if history else 'q'}{item.id}"
        hover, body = f"row-{key}", f"body-{key}"
        tags = (hover, body)
        playing = not history and item.state == PLAYING
        expanded = key in self.expanded
        x = pad + (px(3) if playing else 0)
        if item.source:
            icon, label, color = theme.ICON_AGENT, f"AGENT · {item.source}", theme.SKY
        else:
            icon, label, color = theme.ICON_PASTE, "CLIPBOARD", theme.MUTED
        meta_y = y + pad + px(6)
        c.create_text(x, meta_y, text=icon, font=(theme.ICONS, 8), fill=color, anchor="w", tags=tags)
        c.create_text(x + px(15), meta_y, text=label, font=self.font(8, family=theme.LABEL), fill=color,
                      anchor="w", tags=tags)

        right = width - pad - px(4)  # centre of the right-hand column of icons
        if history:
            actions = [(theme.ICON_CLOSE, "remove", lambda i=item.id: self.queue.remove_history(i),
                        theme.POMEGRANATE_TEXT),
                       (theme.ICON_COPY, "copy", lambda t=item.text: self.copy_text(t), theme.PAPER),
                       (theme.ICON_REPLAY, "replay", lambda i=item.id: self.replay(i), theme.SAFFRON)]
            when = c.create_text(right - px(52) - px(18), meta_y, text=self._clock(item.finished),
                                 font=(theme.MONO, 8), fill=theme.FAINT, anchor="e", tags=tags)
            word, word_color = HISTORY_MARKS.get(item.state, (item.state.upper(), theme.MUTED))
            c.create_text(c.bbox(when)[0] - px(theme.S2), meta_y, text=word,
                          font=self.font(7, family=theme.DISPLAY), fill=word_color, anchor="e", tags=tags)
        else:
            actions = [(theme.ICON_CLOSE, "remove", lambda i=item.id: self.queue.remove(i), theme.POMEGRANATE_TEXT)]
            if playing:
                text = {pl.PAUSED: "PAUSED", pl.ERROR: "FAILED"}.get(status, "SPEAKING")
                text_color = theme.POMEGRANATE_TEXT if status == pl.ERROR else theme.SAFFRON
                text_font = self.font(8, family=theme.DISPLAY)
            else:
                text, text_color, text_font = self._clock(item.added), theme.FAINT, (theme.MONO, 8)
            c.create_text(width - pad - px(26), meta_y, text=text, font=text_font, fill=text_color, anchor="e",
                          tags=tags)

        fill = theme.SAFFRON_TINT if playing else theme.SURFACE
        body_y = meta_y + px(12)
        text_w = width - x - pad - px(24)
        if expanded:
            bottom = body_y + self._place_well(key, item.text, x, body_y, text_w, fill)
        else:
            preview = c.create_text(x, body_y, text=preview_text(item.text), font=self.font(9), fill=theme.PAPER,
                                    anchor="nw", width=text_w, tags=tags)
            bottom = c.bbox(preview)[3]
        h = bottom - y + pad
        chevron = c.create_text(right, body_y + px(9), font=(theme.ICONS, 7), fill=theme.FAINT, tags=tags,
                                text=theme.ICON_CHEVRON_UP if expanded else theme.ICON_CHEVRON)

        normal = self._row_image(width, h, fill, playing)
        lit = self._row_image(width, h, art.mix(fill, theme.PAPER, 0.06), playing)
        background = c.create_image(0, y, image=normal, anchor="nw", tags=tags)
        c.tag_lower(background)

        d = px(24)
        blank = art.photo(c, ("blank", d), lambda: Image.new("RGBA", (d, d), (0, 0, 0, 0)))
        circle = art.photo(c, ("remove", d), lambda: art.circle(d, theme.PRESSED))
        glyphs = [chevron]
        for n, (glyph_text, name, command, active) in enumerate(actions):
            tag = f"{name}-{key}"
            cx = right - n * px(26)
            spot = c.create_image(cx, meta_y, image=blank, tags=(hover, tag))
            glyph = c.create_text(cx, meta_y, text=glyph_text, font=(theme.ICONS, 8), fill=theme.FAINT,
                                  tags=(hover, tag))
            glyphs.append(glyph)
            self._bind(tag, "<Enter>", lambda _e, s=spot, g=glyph, a=active: (
                c.itemconfigure(s, image=circle), c.itemconfigure(g, fill=a), c.configure(cursor="hand2")))
            self._bind(tag, "<Leave>", lambda _e, s=spot: (c.itemconfigure(s, image=blank), c.configure(cursor="")))
            self._bind(tag, "<ButtonRelease-1>", lambda _e, f=command: f())

        def light(on):
            c.itemconfigure(background, image=lit if on else normal)
            for glyph in glyphs:
                c.itemconfigure(glyph, fill=theme.MUTED if on else theme.FAINT)

        self._bind(hover, "<Enter>", lambda _e: light(True))
        self._bind(hover, "<Leave>", lambda _e: light(False))
        self._bind(body, "<ButtonRelease-1>", lambda _e: self.toggle_row(key))
        return h

    def _place_well(self, key, text, x, y, width, fill) -> int:
        """Embeds the read-only, selectable full text of an expanded row; returns its height."""
        px, c = self.px, self.list_canvas
        well = art.mix(fill, theme.NIGHT, 0.55)
        font, pad_x, pad_y, bar = self.font(9), px(theme.S2), px(6), px(10)
        probe = c.create_text(0, 0, text=text, font=font, width=width - 2 * pad_x - bar, anchor="nw")
        _, top, _, bottom = c.bbox(probe)
        c.delete(probe)
        h = min(bottom - top + 2 * pad_y, px(theme.EXPANDED_MAX))
        entry = self._wells.get(key)
        if entry is None:
            frame = tk.Frame(c, bg=well)
            box = tk.Text(frame, wrap="word", bg=well, fg=theme.PAPER, font=font, relief="flat", bd=0,
                          highlightthickness=0, padx=pad_x, pady=pad_y, cursor="xterm", insertwidth=0,
                          selectbackground=theme.PRESSED, selectforeground=theme.PAPER,
                          inactiveselectbackground=theme.PRESSED, takefocus=0)
            scrollbar = ThinScrollbar(frame, box.yview, px=px)
            box.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")
            box.pack(side="left", fill="both", expand=True)
            box.insert("1.0", text)
            box.configure(state="disabled")  # still selectable; Ctrl+C copies
            box.bind("<MouseWheel>", lambda e, b=box: self._on_text_wheel(b, e))
            # Text reports its view only when it scrolls; show the bar as soon as the text is laid out
            box.bind("<Configure>", lambda _e, b=box, s=scrollbar: b.after_idle(self._sync_bar, b, s))
            entry = self._wells[key] = (frame, box, scrollbar)
        for widget in entry:
            widget.configure(bg=well)
        c.create_window(x, y, window=entry[0], anchor="nw", width=width, height=h)
        return h

    @staticmethod
    def _sync_bar(box, scrollbar):
        if box.winfo_exists():
            box.count("1.0", "end", "update", "ypixels")  # line heights are otherwise measured lazily
            scrollbar.set(*box.yview())

    def _row_image(self, width, height, fill, playing):
        px = self.px
        return art.photo(self.list_canvas, ("row", width, height, fill, playing),
                         lambda: art.rounded(width, height, px(theme.RADIUS_LG), fill,
                                             accent=theme.SAFFRON if playing else None, accent_width=px(3)))

    @staticmethod
    def _clock(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp).strftime("%H:%M")

    def toggle_row(self, key: str):
        self.expanded ^= {key}
        if self._render_list(self._status()):
            self._place_panel()

    def toggle_history(self):
        self.history_open = not self.history_open
        if self._render_list(self._status()):
            self._place_panel()

    # ---------- wheel ----------
    def _on_panel_wheel(self, event):
        steps = self._scroll_wheel.feed(event.delta)
        if steps:
            self.list_canvas.yview_scroll(-steps, "units")

    def _on_text_wheel(self, box, event):
        """Scrolls the expanded text under the pointer; at its top or bottom the panel scrolls instead."""
        steps = self._scroll_wheel.feed(event.delta)
        if steps:
            if wheel_target(*box.yview(), -steps) == "inner":
                box.yview_scroll(-steps * TEXT_LINES_PER_NOTCH, "units")
            else:
                self.list_canvas.yview_scroll(-steps, "units")
        return "break"

    def _on_bubble_wheel(self, event):
        """While speaking or paused each notch changes the speed; otherwise the wheel only shows it."""
        steps = self._speed_wheel.feed(event.delta)
        if self._status() not in (pl.SPEAKING, pl.PAUSED):
            self._show_badge(speed_text(self.player.speed), active=False)
            return
        if steps:
            self.change_speed(steps, badge=True)

    def _on_chip_wheel(self, delta):
        steps = self._speed_wheel.feed(delta)
        if steps:
            self.change_speed(steps)

    def _place_panel(self):
        self.panel.update_idletasks()
        w, h = self.panel_width, self.panel.winfo_reqheight()
        bx, by = self.root.winfo_x(), self.root.winfo_y()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = bx - w - self.px(8) if bx + self.size + w + self.px(8) > sw else bx + self.size + self.px(8)
        y = min(max(by + self.size - h, self.px(8)), sh - h - self.px(8))
        self.panel.geometry(f"{w}x{h}+{max(x, 0)}+{y}")

    def toggle_panel(self):
        if self.panel.winfo_viewable():
            self.hide_panel()
        else:
            self._render_panel()
            self.panel.deiconify()
            self.panel.lift()
            self.panel.focus_force()

    def hide_panel(self):
        self.panel.withdraw()

    # ---------- speed ----------
    def change_speed(self, steps: int, badge: bool = False) -> float:
        """One nudge_speed(±1) per notch; the badge near the bubble says "max"/"min" at the ends."""
        value = start = self.player.speed
        limit = None
        for _ in range(abs(steps)):
            before = value
            value = self.player.nudge_speed(1 if steps > 0 else -1)
            if abs(value - before) < 1e-9:
                limit = "max" if steps > 0 else "min"
                break
        if badge:
            self._show_badge(speed_text(value, limit))
        if abs(value - start) >= 1e-9:
            self._speed_changed(value)
        return value

    def reset_speed(self):
        value = self.player.set_speed(1.0)
        self._speed_changed(value)

    def _speed_changed(self, value: float):
        self.speed_chip.set(speed_text(value), accent=abs(value - 1.0) > 1e-6)
        self._sync_settings(value)
        self._speed_save.trigger()

    def _save_speed(self):
        value = round(self.player.speed, 2)
        try:
            cfg.update("voice", {"speed": value})
        except (OSError, ValueError) as error:
            message = f"Could not save the reading speed (voice.speed) to config.json: {error}"
            print(message)
            self.warn(message)
            return
        print(f"speed saved: {value}")
        self._sync_settings(value, saved=True)

    def _sync_settings(self, value: float, saved: bool = False):
        from .settings_window import SettingsWindow

        window = SettingsWindow.instance
        if window is not None and window.top.winfo_exists():
            window.show_speed(value, saved)

    def _show_badge(self, text: str, active: bool = True):
        """Shows the speed briefly next to the bubble."""
        px = self.px
        if self.badge is None:
            self.badge = tk.Toplevel(self.root, bg=theme.SURFACE)
            self.badge.withdraw()
            self.badge.overrideredirect(True)
            self.badge.attributes("-topmost", True)
            if not theme.window_chrome(self.badge, popup=True):
                self.badge.configure(highlightthickness=1, highlightbackground=theme.LINE)
            tk.Label(self.badge, text="SPEED", bg=theme.SURFACE, fg=theme.MUTED,
                     font=self.font(7, family=theme.LABEL)).pack(side="left", padx=(px(10), 0), pady=px(5))
            self.badge_value = tk.Label(self.badge, bg=theme.SURFACE, font=(theme.MONO, 10))
            self.badge_value.pack(side="left", padx=(px(6), px(10)), pady=px(5))
        self.badge_value.configure(text=text, fg=theme.SAFFRON if active else theme.PAPER)
        self.badge.update_idletasks()
        w, h = self.badge.winfo_reqwidth(), self.badge.winfo_reqheight()
        bx, by = self.root.winfo_x(), self.root.winfo_y()
        x = min(max(bx + (self.size - w) // 2, 0), self.root.winfo_screenwidth() - w)
        y = by - h + px(4) if by - h + px(4) >= 0 else by + self.size - px(4)
        self.badge.geometry(f"+{x}+{y}")
        self.badge.deiconify()
        self.badge.lift()
        if self._badge_job is not None:
            self.root.after_cancel(self._badge_job)
        self._badge_job = self.root.after(SPEED_BADGE_MS, self._hide_badge)

    def _hide_badge(self):
        self._badge_job = None
        if self.badge is not None:
            self.badge.withdraw()

    # ---------- history ----------
    def replay(self, item_id: int):
        """Queues a history item again; a refusal flashes the bubble red and says why in the panel."""
        try:
            item = self.queue.replay(item_id)
        except Rejected as error:
            item, reason = None, f"Not queued again: {error}."
            print(f"not replayed: {error}")
        else:
            reason = "Not queued again: the text is already in the queue."
            print(f"replayed history #{item_id} as #{item.id}" if item else "not replayed: already queued")
        self._flash = "ok" if item else "reject"
        self._flash_start = time.monotonic()
        if item is None:
            self._notify(reason, "warning")

    def copy_text(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._notify("Copied to the clipboard.", "ok")

    def clear_history(self):
        self.queue.clear_history()

    def _notify(self, text: str, kind: str = "ok"):
        """A short message above the panel buttons; it goes away by itself."""
        self._notice = (text, kind, time.monotonic() + NOTICE_SECONDS)
        self._seen = None

    # ---------- actions ----------
    def add_from_clipboard(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            text = ""
        try:
            item = self.queue.add(text)
        except Rejected as error:
            item = None
            print(f"not added: {error}")
        else:
            print(f"queued #{item.id} ({len(item.text)} chars)" if item
                  else "not added: clipboard is empty or the text is already queued")
        self._flash = "ok" if item else "reject"
        self._flash_start = time.monotonic()

    def toggle_pause(self):
        self.player.toggle_pause()

    def skip_current(self):
        for item in self.queue.snapshot():
            if item.state == PLAYING:
                self.queue.remove(item.id)

    def open_settings(self):
        from .settings_window import SettingsWindow  # imported lazily: settings_window imports from here

        SettingsWindow.show(self)

    def quit(self):
        self._speed_save.flush()
        self._save_position()
        self.root.destroy()

    # ---------- loop ----------
    def _tick(self):
        while True:
            try:
                event = self.events.get_nowait()
            except stdqueue.Empty:
                break
            if event == "clipboard":
                self.add_from_clipboard()
            elif event == "pause":
                self.toggle_pause()
            elif event == "skip":
                self.skip_current()
            elif event == "panel":
                self.toggle_panel()
            elif event == "clear":
                self.queue.clear()

        seen = (self.queue.version, self.player.status, self.player.paused, self.player.speech_error,
                self.player.speed)
        changed = seen != self._seen
        self._seen = seen
        if self._notice and time.monotonic() > self._notice[2]:
            self._notice = None
            changed = True
        animating = self.player.status in (pl.SPEAKING, pl.LOADING) and not self.player.paused
        flashing = False
        if self._flash:
            limit = art.OK_SECONDS if self._flash == "ok" else art.REJECT_SECONDS
            flashing = time.monotonic() - self._flash_start < limit
            if not flashing:
                self._flash = None
                changed = True
        if changed or animating or flashing:
            self._draw_bubble()
        if changed and self.panel.winfo_viewable():
            self._render_panel()
        self.root.after(FRAME_MS if animating or flashing else IDLE_MS, self._tick)

    def run(self):
        self.root.mainloop()
