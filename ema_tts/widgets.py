"""Themed tk widgets drawn on canvases, plus a per-pixel-alpha window for the bubble."""

import ctypes
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

import numpy as np

from . import art, theme


class LayeredWindow:
    """Shows an RGBA image as the whole window with UpdateLayeredWindow (Windows only).

    Unlike -transparentcolor this keeps anti-aliased edges and soft shadows. Fully transparent
    pixels let clicks through; Tk still receives mouse events on the rest.
    """

    def __init__(self, window: tk.Misc):
        if sys.platform != "win32":
            raise OSError("layered windows need Windows")
        self.user32, self.gdi32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32")
        self._declare()
        window.update_idletasks()
        self.hwnd = int(window.wm_frame(), 16)
        style = self.user32.GetWindowLongPtrW(self.hwnd, -20)  # GWL_EXSTYLE
        self.user32.SetWindowLongPtrW(self.hwnd, -20, style | 0x00080000)  # WS_EX_LAYERED

    def _declare(self):
        u, g = self.user32, self.gdi32
        u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.GetDC.argtypes = [wintypes.HWND]
        u.GetDC.restype = wintypes.HDC
        u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        u.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.POINTER(wintypes.SIZE),
                                          wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
                                          ctypes.POINTER(_Blend), wintypes.DWORD]
        u.UpdateLayeredWindow.restype = wintypes.BOOL
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BitmapInfo), wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g.DeleteDC.argtypes = [wintypes.HDC]

    def show(self, image) -> bool:
        width, height = image.size
        pixels = np.asarray(image.convert("RGBA"), dtype=np.uint16)
        alpha = pixels[..., 3:4]
        rgb = (pixels[..., :3] * alpha + 127) // 255  # premultiplied
        data = np.dstack((rgb[..., 2], rgb[..., 1], rgb[..., 0], alpha[..., 0])).astype(np.uint8).tobytes()

        info = _BitmapInfo()
        info.header.size = ctypes.sizeof(_BitmapHeader)
        info.header.width, info.header.height = width, -height  # top-down rows
        info.header.planes, info.header.bit_count = 1, 32
        screen = self.user32.GetDC(None)
        memory = self.gdi32.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        bitmap = self.gdi32.CreateDIBSection(memory, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        try:
            if not bitmap:
                return False
            ctypes.memmove(bits, data, len(data))
            old = self.gdi32.SelectObject(memory, bitmap)
            blend = _Blend(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
            ok = self.user32.UpdateLayeredWindow(self.hwnd, screen, None, ctypes.byref(wintypes.SIZE(width, height)),
                                                 memory, ctypes.byref(wintypes.POINT(0, 0)), 0,
                                                 ctypes.byref(blend), 2)  # ULW_ALPHA
            self.gdi32.SelectObject(memory, old)
            return bool(ok)
        finally:
            if bitmap:
                self.gdi32.DeleteObject(bitmap)
            self.gdi32.DeleteDC(memory)
            self.user32.ReleaseDC(None, screen)


class _BitmapHeader(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
                ("planes", wintypes.WORD), ("bit_count", wintypes.WORD), ("compression", wintypes.DWORD),
                ("size_image", wintypes.DWORD), ("x_ppm", wintypes.LONG), ("y_ppm", wintypes.LONG),
                ("clr_used", wintypes.DWORD), ("clr_important", wintypes.DWORD)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("header", _BitmapHeader), ("colors", wintypes.DWORD * 1)]


class _Blend(ctypes.Structure):
    _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte),
                ("format", ctypes.c_ubyte)]


def _measure(widget, font, text: str) -> int:
    return tkfont.Font(root=widget._root(), font=font).measure(text)


class _CanvasControl(tk.Canvas):
    """Base: a canvas on the parent's background with hover, press and keyboard focus state."""

    def __init__(self, parent, px, takefocus=True, cursor="hand2"):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, cursor=cursor,
                         takefocus=1 if takefocus else 0)
        self.px = px
        self.hover = self.pressed = self.focused = False
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        self.bind("<FocusIn>", lambda _e: self._set(focused=True))
        self.bind("<FocusOut>", lambda _e: self._set(focused=False))

    def _set(self, **state):
        for key, value in state.items():
            setattr(self, key, value)
        self.redraw()

    def redraw(self):
        raise NotImplementedError

    def _trace(self, variable):
        name = variable.trace_add("write", lambda *_: self.redraw())

        def forget(_event):
            try:
                variable.trace_remove("write", name)
            except tk.TclError:
                pass

        self.bind("<Destroy>", forget, add="+")


