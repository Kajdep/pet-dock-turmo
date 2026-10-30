from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from framing import CropTracker, detect_content_crop


class ContentFramingTests(unittest.TestCase):
    def _pet_with_separate_bubble(self, pet_color=(181, 63, 132, 255)) -> Image.Image:
        """Build a non-blue/non-black/non-white pet below a detached bubble."""
        image = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((45, 65, 260, 170), radius=18, fill=(247, 211, 75, 255))
        # Deliberately separate from the bubble.  The lower, upright component
        # resembles a small humanoid with head, torso, arms, and legs.
        draw.ellipse((160, 290, 220, 350), fill=pet_color)
        draw.rounded_rectangle((150, 345, 230, 485), radius=20, fill=pet_color)
        draw.rectangle((125, 365, 150, 430), fill=pet_color)
        draw.rectangle((230, 365, 255, 430), fill=pet_color)
        draw.rectangle((160, 475, 185, 555), fill=pet_color)
        draw.rectangle((197, 475, 222, 555), fill=pet_color)
        return image

    def test_alpha_default_includes_detached_bubble_and_custom_color_pet(self):
        crop = detect_content_crop(self._pet_with_separate_bubble())

        self.assertIsNotNone(crop)
        left, top, right, bottom = crop
        # The raw content starts at x=45/y=65 and ends at x=255/y=555.  The
        # returned crop must include it and preserve a useful padded frame.
        self.assertLessEqual(left, 45 / 400)
        self.assertLessEqual(top, 65 / 600)
        self.assertGreaterEqual(right, 255 / 400)
        self.assertGreaterEqual(bottom, 555 / 600)
        self.assertLess(right - left, 0.90)
        # The required 20% breathing room correctly clips to both canvas
        # edges here, so a full-height crop is valid.
        self.assertLessEqual(bottom - top, 1.0)

    def test_pet_only_prefers_lower_humanoid_over_large_upper_bubble(self):
        crop = detect_content_crop(self._pet_with_separate_bubble(), mode="Pet only")

        self.assertIsNotNone(crop)
        left, top, right, bottom = crop
        # It should not frame the upper-left speech bubble, but should retain
        # the whole humanoid and its animation margin.
        self.assertGreater(left, 0.20)
        self.assertGreater(top, 0.35)
        self.assertLess(right, 0.80)
        self.assertGreaterEqual(bottom, 555 / 600)

    def test_nonstandard_pet_color_is_not_treated_as_background(self):
        image = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
        # A muted green custom pet, specifically not a black/white/blue keyed
        # color, has alpha and therefore must still frame correctly.
        ImageDraw.Draw(image).rounded_rectangle((120, 110, 190, 270), radius=22, fill=(71, 154, 94, 255))

        crop = detect_content_crop(image)

        self.assertIsNotNone(crop)
        left, top, right, bottom = crop
        self.assertLess(left, 120 / 320)
        self.assertLess(top, 110 / 320)
        self.assertGreater(right, 190 / 320)
        self.assertGreater(bottom, 270 / 320)

    def test_opaque_uniform_background_can_be_framed_without_color_keying(self):
        image = Image.new("RGBA", (400, 500), (233, 181, 102, 255))
        # Purple is intentionally unrelated to conventional black/white/blue
        # key colors.  Detection is based on the uniform corner background.
        ImageDraw.Draw(image).rounded_rectangle((155, 160, 245, 420), radius=24, fill=(126, 47, 173, 255))

        crop = detect_content_crop(image)

        self.assertIsNotNone(crop)
        left, top, right, bottom = crop
        self.assertLess(left, 155 / 400)
        self.assertLess(top, 160 / 500)
        self.assertGreater(right, 245 / 400)
        self.assertGreater(bottom, 420 / 500)

    def test_complex_opaque_frame_is_not_arbitrarily_cropped(self):
        rng = np.random.default_rng(41)
        rgb = rng.integers(0, 256, (180, 250, 3), dtype=np.uint8)
        image = Image.fromarray(np.dstack((rgb, np.full((180, 250), 255, dtype=np.uint8))), "RGBA")

        self.assertIsNone(detect_content_crop(image))

    def test_transparent_thin_fringe_is_rejected(self):
        image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        ImageDraw.Draw(image).line((200, 20, 200, 380), fill=(255, 255, 255, 255), width=1)

        self.assertIsNone(detect_content_crop(image))

    def test_empty_transparent_frame_has_no_crop(self):
        self.assertIsNone(detect_content_crop(Image.new("RGBA", (200, 200), (0, 0, 0, 0))))


class CropTrackerTests(unittest.TestCase):
    def test_expands_immediately_then_shrinks_after_stable_frames(self):
        now = [0.0]
        tracker = CropTracker(shrink_delay=99, stable_frames=3, clock=lambda: now[0])

        self.assertEqual(tracker.update((0.30, 0.30, 0.70, 0.70), (400, 400)), (0.30, 0.30, 0.70, 0.70))
        # New visible content reaches farther left and down, so it is included
        # in the same call rather than waiting for debounce.
        self.assertEqual(tracker.update((0.20, 0.35, 0.65, 0.80), (400, 400)), (0.20, 0.30, 0.70, 0.80))

        smaller = (0.30, 0.40, 0.60, 0.70)
        self.assertEqual(tracker.update(smaller, (400, 400)), (0.20, 0.30, 0.70, 0.80))
        self.assertEqual(tracker.update(smaller, (400, 400)), (0.20, 0.30, 0.70, 0.80))
        self.assertEqual(tracker.update(smaller, (400, 400)), smaller)

    def test_shrinks_after_delay_and_accepts_pixel_candidate(self):
        now = [0.0]
        tracker = CropTracker(shrink_delay=2.0, stable_frames=99, clock=lambda: now[0])
        self.assertEqual(tracker.update((10, 10, 90, 90), (100, 100)), (0.1, 0.1, 0.9, 0.9))

        self.assertEqual(tracker.update((0.2, 0.2, 0.8, 0.8), (100, 100)), (0.1, 0.1, 0.9, 0.9))
        now[0] = 2.1
        self.assertEqual(tracker.update((0.2, 0.2, 0.8, 0.8), (100, 100)), (0.2, 0.2, 0.8, 0.8))

    def test_none_holds_last_crop_and_reset_clears_it(self):
        tracker = CropTracker()
        tracker.update((0.1, 0.2, 0.8, 0.9), (100, 100))
        self.assertEqual(tracker.update(None, (100, 100)), (0.1, 0.2, 0.8, 0.9))
        tracker.reset()
        self.assertIsNone(tracker.crop)


if __name__ == "__main__":
    unittest.main()
