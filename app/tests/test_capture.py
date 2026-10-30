from __future__ import annotations
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import threading
import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capture import LatestFrame, compose_frame, bgra_to_rgba, WindowCapture


class CaptureTests(unittest.TestCase):
    def test_bgra_channel_order(self):
        image = bgra_to_rgba(np.array([[[11, 22, 33, 255]]], dtype=np.uint8))
        self.assertEqual(image.getpixel((0, 0)), (33, 22, 11, 255))

    def test_premultiplied_alpha(self):
        image = bgra_to_rgba(np.array([[[25, 50, 100, 128]]], dtype=np.uint8))
        self.assertEqual(image.getpixel((0, 0)), (199, 100, 50, 128))

    def test_invalid_buffer(self):
        with self.assertRaises(ValueError):
            bgra_to_rgba(np.zeros((2, 2, 3), dtype=np.uint8))

    def test_latest_frame_is_bounded(self):
        frames = LatestFrame()
        self.assertEqual(frames.get()[0], None)
        for color in ['red', 'green', 'blue']:
            frames.put(Image.new('RGBA', (10, 10), color))
        image, seq, timestamp = frames.get()
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 255, 255))
        self.assertEqual(seq, 3)
        self.assertGreater(timestamp, 0)

    def test_fit_preserves_aspect_and_all_edges(self):
        source = Image.new('RGBA', (100, 200), 'red')
        output = compose_frame(source, (480, 320), background='black')
        self.assertEqual(output.size, (480, 320))
        self.assertEqual(output.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(output.getpixel((160, 0)), (255, 0, 0))
        self.assertEqual(output.getpixel((319, 319)), (255, 0, 0))
        self.assertEqual(output.getpixel((320, 319)), (0, 0, 0))

    def test_fixed_crop(self):
        source = Image.new('RGBA', (100, 100), 'red')
        source.paste(Image.new('RGBA', (50, 100), 'blue'), (50, 0))
        output = compose_frame(source, (50, 100), crop=(.5, 0, 1, 1), pixel_art=True)
        self.assertEqual(output.getpixel((0, 0)), (0, 0, 255))
        self.assertEqual(output.getpixel((49, 99)), (0, 0, 255))

    def test_alpha_compositing_and_ignore_alpha(self):
        source = Image.new('RGBA', (1, 1), (255, 0, 0, 0))
        self.assertEqual(compose_frame(source, (1, 1), background='blue').getpixel((0, 0)), (0, 0, 255))
        self.assertEqual(compose_frame(source, (1, 1), ignore_alpha=True).getpixel((0, 0)), (255, 0, 0))

    def test_invalid_crop_rejected(self):
        source = Image.new('RGB', (10, 10))
        for crop in [(0, 0, 0, 1), (-.1, 0, 1, 1), (0, 0, 2, 1)]:
            with self.assertRaises(ValueError):
                compose_frame(source, (10, 10), crop=crop)

    def test_native_capture_targets_hwnd_not_ambiguous_title(self):
        fake = Mock()
        fake.event.side_effect = lambda fn: setattr(fake, fn.__name__, fn) or fn
        constructor = Mock(return_value=fake)
        module = Mock(WindowsCapture=constructor)
        user = Mock()
        frames = LatestFrame()
        stopped = threading.Event()
        fake.start_free_threaded.return_value.stop.side_effect = stopped.set
        with patch('capture._winapi', return_value=(user, None, None)), patch.dict(sys.modules, {'windows_capture': module}):
            capture = WindowCapture(0x123456788765, frames, Mock())
            capture.start()
            constructor.assert_called_once_with(cursor_capture=False, draw_border=False, secondary_window=None,
                                                minimum_update_interval=None, window_hwnd=0x123456788765)
            frame = Mock(frame_buffer=np.array([[[0, 0, 255, 255]]], dtype=np.uint8))
            fake.on_frame_arrived(frame, Mock())
            self.assertEqual(frames.get()[0].getpixel((0, 0)), (255, 0, 0, 255))
            capture.stop()
            self.assertTrue(stopped.wait(1))
            fake.start_free_threaded.return_value.stop.assert_called_once()

    def test_borderless_rejection_retries_system_default(self):
        rejected, allowed = Mock(), Mock()
        rejected.start_free_threaded.side_effect = RuntimeError('borderless not permitted')
        constructor = Mock(side_effect=[rejected, allowed])
        module = Mock(WindowsCapture=constructor)
        stopped = threading.Event()
        allowed.start_free_threaded.return_value.stop.side_effect = stopped.set
        with patch('capture._winapi', return_value=(Mock(), None, None)), patch.dict(sys.modules, {'windows_capture': module}):
            capture = WindowCapture(123, LatestFrame(), Mock())
            capture.start()
            self.assertEqual(constructor.call_count, 2)
            self.assertFalse(capture.border_hidden)
            self.assertFalse(constructor.call_args_list[0].kwargs['draw_border'])
            self.assertIsNone(constructor.call_args_list[1].kwargs['draw_border'])
            capture.stop()
            self.assertTrue(stopped.wait(1))

if __name__ == '__main__':
    unittest.main()