class Button(_CanvasControl):
    """Rounded button with an optional Fluent icon. kind: primary, secondary, ghost or danger."""

    def __init__(self, parent, text, command, *, px, kind="secondary", icon=None, height=30, font=None,
                 square=False, takefocus=True):
        super().__init__(parent, px, takefocus)
        self.text, self.icon, self.kind, self.command = text, icon, kind, command
        self.height, self.square = height, square
        self.font = font or (theme.BODY_STRONG, 9)
        self.icon_font = (theme.ICONS, 10 if square else 9)
        self.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<KeyPress-space>", lambda _e: self.invoke())
        self.bind("<Return>", lambda _e: self.invoke())
        self.redraw()

    def set(self, text=None, icon=None, kind=None):
        self.text = self.text if text is None else text
        self.icon = self.icon if icon is None else icon
        self.kind = self.kind if kind is None else kind
        self.redraw()

    def invoke(self):
        if self.command:
            self.command()

    def _release(self, _event):
        fire = self.pressed and self.hover
        self._set(pressed=False)
        if fire:
            self.invoke()

    def redraw(self):
        px = self.px
        base, hover, pressed, fg = theme.BUTTONS[self.kind]
        color = pressed if self.pressed else hover if self.hover else base
        if self.hover and self.kind == "danger":
            fg = theme.POMEGRANATE_TEXT
        elif self.hover and self.kind == "ghost":
            fg = theme.PAPER
        h = px(self.height)
        icon_w = _measure(self, self.icon_font, self.icon) if self.icon else 0
        text_w = _measure(self, self.font, self.text) if self.text else 0
        gap = px(7) if self.icon and self.text else 0
        w = h if self.square else icon_w + gap + text_w + 2 * px(12 if self.text else 8)
        outline = theme.SAFFRON if self.focused and self.kind != "primary" else None
        if self.focused and self.kind == "primary":
            outline = theme.PAPER
        key = ("button", w, h, color, outline)
        image = art.photo(self, key, lambda: art.rounded(w, h, px(theme.RADIUS_MD), color, outline, px(1)))
        self.configure(width=w, height=h)
        self.delete("all")
        self.create_image(0, 0, image=image, anchor="nw")
        x = (w - icon_w - gap - text_w) / 2
        if self.icon:
            self.create_text(x + icon_w / 2, h / 2 + 1, text=self.icon, font=self.icon_font, fill=fg)
        if self.text:
            self.create_text(x + icon_w + gap, h / 2, text=self.text, font=self.font, fill=fg, anchor="w")


class Dropdown(_CanvasControl):
    """Value field that opens a themed menu; the menu is available as .menu."""

    def __init__(self, parent, variable, values, *, px, width, font=None):
        super().__init__(parent, px)
        self.variable, self.width = variable, width
        self.font = font or (theme.BODY, 9)
        self.menu = tk.Menu(self, **theme.menu_options(self.font))
        for value in values:
            self.menu.add_command(label=value, command=lambda v=value: variable.set(v))
        self.bind("<ButtonRelease-1>", lambda _e: self.open())
        self.bind("<KeyPress-space>", lambda _e: self.open())
        self.bind("<Down>", lambda _e: self.open())
        self._trace(variable)
        self.redraw()

    def open(self):
        self.update_idletasks()
        self.menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + self.px(2))

    def redraw(self):
        px = self.px
        w, h = px(self.width), px(30)
        color = theme.RAISED if self.hover else theme.SURFACE
        outline = theme.SAFFRON if self.focused else theme.LINE
        image = art.photo(self, ("field", w, h, color, outline),
                          lambda: art.rounded(w, h, px(theme.RADIUS_MD), color, outline, px(1)))
        self.configure(width=w, height=h)
        self.delete("all")
        self.create_image(0, 0, image=image, anchor="nw")
        text, room = self.variable.get(), w - px(40)
        font = tkfont.Font(root=self._root(), font=self.font)
        if font.measure(text) > room:
            while text and font.measure(text + "…") > room:
                text = text[:-1]
            text += "…"
        self.create_text(px(10), h / 2, text=text, font=self.font, fill=theme.PAPER, anchor="w")
        self.create_text(w - px(14), h / 2 + 1, text=theme.ICON_CHEVRON, font=(theme.ICONS, 8),
                         fill=theme.PAPER if self.hover else theme.MUTED)


