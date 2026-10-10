"""Anti-aliased drawings with Pillow: the bubble, rounded surfaces and the app icon.

Everything is drawn at SS times the size and scaled down, which gives smooth edges at any DPI.
Run `python -m ema_tts.art` to regenerate assets/icon.ico.
"""

import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import theme

SS = 4
LOADING, READY, SPEAKING, PAUSED, ERROR = "loading", "ready", "speaking", "paused", "error"
BAR_SHAPE = (0.42, 0.7, 1.0, 0.7, 0.42)
OK_SECONDS = 0.7
REJECT_SECONDS = 0.55

# state: (fill, rim, glyph)
BUBBLE_COLORS = {
    LOADING: ("#141B2E", "#2A3552", theme.FAINT),
    READY: ("#18213A", "#36436B", theme.PAPER),
    SPEAKING: (theme.SAFFRON, "#FFD58A", theme.NIGHT),
    PAUSED: ("#18213A", theme.SAFFRON, theme.SAFFRON),
    ERROR: (theme.POMEGRANATE, "#FF9296", theme.NIGHT),
}


def rgb(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16), max(0, min(255, int(alpha)))


def mix(color: str, other: str, amount: float) -> str:
    a, b = rgb(color), rgb(other)
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a[:3], b[:3]))


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in theme.BADGE_FONTS:
        try:
            font = ImageFont.truetype(name, size)
        except OSError:
            continue
        try:
            font.set_variation_by_name("Bold")
        except (OSError, ValueError, AttributeError):
            pass
        return font
    return ImageFont.load_default(size)


def _gradient_disc(size: int, cx: float, cy: float, r: float, color: str) -> Image.Image:
    """A disc with a soft top-to-bottom light, so the bubble reads as a bubble rather than a dot."""
    top, bottom = rgb(mix(color, "#ffffff", 0.12)), rgb(mix(color, "#000000", 0.08))
    column = Image.new("RGBA", (1, 256))
    for y in range(256):
        k = y / 255
        column.putpixel((0, y), tuple(round(a + (b - a) * k) for a, b in zip(top, bottom)))
    box = (round(cx - r), round(cy - r), round(cx + r), round(cy + r))
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    layer.paste(column.resize((box[2] - box[0], box[3] - box[1])), box[:2])
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(layer, (0, 0), mask)
    return out


def _bar(draw, x, cy, width, height, color):
    half = max(height, width) / 2
    draw.rounded_rectangle((x, cy - half, x + width, cy + half), radius=width / 2, fill=color)


def _bars(draw, cx, cy, r, heights, color, width=0.15, gap=0.11):
    w, g = width * r, gap * r
    x = cx - (len(heights) * w + (len(heights) - 1) * g) / 2
    for h in heights:
        _bar(draw, x, cy, w, 0.92 * r * h, color)
        x += w + g


def speaking_heights(t: float) -> list[float]:
    """Calm, slightly uneven waveform: two slow sines per bar instead of a fast pulse."""
    heights = []
    for i, shape in enumerate(BAR_SHAPE):
        a = 0.5 + 0.5 * math.sin(2 * math.pi * 1.05 * t + i * 1.15)
        b = 0.5 + 0.5 * math.sin(2 * math.pi * 0.62 * t - i * 0.8 + 1.3)
        heights.append(0.3 + 0.7 * shape * (0.55 * a + 0.45 * b))
    return heights


