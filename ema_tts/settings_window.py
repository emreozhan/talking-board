"""Ses ayarları penceresi. Değişiklikler oynatıcıya hemen uygulanır, Kaydet ile config.json'a yazılır."""

import ctypes
import sys
import tkinter as tk
from pathlib import Path

from . import config as cfg
from . import player as pl
from .speech_queue import PLAYING
from .ui import BG, FG, MUTED, ROW_BG

SAMPLE_TEXT = "Merhaba! Bu, seçtiğin ayarlarla yapılmış kısa bir deneme okumasıdır."
SPEED_MIN, SPEED_MAX, SPEED_STEP = 0.5, 2.0, 0.05
SAMPLE_RATES = (48000, 24000, 16000)
MAX_CHARS_MIN, MAX_CHARS_MAX = 50, 1000
MAX_SEED = 2**31 - 1
RANDOM = "Rastgele"
CUSTOM = "Özel sayı"
# Ölçüm: seed perdeyi cümleden bağımsız olarak kaydırır (16 ≈ -1.2, 1 ≈ 0, 23 ≈ +0.8 yarım ton)
SEED_PRESETS = {"Pes": 16, "Doğal": 1, "Tiz": 23}
DEFAULT_DEVICE = "Sistem varsayılanı"
ERROR_FG = "#fca5a5"
OK_FG = "#86efac"
ACCENT = "#2563eb"


def save_voice(voice: dict, path: Path | None = None) -> dict:
    """config.json'da yalnızca voice bölümünü değiştirir; diğer bölümler olduğu gibi kalır."""
    path = path or cfg.CONFIG_FILE
    config = cfg.load(path)
    config["voice"] = {**config["voice"], **voice}
    cfg.save(config, path)
    return config


def seed_choice(seed) -> tuple[str, str]:
    """seed değerini (açılır liste seçimi, sayı kutusu metni) çiftine çevirir."""
    if seed is None:
        return RANDOM, ""
    for name, value in SEED_PRESETS.items():
        if seed == value:
            return name, str(value)
    return CUSTOM, str(seed)


def parse_seed(choice: str, text: str) -> int | None:
    if choice == RANDOM:
        return None
    if choice in SEED_PRESETS:
        return SEED_PRESETS[choice]
    try:
        seed = int(text.strip())
    except ValueError:
        seed = -1
    if not 0 <= seed <= MAX_SEED:
        raise ValueError(f"voice.seed: 0 ile {MAX_SEED} arasında bir tam sayı girin.")
    return seed


def parse_max_chars(text: str) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        value = 0
    if not MAX_CHARS_MIN <= value <= MAX_CHARS_MAX:
        raise ValueError(f"voice.max_chars: {MAX_CHARS_MIN} ile {MAX_CHARS_MAX} arasında bir tam sayı girin.")
    return value


def _dark_title_bar(window: tk.Toplevel):
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


