# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure regression checks for local screen-design assets and composition.

These tests do not create a Tk root, inspect real settings, or contact a
sensor/capture/USB device.  Their inputs are generated pixels in a temporary
application data directory.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pet_bridge import compose_output
from screen_style import DEFAULT_STYLE, import_image, normalize_style, render_background


_HASHED_PNG = re.compile(r"assets/[0-9a-f]{64}\.png\Z")


def _transparent_pet() -> Image.Image:
    """Return a synthetic transparent source with one small opaque pet block."""
    image = Image.new("RGBA", (48, 36), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (10, 14), "#e08a4f"), (18, 10))
    return image


class BackgroundCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="pet-dock-design-")
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.style = normalize_style(
            dict(DEFAULT_STYLE, background_color="#112233", background_image="")
        )

    def _settings(self, **overrides: object) -> dict[str, object]:
        settings: dict[str, object] = {
            "design": self.style,
            "asset_dir": str(self.data_dir),
            "fit_visible": True,
            "pet_scale": 0.8,
            "metrics_selected": [],
        }
        settings.update(overrides)
        return settings

    def test_empty_and_transparent_source_leave_background_visible(self) -> None:
        background = (17, 34, 51)
        empty = Image.new("RGBA", (48, 36), (0, 0, 0, 0))
        output = compose_output(empty, (120, 80), self._settings())
        self.assertEqual(output.getcolors(maxcolors=2), [(120 * 80, background)])

        output = compose_output(_transparent_pet(), (120, 80), self._settings())
        changed = [
            (x, y)
            for y in range(output.height)
            for x in range(output.width)
            if output.getpixel((x, y)) != background
        ]
        self.assertTrue(changed, "the synthetic pet should remain visible")
        self.assertGreater(sum(output.getpixel((x, y)) == background for x in range(120) for y in range(80)), 6_000)

        # The fitted source has transparent padding.  Those transparent pixels
        # must not become an opaque rectangular fill over the new background.
        xs, ys = zip(*changed)
        self.assertEqual(output.getpixel((min(xs) - 1, min(ys) - 1)), background)
        self.assertEqual(output.getpixel((max(xs) + 1, max(ys) + 1)), background)

    def test_contain_cover_and_dimming_preserve_expected_background_behavior(self) -> None:
        source = self.data_dir / "wide-source.png"
        Image.new("RGB", (40, 20), "#c86432").save(source)
        relative = import_image(source, self.data_dir)
        style = normalize_style(dict(self.style, background_image=relative, background_fit="Contain"))

        contain = render_background((100, 100), style, self.data_dir)
        self.assertEqual(contain.getpixel((0, 0)), (17, 34, 51))
        colored = [
            (x, y)
            for y in range(contain.height)
            for x in range(contain.width)
            if contain.getpixel((x, y)) == (200, 100, 50)
        ]
        xs, ys = zip(*colored)
        self.assertEqual((min(xs), min(ys), max(xs) + 1, max(ys) + 1), (0, 25, 100, 75))

        cover = render_background(
            (100, 100), normalize_style(dict(style, background_fit="Cover")), self.data_dir
        )
        self.assertEqual(cover.getpixel((0, 0)), (200, 100, 50))
        self.assertEqual(cover.getpixel((99, 99)), (200, 100, 50))

        dimmed = render_background(
            (100, 100), normalize_style(dict(style, background_fit="Cover", background_dim=50)), self.data_dir
        )
        expected = Image.blend(
            Image.new("RGBA", (1, 1), "#c86432"), Image.new("RGBA", (1, 1), "#000000"), 0.5
        ).convert("RGB").getpixel((0, 0))
        self.assertEqual(dimmed.getpixel((50, 50)), expected)

    def test_missing_background_asset_falls_back_safely(self) -> None:
        missing = "assets/" + "a" * 64 + ".png"
        style = normalize_style(dict(self.style, background_image=missing))
        output = render_background((23, 17), style, self.data_dir)
        self.assertEqual(output.mode, "RGB")
        self.assertEqual(output.getcolors(maxcolors=2), [(23 * 17, (17, 34, 51))])

    def test_unicode_import_is_relative_hashed_and_strips_exif(self) -> None:
        source = self.data_dir / "背景 café source.jpg"
        exif = Image.Exif()
        exif[270] = "private original title"
        Image.new("RGB", (12, 8), "#3478c0").save(source, exif=exif)

        relative = import_image(source, self.data_dir)
        self.assertRegex(relative, _HASHED_PNG)
        self.assertNotIn(str(source), relative)
        target = self.data_dir / relative
        self.assertTrue(target.is_file())
        with Image.open(target) as clean:
            self.assertEqual(clean.format, "PNG")
            self.assertEqual(dict(clean.getexif()), {})
            self.assertNotIn("exif", clean.info)
            self.assertNotIn("private original title", repr(clean.info))

    def test_json_style_cannot_leak_an_absolute_source_path(self) -> None:
        source = self.data_dir / "asset.png"
        Image.new("RGBA", (6, 6), "#aabbcc").save(source)
        relative = import_image(source, self.data_dir)
        persisted = normalize_style(
            dict(self.style, background_image=relative, font_path=str(source))
        )
        decoded = json.loads(json.dumps(persisted))
        self.assertRegex(decoded["background_image"], _HASHED_PNG)
        self.assertEqual(decoded["font_path"], "")
        self.assertNotIn(str(source), json.dumps(decoded))


if __name__ == "__main__":
    result = unittest.main(exit=False)
    if result.result.wasSuccessful():
        print("PASS: pure screen-design background and asset regressions passed.")