def _glyph(draw, state, cx, cy, r, color, t):
    if state == SPEAKING:
        _bars(draw, cx, cy, r, speaking_heights(t), color)
    elif state == PAUSED:
        _bars(draw, cx, cy, r, (0.72, 0.72), color, width=0.2, gap=0.2)
    elif state == ERROR:
        w = 0.19 * r
        draw.rounded_rectangle((cx - w / 2, cy - 0.52 * r, cx + w / 2, cy + 0.14 * r), radius=w / 2, fill=color)
        d = 0.115 * r
        draw.ellipse((cx - d, cy + 0.38 * r - d, cx + d, cy + 0.38 * r + d), fill=color)
    elif state == LOADING:
        _bars(draw, cx, cy, r, [h * 0.45 for h in BAR_SHAPE], color)
        ring = 0.84 * r
        width = 0.1 * r
        start = (t * 320) % 360
        box = (cx - ring, cy - ring, cx + ring, cy + ring)
        draw.arc(box, start, start + 100, fill=rgb(theme.PAPER, 230), width=round(width))
        for angle in (start, start + 100):  # round caps
            ax = cx + (ring - width / 2) * math.cos(math.radians(angle))
            ay = cy + (ring - width / 2) * math.sin(math.radians(angle))
            draw.ellipse((ax - width / 2, ay - width / 2, ax + width / 2, ay + width / 2),
                         fill=rgb(theme.PAPER, 230))
    else:
        _bars(draw, cx, cy, r, BAR_SHAPE, color)


def _badge(draw, size, cx, cy, r, count, u):
    text = str(count) if count < 100 else "99+"
    font = _font(round(10.5 * u))
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    rb = 8.2 * u
    width = max(2 * rb, right - left + 7 * u)
    bx = min(cx + r * 0.72, size - width / 2 - 1.2 * u)
    by = max(cy - r * 0.72, rb + 1.2 * u)
    ring = 1.7 * u
    draw.rounded_rectangle((bx - width / 2 - ring, by - rb - ring, bx + width / 2 + ring, by + rb + ring),
                           radius=rb + ring, fill=rgb(theme.NIGHT))
    draw.rounded_rectangle((bx - width / 2, by - rb, bx + width / 2, by + rb), radius=rb, fill=rgb(theme.PAPER))
    draw.text((bx, by + 0.4 * u), text, font=font, fill=rgb(theme.NIGHT), anchor="mm")


def logo(size: int, state: str = READY, t: float = 0.0, flash: str | None = None, flash_age: float = 0.0,
         count: int = 0, hover: bool = False, mark: bool = False) -> Image.Image:
    """The bubble as an RGBA image.

    Shape, not just colour, tells the states apart: five resting bars (ready), moving bars and a
    breathing halo (speaking), two bars (paused), "!" (error) and a spinning arc (loading).
    flash "ok" sends a ring outward; "reject" shakes the bubble. mark=True is the small header
    version: no shadow, halo, flash or badge.
    """
    big = size * SS
    u = big / theme.BUBBLE
    fill, rim, glyph = BUBBLE_COLORS.get(state, BUBBLE_COLORS[READY])
    if hover and state in (READY, LOADING, PAUSED):
        rim = mix(rim, theme.PAPER, 0.35)
    r = big / 2 - SS * 0.5 if mark else 22.5 * u
    cx = cy = big / 2

    ok = flash == "ok" and flash_age < OK_SECONDS
    reject = flash == "reject" and flash_age < REJECT_SECONDS
    if reject:
        p = flash_age / REJECT_SECONDS
        cx += 3.2 * u * math.sin(p * math.pi * 5) * (1 - p)

    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    if not mark:
        shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).ellipse((cx - r, cy - r + 1.4 * u, cx + r, cy + r + 1.4 * u), fill=(0, 0, 0, 120))
        image = Image.alpha_composite(image, shadow.filter(ImageFilter.GaussianBlur(2.4 * u)))

    draw = ImageDraw.Draw(image)
    if state == SPEAKING and not mark:
        b = 0.5 + 0.5 * math.sin(2 * math.pi * 0.5 * t)
        halo = r + (1.6 + 2.2 * b) * u
        draw.ellipse((cx - halo, cy - halo, cx + halo, cy + halo), outline=rgb(theme.SAFFRON, 40 + 110 * (1 - b)),
                     width=round(1.4 * u))

    rim_width = max(SS, 0.075 * r)
    if ok:
        rim = theme.MINT
    elif reject:
        rim = theme.POMEGRANATE
    if ok or reject or state == PAUSED:
        rim_width = 0.1 * r
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rgb(rim))
    image = Image.alpha_composite(image, _gradient_disc(big, cx, cy, r - rim_width, fill))
    draw = ImageDraw.Draw(image)
    _glyph(draw, state, cx, cy, r, rgb(glyph), t)

    if ok:
        p = flash_age / OK_SECONDS
        e = 1 - (1 - p) ** 3
        ring = r + (1.2 + 5.0 * e) * u
        draw.ellipse((cx - ring, cy - ring, cx + ring, cy + ring), outline=rgb(theme.MINT, 255 * (1 - p)),
                     width=max(1, round((2.4 - 1.6 * p) * u)))
    if count and not mark:
        _badge(draw, big, cx, cy, r, count, u)
    return image.resize((size, size), Image.LANCZOS)


