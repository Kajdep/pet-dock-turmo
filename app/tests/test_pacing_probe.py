"""Vendor timing and passive probe checks; no physical serial writes."""
import sys
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import display_io
import device_probe


class VendorPacingTests(unittest.TestCase):
    def driver(self, enabled):
        driver = display_io._SafeRevA.__new__(display_io._SafeRevA)
        driver.com_port = 'COM38'
        driver.lcd_serial = None
        driver.update_queue = None
        driver.update_queue_mutex = threading.Lock()
        driver.get_width = Mock(return_value=320)
        driver.get_height = Mock(return_value=480)
        driver.compatible_pacing = enabled
        return driver

    def test_header_delay_then_pixels_then_post_delay(self):
        events = []
        driver = self.driver(True)
        driver.serial_write = Mock(side_effect=lambda data: events.append('header' if len(data) == 6 else 'pixels'))
        with patch.object(display_io.time, 'sleep', side_effect=lambda delay: events.append(delay)):
            driver.DisplayPILImage(Image.new('RGB', (2, 2), '#abcdef'))
        self.assertEqual(events, ['header', .025, 'pixels', .020])

    def test_compatibility_delay_can_be_disabled(self):
        driver = self.driver(False)
        driver.serial_write = Mock()
        with patch.object(display_io.time, 'sleep') as delay:
            driver.DisplayPILImage(Image.new('RGB', (2, 2), '#abcdef'))
        delay.assert_not_called()
        self.assertEqual(driver.serial_write.call_count, 2)


class PassiveProbeTests(unittest.TestCase):
    def test_only_exact_device_is_reported_without_opening_port(self):
        supported = SimpleNamespace(vid=0x1A86, pid=0x5722, device='COM38', description='USB serial')
        other = SimpleNamespace(vid=0x1A86, pid=0x7523, device='COM7')
        with patch.object(device_probe.list_ports, 'comports', return_value=[other, supported]), patch.object(device_probe, '_registry_device_info', return_value=[]), patch.object(device_probe, 'DisplayConnection') as connection:
            report = device_probe.collect_device_info()
        self.assertEqual([p['device'] for p in report['matching_ports']], ['COM38'])
        connection.assert_not_called()
        self.assertIn('Unknown', report['known_vs_unknown']['physical_max_fps'])


if __name__ == '__main__':
    unittest.main()
