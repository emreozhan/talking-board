"""Voice settings window. Changes apply to the player immediately; Save writes them to config.json."""

import tkinter as tk
from pathlib import Path

from . import config as cfg
from . import player as pl
from . import theme
from .speech_queue import PLAYING
from .widgets import Button, Dropdown, Slider, Toggle

# Read aloud by the Try button; Turkish because the TTS model only reads Turkish.
SAMPLE_TEXT = "Merhaba! Bu, seçtiğin ayarlarla yapılmış kısa bir deneme okumasıdır."
SPEED_MIN, SPEED_MAX, SPEED_STEP = 0.5, 2.0, 0.05
SAMPLE_RATES = (48000, 24000, 16000)
(MAX_CHARS_MIN, MAX_CHARS_MAX), MAX_CHARS_STEP = cfg.MAX_CHARS_RANGE, 10
MAX_SEED = cfg.MAX_SEED
RANDOM = "Random"
CUSTOM = "Custom number"
# Measured: the seed shifts the pitch regardless of the sentence (16 ≈ -1.2, 1 ≈ 0, 23 ≈ +0.8 semitones)
SEED_PRESETS = {"Low": 16, "Natural": 1, "High": 23}
DEFAULT_DEVICE = "System default"
ERROR_FG = theme.POMEGRANATE_TEXT
OK_FG = theme.MINT_TEXT
MUTED = theme.MUTED
LABEL_WIDTH = 116


def save_voice(voice: dict, path: Path | None = None) -> dict:
    """Changes only the voice section of config.json; other sections stay as they are."""
    return cfg.update("voice", voice, path or cfg.CONFIG_FILE)


def seed_choice(seed) -> tuple[str, str]:
    """Turns a seed into (dropdown choice, number field text)."""
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
        raise ValueError(f"voice.seed: enter a whole number between 0 and {MAX_SEED}.")
    return seed


def parse_max_chars(text: str) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        value = 0
    if not MAX_CHARS_MIN <= value <= MAX_CHARS_MAX:
        raise ValueError(f"voice.max_chars: enter a whole number between {MAX_CHARS_MIN} and {MAX_CHARS_MAX}.")
    return value


