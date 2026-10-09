"""Ekranda yüzen logo ve kuyruk paneli (tkinter)."""

import json
import math
import queue as stdqueue
import tkinter as tk
from pathlib import Path

from . import player as pl
from .speech_queue import PLAYING, SpeechQueue

TRANSPARENT = "#ff00fe"
STATUS_COLORS = {
    pl.LOADING: "#6b7280",
    pl.READY: "#1f2937",
    pl.SPEAKING: "#2563eb",
    pl.PAUSED: "#d97706",
    pl.ERROR: "#dc2626",
}
OK_RING = "#22c55e"
REJECT_RING = "#ef4444"
FONT = "Segoe UI"
BG = "#111827"
ROW_BG = "#1f2937"
FG = "#e5e7eb"
MUTED = "#9ca3af"
PREVIEW_CHARS = 160


class App:
    def __init__(self, speech_queue: SpeechQueue, player: pl.Player, hotkey_label: str, state_file: Path):
        self.queue = speech_queue
        self.player = player
        self.hotkey_label = hotkey_label
        self.state_file = state_file
        self.events: stdqueue.Queue[str] = stdqueue.Queue()  # başka thread'lerden gelen istekler

        self.root = tk.Tk()
        self.scale = self.root.winfo_fpixels("1i") / 96
        self.size = round(60 * self.scale)
        self._seen = (None, None, None)
        self._flash = None
        self._flash_ticks = 0
        self._phase = 0.0
        self._drag = None

        self._build_bubble()
        self._build_panel()
        self._build_menu()
        self.root.after(100, self._tick)

    # ---------- yardımcılar ----------
    def px(self, value: float) -> int:
        return round(value * self.scale)

    def font(self, size: int, weight: str = "normal"):
        return (FONT, size, weight)

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

    # ---------- logo ----------
    def _build_bubble(self):
        root = self.root
        root.title("EMA TTS")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=TRANSPARENT)
        root.attributes("-transparentcolor", TRANSPARENT)
        x, y = self._load_position()
        root.geometry(f"{self.size}x{self.size}+{x}+{y}")

        self.canvas = tk.Canvas(root, width=self.size, height=self.size, bg=TRANSPARENT,
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_menu)
        self._draw_bubble()

    def _draw_bubble(self):
        c, s = self.canvas, self.size
        c.delete("all")
        pad = self.px(5)
        status = self.player.status
        if self.player.paused and status != pl.ERROR:
            status = pl.PAUSED
        ring = {"ok": OK_RING, "reject": REJECT_RING}.get(self._flash, "#374151")
        c.create_oval(pad, pad, s - pad, s - pad, fill=STATUS_COLORS.get(status, "#1f2937"),
                      outline=ring, width=self.px(3))

        # ses dalgası çubukları
        heights = [0.35, 0.65, 1.0, 0.65, 0.35]
        speaking = status == pl.SPEAKING
        bar_w, gap = self.px(4), self.px(3)
        total = len(heights) * bar_w + (len(heights) - 1) * gap
        x0, mid, max_h = (s - total) / 2, s / 2, s * 0.36
        for i, base in enumerate(heights):
            h = base
            if speaking:
                h = 0.3 + 0.7 * abs(math.sin(self._phase + i * 0.9)) * (0.6 + 0.4 * base)
            elif status == pl.LOADING:
                h = 0.25 + 0.2 * abs(math.sin(self._phase + i * 0.6))
            half = max_h * h / 2
            x = x0 + i * (bar_w + gap)
            c.create_line(x + bar_w / 2, mid - half, x + bar_w / 2, mid + half,
                          fill="#ffffff", width=bar_w, capstyle=tk.ROUND)

        count = len(self.queue.snapshot())
        if count:
            r = self.px(10)
            cx, cy = s - r - self.px(1), r + self.px(1)
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#ef4444", outline=BG, width=self.px(2))
            c.create_text(cx, cy, text=str(count) if count < 100 else "99+", fill="#ffffff",
                          font=self.font(8, "bold"))

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

    # ---------- sağ tık menüsü ----------
    def _build_menu(self):
        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="Kuyruğu göster", command=self.toggle_panel)
        self.menu.add_command(label="Panodan ekle", command=self.add_from_clipboard)
        self.menu.add_command(label="Duraklat / Devam", command=self.toggle_pause)
        self.menu.add_command(label="Ayarlar…", command=self.open_settings)
        self.menu.add_separator()
        self.menu.add_command(label="Çıkış", command=self.quit)

    def _on_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    # ---------- panel ----------
    def _build_panel(self):
        self.panel = tk.Toplevel(self.root, bg=BG)
        self.panel.withdraw()
        self.panel.overrideredirect(True)
        self.panel.attributes("-topmost", True)
        self.panel.bind("<Escape>", lambda _e: self.hide_panel())
        width = self.px(380)
        self.panel_width = width

        header = tk.Frame(self.panel, bg=BG)
        header.pack(fill="x", padx=self.px(12), pady=(self.px(10), self.px(4)))
        tk.Label(header, text="Seslendirme kuyruğu", bg=BG, fg=FG,
                 font=self.font(11, "bold")).pack(side="left")
        tk.Button(header, text="✕", command=self.hide_panel, bg=BG, fg=MUTED, bd=0,
                  activebackground=ROW_BG, activeforeground=FG, font=self.font(10),
                  cursor="hand2").pack(side="right")
        tk.Button(header, text="⚙", command=self.open_settings, bg=BG, fg=MUTED, bd=0,
                  activebackground=ROW_BG, activeforeground=FG, font=self.font(10),
                  cursor="hand2").pack(side="right", padx=(0, self.px(4)))

        self.status_label = tk.Label(self.panel, bg=BG, fg=MUTED, font=self.font(9), anchor="w")
        self.status_label.pack(fill="x", padx=self.px(12))

        actions = tk.Frame(self.panel, bg=BG)
        actions.pack(fill="x", padx=self.px(12), pady=self.px(8))
        self.pause_button = self._button(actions, "Duraklat", self.toggle_pause)
        self.pause_button.pack(side="left")
        self._button(actions, "Panodan ekle", self.add_from_clipboard).pack(side="left", padx=self.px(6))
        self._button(actions, "Tümünü sil", self.queue.clear).pack(side="right")

        body = tk.Frame(self.panel, bg=BG)
        body.pack(fill="both", expand=True, padx=(self.px(12), self.px(4)), pady=(0, self.px(12)))
        self.list_canvas = tk.Canvas(body, bg=BG, highlightthickness=0, width=width - self.px(36))
        scrollbar = tk.Scrollbar(body, orient="vertical", command=self.list_canvas.yview)
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.rows = tk.Frame(self.list_canvas, bg=BG)
        self.list_canvas.create_window((0, 0), window=self.rows, anchor="nw")
        self.rows.bind("<Configure>", lambda _e: self.list_canvas.configure(
            scrollregion=self.list_canvas.bbox("all")))
        self.panel.bind("<MouseWheel>", lambda e: self.list_canvas.yview_scroll(-e.delta // 120, "units"))

    def _button(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, bg=ROW_BG, fg=FG, bd=0,
                         padx=self.px(10), pady=self.px(4), activebackground="#374151",
                         activeforeground=FG, font=self.font(9), cursor="hand2")

    def _render_panel(self):
        status = pl.PAUSED if self.player.paused and self.player.status != pl.ERROR else self.player.status
        items = self.queue.snapshot()
        text = f"{status} · {len(items)} öğe · kısayol: {self.hotkey_label}"
        if self.player.status == pl.ERROR:
            text = f"Model yüklenemedi: {self.player.error.strip().splitlines()[-1]}"
        self.status_label.configure(text=text, fg="#fca5a5" if self.player.status == pl.ERROR else MUTED)
        self.pause_button.configure(text="Devam" if self.player.paused else "Duraklat")

        for child in self.rows.winfo_children():
            child.destroy()
        if not items:
            tk.Label(self.rows, text="Kuyruk boş. Metni kopyala ve kısayola bas.", bg=BG, fg=MUTED,
                     font=self.font(9), pady=self.px(16)).pack(anchor="w")
        wrap = self.panel_width - self.px(110)
        for item in items:
            row = tk.Frame(self.rows, bg=ROW_BG, padx=self.px(8), pady=self.px(6))
            row.pack(fill="x", pady=(0, self.px(6)))
            playing = item.state == PLAYING
            tk.Label(row, text="▶" if playing else "•", bg=ROW_BG,
                     fg="#60a5fa" if playing else MUTED, font=self.font(10)).pack(side="left", anchor="n")
            preview = " ".join(item.text.split())
            if len(preview) > PREVIEW_CHARS:
                preview = preview[:PREVIEW_CHARS].rstrip() + "…"
            tk.Label(row, text=preview, bg=ROW_BG, fg=FG, font=self.font(9), justify="left",
                     anchor="w", wraplength=wrap).pack(side="left", fill="x", expand=True, padx=self.px(6))
            tk.Button(row, text="✕", command=lambda i=item.id: self.queue.remove(i), bg=ROW_BG,
                      fg=MUTED, bd=0, activebackground="#374151", activeforeground="#fca5a5",
                      font=self.font(10), cursor="hand2").pack(side="right", anchor="n")

        self.rows.update_idletasks()
        list_h = min(max(self.rows.winfo_reqheight(), self.px(60)), self.px(360))
        self.list_canvas.configure(height=list_h)
        self._place_panel()

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

    # ---------- eylemler ----------
    def add_from_clipboard(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            text = ""
        item = self.queue.add(text)
        print(f"kuyruğa eklendi #{item.id} ({len(item.text)} karakter)" if item
              else "eklenmedi: pano boş ya da metin zaten kuyrukta")
        self._flash = "ok" if item else "reject"
        self._flash_ticks = 6

    def toggle_pause(self):
        self.player.toggle_pause()

    def open_settings(self):
        from .settings_window import SettingsWindow  # settings_window bu modülden import ediyor

        SettingsWindow.show(self)

    def quit(self):
        self._save_position()
        self.root.destroy()

    # ---------- döngü ----------
    def _tick(self):
        while True:
            try:
                event = self.events.get_nowait()
            except stdqueue.Empty:
                break
            if event == "clipboard":
                self.add_from_clipboard()

        seen = (self.queue.version, self.player.status, self.player.paused)
        changed = seen != self._seen
        self._seen = seen
        animating = self.player.status in (pl.SPEAKING, pl.LOADING) and not self.player.paused
        if animating:
            self._phase += 0.45
        flashing = self._flash_ticks > 0
        if flashing:
            self._flash_ticks -= 1
            if not self._flash_ticks:
                self._flash = None
        if changed or animating or flashing:
            self._draw_bubble()
        if changed and self.panel.winfo_viewable():
            self._render_panel()
        self.root.after(100, self._tick)

    def run(self):
        self.root.mainloop()