def rounded(width: int, height: int, radius: float, fill: str, outline: str | None = None,
            outline_width: float = 1, accent: str | None = None, accent_width: float = 0) -> Image.Image:
    """Rounded rectangle with transparent corners; accent paints a bar along the left edge."""
    big = Image.new("RGBA", (width * SS, height * SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    box = (0, 0, width * SS - 1, height * SS - 1)
    r = radius * SS
    if outline:
        draw.rounded_rectangle(box, r, fill=rgb(outline))
        inset = outline_width * SS
        box = (inset, inset, box[2] - inset, box[3] - inset)
        r = max(0, r - inset)
    if accent:
        draw.rounded_rectangle(box, r, fill=rgb(accent))
        box = (box[0] + accent_width * SS, box[1], box[2], box[3])
    draw.rounded_rectangle(box, r, fill=rgb(fill))
    return big.resize((width, height), Image.LANCZOS)


def circle(diameter: int, fill: str, outline: str | None = None, outline_width: float = 0) -> Image.Image:
    return rounded(diameter, diameter, diameter / 2, fill, outline, outline_width)


def toggle(width: int, height: int, on: bool, hover: bool = False) -> Image.Image:
    track = theme.SAFFRON if on else (theme.RAISED if hover else theme.SURFACE)
    image = rounded(width, height, height / 2, track, None if on else theme.LINE, max(1, height / 14))
    knob = height - 2 * round(height * 0.18)
    color = theme.NIGHT if on else (theme.PAPER if hover else theme.MUTED)
    x = width - height + round(height * 0.18) if on else round(height * 0.18)
    image.alpha_composite(circle(knob, color), (x, round(height * 0.18)))
    return image


def photo(widget, key, factory):
    """Cached ImageTk.PhotoImage, kept alive on the Tk root (tk forgets images without a reference)."""
    from PIL import ImageTk

    root = widget._root()
    cache = root.__dict__.setdefault("_ema_photos", {})
    if key not in cache:
        cache[key] = ImageTk.PhotoImage(factory(), master=root)
    return cache[key]


def icon_image(size: int) -> Image.Image:
    """App icon: the speaking bubble, full bleed; small sizes get three wider bars to stay legible."""
    big = size * SS
    r = big / 2 - max(SS * 0.5, big * 0.02)
    image = _gradient_disc(big, big / 2, big / 2, r, theme.SAFFRON)
    draw = ImageDraw.Draw(image)
    if size <= 24:
        _bars(draw, big / 2, big / 2, r, (0.55, 1.0, 0.55), rgb(theme.NIGHT), width=0.22, gap=0.16)
    else:
        _bars(draw, big / 2, big / 2, r, BAR_SHAPE, rgb(theme.NIGHT), width=0.15, gap=0.11)
    return image.resize((size, size), Image.LANCZOS)


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def write_icon(path: Path = theme.ICON_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    images = [icon_image(size) for size in ICON_SIZES]
    images[-1].save(path, format="ICO", sizes=[(s, s) for s in ICON_SIZES], append_images=images[:-1])


if __name__ == "__main__":
    write_icon()
    print(f"icon written: {theme.ICON_FILE}")