class SettingsWindow:
    instance = None

    @classmethod
    def show(cls, app) -> "SettingsWindow":
        """Single instance: brings the window to the front if it is already open."""
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

        self.top = tk.Toplevel(app.root, bg=theme.NIGHT)
        self.top.withdraw()
        self.top.title("Talking Board · Voice settings")
        self.top.resizable(False, False)
        self.top.attributes("-topmost", True)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _e: self.close())
        theme.set_icon(self.top)

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
        theme.window_chrome(self.top)
        w, h = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        self.top.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")
        self.top.deiconify()
        self.top.focus_force()

    # ---------- layout ----------
    def _build(self):
        px, bg = self.px, theme.NIGHT
        pad = px(theme.S5)
        tk.Label(self.top, text="Voice settings", bg=bg, fg=theme.PAPER, font=self.font(14, family=theme.DISPLAY),
                 anchor="w").pack(fill="x", padx=pad, pady=(px(theme.S4), 0))
        tk.Label(self.top, text="AI-generated speech · EMA Lightning", bg=bg, fg=theme.MUTED,
                 font=self.font(8, family=theme.LABEL), anchor="w").pack(fill="x", padx=pad)
        tk.Label(self.top, text="Changes are heard right away; press Save to keep them.",
                 bg=bg, fg=theme.MUTED, font=self.font(9), anchor="w").pack(fill="x", padx=pad, pady=(px(2), 0))

        form = tk.Frame(self.top, bg=bg)
        form.pack(fill="x", padx=pad, pady=(px(theme.S2), 0))
        form.columnconfigure(0, minsize=px(LABEL_WIDTH))
        form.columnconfigure(1, weight=1)
        self._row = 0

        self._section(form, "Voice")
        self.speed_label = self._value_label(form)
        self._field(form, "Speed", self._slider(form, self.speed, SPEED_MIN, SPEED_MAX, SPEED_STEP),
                    self.speed_label)
        self.volume_label = self._value_label(form)
        self._field(form, "Volume", self._slider(form, self.volume, 0, 100, 1), self.volume_label)

        seed_box = tk.Frame(form, bg=bg)
        Dropdown(seed_box, self.seed_choice, [RANDOM, *SEED_PRESETS, CUSTOM], px=px, width=150).pack(side="left")
        self.seed_entry = self._entry(seed_box, self.seed_text, width=12)
        self._field(form, "Voice colour", seed_box,
                    hint="Fixed choices give the same timbre and intonation on every reading; "
                         "Low and High shift the pitch slightly.")

        self._section(form, "Output")
        self.device_menu = Dropdown(form, self.device, [DEFAULT_DEVICE], px=px, width=280)
        self._field(form, "Output device", self.device_menu,
                    hint="If the chosen device goes missing, reading pauses instead of switching to the speakers.")
        self.device_note = tk.Label(form, bg=bg, fg=ERROR_FG, font=self.font(8), anchor="w", justify="left",
                                    wraplength=px(300))
        self.device_note.grid(row=self._row, column=1, columnspan=2, sticky="w", pady=(px(theme.S1), 0))
        self._row += 1
        self._field(form, "Sample rate",
                    Dropdown(form, self.sample_rate, [f"{r} Hz" for r in SAMPLE_RATES], px=px, width=150))

        self._section(form, "Reading")
        chars_box = tk.Frame(form, bg=bg)
        self.max_chars_box = self._entry(chars_box, self.max_chars, width=6)
        self.max_chars_box.pack(side="left", fill="y")
        for icon, step in ((theme.ICON_REMOVE, -MAX_CHARS_STEP), (theme.ICON_ADD, MAX_CHARS_STEP)):
            Button(chars_box, "", lambda s=step: self._step_max_chars(s), px=px, icon=icon, square=True,
                   takefocus=False).pack(side="left", padx=(px(theme.S1), 0))
        tk.Label(chars_box, text="characters", bg=bg, fg=theme.MUTED, font=self.font(9)).pack(
            side="left", padx=(px(theme.S2), 0))
        self._field(form, "Longest segment", chars_box, hint="Long texts are split into segments of this length.")
        self._field(form, "Model", Toggle(form, self.use_lightning, "Fast path (lightning, CUDA)", px=px),
                    hint="Needs a restart.")

        status = tk.Frame(self.top, bg=bg)
        status.pack(fill="x", padx=pad, pady=(px(theme.S3), 0))
        self.status_icon = tk.Label(status, bg=bg, font=(theme.ICONS, 9), width=2, anchor="w")
        self.status_icon.pack(side="left", anchor="n", pady=(px(2), 0))
        self.status = tk.Label(status, bg=bg, fg=theme.MUTED, font=self.font(9), anchor="w", justify="left",
                               wraplength=px(400))
        self.status.pack(side="left", fill="x")

        tk.Frame(self.top, bg=theme.LINE, height=1).pack(fill="x", pady=(px(theme.S3), 0))
        buttons = tk.Frame(self.top, bg=bg)
        buttons.pack(fill="x", padx=pad, pady=px(theme.S3))
        Button(buttons, "Try", self.try_voice, px=px, icon=theme.ICON_PLAY).pack(side="left")
        Button(buttons, "Reset to defaults", self.reset, px=px, kind="ghost", icon=theme.ICON_RESET).pack(
            side="left", padx=(px(theme.S2), 0))
        Button(buttons, "Save", self.save, px=px, kind="primary").pack(side="right")
        Button(buttons, "Close", self.close, px=px, kind="ghost").pack(side="right", padx=(0, px(theme.S2)))

    def _section(self, form, title):
        px = self.px
        head = tk.Frame(form, bg=theme.NIGHT)
        head.grid(row=self._row, column=0, columnspan=3, sticky="ew",
                  pady=(px(theme.S4) if self._row else px(theme.S3), 0))
        tk.Label(head, text=title.upper(), bg=theme.NIGHT, fg=theme.SAFFRON,
                 font=self.font(8, family=theme.DISPLAY)).pack(side="left")
        tk.Frame(head, bg=theme.LINE, height=1).pack(side="left", fill="x", expand=True, padx=(px(theme.S2), 0))
        self._row += 1

    def _field(self, form, label, widget, extra=None, hint=None):
        px = self.px
        tk.Label(form, text=label, bg=theme.NIGHT, fg=theme.PAPER, font=self.font(9), anchor="w").grid(
            row=self._row, column=0, sticky="w", padx=(0, px(theme.S3)), pady=(px(6), 0))
        widget.grid(row=self._row, column=1, sticky="w", pady=(px(6), 0))
        if extra is not None:
            extra.grid(row=self._row, column=2, sticky="e", padx=(px(theme.S2), 0), pady=(px(6), 0))
        self._row += 1
        if hint:
            tk.Label(form, text=hint, bg=theme.NIGHT, fg=theme.FAINT, font=self.font(8), anchor="w",
                     justify="left", wraplength=px(340)).grid(row=self._row, column=1, columnspan=2, sticky="w",
                                                              pady=(px(2), 0))
            self._row += 1

    def _value_label(self, parent):
        return tk.Label(parent, bg=theme.NIGHT, fg=theme.PAPER, font=(theme.MONO, 9), width=6, anchor="e")

    def _slider(self, parent, var, start, end, step):
        return Slider(parent, var, start, end, step, px=self.px, length=240)

    def _entry(self, parent, var, width):
        return tk.Entry(parent, textvariable=var, width=width, bg=theme.SURFACE, fg=theme.PAPER,
                        insertbackground=theme.SAFFRON, disabledbackground=theme.NIGHT,
                        disabledforeground=theme.FAINT, selectbackground=theme.RAISED, selectforeground=theme.PAPER,
                        relief="flat", bd=self.px(6), font=(theme.MONO, 9), highlightthickness=self.px(1),
                        highlightbackground=theme.LINE, highlightcolor=theme.SAFFRON)

    def _step_max_chars(self, step: int):
        try:
            value = int(self.max_chars.get().strip()) + step
        except ValueError:
            value = MAX_CHARS_MIN
        self.max_chars.set(str(min(max(value, MAX_CHARS_MIN), MAX_CHARS_MAX)))

    def _set_devices(self, selected: str | None):
        try:
            names = pl.output_devices()
        except Exception as error:  # the audio driver could not be queried
            names, note = [], f"Could not list devices: {error}"
        else:
            note = ""
        if selected and selected not in names:
            names.append(selected)
            note = "This device is not available right now; reading pauses until it is back or you pick another."
        menu = self.device_menu.menu
        menu.delete(0, "end")
        for name in [DEFAULT_DEVICE, *names]:
            menu.add_command(label=name, command=lambda n=name: self.device.set(n))
        self.device.set(selected or DEFAULT_DEVICE)
        self.device_note.configure(text=note)
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
        widget.configure(highlightbackground=theme.POMEGRANATE if bad else theme.LINE,
                         highlightcolor=theme.POMEGRANATE if bad else theme.SAFFRON)

    def _changed(self) -> dict | None:
        """Reads the form and applies it to the player if valid. Returns None on errors."""
        if self._loading:
            return None
        self.speed_label.configure(text=f"{self.speed.get():.2f}×")
        self.volume_label.configure(text=f"{self.volume.get()} %")
        choice = self.seed_choice.get()
        if choice in SEED_PRESETS and self.seed_text.get() != str(SEED_PRESETS[choice]):
            self.seed_text.set(str(SEED_PRESETS[choice]))  # the trace calls this function again
            return self._changed()
        if choice == CUSTOM:
            self.seed_entry.pack(side="left", padx=(self.px(theme.S2), 0), fill="y")
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
        self.player.set_speed(voice["speed"])  # heard while a text is being read, not only from the next one
        self.player.update(voice)
        self._status("You have unsaved changes." if voice != self._saved_view() else "", MUTED)
        return voice

    def _saved_view(self) -> dict:
        return {key: self.saved.get(key) for key in cfg.DEFAULTS["voice"]}

    def _status(self, text: str, color: str):
        icon = {ERROR_FG: theme.ICON_WARNING, OK_FG: theme.ICON_OK}.get(color, theme.ICON_INFO if text else "")
        self.status_icon.configure(text=icon, fg=color)
        self.status.configure(text=text, fg=color)

    def show_speed(self, value: float, saved: bool = False):
        """Follows a speed change made with the mouse wheel; saved=True once it is in config.json."""
        if saved:
            self.saved = {**self.saved, "speed": value}
        if abs(float(self.speed.get()) - value) > 1e-9:
            self.speed.set(value)  # the trace refreshes the label and the status
        elif saved:
            self._changed()

    # ---------- actions ----------
    def try_voice(self):
        if self._changed() is None:
            return
        self.player.preview(SAMPLE_TEXT)
        if self.player.status == pl.ERROR and not self.player.speech_error:
            self._status("The model failed to load, so the sample cannot be read.", ERROR_FG)
        elif self.player.status == pl.LOADING:
            self._status("The model is loading; the sample will play when it is ready.", MUTED)
        elif any(item.state == PLAYING for item in self.app.queue.snapshot()):
            self._status("The sample will play after the current text.", MUTED)
        else:
            self._status("Playing the sample…", MUTED)

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
            self._status(f"Could not write config.json: {error}", ERROR_FG)
            return
        self.saved = voice
        note = " The fast path change takes effect after a restart." \
            if voice["use_lightning"] != self.player.lightning else ""
        self._status("Saved." + note, OK_FG)

    def close(self):
        SettingsWindow.instance = None
        self.top.destroy()
