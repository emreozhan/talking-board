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
from .widgets import Button, LayeredWindow, ThinScrollbar

APP_NAME = "Talking Board"
TRANSPARENT = "#ff00fe"  # colour key, only used when layered windows are unavailable
PREVIEW_CHARS = 160
FRAME_MS = 40  # while animating
IDLE_MS = 100
STATUS_LABELS = {pl.LOADING: "Loading model", pl.READY: "Ready", pl.SPEAKING: "Speaking",
                 pl.PAUSED: "Paused", pl.ERROR: "Error"}
STATUS_COLORS = {pl.LOADING: theme.MUTED, pl.READY: theme.PAPER, pl.SPEAKING: theme.SAFFRON,
                 pl.PAUSED: theme.SAFFRON, pl.ERROR: theme.POMEGRANATE_TEXT}


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
        self._seen = (None, None, None, None)
        self._flash = None
        self._flash_start = 0.0
        self._start = time.monotonic()
        self._hover = False
        self._drag = None
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
        self._seen = (None, None, None, None)  # redraw the panel

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
        self.panel.bind("<MouseWheel>", lambda e: self.list_canvas.yview_scroll(-e.delta // 120, "units"))

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

        lines = []
        if self.player.speech_error:
            lines.append(self.player.speech_error)
        elif self.player.status == pl.ERROR:
            lines.append(f"Model failed to load: {self.player.error.strip().splitlines()[-1]}")
        lines += self.warnings
        if lines:
            color = theme.POMEGRANATE_TEXT if self.player.status == pl.ERROR else theme.AMBER_TEXT
            self.message_icon.configure(text=theme.ICON_WARNING, fg=color)
            self.message_text.configure(text="\n".join(lines), fg=color)
            self.message.pack(fill="x", padx=self.px(theme.S4), pady=(self.px(theme.S2), 0),
                              before=self.actions)
        else:
            self.message.pack_forget()
        paused = self.player.paused
        self.pause_button.set("Resume" if paused else "Pause", theme.ICON_PLAY if paused else theme.ICON_PAUSE)

        content = self._render_rows(items, status)
        list_h = min(max(content, self.px(60)), self.px(360))
        self.list_canvas.configure(height=list_h, scrollregion=(0, 0, self.list_width, max(content, list_h)))
        self._place_panel()

    def _render_rows(self, items, status) -> int:
        """Draws the queue on the list canvas and returns the content height."""
        px, c, width = self.px, self.list_canvas, self.list_width
        c.delete("all")
        if not items:
            label = self.hotkey_label
            hint = ("Copy text and press " + label + ".\nAgent replies show up here too."
                    if label not in ("off", "invalid")
                    else "Copy text, then right-click the bubble\nand choose Add from clipboard.")
            c.create_text(width / 2, px(24), text=theme.ICON_SPEAKER, font=(theme.ICONS, 18), fill=theme.FAINT)
            c.create_text(width / 2, px(56), text="Queue is empty", font=self.font(11, family=theme.DISPLAY),
                          fill=theme.PAPER)
            c.create_text(width / 2, px(86), text=hint, font=self.font(9), fill=theme.MUTED, justify="center")
            return px(112)

        pad, y = px(theme.S3), 0
        for item in items:
            y += self._render_row(item, y, pad, width, status) + px(theme.S2)
        return y - px(theme.S2)

    def _render_row(self, item, y, pad, width, status) -> int:
        px, c = self.px, self.list_canvas
        playing = item.state == PLAYING
        row, remove = f"row{item.id}", f"remove{item.id}"
        x = pad + (px(3) if playing else 0)
        if item.source:
            icon, label, color = theme.ICON_AGENT, f"AGENT · {item.source}", theme.SKY
        else:
            icon, label, color = theme.ICON_PASTE, "CLIPBOARD", theme.MUTED
        meta_y = y + pad + px(6)
        c.create_text(x, meta_y, text=icon, font=(theme.ICONS, 8), fill=color, anchor="w", tags=row)
        c.create_text(x + px(15), meta_y, text=label, font=self.font(8, family=theme.LABEL), fill=color,
                      anchor="w", tags=row)
        if playing:
            right = {pl.PAUSED: "PAUSED", pl.ERROR: "FAILED"}.get(status, "SPEAKING")
            right_color = theme.POMEGRANATE_TEXT if status == pl.ERROR else theme.SAFFRON
            right_font = self.font(8, family=theme.DISPLAY)
        else:
            right, right_color, right_font = datetime.fromtimestamp(item.added).strftime("%H:%M"), theme.FAINT, \
                (theme.MONO, 8)
        c.create_text(width - pad - px(26), meta_y, text=right, font=right_font, fill=right_color, anchor="e",
                      tags=row)

        preview = " ".join(item.text.split())
        if len(preview) > PREVIEW_CHARS:
            preview = preview[:PREVIEW_CHARS].rstrip() + "…"
        text = c.create_text(x, meta_y + px(12), text=preview, font=self.font(9), fill=theme.PAPER, anchor="nw",
                             width=width - x - pad - px(8), tags=row)
        h = c.bbox(text)[3] - y + pad

        fill = theme.SAFFRON_TINT if playing else theme.SURFACE
        normal = self._row_image(width, h, fill, playing)
        hover = self._row_image(width, h, art.mix(fill, theme.PAPER, 0.06), playing)
        background = c.create_image(0, y, image=normal, anchor="nw", tags=row)
        c.tag_lower(background)

        d = px(24)
        cx, cy = width - pad - px(4), meta_y
        blank = art.photo(c, ("blank", d), lambda: Image.new("RGBA", (d, d), (0, 0, 0, 0)))
        circle = art.photo(c, ("remove", d), lambda: art.circle(d, theme.PRESSED))
        spot = c.create_image(cx, cy, image=blank, tags=(row, remove))
        glyph = c.create_text(cx, cy, text=theme.ICON_CLOSE, font=(theme.ICONS, 8), fill=theme.FAINT,
                              tags=(row, remove))

        c.tag_bind(row, "<Enter>", lambda _e: (c.itemconfigure(background, image=hover),
                                               c.itemconfigure(glyph, fill=theme.MUTED)))
        c.tag_bind(row, "<Leave>", lambda _e: (c.itemconfigure(background, image=normal),
                                               c.itemconfigure(glyph, fill=theme.FAINT)))
        c.tag_bind(remove, "<Enter>", lambda _e: (c.itemconfigure(spot, image=circle),
                                                  c.itemconfigure(glyph, fill=theme.POMEGRANATE_TEXT),
                                                  c.configure(cursor="hand2")), add="+")
        c.tag_bind(remove, "<Leave>", lambda _e: (c.itemconfigure(spot, image=blank), c.configure(cursor="")),
                   add="+")
        c.tag_bind(remove, "<ButtonRelease-1>", lambda _e, i=item.id: self.queue.remove(i))
        return h

    def _row_image(self, width, height, fill, playing):
        px = self.px
        return art.photo(self.list_canvas, ("row", width, height, fill, playing),
                         lambda: art.rounded(width, height, px(theme.RADIUS_LG), fill,
                                             accent=theme.SAFFRON if playing else None, accent_width=px(3)))

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

        seen = (self.queue.version, self.player.status, self.player.paused, self.player.speech_error)
        changed = seen != self._seen
        self._seen = seen
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
