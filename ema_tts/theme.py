"""Visual identity: "Night broadcast".

A deep navy night with one saffron signal light. The bubble is a small on-air lamp: navy and
quiet while idle, saffron while speaking, so it is easy to forget until it has something to say.

Palette     navy surfaces (NIGHT < SURFACE < RAISED < LINE), warm paper text (PAPER, MUTED, FAINT),
            one accent (SAFFRON) for "speaking" and the primary action, POMEGRANATE for errors and
            removal, MINT for success, SKY for agent-sent items.
Type        Bahnschrift (DIN-like signage) for titles, labels and the badge; Segoe UI Variable Text
            for reading text; Cascadia Mono for keys and numbers; Segoe Fluent Icons for glyphs.
Radii       RADIUS_SM 4 (tags, keycaps), RADIUS_MD 8 (buttons, fields), RADIUS_LG 10 (queue rows).
Spacing     4-point scale: S1 4, S2 8, S3 12, S4 16, S5 24 (logical px; scale with App.px).
States      never by colour alone: each bubble state has its own glyph or motion (see art.logo).
"""

import ctypes
import sys
from pathlib import Path

# ---------- palette ----------
NIGHT = "#0E1424"  # window background
SURFACE = "#161E31"  # rows, fields, secondary buttons
RAISED = "#1F2942"  # hover
PRESSED = "#273352"
LINE = "#2A3552"  # borders, separators, tracks
PAPER = "#F2EDE3"  # primary text
MUTED = "#939BB0"  # secondary text
FAINT = "#5E6882"  # tertiary text, idle glyphs
SAFFRON = "#F5B53F"  # accent: speaking, primary action
SAFFRON_HOVER = "#FFC65C"
SAFFRON_PRESSED = "#DE9F2B"
SAFFRON_TINT = "#2A2A2E"  # playing row background
POMEGRANATE = "#EF5A5F"  # error, remove
POMEGRANATE_TEXT = "#FF9EA1"
MINT = "#46D39A"  # success
MINT_TEXT = "#8BE6BE"
AMBER_TEXT = "#F5CF7D"  # warnings
SKY = "#8FB3FF"  # items sent by agents

# ---------- type ----------
DISPLAY = "Bahnschrift SemiBold"
LABEL = "Bahnschrift"
BODY = "Segoe UI Variable Text"
BODY_STRONG = "Segoe UI Variable Text Semibold"
MONO = "Cascadia Mono"
ICONS = "Segoe Fluent Icons"
# Pillow needs files; the first one that exists wins
BADGE_FONTS = ("bahnschrift.ttf", "segoeuib.ttf", "arialbd.ttf")

# Segoe Fluent Icons code points
ICON_SETTINGS = ""
ICON_CLOSE = ""
ICON_PAUSE = ""
ICON_PLAY = ""
ICON_PASTE = ""
ICON_CLEAR = ""
ICON_WARNING = ""
ICON_OK = ""
ICON_INFO = ""
ICON_CHEVRON = ""
ICON_ADD = ""
ICON_REMOVE = ""
ICON_AGENT = ""
ICON_SPEAKER = ""
ICON_RESET = ""

# ---------- shape ----------
RADIUS_SM = 4
RADIUS_MD = 8
RADIUS_LG = 10
S1, S2, S3, S4, S5 = 4, 8, 12, 16, 24
BUBBLE = 60  # logical size of the floating bubble window
PANEL_WIDTH = 380

# kind: (background, hover, pressed, text)
BUTTONS = {
    "primary": (SAFFRON, SAFFRON_HOVER, SAFFRON_PRESSED, NIGHT),
    "secondary": (SURFACE, RAISED, PRESSED, PAPER),
    "ghost": (NIGHT, SURFACE, RAISED, MUTED),
    "danger": (NIGHT, SURFACE, RAISED, MUTED),  # turns pomegranate on hover
}

ICON_FILE = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"


def menu_options(font) -> dict:
    """tk.Menu colours; Windows still draws the outer frame itself."""
    return {"bg": SURFACE, "fg": PAPER, "activebackground": RAISED, "activeforeground": PAPER,
            "selectcolor": SAFFRON, "disabledforeground": FAINT, "font": font, "bd": 0,
            "activeborderwidth": 0, "relief": "flat", "tearoff": False}


def _dwm(hwnd: int, attribute: int, value: int) -> bool:
    data = ctypes.c_int(value)
    return ctypes.windll.dwmapi.DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd), attribute, ctypes.byref(data), ctypes.sizeof(data)) == 0


def _colorref(color: str) -> int:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return r | g << 8 | b << 16


def window_chrome(window, popup: bool = False) -> bool:
    """Windows 11: dark title bar in the theme colours, rounded corners and a thin border.

    Returns False where DWM does not support it (older Windows); callers then draw their own border.
    """
    if sys.platform != "win32":
        return False
    try:
        window.update_idletasks()
        hwnd = int(window.wm_frame(), 16)
        _dwm(hwnd, 20, 1)  # DWMWA_USE_IMMERSIVE_DARK_MODE
        rounded = _dwm(hwnd, 33, 2 if not popup else 3)  # DWMWA_WINDOW_CORNER_PREFERENCE: round / small
        _dwm(hwnd, 34, _colorref(LINE))  # DWMWA_BORDER_COLOR
        if not popup:
            _dwm(hwnd, 35, _colorref(NIGHT))  # DWMWA_CAPTION_COLOR
            _dwm(hwnd, 36, _colorref(PAPER))  # DWMWA_TEXT_COLOR
        return rounded
    except (AttributeError, OSError, ValueError):
        return False


def set_icon(window):
    if ICON_FILE.exists():
        try:
            window.iconbitmap(default=str(ICON_FILE))
        except Exception:  # tk.TclError: unsupported icon format on this platform
            pass
