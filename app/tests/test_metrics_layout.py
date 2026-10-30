from __future__ import annotations

from pathlib import Path
import sys
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framing import detect_content_crop
from metrics import METRIC_KEYS, metrics_panel_height
from pet_bridge import compose_output


BACKGROUND = "#10141b"
DISPLAY = (320, 480)


def tall_pet_source() -> Image.Image:
    """A sparse transparent capture with a tall pet and detached speech bubble."""
    image = Image.new("RGBA", (960, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((690, 520, 770, 860), radius=28, fill="#e8aa7c")
    draw.ellipse((700, 440, 760, 530), fill="#e8aa7c")
    draw.rounded_rectangle((560, 310, 770, 400), radius=24, fill="#89b8f0")
    return image


def pet_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Return the known warm-color pet extent without counting a metrics panel."""
    pixels = image.convert("RGB")
    matches = [
        (x, y)
        for y in range(image.height)
        for x in range(image.width)
        if (red := pixels.getpixel((x, y))[0]) > 180
        and pixels.getpixel((x, y))[1] > 100
        and pixels.getpixel((x, y))[1] < 210
        and pixels.getpixel((x, y))[2] < 180
    ]
    if not matches:
        return None
    xs, ys = zip(*matches)
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


class MetricsLayoutRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = tall_pet_source()
        self.auto_crop = detect_content_crop(self.source, mode="Pet + bubble")
        self.assertIsNotNone(self.auto_crop)
        self.base_settings = {
            "crop": self.auto_crop,
            "ignore_alpha": False,
            "pixel_art": False,
            "background": BACKGROUND,
            "pet_scale": 0.95,
            "metrics_selected": [],
            "metrics_values": {key: "42%" for key in METRIC_KEYS},
        }

    def compose(self, crop, selected):
        settings = dict(self.base_settings, crop=crop, metrics_selected=list(selected))
        return compose_output(self.source, DISPLAY, settings)

    def assert_metrics_round_trip(self, crop) -> None:
        original_crop = tuple(crop)
        baseline = self.compose(crop, [])
        baseline_bbox = pet_bbox(baseline)
        self.assertIsNotNone(baseline_bbox)
        baseline_height = baseline_bbox[3] - baseline_bbox[1]

        # Each selectable card is independently safe. The on-state can reduce
        # available vertical stage space, but does not mutate crop or scale.
        for key in METRIC_KEYS:
            with self.subTest(selection=key):
                rendered = self.compose(crop, [key])
                self.assertGreater(metrics_panel_height(DISPLAY, [key]), 0)
                bbox = pet_bbox(rendered)
                self.assertIsNotNone(bbox)
                self.assertGreater(bbox[3] - bbox[1], 0)
                self.assertLessEqual(bbox[3] - bbox[1], baseline_height)
                self.assertEqual(self.base_settings["pet_scale"], 0.95)
                self.assertEqual(crop, original_crop)
                self.assertEqual(self.compose(crop, []).tobytes(), baseline.tobytes())

        all_rendered = self.compose(crop, METRIC_KEYS)
        self.assertGreater(metrics_panel_height(DISPLAY, METRIC_KEYS), 0)
        self.assertIsNotNone(pet_bbox(all_rendered))
        self.assertEqual(self.compose(crop, []).tobytes(), baseline.tobytes())

    def test_metrics_round_trip_preserves_auto_detected_crop(self) -> None:
        self.assert_metrics_round_trip(self.auto_crop)

    def test_metrics_round_trip_preserves_manual_crop(self) -> None:
        manual_crop = (0.68, 0.38, 0.84, 0.84)
        self.assert_metrics_round_trip(manual_crop)


if __name__ == "__main__":
    unittest.main()
