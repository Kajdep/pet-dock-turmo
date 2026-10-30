"""Safe serial I/O for TM03501A-compatible Turing smart displays.

Both known board revisions enumerate as USB VID 1A86 / PID 5722.  USB identity
therefore establishes that a selected port is an eligible display, but cannot
identify its protocol revision.  Revision A is the default; callers must opt
into revision B explicitly.
"""

from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Final

import numpy as np
from PIL import Image
import serial
from serial.tools import list_ports

# The upstream modules use absolute ``library.*`` imports. Keep the vendored
# subset private to this adapter while allowing those imports to resolve.
_VENDOR_ROOT = Path(__file__).resolve().parent / "vendor"
if str(_VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_VENDOR_ROOT))

from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_rev_a import LcdCommRevA, Command as RevACommand
from library.lcd.lcd_comm_rev_b import LcdCommRevB

SUPPORTED_VID: Final[int] = 0x1A86
SUPPORTED_PID: Final[int] = 0x5722
DEFAULT_BRIGHTNESS: Final[int] = 60
_SERIAL_TIMEOUT_SECONDS: Final[float] = 1.0
_DELTA_FULL_FRAME_THRESHOLD: Final[float] = 0.70
_MAX_DELTA_REGIONS: Final[int] = 6
_READY_POLL_SECONDS: Final[float] = 0.02


class DisplayIOError(RuntimeError):
    """Base exception for predictable display discovery and I/O failures."""


class UnsupportedDeviceError(DisplayIOError):
    """The requested serial port is not the supported USB device."""


class DisplayConnectionError(DisplayIOError):
    """A selected display could not be opened, initialized, or written."""


class DisplayNotOpenError(DisplayIOError):
    """An operation requires :meth:`DisplayConnection.open` first."""


_ORIENTATIONS: Final[dict[str, Orientation]] = {
    "Portrait": Orientation.PORTRAIT,
    "Landscape": Orientation.LANDSCAPE,
    "Reverse portrait": Orientation.REVERSE_PORTRAIT,
    "Reverse landscape": Orientation.REVERSE_LANDSCAPE,
}


