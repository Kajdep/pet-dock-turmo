# SPDX-License-Identifier: GPL-3.0-or-later
"""Lightweight, local system metrics for the optional display overlay.

``MetricCollector`` never performs a system probe from :meth:`snapshot`.  Its
worker polls at the requested interval (one second by default), so composing a
capture frame and writing it to the USB display remain independent of metrics
collection.  The returned values are display-ready strings, using ``"N/A"``
when a metric cannot be obtained.

GPU support is deliberately narrow and honest: the collector asks the local
``nvidia-smi`` executable for its *first* NVIDIA adapter only.  It does not run
PowerShell, WMI, or Windows performance-counter probes for other GPU vendors;
those paths vary substantially by driver and can either be expensive or imply
coverage that is not actually available.  CPU and GPU temperatures also are
not universally exposed by Windows or by psutil without a hardware sensor
provider, so unavailable temperature readings stay ``"N/A"``.
"""
from __future__ import annotations

import csv
import math
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Final, Iterable, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

try:  # psutil is bundled by the application, but keep module import safe.
    import psutil  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised only in incomplete installs.
    psutil = None  # type: ignore[assignment]


METRIC_KEYS: Final[tuple[str, ...]] = (
    "cpu",
    "ram",
    "disk",
    "net",
    "gpu",
    "gpu_temp",
    "vram",
    "cpu_temp",
    "clock",
)
"""Supported selectable metric keys, in the default display order."""

METRIC_LABELS: Final[dict[str, str]] = {
    "cpu": "CPU",
    "ram": "RAM",
    "disk": "DISK",
    "net": "NET / S",
    "gpu": "GPU",
    "gpu_temp": "GPU TEMP",
    "vram": "VRAM",
    "cpu_temp": "CPU TEMP",
    "clock": "CPU CLOCK",
}

NOT_AVAILABLE: Final[str] = "N/A"
_NVIDIA_COMMAND: Final[tuple[str, ...]] = (
    "nvidia-smi",
    "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
    "--format=csv,noheader,nounits",
)
_GPU_KEYS: Final[tuple[str, ...]] = ("gpu", "gpu_temp", "vram")


def _default_values() -> dict[str, str]:
    return {key: NOT_AVAILABLE for key in METRIC_KEYS}


def _normalise_selected(selected: Iterable[str] | None) -> list[str]:
    """Keep supported selected keys once, in the caller's chosen order."""
    if selected is None:
        return []
    result: list[str] = []
    for key in selected:
        if key in METRIC_KEYS and key not in result:
            result.append(key)
    return result


