"""Targeted 0.2.1 regressions; no Windows/USB device is accessed."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import display_io
from pet_bridge import compose_output


class LiveOrientationTests(unittest.TestCase):
    def connection(self):
        connection = display_io.DisplayConnection('COM38', orientation='Portrait')
        driver = Mock()
        state = {'orientation': display_io._ORIENTATIONS['Portrait']}
        driver.SetOrientation.side_effect = lambda value: state.update(orientation=value)
        portrait = [display_io._ORIENTATIONS['Portrait'], display_io._ORIENTATIONS['Reverse portrait']]
        driver.get_width.side_effect = lambda: 320 if state['orientation'] in portrait else 480
        driver.get_height.side_effect = lambda: 480 if state['orientation'] in portrait else 320
        connection._driver = driver
        return connection, driver

    def test_rotation_forces_full_frame_in_new_dimensions(self):
        c, d = self.connection()
        c.send(Image.new('RGB', c.size, '#10141b'))
        c.send(Image.new('RGB', c.size, '#10141b'))
        self.assertEqual(c.last_send_stats['bytes'], 0)
        for orientation in ['Landscape', 'Reverse landscape', 'Portrait', 'Reverse portrait']:
            c.set_orientation(orientation)
            self.assertIsNone(c._last_successful_rgb565)
            c.send(Image.new('RGB', c.size, '#10141b'))
            self.assertTrue(c.last_send_stats['full_frame'])
            self.assertEqual(c.last_send_stats['bytes'], c.size[0]*c.size[1]*2)
        self.assertEqual(d.SetOrientation.call_count, 4)

    def test_reselecting_same_orientation_is_noop(self):
        c, d = self.connection()
        c.send(Image.new('RGB', c.size, '#10141b'))
        previous = c._last_successful_rgb565
        c.set_orientation('Portrait')
        d.SetOrientation.assert_not_called()
        self.assertIs(c._last_successful_rgb565, previous)

    def test_failed_rotation_preserves_connection_state(self):
        c, d = self.connection()
        c.send(Image.new('RGB', c.size, '#10141b'))
        previous = c._last_successful_rgb565
        d.SetOrientation.side_effect = OSError('write rejected')
        with self.assertRaises(display_io.DisplayConnectionError):
            c.set_orientation('Landscape')
        self.assertEqual(c.orientation, 'Portrait')
        self.assertIs(c._last_successful_rgb565, previous)

    def test_layout_redraw_sends_even_unchanged_pixels(self):
        c, d = self.connection()
        image = Image.new('RGB', c.size, '#10141b')
        c.send(image)
        c.send(image)
        self.assertEqual(c.last_send_stats['bytes'], 0)
        c.request_full_redraw()
        c.send(image)
        self.assertTrue(c.last_send_stats['full_frame'])


class VisibleContentTests(unittest.TestCase):
    def source(self):
        image = Image.new('RGBA', (880, 1080), (0, 0, 0, 0))
        image.paste(Image.new('RGBA', (30, 100), '#d09b70'), (700, 820))
        image.paste(Image.new('RGBA', (20, 20), '#00ff00'), (20, 20))
        return image

    def test_large_manual_padding_does_not_make_visible_pet_tiny(self):
        source = self.source()
        before = source.tobytes()
        crop = (.5, .5, .9, .9)
        settings = {'crop': crop, 'pet_scale': 1., 'fit_visible': True, 'metrics_selected': []}
        output = np.asarray(compose_output(source, (320, 480), settings))
        skin = (output[:,:,0] > 120) & (output[:,:,1] < 180)
        rows = np.flatnonzero(skin.any(axis=1))
        self.assertGreater(rows[-1]-rows[0]+1, 400)
        self.assertFalse(((output[:,:,1] > 200) & (output[:,:,0] < 20)).any())
        self.assertEqual(source.tobytes(), before)
        self.assertEqual(settings['crop'], crop)

    def test_opaque_and_empty_frames_are_safe(self):
        for image in [Image.new('RGB', (100,100), '#123456'), Image.new('RGBA', (100,100), (0,0,0,0))]:
            self.assertEqual(compose_output(image, (320,480), {'fit_visible': True}).size, (320,480))


if __name__ == '__main__':
    unittest.main()