def _usb_id(value: Any) -> int | None:
    """Return a USB id from pyserial's value, without treating missing ids as 0."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return None
    return None


def _is_supported_port(port: Any) -> bool:
    return (
        _usb_id(getattr(port, "vid", None)) == SUPPORTED_VID
        and _usb_id(getattr(port, "pid", None)) == SUPPORTED_PID
    )


def _all_serial_ports() -> list[Any]:
    try:
        return list(list_ports.comports())
    except Exception as exc:  # pyserial can surface OS enumeration failures.
        raise DisplayConnectionError(f"Unable to enumerate serial ports: {exc}") from exc


def list_displays() -> list[dict]:
    """List compatible serial displays without sending any device commands.

    The two supported protocol revisions have the same USB identity.  Each
    result deliberately describes revision A as the default and revision B as
    an explicit option rather than trying (or probing) both protocols.
    """
    displays: list[dict] = []
    for port in _all_serial_ports():
        if not _is_supported_port(port):
            continue
        device = getattr(port, "device", None)
        if not device:
            continue
        displays.append(
            {
                "device": device,
                "label": f"{device} · USB 1A86:5722",
                "serial_number": getattr(port, "serial_number", None),
                "vid": SUPPORTED_VID,
                "pid": SUPPORTED_PID,
            }
        )
    return sorted(displays, key=lambda display: str(display["device"]).casefold())


def _same_port(requested: str, discovered: str) -> bool:
    # COM port names are case-insensitive on Windows. Do not alter POSIX device
    # names, where case may be meaningful.
    if os.name == "nt":
        return requested.casefold() == discovered.casefold()
    return requested == discovered


def _supported_port_or_raise(port_name: str) -> Any:
    for port in _all_serial_ports():
        discovered_name = getattr(port, "device", None)
        if discovered_name and _same_port(port_name, discovered_name):
            if _is_supported_port(port):
                return port
            actual_vid = getattr(port, "vid", None)
            actual_pid = getattr(port, "pid", None)
            raise UnsupportedDeviceError(
                f"Refusing to send TM03501A commands to {port_name}: expected USB "
                f"VID:PID {SUPPORTED_VID:04X}:{SUPPORTED_PID:04X}, found "
                f"{actual_vid!r}:{actual_pid!r}."
            )
    raise UnsupportedDeviceError(
        f"Refusing to open {port_name}: it is not currently an enumerated "
        f"TM03501A-compatible USB serial device ({SUPPORTED_VID:04X}:{SUPPORTED_PID:04X})."
    )


class _BoundedSerialMixin:
    """Replace upstream exit/reopen behavior with one bounded serial operation."""

    def openSerial(self) -> None:  # noqa: N802 - name required by upstream driver
        existing = getattr(self, "lcd_serial", None)
        if existing is not None:
            try:
                existing.close()
            except Exception:
                # The new open is authoritative; report any failure below.
                pass
            self.lcd_serial = None

        try:
            serial_handle = serial.Serial(
                self.com_port,
                115200,
                timeout=_SERIAL_TIMEOUT_SECONDS,
                write_timeout=_SERIAL_TIMEOUT_SECONDS,
                rtscts=True,
            )
        except Exception as exc:
            raise DisplayConnectionError(
                f"Unable to open display serial port {self.com_port!r} within "
                f"{_SERIAL_TIMEOUT_SECONDS:g}s: {exc}"
            ) from exc

        if not getattr(serial_handle, "is_open", True):
            try:
                serial_handle.close()
            except Exception:
                pass
            raise DisplayConnectionError(f"Display serial port {self.com_port!r} did not open.")
        self.lcd_serial = serial_handle

    def closeSerial(self) -> None:  # noqa: N802 - name required by upstream driver
        serial_handle = getattr(self, "lcd_serial", None)
        self.lcd_serial = None
        if serial_handle is None:
            return
        try:
            serial_handle.close()
        except Exception as exc:
            raise DisplayConnectionError(
                f"Unable to close display serial port {self.com_port!r}: {exc}"
            ) from exc

    def _serial_handle(self) -> Any:
        serial_handle = getattr(self, "lcd_serial", None)
        if serial_handle is None or not getattr(serial_handle, "is_open", True):
            raise DisplayConnectionError(f"Display serial port {self.com_port!r} is not open.")
        return serial_handle

    def serial_write(self, data: bytes) -> None:
        try:
            written = self._serial_handle().write(data)
        except DisplayConnectionError:
            raise
        except Exception as exc:
            raise DisplayConnectionError(
                f"Timed out or failed writing {len(data)} byte(s) to display port "
                f"{self.com_port!r}: {exc}"
            ) from exc
        if written is not None and written != len(data):
            raise DisplayConnectionError(
                f"Timed out writing to display port {self.com_port!r}: wrote {written} of {len(data)} byte(s)."
            )

    def serial_read(self, size: int) -> bytes:
        try:
            response = self._serial_handle().read(size)
        except DisplayConnectionError:
            raise
        except Exception as exc:
            raise DisplayConnectionError(
                f"Timed out or failed reading {size} byte(s) from display port "
                f"{self.com_port!r}: {exc}"
            ) from exc
        return bytes(response or b"")

    def serial_readall(self) -> bytes:
        try:
            serial_handle = self._serial_handle()
            read_all = getattr(serial_handle, "read_all", None)
            response = read_all() if callable(read_all) else serial_handle.readall()
        except DisplayConnectionError:
            raise
        except Exception as exc:
            raise DisplayConnectionError(
                f"Failed reading buffered data from display port {self.com_port!r}: {exc}"
            ) from exc
        return bytes(response or b"")

    def serial_flush_input(self) -> None:
        try:
            self._serial_handle().reset_input_buffer()
        except DisplayConnectionError:
            raise
        except Exception as exc:
            raise DisplayConnectionError(
                f"Failed clearing input on display port {self.com_port!r}: {exc}"
            ) from exc

    # Upstream WriteLine catches pyserial failures, sleeps, reopens the port,
    # and can ultimately exit the process. Directly dispatch instead so the
    # caller receives the bounded exception above.
    def WriteLine(self, line: bytes) -> None:  # noqa: N802 - upstream API name
        self.serial_write(line)

    def ReadData(self, readSize: int) -> bytes:  # noqa: N802 - upstream API name
        return self.serial_read(readSize)


class _SafeRevA(_BoundedSerialMixin, LcdCommRevA):
    """Revision A upstream protocol with safe serial lifecycle behavior."""

    def SendCommand(self, cmd, x, y, ex, ey, bypass_queue=False):
        super().SendCommand(cmd, x, y, ex, ey, bypass_queue=bypass_queue)
        if cmd == RevACommand.DISPLAY_BITMAP and getattr(self, 'compatible_pacing', True):
            # Official TURMO 5.5.13 sendCMD sleeps 25 ms after bitmap headers.
            time.sleep(.025)

    def DisplayPILImage(self, image, x=0, y=0, image_width=0, image_height=0):
        super().DisplayPILImage(image, x, y, image_width, image_height)
        if getattr(self, 'compatible_pacing', True):
            # Official RenderBitmap leaves a further 20 ms after pixel submission.
            time.sleep(.020)


class _SafeRevB(_BoundedSerialMixin, LcdCommRevB):
    """Revision B upstream protocol with safe serial lifecycle behavior."""


class DisplayConnection:
    """A selected TM03501A-compatible display connection.

    The connection opens exactly the supplied port and exactly the selected
    protocol revision. It never issues reset, firmware, or driver commands.
    """

    def __init__(
        self,
        port: str,
        revision: str = "A",
        orientation: str = "Landscape",
        brightness: int = DEFAULT_BRIGHTNESS,
        efficient_updates: bool = True,
        compatible_pacing: bool = True,
    ) -> None:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("port must be a non-empty serial port name")
        if not isinstance(revision, str) or revision.upper() not in {"A", "B"}:
            raise ValueError("revision must be 'A' (default) or 'B'")
        if orientation not in _ORIENTATIONS:
            choices = ", ".join(_ORIENTATIONS)
            raise ValueError(f"orientation must be one of: {choices}")
        if isinstance(brightness, bool) or not isinstance(brightness, int) or not 0 <= brightness <= 100:
            raise ValueError("brightness must be an integer from 0 through 100")
        if not isinstance(efficient_updates, bool):
            raise TypeError("efficient_updates must be a bool")
        if not isinstance(compatible_pacing, bool):
            raise TypeError("compatible_pacing must be a bool")

        self.port = port.strip()
        self.revision = revision.upper()
        self.orientation = orientation
        self.brightness = brightness
        self.efficient_updates = efficient_updates
        self.compatible_pacing = compatible_pacing
        self._driver: _SafeRevA | _SafeRevB | None = None
        # This cache only advances after every requested region has been sent.
        # Values are RGB565 pixels, exactly matching the fidelity of the LCD
        # payload rather than the source image's 24-bit RGB representation.
        self._last_successful_rgb565: np.ndarray | None = None
        # ``bytes`` is RGB565 pixel payload only; upstream adds its own small
        # per-rectangle command headers and retains ownership of chunking.
        self.last_send_stats: dict[str, int | float | bool] = {
            "bytes": 0,
            "regions": 0,
            "full_frame": False,
            "send_ms": 0.0,
        }

    @property
    def size(self) -> tuple[int, int]:
        """Current pixel dimensions after the display has been initialized."""
        driver = self._require_driver()
        return int(driver.get_width()), int(driver.get_height())

    def open(self) -> "DisplayConnection":
        """Validate, open, and initialize the selected protocol once.

        Initialization is deliberately ordered as upstream protocol hello,
        orientation, then brightness. In particular no ``ScreenOn`` follows
        brightness: revision B implements ``ScreenOn`` as a brightness write
        that would overwrite the caller's selected level.
        """
        if self._driver is not None:
            return self

        selected_port = _supported_port_or_raise(self.port)
        selected_name = str(getattr(selected_port, "device", self.port))
        driver_class = _SafeRevA if self.revision == "A" else _SafeRevB
        driver: _SafeRevA | _SafeRevB | None = None
        try:
            driver = driver_class(com_port=selected_name)
            driver.compatible_pacing = self.compatible_pacing
            driver.InitializeComm()
            driver.SetOrientation(_ORIENTATIONS[self.orientation])
            driver.SetBrightness(self.brightness)
        except DisplayIOError:
            self._close_after_failed_open(driver)
            raise
        except Exception as exc:
            self._close_after_failed_open(driver)
            raise DisplayConnectionError(
                f"Display protocol initialization failed on {selected_name!r} using revision {self.revision}: {exc}"
            ) from exc

        self._driver = driver
        return self

    def set_orientation(self, orientation: str) -> None:
        """Apply on the owning USB worker between drained image transfers."""
        if orientation not in _ORIENTATIONS:
            raise ValueError(f'Unsupported display orientation: {orientation!r}')
        driver = self._require_driver()
        if orientation == self.orientation:
            return
        try:
            driver.SetOrientation(_ORIENTATIONS[orientation])
        except DisplayIOError:
            raise
        except Exception as exc:
            raise DisplayConnectionError(f'Failed changing orientation on {self.port!r}: {exc}') from exc
        self.orientation = orientation
        self._last_successful_rgb565 = None

    def request_full_redraw(self) -> None:
        """Forget the delta base after a layout change; call on the USB worker."""
        self._require_driver()
        self._last_successful_rgb565 = None

    def send(self, image: Image.Image) -> None:
        """Fit ``image`` to the display and send a full frame or revision-A delta.

        The first successful frame is always complete.  Subsequent revision-A
        frames may use the upstream ``DisplayPILImage(image, x, y)`` rectangle
        API when their RGB565 payload is meaningfully smaller.  Revision B keeps
        its known full-frame path for reliability.
        """
        if not isinstance(image, Image.Image):
            raise TypeError("image must be a PIL.Image.Image")

        driver = self._require_driver()
        width, height = self.size
        frame = image if image.mode == "RGB" else image.convert("RGB")
        if frame.size != (width, height):
            frame = frame.resize((width, height), Image.Resampling.LANCZOS)

        current_rgb565 = self._rgb565_pixels(frame)
        full_payload_bytes = width * height * 2
        regions: list[tuple[int, int, int, int]] = []
        full_frame = True

        # Revision A has a documented, tested rectangular update command. Keep
        # revision B on full frames even though its method signature accepts
        # coordinates, avoiding a protocol-specific reliability regression.
        if (
            self.efficient_updates
            and self.revision == "A"
            and self._last_successful_rgb565 is not None
            and self._last_successful_rgb565.shape == current_rgb565.shape
        ):
            regions = self._delta_regions(self._last_successful_rgb565, current_rgb565)
            if not regions:
                # No serial command was issued, so there is no transport time
                # to report. The old successful cache remains authoritative.
                self.last_send_stats = {
                    "bytes": 0,
                    "regions": 0,
                    "full_frame": False,
                    "send_ms": 0.0,
                }
                return

            delta_payload_bytes = sum(region_width * region_height * 2 for _, _, region_width, region_height in regions)
            if delta_payload_bytes < full_payload_bytes * _DELTA_FULL_FRAME_THRESHOLD:
                full_frame = False
            else:
                regions = []

        send_started = time.perf_counter()
        try:
            if full_frame:
                driver.DisplayPILImage(frame)
                payload_bytes = full_payload_bytes
                region_count = 1
            else:
                for x, y, region_width, region_height in regions:
                    region = frame.crop((x, y, x + region_width, y + region_height))
                    # Keep upstream's payload chunking and pass only its
                    # documented image/x/y arguments for a partial update.
                    driver.DisplayPILImage(region, x, y)
                payload_bytes = sum(
                    region_width * region_height * 2 for _, _, region_width, region_height in regions
                )
                region_count = len(regions)
        except DisplayIOError:
            raise
        except Exception as exc:
            raise DisplayConnectionError(f"Failed to send frame to {self.port!r}: {exc}") from exc

        # Do not advance this cache before all regions have been accepted by the
        # driver; a failed partial send must be retried against the last frame
        # known to have been sent successfully.
        self._last_successful_rgb565 = current_rgb565
        self.last_send_stats = {
            "bytes": payload_bytes,
            "regions": region_count,
            "full_frame": full_frame,
            "send_ms": (time.perf_counter() - send_started) * 1000,
        }

    def wait_ready(self, timeout: float = 6.0) -> bool:
        """Wait at most ``timeout`` seconds for the serial output queue to drain.

        This polls pyserial's actual ``out_waiting`` count; it intentionally
        never flushes or resets the output buffer, which could interrupt an
        in-flight bitmap command.  Serial doubles that do not expose
        ``out_waiting`` are treated as ready so existing callers and tests are
        not broken by an optional pyserial capability.
        """
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("timeout must be a non-negative number of seconds")

        driver = self._require_driver()
        serial_handle = getattr(driver, "lcd_serial", None)
        if serial_handle is None:
            return True
        if getattr(serial_handle, "is_open", True) is False:
            return False

        deadline = time.monotonic() + float(timeout)
        while True:
            try:
                pending = getattr(serial_handle, "out_waiting")
            except AttributeError:
                return True
            except Exception:
                return False

            # ``Mock.out_waiting`` and non-pyserial serial implementations are
            # not a measurable byte count. Do not make those mocks unusable.
            if isinstance(pending, bool) or not isinstance(pending, (int, float)):
                return True
            if pending <= 0:
                return True

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(_READY_POLL_SECONDS, remaining))

    def close(self) -> None:
        """Close the serial connection. It is safe to call more than once."""
        driver = self._driver
        self._driver = None
        self._last_successful_rgb565 = None
        if driver is not None:
            driver.closeSerial()

    @staticmethod
    def _rgb565_pixels(frame: Image.Image) -> np.ndarray:
        """Return the RGB565 value of each RGB source pixel as ``uint16``."""
        pixels = np.asarray(frame, dtype=np.uint8)
        red = pixels[..., 0].astype(np.uint16) >> 3
        green = pixels[..., 1].astype(np.uint16) >> 2
        blue = pixels[..., 2].astype(np.uint16) >> 3
        return (red << 11) | (green << 5) | blue

    @staticmethod
    def _delta_regions(previous: np.ndarray, current: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Return at most six tight rectangles over changed RGB565 pixels.

        Initially, each contiguous run of changed rows is separate. Blank
        vertical gaps therefore keep a moving pet and independent bottom
        metrics from becoming one huge bounding union. If there are more than
        six runs, merge the adjacent pair with the least added area until the
        documented command budget is met.
        """
        changed = previous != current
        changed_rows = np.flatnonzero(np.any(changed, axis=1))
        if not len(changed_rows):
            return []

        row_runs: list[tuple[int, int]] = []
        run_start = int(changed_rows[0])
        run_end = run_start
        for row in changed_rows[1:]:
            row_number = int(row)
            if row_number == run_end + 1:
                run_end = row_number
            else:
                row_runs.append((run_start, run_end))
                run_start = row_number
                run_end = row_number
        row_runs.append((run_start, run_end))

        regions: list[tuple[int, int, int, int]] = []
        for top, bottom in row_runs:
            changed_columns = np.flatnonzero(np.any(changed[top : bottom + 1], axis=0))
            left = int(changed_columns[0])
            right = int(changed_columns[-1]) + 1
            regions.append((left, top, right - left, bottom - top + 1))

        while len(regions) > _MAX_DELTA_REGIONS:
            merge_index = min(
                range(len(regions) - 1),
                key=lambda index: DisplayConnection._merged_area_cost(regions[index], regions[index + 1]),
            )
            first = regions[merge_index]
            second = regions[merge_index + 1]
            merged_left = min(first[0], second[0])
            merged_top = first[1]
            merged_right = max(first[0] + first[2], second[0] + second[2])
            merged_bottom = second[1] + second[3]
            regions[merge_index : merge_index + 2] = [
                (merged_left, merged_top, merged_right - merged_left, merged_bottom - merged_top)
            ]

        return regions

    @staticmethod
    def _merged_area_cost(
        first: tuple[int, int, int, int], second: tuple[int, int, int, int]
    ) -> int:
        """Return the extra pixel area introduced by merging vertical regions."""
        merged_left = min(first[0], second[0])
        merged_right = max(first[0] + first[2], second[0] + second[2])
        merged_area = (merged_right - merged_left) * (second[1] + second[3] - first[1])
        return merged_area - first[2] * first[3] - second[2] * second[3]

    def _require_driver(self) -> _SafeRevA | _SafeRevB:
        if self._driver is None:
            raise DisplayNotOpenError("Display connection is not open. Call open() first.")
        return self._driver

    @staticmethod
    def _close_after_failed_open(driver: _SafeRevA | _SafeRevB | None) -> None:
        if driver is None:
            return
        try:
            driver.closeSerial()
        except Exception:
            # Preserve the original open/initialization error.
            pass
