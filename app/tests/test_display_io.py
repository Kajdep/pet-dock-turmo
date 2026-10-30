from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from PIL import Image
import serial

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import display_io


SUPPORTED_PORT = SimpleNamespace(
    device="COM7",
    serial_number="USB35INCHIPSV2",
    vid=0x1A86,
    pid=0x5722,
)


class FakeSerial:
    def __init__(self, read_data: bytes = b"", out_waiting: int = 0) -> None:
        self.read_data = read_data
        self.is_open = True
        self.out_waiting = out_waiting
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def read(self, size: int) -> bytes:
        return self.read_data[:size]

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        self.is_open = False


class DisplayDiscoveryTests(unittest.TestCase):
    @patch("display_io.list_ports.comports")
    def test_list_displays_filters_by_exact_vid_and_pid(self, comports: Mock) -> None:
        comports.return_value = [
            SimpleNamespace(device="COM11", serial_number="wrong-pid", vid=0x1A86, pid=0x7523),
            SimpleNamespace(device="COM9", serial_number="two", vid=0x1A86, pid=0x5722),
            SUPPORTED_PORT,
        ]

        self.assertEqual(
            display_io.list_displays(),
            [
                {
                    "device": "COM7",
                    "label": "COM7 · USB 1A86:5722",
                    "serial_number": "USB35INCHIPSV2",
                    "vid": 0x1A86,
                    "pid": 0x5722,
                },
                {
                    "device": "COM9",
                    "label": "COM9 · USB 1A86:5722",
                    "serial_number": "two",
                    "vid": 0x1A86,
                    "pid": 0x5722,
                },
            ],
        )

    @patch("display_io.serial.Serial")
    @patch("display_io.list_ports.comports")
    def test_open_refuses_selected_unrelated_serial_device(self, comports: Mock, serial_ctor: Mock) -> None:
        comports.return_value = [
            SimpleNamespace(device="COM7", serial_number="other", vid=0x1234, pid=0x5678)
        ]

        connection = display_io.DisplayConnection("COM7")
        with self.assertRaisesRegex(display_io.UnsupportedDeviceError, "Refusing to send"):
            connection.open()
        serial_ctor.assert_not_called()