def _format_percent(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return NOT_AVAILABLE
    if not math.isfinite(number):
        return NOT_AVAILABLE
    # psutil and nvidia-smi report percentages. Clamp only impossible values
    # so a broken provider never paints a fictitious value beyond 0--100.
    return f"{max(0, min(100, int(round(number))))}%"


def _format_temperature(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return NOT_AVAILABLE
    if not math.isfinite(number):
        return NOT_AVAILABLE
    return f"{int(round(number))}\N{DEGREE SIGN}C"


def _format_clock(value: Any) -> str:
    try:
        mhz = float(value)
    except (TypeError, ValueError):
        return NOT_AVAILABLE
    if not math.isfinite(mhz) or mhz <= 0:
        return NOT_AVAILABLE
    if mhz >= 1000:
        return f"{mhz / 1000:.2f} GHz"
    return f"{int(round(mhz))} MHz"


def _format_vram(used: Any, total: Any) -> str:
    try:
        used_mib = float(used)
        total_mib = float(total)
    except (TypeError, ValueError):
        return NOT_AVAILABLE
    if not (math.isfinite(used_mib) and math.isfinite(total_mib)) or used_mib < 0 or total_mib <= 0:
        return NOT_AVAILABLE
    return f"{used_mib / 1024:.1f}/{total_mib / 1024:.1f} GiB"


def _format_rate(bytes_per_second: float) -> str:
    """Format an unsigned byte rate compactly; the NET / S label supplies units."""
    value = max(0.0, float(bytes_per_second))
    units = ("B", "K", "M", "G", "T")
    unit_index = 0
    while value >= 1024.0 and unit_index < len(units) - 1:
        value /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(round(value))}{units[unit_index]}"
    return f"{value:.1f}{units[unit_index]}"


def _default_disk_path() -> str:
    """Return the system volume root on Windows and root on other platforms."""
    if os.name != "nt":
        return "/"
    drive = os.environ.get("SystemDrive", "C:").strip() or "C:"
    return drive.rstrip("\\/") + "\\"


def _first_cpu_temperature() -> str:
    """Return one provider-reported CPU temperature, never a guessed value."""
    if psutil is None:
        return NOT_AVAILABLE
    reader = getattr(psutil, "sensors_temperatures", None)
    if not callable(reader):
        return NOT_AVAILABLE
    try:
        groups = reader()
    except Exception:
        return NOT_AVAILABLE
    if not isinstance(groups, Mapping):
        return NOT_AVAILABLE

    # Prefer Linux CPU sensor groups where present. On standard Windows Python
    # installations this normally has no readings, which correctly remains N/A.
    preferred = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")
    ordered_names = [name for name in preferred if name in groups]
    ordered_names.extend(name for name in groups if name not in ordered_names)
    for name in ordered_names:
        for reading in groups.get(name) or ():
            current = getattr(reading, "current", None)
            rendered = _format_temperature(current)
            if rendered != NOT_AVAILABLE:
                return rendered
    return NOT_AVAILABLE


def _query_nvidia() -> dict[str, str]:
    """Query the first NVIDIA adapter, with a one-second, no-console bound.

    There is intentionally no fallback vendor command.  A missing executable,
    timeout, invalid output, or unavailable individual field is represented as
    ``N/A`` rather than carrying forward a stale value.
    """
    result = {key: NOT_AVAILABLE for key in _GPU_KEYS}
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": 1.0,
        "check": False,
    }
    if os.name == "nt":
        # Avoid a visible console flash for a packaged Windows GUI executable.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(_NVIDIA_COMMAND, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return result
    if getattr(completed, "returncode", 1) != 0:
        return result

    try:
        rows = [row for row in csv.reader((completed.stdout or "").splitlines()) if any(cell.strip() for cell in row)]
        first_adapter = rows[0]
    except (IndexError, csv.Error, TypeError):
        return result
    if len(first_adapter) < 4:
        return result

    result["gpu"] = _format_percent(first_adapter[0].strip())
    result["gpu_temp"] = _format_temperature(first_adapter[1].strip())
    result["vram"] = _format_vram(first_adapter[2].strip(), first_adapter[3].strip())
    return result