class Slider(_CanvasControl):
    """Horizontal slider bound to a tk variable; arrow keys step by the resolution."""

    def __init__(self, parent, variable, from_, to, resolution, *, px, length):
        super().__init__(parent, px)
        self.variable, self.from_, self.to, self.resolution = variable, from_, to, resolution
        self.length, self.dragging = length, False
        self.configure(width=px(length), height=px(26))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", lambda e: self._set_from_x(e.x))
        self.bind("<ButtonRelease-1>", lambda _e: self._set(dragging=False))
        for key, steps in (("<Left>", -1), ("<Down>", -1), ("<Right>", 1), ("<Up>", 1)):
            self.bind(key, lambda _e, s=steps: self.set(self._value() + s * self.resolution))
        self.bind("<Home>", lambda _e: self.set(self.from_))
        self.bind("<End>", lambda _e: self.set(self.to))
        self._trace(variable)
        self.redraw()

    def _value(self) -> float:
        try:
            return float(self.variable.get())
        except (tk.TclError, ValueError):
            return self.from_

    def _span(self):
        r = self.px(9)
        return r + 1, self.px(self.length) - r - 1

    def _press(self, event):
        self.focus_set()
        self._set(dragging=True)
        self._set_from_x(event.x)

    def _set_from_x(self, x):
        x0, x1 = self._span()
        self.set(self.from_ + (x - x0) / (x1 - x0) * (self.to - self.from_))

    def set(self, value: float):
        steps = round((value - self.from_) / self.resolution)
        value = min(max(self.from_ + steps * self.resolution, self.from_), self.to)
        self.variable.set(int(round(value)) if isinstance(self.variable, tk.IntVar) else round(value, 4))

    def redraw(self):
        px = self.px
        x0, x1 = self._span()
        cy = px(13)
        x = x0 + (self._value() - self.from_) / (self.to - self.from_) * (x1 - x0)
        self.delete("all")
        track = px(4)
        self.create_line(x0, cy, x1, cy, fill=theme.LINE, width=track, capstyle="round")
        self.create_line(x0, cy, x, cy, fill=theme.SAFFRON, width=track, capstyle="round")
        big = self.hover or self.dragging
        d = px(18 if big else 16)
        ring = theme.SAFFRON if self.focused or self.dragging else theme.NIGHT
        image = art.photo(self, ("knob", d, ring), lambda: art.circle(d, theme.PAPER, ring, max(1, px(2))))
        self.create_image(x, cy, image=image)


class Toggle(tk.Frame):
    """Switch with a clickable label, bound to a BooleanVar."""

    def __init__(self, parent, variable, text, *, px, font=None):
        super().__init__(parent, bg=parent["bg"])
        self.variable, self.px, self.hover = variable, px, False
        self.switch = tk.Canvas(self, bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2",
                                width=px(36), height=px(20), takefocus=1)
        self.switch.pack(side="left")
        self.label = tk.Label(self, text=text, bg=parent["bg"], fg=theme.PAPER, font=font or (theme.BODY, 9),
                              cursor="hand2")
        self.label.pack(side="left", padx=(px(8), 0))
        for widget in (self.switch, self.label):
            widget.bind("<ButtonRelease-1>", lambda _e: self.flip())
            widget.bind("<Enter>", lambda _e: self._hover(True))
            widget.bind("<Leave>", lambda _e: self._hover(False))
        self.switch.bind("<KeyPress-space>", lambda _e: self.flip())
        name = variable.trace_add("write", lambda *_: self.redraw())
        self.switch.bind("<Destroy>", lambda _e: self._forget(name))
        self.redraw()

    def _forget(self, name):
        try:
            self.variable.trace_remove("write", name)
        except tk.TclError:
            pass

    def _hover(self, value):
        self.hover = value
        self.redraw()

    def flip(self):
        self.variable.set(not self.variable.get())

    def redraw(self):
        w, h, on = self.px(36), self.px(20), bool(self.variable.get())
        image = art.photo(self, ("toggle", w, h, on, self.hover), lambda: art.toggle(w, h, on, self.hover))
        self.switch.delete("all")
        self.switch.create_image(0, 0, image=image, anchor="nw")


