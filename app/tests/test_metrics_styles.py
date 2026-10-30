# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import metrics


DISPLAY = (320, 480)
VALUES = {
    "cpu": "50%",
    "ram": "64%",
    "disk": "81%",
    "net": "U 1.0K D 2.0K",
    "gpu": "27%",
    "gpu_temp": "68\N{DEGREE SIGN}C",
    "vram": "1.5/8.0 GiB",
    "cpu_temp": "55\N{DEGREE SIGN}C",
    "clock": "3.20 GHz",
}


def pixels(image):
    """Support current and older Pillow pixel iteration APIs."""
    return image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()


class MetricsStyleTests(unittest.TestCase):
    def test_default_api_is_rgb_and_keeps_legacy_height_signature(self) -> None:
        selected = ["cpu", "ram", "gpu"]
        old_height = metrics.metrics_panel_height(DISPLAY, selected)
        explicit_none_height = metrics.metrics_panel_height(DISPLAY, selected, None)
        panel = metrics.render_metrics_panel(DISPLAY, VALUES, selected)

        self.assertEqual(old_height, explicit_none_height)
        self.assertEqual(panel.mode, "RGB")
        self.assertEqual(panel.size, (DISPLAY[0], old_height))
        self.assertEqual(panel.getpixel((0, 0)), (16, 20, 27))

    def test_percentage_and_ring_mapping_is_a_pure_half_ratio(self) -> None:
        self.assertEqual(metrics.metric_indicator_ratio("cpu", "50%"), 0.5)
        self.assertEqual(metrics.metric_indicator_ratio("ram", 50), 0.5)
        self.assertEqual(metrics.metric_indicator_ratio("gpu", "50"), 0.5)
        self.assertEqual(metrics.metric_indicator_ratio("disk", "50.0%"), 0.5)

    def test_vram_and_temperature_indicators_use_real_explicit_ranges(self) -> None:
        self.assertAlmostEqual(metrics.metric_indicator_ratio("vram", "1.5/8.0 GiB"), 0.1875)
        self.assertEqual(metrics.metric_indicator_ratio("cpu_temp", "50\N{DEGREE SIGN}C", 100), 0.5)
        self.assertEqual(metrics.metric_indicator_ratio("gpu_temp", "50 C", 200), 0.25)
        self.assertIsNone(metrics.metric_indicator_ratio("net", "U 1.0K D 2.0K"))
        self.assertIsNone(metrics.metric_indicator_ratio("clock", "3.20 GHz"))

    def test_unavailable_and_invalid_data_never_becomes_a_zero_indicator(self) -> None:
        for key, value in (
            ("cpu", "N/A"),
            ("cpu", "nan%"),
            ("cpu", "-1%"),
            ("ram", float("nan")),
            ("gpu", "not a percent"),
            ("vram", "N/A"),
            ("vram", "1.0/0 GiB"),
            ("vram", "nan/8 GiB"),
            ("cpu_temp", "N/A"),
            ("gpu_temp", "inf C"),
        ):
            with self.subTest(key=key, value=value):
                self.assertIsNone(metrics.metric_indicator_ratio(key, value))

        panel = metrics.render_metrics_panel(
            DISPLAY,
            {"cpu": "N/A"},
            ["cpu"],
            style={"metrics_style": "Bars", "accent_color": "#ff00ff"},
        )
        self.assertNotIn((255, 0, 255), pixels(panel))

    def test_cards_bars_and_rings_render_distinct_indicator_treatments(self) -> None:
        selected = ["cpu", "ram", "gpu", "vram"]
        panels = {
            kind: metrics.render_metrics_panel(
                DISPLAY,
                VALUES,
                selected,
                style={"metrics_style": kind, "accent_color": "#00ff00"},
            )
            for kind in ("Cards", "Bars", "Rings")
        }
        self.assertEqual({panel.mode for panel in panels.values()}, {"RGB"})
        self.assertEqual({panel.size[0] for panel in panels.values()}, {DISPLAY[0]})
        self.assertNotEqual(panels["Cards"].tobytes(), panels["Bars"].tobytes())
        self.assertNotEqual(panels["Bars"].tobytes(), panels["Rings"].tobytes())
        self.assertGreaterEqual(
            metrics.metrics_panel_height(DISPLAY, selected, {"metrics_style": "Rings"}),
            metrics.metrics_panel_height(DISPLAY, selected, {"metrics_style": "Cards"}),
        )

    def test_custom_colors_fonts_and_opacity_are_safe(self) -> None:
        style = {
            "background_color": "#010203",
            "card_color": "#102030",
            "border_color": "#405060",
            "text_color": "#fefefe",
            "label_color": "#aabbcc",
            "accent_color": "#abcdef",
            "muted_color": "#8899aa",
            "metrics_style": "Bars",
            "font_size": 16,
            "font_family": "Arial",
            "panel_opacity": 100,
        }
        panel = metrics.render_metrics_panel(DISPLAY, VALUES, ["cpu"], style=style)
        self.assertEqual(panel.mode, "RGB")
        self.assertEqual(panel.getpixel((0, 0)), (1, 2, 3))
        self.assertIn((16, 32, 48), pixels(panel))
        self.assertIn((171, 205, 239), pixels(panel))

        transparent = metrics.render_metrics_panel(
            DISPLAY,
            VALUES,
            ["cpu"],
            style={"panel_opacity": 50, "metrics_style": "Bars"},
        )
        self.assertEqual(transparent.mode, "RGBA")
        self.assertEqual(transparent.getpixel((0, 0))[3], 128)
        self.assertEqual(max(pixel[3] for pixel in pixels(transparent)), 255)

        fallback = metrics.render_metrics_panel(
            DISPLAY,
            VALUES,
            ["cpu"],
            style={"background_color": "not-a-colour", "font_size": "not-a-size"},
        )
        self.assertEqual(fallback.getpixel((0, 0)), (16, 20, 27))

    def test_common_windows_font_candidates_and_cached_loading(self) -> None:
        with patch.object(metrics.os, "name", "nt"), patch.dict(
            metrics.os.environ, {"WINDIR": r"C:\Windows"}, clear=False
        ):
            candidates = metrics._font_candidates(None, "Arial")
        self.assertIn(Path(r"C:\Windows") / "Fonts" / "arial.ttf", candidates)

        metrics._cached_font.cache_clear()
        metrics._load_font(12, None, "Arial")
        metrics._load_font(12, None, "Arial")
        self.assertGreaterEqual(metrics._cached_font.cache_info().hits, 1)

    def test_style_rendering_does_not_mutate_values_or_selected(self) -> None:
        values = deepcopy(VALUES)
        selected = list(metrics.METRIC_KEYS)
        values_before = deepcopy(values)
        selected_before = list(selected)

        panel = metrics.render_metrics_panel(
            DISPLAY,
            values,
            selected,
            style={"metrics_style": "Rings", "temperature_max": 90},
        )

        self.assertEqual(values, values_before)
        self.assertEqual(selected, selected_before)
        self.assertEqual(panel.size[1], metrics.metrics_panel_height(DISPLAY, selected, {"metrics_style": "Rings", "temperature_max": 90}))
        self.assertLessEqual(panel.size[1], int(DISPLAY[1] * 0.45))

    def test_all_nine_metrics_fit_in_both_target_orientations(self) -> None:
        selected = list(metrics.METRIC_KEYS)
        for size in ((320, 480), (480, 320)):
            for treatment in ("Cards", "Bars", "Rings"):
                with self.subTest(size=size, treatment=treatment):
                    style = {"metrics_style": treatment, "font_size": 14}
                    panel = metrics.render_metrics_panel(size, VALUES, selected, style=style)
                    self.assertEqual(panel.size[0], size[0])
                    self.assertEqual(panel.size[1], metrics.metrics_panel_height(size, selected, style))
                    self.assertGreater(panel.size[1], 0)
                    self.assertLessEqual(panel.size[1], int(size[1] * 0.45))


if __name__ == "__main__":
    unittest.main()