class SettingsWindow:
    instance = None

    @classmethod
    def show(cls, app) -> "SettingsWindow":
        """Tek örnek: pencere açıksa öne getirir."""
        window = cls.instance
        if window is not None and window.top.winfo_exists():
            window.top.deiconify()
            window.top.lift()
            window.top.focus_force()
            return window
        cls.instance = cls(app)
        return cls.instance

    def __init__(self, app):
        self.app = app
        self.player = app.player
        self.px, self.font = app.px, app.font
        try:
            self.saved = cfg.load()["voice"]
        except (OSError, ValueError):
            self.saved = dict(self.player.voice)
        self._loading = True

        self.top = tk.Toplevel(app.root, bg=BG)
        self.top.title("EMA TTS · Ses ayarları")
        self.top.resizable(False, False)
        self.top.attributes("-topmost", True)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _e: self.close())

        self.speed = tk.DoubleVar()
        self.volume = tk.IntVar()
        self.seed_choice = tk.StringVar()
        self.seed_text = tk.StringVar()
        self.device = tk.StringVar()
        self.sample_rate = tk.StringVar()
        self.max_chars = tk.StringVar()
        self.use_lightning = tk.BooleanVar()
        self._build()
        self._fill(self.player.voice)
        for var in (self.speed, self.volume, self.seed_choice, self.seed_text, self.device,
                    self.sample_rate, self.max_chars, self.use_lightning):
            var.trace_add("write", lambda *_: self._changed())
        self._loading = False
        self._changed()

        self.top.update_idletasks()
        _dark_title_bar(self.top)
        w, h = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        self.top.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")
        self.top.focus_force()

    # ---------- yapı ----------
    def _build(self):
        px = self.px
        tk.Label(self.top, text="Ses ayarları", bg=BG, fg=FG, font=self.font(12, "bold"),
                 anchor="w").pack(fill="x", padx=px(16), pady=(px(14), 0))
        tk.Label(self.top, text="Değişiklikler hemen duyulur; kalıcı olması için Kaydet'e bas.",
                 bg=BG, fg=MUTED, font=self.font(9), anchor="w").pack(fill="x", padx=px(16))

        form = tk.Frame(self.top, bg=BG)
        form.pack(fill="x", padx=px(16), pady=px(10))
        form.columnconfigure(1, weight=1)
        self._row = 0

        self.speed_label = self._value_label(form)
        self._field(form, "Hız", self._scale(form, self.speed, SPEED_MIN, SPEED_MAX, SPEED_STEP), self.speed_label)

        self.volume_label = self._value_label(form)
        self._field(form, "Ses seviyesi", self._scale(form, self.volume, 0, 100, 1), self.volume_label)

        seed_box = tk.Frame(form, bg=BG)
        self._option(seed_box, self.seed_choice, [RANDOM, *SEED_PRESETS, CUSTOM]).pack(side="left")
        self.seed_entry = self._entry(seed_box, self.seed_text, width=12)
        self._field(form, "Ses rengi", seed_box,
                    hint="Sabit seçenekler her okumada aynı tını ve tonlamayı verir; Pes/Tiz perdeyi hafifçe kaydırır.")

        self.device_menu = self._option(form, self.device, [DEFAULT_DEVICE])
        self.device_note = tk.Label(form, bg=BG, fg=MUTED, font=self.font(8), anchor="w", justify="left")
        self._field(form, "Çıkış cihazı", self.device_menu)
        self.device_note.grid(row=self._row, column=1, columnspan=2, sticky="w")
        self._row += 1

        self._field(form, "Örnekleme hızı", self._option(form, self.sample_rate, [f"{r} Hz" for r in SAMPLE_RATES]))

        chars_box = tk.Frame(form, bg=BG)
        self.max_chars_box = tk.Spinbox(chars_box, textvariable=self.max_chars, from_=MAX_CHARS_MIN,
                                        to=MAX_CHARS_MAX, increment=10, width=8, bg=ROW_BG, fg=FG,
                                        buttonbackground=ROW_BG, insertbackground=FG, relief="flat",
                                        font=self.font(9), highlightthickness=px(1),
                                        highlightbackground=ROW_BG, highlightcolor=ACCENT)
        self.max_chars_box.pack(side="left")
        tk.Label(chars_box, text="karakter", bg=BG, fg=MUTED, font=self.font(9)).pack(side="left", padx=px(6))
        self._field(form, "En uzun parça", chars_box, hint="Uzun metinler bu uzunlukta parçalara bölünerek okunur.")

        lightning = tk.Checkbutton(form, text="Hızlı yol (lightning, CUDA)", variable=self.use_lightning,
                                   bg=BG, fg=FG, selectcolor=ROW_BG, activebackground=BG, activeforeground=FG,
                                   font=self.font(9), bd=0, highlightthickness=0, anchor="w", cursor="hand2")
        self._field(form, "Model", lightning, hint="Yeniden başlatma gerekir.")

        self.status = tk.Label(self.top, bg=BG, fg=MUTED, font=self.font(9), anchor="w", justify="left",
                               wraplength=px(420))
        self.status.pack(fill="x", padx=px(16))

        buttons = tk.Frame(self.top, bg=BG)
        buttons.pack(fill="x", padx=px(16), pady=(px(8), px(14)))
        self.app._button(buttons, "Dene", self.try_voice).pack(side="left")
        self.app._button(buttons, "Varsayılana dön", self.reset).pack(side="left", padx=px(6))
        save = self.app._button(buttons, "Kaydet", self.save)
        save.configure(bg=ACCENT, activebackground="#1d4ed8", fg="#ffffff", activeforeground="#ffffff")
        save.pack(side="right")
        self.app._button(buttons, "Kapat", self.close).pack(side="right", padx=px(6))

    def _field(self, form, label, widget, extra=None, hint=None):
        px = self.px
        tk.Label(form, text=label, bg=BG, fg=FG, font=self.font(9), anchor="w").grid(
            row=self._row, column=0, sticky="w", padx=(0, px(14)), pady=(px(6), 0))
        widget.grid(row=self._row, column=1, sticky="w", pady=(px(6), 0))
        if extra is not None:
            extra.grid(row=self._row, column=2, sticky="w", padx=(px(8), 0), pady=(px(6), 0))
        self._row += 1
        if hint:
            tk.Label(form, text=hint, bg=BG, fg=MUTED, font=self.font(8), anchor="w", justify="left",
                     wraplength=px(330)).grid(row=self._row, column=1, columnspan=2, sticky="w")
            self._row += 1

    def _value_label(self, parent):
        return tk.Label(parent, bg=BG, fg=FG, font=self.font(9), width=6, anchor="w")

    def _scale(self, parent, var, start, end, step):
        return tk.Scale(parent, variable=var, from_=start, to=end, resolution=step, orient="horizontal",
                        showvalue=False, length=self.px(240), sliderlength=self.px(18), width=self.px(10),
                        bg=MUTED, fg=FG, troughcolor=ROW_BG, activebackground=ACCENT, bd=0,
                        highlightthickness=0, sliderrelief="flat", cursor="hand2")

    def _option(self, parent, var, values):
        menu = tk.OptionMenu(parent, var, *values)
        menu.configure(bg=ROW_BG, fg=FG, activebackground="#374151", activeforeground=FG, bd=0,
                       highlightthickness=0, relief="flat", font=self.font(9), anchor="w",
                       padx=self.px(8), pady=self.px(3), cursor="hand2")
        menu["menu"].configure(bg=ROW_BG, fg=FG, activebackground=ACCENT, activeforeground="#ffffff",
                               font=self.font(9), bd=0)
        return menu

    def _entry(self, parent, var, width):
        return tk.Entry(parent, textvariable=var, width=width, bg=ROW_BG, fg=FG, insertbackground=FG,
                        disabledbackground=BG, disabledforeground=MUTED, relief="flat", font=self.font(9),
                        highlightthickness=self.px(1), highlightbackground=ROW_BG, highlightcolor=ACCENT)

    def _set_devices(self, selected: str | None):
        try:
            names = pl.output_devices()
        except Exception as error:  # ses sürücüsü sorgulanamadı
            names, note = [], f"Cihazlar listelenemedi: {error}"
        else:
            note = ""
        if selected and selected not in names:
            names.append(selected)
            note = "Bu cihaz şu an bulunamadı; sistem varsayılanı kullanılıyor."
        menu = self.device_menu["menu"]
        menu.delete(0, "end")
        for name in [DEFAULT_DEVICE, *names]:
            menu.add_command(label=name, command=lambda n=name: self.device.set(n))
        self.device.set(selected or DEFAULT_DEVICE)
        self.device_note.configure(text=note, fg=ERROR_FG)
        if note:
            self.device_note.grid()
        else:
            self.device_note.grid_remove()

    def _fill(self, voice: dict):
        loading, self._loading = self._loading, True
        self.speed.set(voice["speed"])
        self.volume.set(voice["volume"])
        choice, text = seed_choice(voice["seed"])
        self.seed_choice.set(choice)
        self.seed_text.set(text)
        self._set_devices(voice["output_device"])
        self.sample_rate.set(f"{voice['sample_rate']} Hz")
        self.max_chars.set(str(voice["max_chars"]))
        self.use_lightning.set(bool(voice["use_lightning"]))
        self._loading = loading

    # ---------- form ----------
    def _read(self) -> dict:
        device = self.device.get()
        return {
            "speed": round(float(self.speed.get()), 2),
            "volume": int(self.volume.get()),
            "seed": parse_seed(self.seed_choice.get(), self.seed_text.get()),
            "output_device": None if device == DEFAULT_DEVICE else device,
            "sample_rate": int(self.sample_rate.get().split()[0]),
            "max_chars": parse_max_chars(self.max_chars.get()),
            "use_lightning": bool(self.use_lightning.get()),
        }

    def _mark(self, widget, bad: bool):
        widget.configure(highlightbackground="#ef4444" if bad else ROW_BG)

    def _changed(self) -> dict | None:
        """Formu okur, geçerliyse oynatıcıya uygular. Hata varsa None döner."""
        if self._loading:
            return None
        self.speed_label.configure(text=f"{self.speed.get():.2f}×")
        self.volume_label.configure(text=f"%{self.volume.get()}")
        choice = self.seed_choice.get()
        if choice in SEED_PRESETS and self.seed_text.get() != str(SEED_PRESETS[choice]):
            self.seed_text.set(str(SEED_PRESETS[choice]))  # trace bu fonksiyonu yeniden çağırır
            return self._changed()
        if choice == CUSTOM:
            self.seed_entry.pack(side="left", padx=(self.px(8), 0), fill="y")
        else:
            self.seed_entry.pack_forget()

        errors = []
        for widget, parse, args in ((self.seed_entry, parse_seed, (choice, self.seed_text.get())),
                                    (self.max_chars_box, parse_max_chars, (self.max_chars.get(),))):
            try:
                parse(*args)
                self._mark(widget, False)
            except ValueError as error:
                self._mark(widget, True)
                errors.append(str(error))
        if errors:
            self._status("\n".join(errors), ERROR_FG)
            return None

        voice = self._read()
        self.player.update(voice)
        self._status("Kaydedilmemiş değişiklikler var." if voice != self._saved_view() else "", MUTED)
        return voice

    def _saved_view(self) -> dict:
        return {key: self.saved.get(key) for key in cfg.DEFAULTS["voice"]}

    def _status(self, text: str, color: str):
        self.status.configure(text=text, fg=color)

    # ---------- eylemler ----------
    def try_voice(self):
        if self._changed() is None:
            return
        self.player.preview(SAMPLE_TEXT)
        if self.player.status == pl.ERROR:
            self._status("Model yüklenemediği için deneme okunamaz.", ERROR_FG)
        elif self.player.status == pl.LOADING:
            self._status("Model yükleniyor; deneme hazır olunca okunacak.", MUTED)
        elif any(item.state == PLAYING for item in self.app.queue.snapshot()):
            self._status("Deneme, okunan metin bitince çalınacak.", MUTED)
        else:
            self._status("Deneme okunuyor…", MUTED)

    def reset(self):
        self._fill(cfg.DEFAULTS["voice"])
        self._changed()

    def save(self):
        voice = self._changed()
        if voice is None:
            return
        try:
            save_voice(voice)
        except (OSError, ValueError) as error:
            self._status(f"config.json yazılamadı: {error}", ERROR_FG)
            return
        self.saved = voice
        note = " Hızlı yol değişikliği yeniden başlatınca geçerli olur." \
            if voice["use_lightning"] != self.player.lightning else ""
        self._status("Kaydedildi." + note, OK_FG)

    def close(self):
        SettingsWindow.instance = None
        self.top.destroy()