class DisplayConnectionTests(unittest.TestCase):
    def _open_with_mock_driver(
        self,
        revision: str = "A",
        orientation: str = "Landscape",
        **connection_options: object,
    ):
        driver = Mock()
        driver.get_width.return_value = 480
        driver.get_height.return_value = 320
        driver_class = "display_io._SafeRevA" if revision == "A" else "display_io._SafeRevB"
        with (
            patch("display_io.list_ports.comports", return_value=[SUPPORTED_PORT]),
            patch(driver_class, return_value=driver) as constructor,
        ):
            connection = display_io.DisplayConnection(
                "COM7", revision=revision, orientation=orientation, brightness=37, **connection_options
            )
            self.assertIs(connection.open(), connection)
        return connection, driver, constructor

    @staticmethod
    def _frame(color: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
        return Image.new("RGB", (480, 320), color)

    def test_open_uses_a_default_and_preserves_required_call_order(self) -> None:
        connection, driver, constructor = self._open_with_mock_driver()

        constructor.assert_called_once_with(com_port="COM7")
        self.assertEqual(
            driver.method_calls,
            [
                call.InitializeComm(),
                call.SetOrientation(display_io.Orientation.LANDSCAPE),
                call.SetBrightness(37),
            ],
        )
        driver.ScreenOn.assert_not_called()
        self.assertEqual(connection.size, (480, 320))

    def test_open_b_uses_selected_protocol_and_never_calls_screen_on(self) -> None:
        _, driver, constructor = self._open_with_mock_driver("B", "Reverse landscape")

        constructor.assert_called_once_with(com_port="COM7")
        self.assertEqual(
            driver.method_calls,
            [
                call.InitializeComm(),
                call.SetOrientation(display_io.Orientation.REVERSE_LANDSCAPE),
                call.SetBrightness(37),
            ],
        )
        driver.ScreenOn.assert_not_called()

    def test_send_resizes_to_display_and_uses_complete_frame_api(self) -> None:
        connection, driver, _ = self._open_with_mock_driver()
        source = Image.new("RGBA", (24, 12), (1, 2, 3, 255))

        connection.send(source)

        driver.DisplayPILImage.assert_called_once()
        sent = driver.DisplayPILImage.call_args.args[0]
        self.assertEqual(sent.mode, "RGB")
        self.assertEqual(sent.size, (480, 320))
        self.assertEqual(len(driver.DisplayPILImage.call_args.args), 1)
        self.assertFalse(driver.DisplayPILImage.call_args.kwargs)
        self.assertEqual(
            connection.last_send_stats,
            {
                "bytes": 480 * 320 * 2,
                "regions": 1,
                "full_frame": True,
                "send_ms": connection.last_send_stats["send_ms"],
            },
        )
        self.assertIsInstance(connection.last_send_stats["send_ms"], float)
        self.assertGreaterEqual(connection.last_send_stats["send_ms"], 0.0)

    def test_small_rgb565_change_uses_a_single_partial_rectangle(self) -> None:
        connection, driver, _ = self._open_with_mock_driver()
        first = self._frame()
        connection.send(first)
        driver.reset_mock()

        changed = first.copy()
        changed.putpixel((41, 73), (255, 0, 0))
        connection.send(changed)

        driver.DisplayPILImage.assert_called_once()
        region, x, y = driver.DisplayPILImage.call_args.args
        self.assertEqual((x, y), (41, 73))
        self.assertEqual(region.size, (1, 1))
        self.assertEqual(region.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(
            connection.last_send_stats,
            {"bytes": 2, "regions": 1, "full_frame": False, "send_ms": connection.last_send_stats["send_ms"]},
        )

    def test_rgb_values_that_encode_to_the_same_rgb565_are_skipped(self) -> None:
        connection, driver, _ = self._open_with_mock_driver()
        first = self._frame()
        connection.send(first)
        driver.reset_mock()

        visually_equivalent = first.copy()
        # (1, 1, 1) rounds down to the same RGB565 value as black.
        visually_equivalent.putpixel((100, 100), (1, 1, 1))
        connection.send(visually_equivalent)

        driver.DisplayPILImage.assert_not_called()
        self.assertEqual(
            connection.last_send_stats,
            {"bytes": 0, "regions": 0, "full_frame": False, "send_ms": 0.0},
        )

    def test_moved_pet_repaints_its_old_pixels(self) -> None:
        connection, driver, _ = self._open_with_mock_driver()
        background = self._frame()
        old_position = background.copy()
        for y in range(20, 24):
            for x in range(10, 15):
                old_position.putpixel((x, y), (255, 0, 0))
        connection.send(old_position)
        driver.reset_mock()

        new_position = background.copy()
        for y in range(20, 24):
            for x in range(30, 35):
                new_position.putpixel((x, y), (255, 0, 0))
        connection.send(new_position)

        driver.DisplayPILImage.assert_called_once()
        region, x, y = driver.DisplayPILImage.call_args.args
        self.assertEqual((x, y), (10, 20))
        self.assertEqual(region.size, (25, 4))
        self.assertEqual(region.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(region.getpixel((20, 0)), (255, 0, 0))

    def test_pet_and_bottom_metrics_use_separate_vertical_regions(self) -> None:
        connection, driver, _ = self._open_with_mock_driver()
        first = self._frame()
        connection.send(first)
        driver.reset_mock()

        changed = first.copy()
        changed.putpixel((12, 20), (255, 0, 0))
        changed.putpixel((250, 280), (0, 255, 0))
        connection.send(changed)

        self.assertEqual(driver.DisplayPILImage.call_count, 2)
        first_region, first_x, first_y = driver.DisplayPILImage.call_args_list[0].args
        second_region, second_x, second_y = driver.DisplayPILImage.call_args_list[1].args
        self.assertEqual((first_x, first_y, first_region.size), (12, 20, (1, 1)))
        self.assertEqual((second_x, second_y, second_region.size), (250, 280, (1, 1)))
        self.assertEqual(connection.last_send_stats["bytes"], 4)
        self.assertEqual(connection.last_send_stats["regions"], 2)
        self.assertFalse(connection.last_send_stats["full_frame"])

    def test_delta_regions_are_capped_at_six(self) -> None:
        connection, driver, _ = self._open_with_mock_driver()
        first = self._frame()
        connection.send(first)
        driver.reset_mock()

        changed = first.copy()
        # Seven isolated changed rows require one low-cost merge, rather than
        # sending more than the bounded number of rectangle commands.
        for y in range(10, 80, 10):
            changed.putpixel((20, y), (255, 0, 0))
        connection.send(changed)

        self.assertLessEqual(driver.DisplayPILImage.call_count, 6)
        self.assertEqual(connection.last_send_stats["regions"], 6)
        self.assertFalse(connection.last_send_stats["full_frame"])

    def test_large_delta_uses_full_frame_at_seventy_percent_threshold(self) -> None:
        connection, driver, _ = self._open_with_mock_driver()
        first = self._frame()
        connection.send(first)
        driver.reset_mock()

        changed = self._frame((255, 0, 0))
        connection.send(changed)

        driver.DisplayPILImage.assert_called_once_with(changed)
        self.assertTrue(connection.last_send_stats["full_frame"])
        self.assertEqual(connection.last_send_stats["bytes"], 480 * 320 * 2)

    def test_efficient_updates_disabled_always_uses_full_frames(self) -> None:
        connection, driver, _ = self._open_with_mock_driver(efficient_updates=False)
        first = self._frame()
        connection.send(first)
        driver.reset_mock()

        changed = first.copy()
        changed.putpixel((1, 1), (255, 0, 0))
        connection.send(changed)

        driver.DisplayPILImage.assert_called_once_with(changed)
        self.assertTrue(connection.last_send_stats["full_frame"])
        self.assertEqual(connection.last_send_stats["bytes"], 480 * 320 * 2)

    def test_revision_b_falls_back_to_full_frames(self) -> None:
        connection, driver, _ = self._open_with_mock_driver("B")
        first = self._frame()
        connection.send(first)
        driver.reset_mock()

        changed = first.copy()
        changed.putpixel((1, 1), (255, 0, 0))
        connection.send(changed)

        driver.DisplayPILImage.assert_called_once_with(changed)
        self.assertTrue(connection.last_send_stats["full_frame"])

    def test_wait_ready_uses_output_queue_without_flushing(self) -> None:
        connection, driver, _ = self._open_with_mock_driver()
        serial_handle = FakeSerial(out_waiting=0)
        driver.lcd_serial = serial_handle

        self.assertTrue(connection.wait_ready(0.01))
        self.assertEqual(serial_handle.writes, [])

    def test_wait_ready_accepts_serial_mocks_without_out_waiting(self) -> None:
        connection, driver, _ = self._open_with_mock_driver()
        driver.lcd_serial = SimpleNamespace(is_open=True)

        self.assertTrue(connection.wait_ready(0.01))

    def test_wait_ready_times_out_with_bounded_polling(self) -> None:
        connection, driver, _ = self._open_with_mock_driver()
        driver.lcd_serial = FakeSerial(out_waiting=64)

        with (
            patch("display_io.time.monotonic", side_effect=[100.0, 100.0, 100.2]),
            patch("display_io.time.sleep") as sleep,
        ):
            self.assertFalse(connection.wait_ready(0.1))

        sleep.assert_called_once_with(display_io._READY_POLL_SECONDS)

    def test_size_and_send_require_an_open_connection(self) -> None:
        connection = display_io.DisplayConnection("COM7")
        with self.assertRaises(display_io.DisplayNotOpenError):
            _ = connection.size
        with self.assertRaises(display_io.DisplayNotOpenError):
            connection.send(Image.new("RGB", (1, 1)))

    def test_rejects_invalid_options(self) -> None:
        with self.assertRaises(ValueError):
            display_io.DisplayConnection("COM7", revision="C")
        with self.assertRaises(ValueError):
            display_io.DisplayConnection("COM7", orientation="Sideways")
        with self.assertRaises(ValueError):
            display_io.DisplayConnection("COM7", brightness=101)
        with self.assertRaises(TypeError):
            display_io.DisplayConnection("COM7", efficient_updates=1)  # type: ignore[arg-type]


class BoundedSerialTests(unittest.TestCase):
    @patch("display_io.serial.Serial")
    def test_revision_a_write_failure_raises_without_reopen_or_exit(self, serial_ctor: Mock) -> None:
        fake_serial = FakeSerial()
        serial_ctor.return_value = fake_serial
        driver = display_io._SafeRevA(com_port="COM7")
        fake_serial.write = Mock(side_effect=serial.SerialException("cable removed"))

        with self.assertRaisesRegex(display_io.DisplayConnectionError, "failed writing"):
            driver.WriteLine(b"abc")

        serial_ctor.assert_called_once_with(
            "COM7",
            115200,
            timeout=1.0,
            write_timeout=1.0,
            rtscts=True,
        )
        self.assertEqual(serial_ctor.call_count, 1)

    @patch("display_io.serial.Serial")
    @patch("display_io.list_ports.comports", return_value=[SUPPORTED_PORT])
    def test_bad_revision_b_hello_becomes_descriptive_connection_error(
        self, _comports: Mock, serial_ctor: Mock
    ) -> None:
        serial_ctor.return_value = FakeSerial(read_data=b"")
        connection = display_io.DisplayConnection("COM7", revision="B")

        with self.assertRaisesRegex(display_io.DisplayConnectionError, "initialization failed"):
            connection.open()

        self.assertEqual(serial_ctor.call_count, 1)


if __name__ == "__main__":
    unittest.main()