class ThinScrollbar(tk.Canvas):
    """Slim overlay-style scrollbar: hidden when everything fits, wider on hover."""

    def __init__(self, parent, command, *, px):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, width=px(10), height=1)
        self.command, self.px = command, px
        self.first, self.last = 0.0, 1.0
        self.hover, self._drag = False, None
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None) or self.redraw())

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self.redraw()

    def _hover(self, value):
        self.hover = value
        self.redraw()

    def _press(self, event):
        h = self.winfo_height()
        if self.first * h <= event.y <= self.last * h:
            self._drag = (event.y, self.first)
        else:
            self.command("scroll", -1 if event.y < self.first * h else 1, "pages")
        self.redraw()

    def _motion(self, event):
        if self._drag:
            y, first = self._drag
            self.command("moveto", first + (event.y - y) / max(1, self.winfo_height()))

    def redraw(self):
        self.delete("all")
        if self.last - self.first >= 0.999:
            return
        h, w = self.winfo_height(), int(self["width"])
        active = self.hover or self._drag
        width = self.px(6 if active else 4)
        x = w - self.px(2) - width / 2
        y0, y1 = self.first * h + width / 2 + 1, self.last * h - width / 2 - 1
        self.create_line(x, y0, x, max(y0, y1), width=width, capstyle="round",
                         fill=theme.MUTED if active else theme.FAINT)


class Chip(Button):
    """Small outlined value, e.g. the reading speed in the panel header. on_wheel(delta) gets wheel turns."""

    def __init__(self, parent, text, command, *, px, on_wheel=None, accent=False, width_text=None, height=24):
        self.accent, self.width_text = accent, width_text or text
        super().__init__(parent, text, command, px=px, kind="ghost", height=height, font=(theme.MONO, 8),
                         takefocus=False)
        if on_wheel:
            self.bind("<MouseWheel>", lambda e: on_wheel(e.delta) or "break")

    def set(self, text=None, icon=None, kind=None, accent=None):
        self.accent = self.accent if accent is None else accent
        super().set(text, icon, kind)

    def redraw(self):
        px = self.px
        h = px(self.height)
        w = _measure(self, self.font, self.width_text) + 2 * px(8)
        color = theme.PRESSED if self.pressed else theme.SURFACE if self.hover else theme.NIGHT
        fg = theme.SAFFRON if self.accent else theme.PAPER if self.hover else theme.MUTED
        image = art.photo(self, ("chip", w, h, color), lambda: art.rounded(w, h, px(theme.RADIUS_SM), color,
                                                                           theme.LINE, px(1)))
        self.configure(width=w, height=h)
        self.delete("all")
        self.create_image(0, 0, image=image, anchor="nw")
        self.create_text(w / 2, h / 2, text=self.text, font=self.font, fill=fg)


class WheelAccumulator:
    """Turns <MouseWheel> deltas into whole notches.

    Windows reports 120 per notch; high-resolution wheels and touchpads send smaller deltas, which add up
    here. A change of direction or a pause longer than `idle` seconds drops the unused remainder.
    """

    NOTCH = 120

    def __init__(self, clock=time.monotonic, idle: float = 1.0):
        self.clock, self.idle = clock, idle
        self.rest, self.last = 0, None

    def feed(self, delta: int) -> int:
        now = self.clock()
        if self.last is not None and now - self.last > self.idle:
            self.rest = 0
        self.last = now
        if self.rest and (delta > 0) != (self.rest > 0):
            self.rest = 0
        self.rest += delta
        steps = int(self.rest / self.NOTCH)  # towards zero, so the remainder keeps its sign
        self.rest -= steps * self.NOTCH
        return steps


class Debounce:
    """Calls `callback` once, `delay` ms after the last trigger(); after/cancel are Tk's after/after_cancel."""

    def __init__(self, after, cancel, delay: int, callback):
        self.after, self.cancel, self.delay, self.callback = after, cancel, delay, callback
        self._job = None

    @property
    def pending(self) -> bool:
        return self._job is not None

    def trigger(self):
        if self._job is not None:
            self.cancel(self._job)
        self._job = self.after(self.delay, self._fire)

    def flush(self):
        """Runs a pending call now, e.g. before quitting."""
        if self._job is not None:
            self.cancel(self._job)
            self._fire()

    def _fire(self):
        self._job = None
        self.callback()


def wheel_target(first: float, last: float, units: int) -> str:
    """Where a wheel turn over an inner scrolling area goes: "inner" while it can still move that way,
    "outer" (the panel) once it is at the top or bottom, like scroll chaining in a browser."""
    if units < 0 and first > 0.0:
        return "inner"
    if units > 0 and last < 1.0:
        return "inner"
    return "outer"