class MetricCollector:
    """Collect local system metrics in one daemon thread.

    Args:
        interval: Seconds between worker polls. It must be positive; the
            default one-second cadence is intentionally much slower than the
            capture and USB display loops.

    ``snapshot()`` returns a defensive copy with exactly the keys in
    :data:`METRIC_KEYS`, and does not probe the operating system. ``version``
    starts at zero and increments after each completed worker poll, including a
    poll whose providers are unavailable. ``start()`` is idempotent and
    non-blocking. ``stop()`` requests shutdown and joins for at most 1.25
    seconds, so it cannot indefinitely hold GUI shutdown.
    """

    def __init__(self, interval: float = 1.0) -> None:
        if isinstance(interval, bool):
            raise ValueError("interval must be a positive number")
        try:
            parsed_interval = float(interval)
        except (TypeError, ValueError) as exc:
            raise ValueError("interval must be a positive number") from exc
        if not math.isfinite(parsed_interval) or parsed_interval <= 0:
            raise ValueError("interval must be a positive number")

        self.interval = parsed_interval
        self._lock = threading.Lock()
        self._values = _default_values()
        self._version = 0
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._net_sent: int | None = None
        self._net_recv: int | None = None
        self._net_timestamp: float | None = None

    @property
    def version(self) -> int:
        """Monotonically increasing completed-poll count."""
        with self._lock:
            return self._version

    def start(self) -> None:
        """Start the collector worker without synchronously probing the system."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set():
                return
            self._stop_event = threading.Event()
            stop_event = self._stop_event
            thread = threading.Thread(
                target=self._run,
                args=(stop_event,),
                name="system-metrics",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self, wait: bool = True) -> None:
        """Request shutdown; GUI toggles can skip joining a sensor subprocess."""
        with self._lock:
            stop_event = self._stop_event
            thread = self._thread
        stop_event.set()
        if wait and thread is not None and thread is not threading.current_thread():
            # nvidia-smi itself is bounded to 1 s; this extra margin covers a
            # worker returning from that subprocess without stalling shutdown.
            thread.join(timeout=1.25)
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def snapshot(self) -> dict[str, str]:
        """Return the latest display strings immediately, without a live poll."""
        with self._lock:
            return dict(self._values)

    def _run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self._collect_once()
            stop_event.wait(self.interval)

    def _collect_once(self) -> None:
        """Perform one worker poll. Kept small and private for deterministic tests."""
        values = _default_values()
        if psutil is not None:
            values["cpu"] = self._read_cpu()
            values["ram"] = self._read_ram()
            values["disk"] = self._read_disk()
            values["net"] = self._read_network()
            values["cpu_temp"] = _first_cpu_temperature()
            values["clock"] = self._read_clock()
        values.update(_query_nvidia())
        with self._lock:
            self._values = values
            self._version += 1

    @staticmethod
    def _read_cpu() -> str:
        try:
            return _format_percent(psutil.cpu_percent(interval=None))
        except Exception:
            return NOT_AVAILABLE

    @staticmethod
    def _read_ram() -> str:
        try:
            return _format_percent(psutil.virtual_memory().percent)
        except Exception:
            return NOT_AVAILABLE

    @staticmethod
    def _read_disk() -> str:
        try:
            return _format_percent(psutil.disk_usage(_default_disk_path()).percent)
        except Exception:
            return NOT_AVAILABLE

    @staticmethod
    def _read_clock() -> str:
        try:
            frequency = psutil.cpu_freq()
            return _format_clock(getattr(frequency, "current", None))
        except Exception:
            return NOT_AVAILABLE

    def _read_network(self) -> str:
        try:
            counters = psutil.net_io_counters()
            sent = int(counters.bytes_sent)
            received = int(counters.bytes_recv)
            now = time.monotonic()
        except Exception:
            return NOT_AVAILABLE

        previous_sent = self._net_sent
        previous_received = self._net_recv
        previous_timestamp = self._net_timestamp
        self._net_sent, self._net_recv, self._net_timestamp = sent, received, now
        if previous_sent is None or previous_received is None or previous_timestamp is None:
            # A rate needs two samples; showing zero here would be invented.
            return NOT_AVAILABLE

        elapsed = max(0.001, now - previous_timestamp)
        # Interface counter resets must never create a negative or huge rate.
        upload = max(0, sent - previous_sent) / elapsed
        download = max(0, received - previous_received) / elapsed
        return f"U {_format_rate(upload)} D {_format_rate(download)}"


def _panel_layout(size: Sequence[int], selected: Iterable[str] | None) -> tuple[int, int, int, int, int]:
    """Return width, height, columns, rows, and content padding for a panel."""
    if len(size) != 2:
        raise ValueError("size must contain width and height")
    try:
        width, display_height = int(size[0]), int(size[1])
    except (TypeError, ValueError) as exc:
        raise ValueError("size must contain integer width and height") from exc
    if width <= 0 or display_height <= 0:
        raise ValueError("size must contain positive width and height")
    count = len(_normalise_selected(selected))
    if not count:
        return width, 0, 0, 0, 0

    # 320-pixel portrait displays retain two legible columns; the 480-pixel
    # landscape display fits three. More columns would make labels unreadable.
    columns = 3 if width >= 440 else 2
    columns = min(columns, count)
    rows = int(math.ceil(count / columns))
    padding = max(3, min(8, width // 80))
    gap = max(3, min(6, width // 100))
    desired_card_height = max(34, min(48, int(round(display_height * 0.115))))
    natural_height = padding * 2 + rows * desired_card_height + max(0, rows - 1) * gap
    height_cap = max(1, int(display_height * 0.45))
    return width, min(height_cap, natural_height), columns, rows, padding


def metrics_panel_height(size: tuple[int, int], selected: list[str]) -> int:
    """Return the vertical space required by selected metric cards.

    No metrics selected reserves no display space.  With selected cards, the
    result is capped at 45% of the supplied display height, leaving the larger
    upper portion available to the live pet capture.
    """
    return _panel_layout(size, selected)[1]


def _font_candidates(font_path: str | os.PathLike[str] | None) -> list[Path]:
    candidates: list[Path] = []
    if font_path:
        candidates.append(Path(font_path))
    if os.name == "nt":
        windows_dir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
        if windows_dir:
            candidates.append(Path(windows_dir) / "Fonts" / "segoeui.ttf")
    module_dir = Path(__file__).resolve().parent
    # These paths make a later bundled open font automatically preferred on
    # non-Windows builds without making any system-font availability assumption.
    candidates.extend(
        (
            module_dir / "assets" / "fonts" / "DejaVuSans.ttf",
            module_dir / "assets" / "DejaVuSans.ttf",
            module_dir / "fonts" / "DejaVuSans.ttf",
        )
    )
    return candidates


def _load_font(size: int, font_path: str | os.PathLike[str] | None = None) -> ImageFont.ImageFont:
    for candidate in _font_candidates(font_path):
        try:
            if candidate.is_file():
                return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow versions before scalable load_default().
        return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    if not text:
        return 0, 0
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _fitted_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    width: int,
    preferred_size: int,
    font_path: str | os.PathLike[str] | None,
) -> ImageFont.ImageFont:
    """Find a readable font that fits, falling back to the smallest size."""
    for size in range(max(7, preferred_size), 6, -1):
        font = _load_font(size, font_path)
        if _text_size(draw, text, font)[0] <= width:
            return font
    return _load_font(7, font_path)


def _ellipsize(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int
) -> str:
    """Prevent clipping for unexpected caller-provided values."""
    if _text_size(draw, text, font)[0] <= width:
        return text
    ellipsis = "..."
    usable = text
    while usable and _text_size(draw, usable + ellipsis, font)[0] > width:
        usable = usable[:-1]
    return (usable + ellipsis) if usable else ellipsis


def render_metrics_panel(
    size: tuple[int, int],
    values: Mapping[str, Any],
    selected: list[str],
    font_path: str | os.PathLike[str] | None = None,
) -> Image.Image:
    """Render selected display strings into a compact RGB dark-theme panel.

    The image width always equals ``size[0]`` and its height equals
    :func:`metrics_panel_height`. Only known keys in ``selected`` are rendered;
    absent values are shown as gray ``N/A`` rather than made-up readings.
    """
    chosen = _normalise_selected(selected)
    width, panel_height, columns, rows, padding = _panel_layout(size, chosen)
    panel = Image.new("RGB", (width, panel_height), "#10141b")
    if not chosen:
        return panel

    draw = ImageDraw.Draw(panel)
    gap = max(3, min(6, width // 100))
    inner_width = width - padding * 2
    card_width = max(1, (inner_width - gap * (columns - 1)) // columns)
    content_height = panel_height - padding * 2 - gap * (rows - 1)
    base_card_height, extra = divmod(max(0, content_height), rows)

    for index, key in enumerate(chosen):
        column = index % columns
        row = index // columns
        x0 = padding + column * (card_width + gap)
        row_before = row * base_card_height + min(row, extra)
        y0 = padding + row_before + row * gap
        card_height = base_card_height + (1 if row < extra else 0)
        x1 = min(width - padding, x0 + card_width)
        y1 = min(panel_height - padding, y0 + card_height)
        draw.rounded_rectangle((x0, y0, x1, y1), radius=max(3, min(7, card_height // 5)), fill="#1b2531", outline="#2b3948")

        inset = max(3, min(7, card_height // 6))
        text_width = max(1, x1 - x0 - inset * 2)
        label = METRIC_LABELS[key]
        raw_value = values.get(key, NOT_AVAILABLE) if values is not None else NOT_AVAILABLE
        value = str(raw_value) if raw_value not in (None, "") else NOT_AVAILABLE
        label_font = _fitted_font(draw, label, text_width, max(8, min(12, card_height // 3)), font_path)
        value_font = _fitted_font(draw, value, text_width, max(9, min(17, card_height // 2)), font_path)
        label_text = _ellipsize(draw, label, label_font, text_width)
        value_text = _ellipsize(draw, value, value_font, text_width)
        _, label_height = _text_size(draw, label_text, label_font)
        _, value_height = _text_size(draw, value_text, value_font)
        label_y = y0 + inset
        value_y = min(y1 - inset - value_height, label_y + label_height + max(1, inset // 2))
        value_y = max(label_y + label_height, value_y)
        draw.text((x0 + inset, label_y), label_text, fill="#9fb0c2", font=label_font)
        draw.text(
            (x0 + inset, value_y),
            value_text,
            fill="#7f8790" if value == NOT_AVAILABLE else "#eef5ff",
            font=value_font,
        )
    return panel
