import itertools
import re
import unittest

from PIL import Image, ImageChops, ImageStat

from ema_tts import art, theme
from ema_tts import player as pl


def luminance(image: Image.Image) -> Image.Image:
    """Greyscale over a mid-grey desktop: what a viewer without colour vision would see."""
    background = Image.new("RGBA", image.size, (128, 128, 128, 255))
    return Image.alpha_composite(background, image).convert("L")


def difference(a: Image.Image, b: Image.Image) -> float:
    return ImageStat.Stat(ImageChops.difference(luminance(a), luminance(b))).mean[0]


class BubbleTest(unittest.TestCase):
    STATES = (art.LOADING, art.READY, art.SPEAKING, art.PAUSED, art.ERROR)

    def test_states_match_player_constants(self):
        self.assertEqual(self.STATES, (pl.LOADING, pl.READY, pl.SPEAKING, pl.PAUSED, pl.ERROR))

    def test_size_and_transparent_corners(self):
        for size in (60, 90):
            image = art.logo(size, art.SPEAKING, t=0.4, count=3)
            self.assertEqual(image.size, (size, size))
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((0, size - 1))[3], 0)

    def test_states_differ_without_colour(self):
        images = {state: art.logo(60, state, t=0.3) for state in self.STATES}
        for a, b in itertools.combinations(self.STATES, 2):
            with self.subTest(pair=(a, b)):
                self.assertGreater(difference(images[a], images[b]), 3.0)

    def test_speaking_moves_and_paused_does_not(self):
        self.assertGreater(difference(art.logo(60, art.SPEAKING, t=0.0), art.logo(60, art.SPEAKING, t=0.5)), 0.5)
        self.assertEqual(difference(art.logo(60, art.PAUSED, t=0.0), art.logo(60, art.PAUSED, t=0.5)), 0)

    def test_flashes_change_the_bubble_and_fade(self):
        base = art.logo(60, art.READY)
        self.assertGreater(difference(base, art.logo(60, art.READY, flash="ok", flash_age=0.1)), 1.0)
        self.assertGreater(difference(base, art.logo(60, art.READY, flash="reject", flash_age=0.1)), 1.0)
        self.assertEqual(difference(base, art.logo(60, art.READY, flash="ok", flash_age=art.OK_SECONDS)), 0)

    def test_badge_text(self):
        plain = art.logo(60, art.READY)
        for count in (1, 12, 100):
            with self.subTest(count=count):
                self.assertGreater(difference(plain, art.logo(60, art.READY, count=count)), 0.5)


class ThemeTest(unittest.TestCase):
    def test_colours_are_hex(self):
        colours = {name: value for name, value in vars(theme).items()
                   if isinstance(value, str) and value.startswith("#")}
        self.assertGreater(len(colours), 10)
        for name, value in colours.items():
            self.assertRegex(value, re.compile(r"^#[0-9A-Fa-f]{6}$"), name)

    def test_icon_has_every_size(self):
        self.assertTrue(theme.ICON_FILE.exists(), "run python -m ema_tts.art")
        with Image.open(theme.ICON_FILE) as icon:
            self.assertEqual(set(icon.info["sizes"]), {(s, s) for s in art.ICON_SIZES})

    def test_rounded_has_soft_corners(self):
        image = art.rounded(40, 20, 8, theme.SURFACE)
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertEqual(image.getpixel((20, 10))[3], 255)
        self.assertTrue(any(0 < image.getpixel((x, 1))[3] < 255 for x in range(8)))


if __name__ == "__main__":
    unittest.main()
