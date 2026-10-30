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
from functools import lru_cache
import math
import os
from pathlib import Path, PosixPath
import re
import subprocess
import threading
import time
from typing import Any, Final, Iterable, Mapping, Sequence

from PIL import Image, ImageColor, ImageDraw, ImageFont

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
_MODULE_DIR: Final[Path] = Path(__file__).resolve().parent


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


_STYLE_DEFAULTS: Final[dict[str, Any]] = {
    "background_color": "#10141b",
    "card_color": "#1b2531",
    "border_color": "#2b3948",
    "text_color": "#eef5ff",
    "label_color": "#9fb0c2",
    "accent_color": "#70c6ff",
    "muted_color": "#7f8790",
    "metrics_style": "Cards",
    "font_size": 14,
    "font_family": "Segoe UI",
    "font_path": "",
    "panel_opacity": 100,
    "temperature_max": 100,
}
_STYLE_NAMES: Final[dict[str, str]] = {
    "cards": "Cards",
    "bars": "Bars",
    "rings": "Rings",
}
_COMMON_WINDOWS_FONTS: Final[dict[str, tuple[str, ...]]] = {
    "segoe ui": ("segoeui.ttf",),
    "arial": ("arial.ttf",),
    "consolas": ("consola.ttf",),
    "tahoma": ("tahoma.ttf",),
    "verdana": ("verdana.ttf",),
}
_NUMBER_PATTERN: Final[str] = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_PERCENT_RE: Final[re.Pattern[str]] = re.compile(rf"^\s*({_NUMBER_PATTERN})\s*%?\s*$")
_TEMPERATURE_RE: Final[re.Pattern[str]] = re.compile(
    rf"^\s*({_NUMBER_PATTERN})\s*(?:\N{{DEGREE SIGN}}\s*[Cc]|[Cc])?\s*$"
)
_VRAM_RE: Final[re.Pattern[str]] = re.compile(
    rf"^\s*({_NUMBER_PATTERN})\s*/\s*({_NUMBER_PATTERN})(?:\s*[A-Za-z][A-Za-z0-9]*)?\s*$"
)


def _parse_color(value: Any, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    """Return a safe RGB color, treating invalid UI settings as absent."""
    if isinstance(value, str) and value.strip():
        try:
            parsed = ImageColor.getrgb(value.strip())
            if len(parsed) >= 3:
                return tuple(int(channel) for channel in parsed[:3])
        except (TypeError, ValueError):
            pass
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        try:
            channels = tuple(float(channel) for channel in value[:3])
            if len(channels) == 3 and all(math.isfinite(channel) and 0 <= channel <= 255 for channel in channels):
                return tuple(int(round(channel)) for channel in channels)
        except (TypeError, ValueError):
            pass
    return fallback


def _finite_number(value: Any, default: float) -> float:
    """Coerce finite non-boolean numbers without allowing settings to raise."""
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _normalise_style(style: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy UI style input into bounded, renderer-ready settings.

    The renderer deliberately never mutates the parent settings dictionary.  A
    malformed optional setting simply falls back to the documented default.
    """
    source: Mapping[str, Any] = style if isinstance(style, Mapping) else {}
    normalised: dict[str, Any] = {}
    for name in (
        "background_color",
        "card_color",
        "border_color",
        "text_color",
        "label_color",
        "accent_color",
        "muted_color",
    ):
        default = ImageColor.getrgb(_STYLE_DEFAULTS[name])
        normalised[name] = _parse_color(source.get(name), default[:3])

    requested_style = str(source.get("metrics_style", _STYLE_DEFAULTS["metrics_style"])).strip().lower()
    normalised["metrics_style"] = _STYLE_NAMES.get(requested_style, "Cards")
    normalised["font_size"] = int(round(min(48, max(7, _finite_number(source.get("font_size"), 14)))))
    font_family = source.get("font_family", _STYLE_DEFAULTS["font_family"])
    normalised["font_family"] = str(font_family).strip() or _STYLE_DEFAULTS["font_family"]
    configured_path = source.get("font_path", "")
    try:
        normalised["font_path"] = os.fspath(configured_path) if configured_path else ""
    except TypeError:
        normalised["font_path"] = ""
    normalised["panel_opacity"] = int(
        round(min(100, max(0, _finite_number(source.get("panel_opacity"), 100))))
    )
    configured_temperature_max = _finite_number(source.get("temperature_max"), 100)
    normalised["temperature_max"] = min(
        10000.0,
        configured_temperature_max if configured_temperature_max > 0 else 100.0,
    )
    return normalised


def _panel_layout(
    size: Sequence[int], selected: Iterable[str] | None, style: Mapping[str, Any] | None = None
) -> tuple[int, int, int, int, int]:
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
    if style is None:
        # Keep the legacy, no-style API layout exact for existing callers.
        desired_card_height = max(34, min(48, int(round(display_height * 0.115))))
    else:
        style_name = _normalise_style(style)["metrics_style"]
        minimum, maximum, fraction = {
            "Cards": (38, 50, 0.125),
            "Bars": (42, 54, 0.135),
            "Rings": (46, 58, 0.145),
        }[style_name]
        # Bar and ring treatments need room for an indicator in addition to
        # the two text lines, while the global 45% cap remains authoritative.
        desired_card_height = max(minimum, min(maximum, int(round(display_height * fraction))))
    natural_height = padding * 2 + rows * desired_card_height + max(0, rows - 1) * gap
    height_cap = max(1, int(display_height * 0.45))
    return width, min(height_cap, natural_height), columns, rows, padding


def metrics_panel_height(
    size: tuple[int, int], selected: Iterable[str] | None, style: Mapping[str, Any] | None = None
) -> int:
    """Return the vertical space required by selected metric cards.

    No metrics selected reserves no display space. With selected cards, the
    result is capped at 45% of the supplied display height. Omitting ``style``
    retains the original compact card layout; styled Bars and Rings may reserve
    more space inside that same cap.
    """
    return _panel_layout(size, selected, style)[1]


def _font_candidates(
    font_path: str | os.PathLike[str] | None, font_family: str | None = None
) -> list[Path]:
    """List explicit, Windows common, then optional bundled font candidates."""
    candidates: list[Path] = []
    if font_path:
        try:
            candidates.append(Path(font_path))
        except TypeError:
            pass
    if os.name == "nt":
        windows_dir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
        if windows_dir:
            try:
                fonts_dir = Path(windows_dir) / "Fonts"
            except NotImplementedError:
                # Tests can emulate Windows while running on POSIX; WindowsPath
                # cannot be instantiated there even though the candidate list
                # itself remains useful and harmless.
                fonts_dir = PosixPath(windows_dir) / "Fonts"
            if font_family:
                requested = str(font_family).strip().lower()
                filenames = _COMMON_WINDOWS_FONTS.get(requested, ())
                # A selected common family wins, then try common Windows
                # fallbacks so a non-standard Windows installation stays legible.
                ordered_families = [requested] + [
                    family for family in _COMMON_WINDOWS_FONTS if family != requested
                ]
                for family in ordered_families:
                    for filename in _COMMON_WINDOWS_FONTS.get(family, filenames):
                        candidate = fonts_dir / filename
                        if candidate not in candidates:
                            candidates.append(candidate)
            else:
                # This is the legacy fallback sequence for the no-style API.
                candidates.append(fonts_dir / "segoeui.ttf")
    # These paths make a later bundled open font automatically preferred on
    # non-Windows builds without making any system-font availability assumption.
    candidates.extend(
        (
            _MODULE_DIR / "assets" / "fonts" / "DejaVuSans.ttf",
            _MODULE_DIR / "assets" / "DejaVuSans.ttf",
            _MODULE_DIR / "fonts" / "DejaVuSans.ttf",
        )
    )
    return candidates


@lru_cache(maxsize=128)
def _cached_font(size: int, font_path: str, font_family: str) -> ImageFont.ImageFont:
    """Load fonts through a small LRU because every frame reuses the same sizes."""
    for candidate in _font_candidates(font_path or None, font_family or None):
        try:
            if candidate.is_file():
                return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow versions before scalable load_default().
        return ImageFont.load_default()


def _load_font(
    size: int,
    font_path: str | os.PathLike[str] | None = None,
    font_family: str | None = None,
) -> ImageFont.ImageFont:
    """Return a cached font while keeping the original two-argument API."""
    try:
        cached_path = os.fspath(font_path) if font_path else ""
    except TypeError:
        cached_path = ""
    return _cached_font(max(1, int(size)), str(cached_path), str(font_family or ""))


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
    font_family: str | None = None,
) -> ImageFont.ImageFont:
    """Find a readable font that fits, falling back to the smallest size."""
    for size in range(max(7, preferred_size), 6, -1):
        font = _load_font(size, font_path, font_family)
        if _text_size(draw, text, font)[0] <= width:
            return font
    return _load_font(7, font_path, font_family)


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


def _number_from_text(value: Any, pattern: re.Pattern[str]) -> float | None:
    """Parse a finite first numeric capture without accepting bools or NaNs."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        match = pattern.fullmatch(value)
        if match is None:
            return None
        try:
            number = float(match.group(1))
        except (IndexError, ValueError):
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def metric_indicator_ratio(key: str, value: Any, temperature_max: Any = 100) -> float | None:
    """Return an honest 0--1 fill ratio for a metric display value.

    CPU, RAM, GPU, and disk values use their supplied percentage. VRAM uses
    the ``used/total`` display value. Temperatures use the configured explicit
    0--``temperature_max`` Celsius range. Network rate and CPU frequency do
    not have a meaningful bounded range, so they intentionally return ``None``.
    Invalid, missing, or non-finite readings also return ``None`` rather than
    looking like a real zero reading.
    """
    if key in {"cpu", "ram", "gpu", "disk"}:
        number = _number_from_text(value, _PERCENT_RE)
        if number is None or number < 0:
            return None
        return min(1.0, max(0.0, number / 100.0))
    if key in {"gpu_temp", "cpu_temp"}:
        number = _number_from_text(value, _TEMPERATURE_RE)
        maximum = _finite_number(temperature_max, 100.0)
        if number is None or maximum <= 0:
            return None
        return min(1.0, max(0.0, number / maximum))
    if key == "vram":
        if not isinstance(value, str):
            return None
        match = _VRAM_RE.fullmatch(value)
        if match is None:
            return None
        try:
            used, total = float(match.group(1)), float(match.group(2))
        except (IndexError, ValueError):
            return None
        if not (math.isfinite(used) and math.isfinite(total)) or used < 0 or total <= 0:
            return None
        return min(1.0, max(0.0, used / total))
    return None


def _value_for_metric(values: Mapping[str, Any] | None, key: str) -> str:
    """Read a display value defensively without mutating caller-owned input."""
    if values is None:
        return NOT_AVAILABLE
    try:
        raw_value = values.get(key, NOT_AVAILABLE)
    except (AttributeError, TypeError, ValueError):
        return NOT_AVAILABLE
    if raw_value is None or raw_value == "":
        return NOT_AVAILABLE
    try:
        rendered = str(raw_value)
    except Exception:
        return NOT_AVAILABLE
    return rendered if rendered.strip() else NOT_AVAILABLE


def _with_opacity(color: tuple[int, int, int], opacity: int) -> tuple[int, int, int, int]:
    return color + (int(round(255 * opacity / 100)),)


def _draw_progress_bar(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    ratio: float | None,
    track_color: Any,
    accent_color: Any,
) -> None:
    """Draw an optional fill, retaining a neutral track for unavailable data."""
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return
    radius = max(1, (y1 - y0) // 2)
    draw.rounded_rectangle(box, radius=radius, fill=track_color)
    if ratio is None or ratio <= 0:
        return
    filled_x = x0 + max(1, int(round((x1 - x0) * min(1.0, ratio))))
    draw.rounded_rectangle((x0, y0, min(x1, filled_x), y1), radius=radius, fill=accent_color)


def _draw_ring(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    ratio: float | None,
    track_color: Any,
    accent_color: Any,
) -> None:
    """Draw a clock-like ring whose fill begins at twelve o'clock."""
    x0, y0, x1, y1 = box
    diameter = min(x1 - x0, y1 - y0)
    if diameter < 5:
        return
    stroke = max(1, min(4, diameter // 5))
    draw.arc(box, start=-90, end=270, fill=track_color, width=stroke)
    if ratio is not None and ratio > 0:
        draw.arc(box, start=-90, end=-90 + 360 * min(1.0, ratio), fill=accent_color, width=stroke)


def _draw_text_pair(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    label: str,
    value: str,
    style: Mapping[str, Any],
    font_path: str | os.PathLike[str] | None,
    text_x: int | None = None,
) -> None:
    """Fit and draw label/value text in a bounded region without clipping."""
    x0, y0, x1, y1 = bounds
    x = x0 if text_x is None else text_x
    text_width = max(1, x1 - x)
    available_height = max(1, y1 - y0)
    label_preferred = min(max(7, int(round(style["font_size"] * 0.70))), max(7, available_height // 2))
    value_preferred = min(max(7, int(style["font_size"])), max(7, available_height - 4))
    label_font = _fitted_font(
        draw, label, text_width, label_preferred, font_path, style["font_family"]
    )
    value_font = _fitted_font(
        draw, value, text_width, value_preferred, font_path, style["font_family"]
    )
    label_text = _ellipsize(draw, label, label_font, text_width)
    value_text = _ellipsize(draw, value, value_font, text_width)
    _, label_height = _text_size(draw, label_text, label_font)
    _, value_height = _text_size(draw, value_text, value_font)
    label_y = y0
    value_y = max(label_y + label_height, min(y1 - value_height, label_y + label_height + 1))
    draw.text((x, label_y), label_text, fill=style["label_color"], font=label_font)
    draw.text(
        (x, value_y),
        value_text,
        fill=style["muted_color"] if value == NOT_AVAILABLE else style["text_color"],
        font=value_font,
    )


def _render_legacy_metrics_panel(
    size: tuple[int, int],
    values: Mapping[str, Any] | None,
    selected: Iterable[str] | None,
    font_path: str | os.PathLike[str] | None,
) -> Image.Image:
    """Render the original RGB Cards panel without altering its visual contract."""
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
        value = _value_for_metric(values, key)
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


def _render_styled_metrics_panel(
    size: tuple[int, int],
    values: Mapping[str, Any] | None,
    selected: Iterable[str] | None,
    font_path: str | os.PathLike[str] | None,
    style: Mapping[str, Any] | None,
) -> Image.Image:
    """Render user-configured Cards, Bars, or Rings metric cards."""
    settings = _normalise_style(style)
    chosen = _normalise_selected(selected)
    width, panel_height, columns, rows, padding = _panel_layout(size, chosen, settings)
    opacity = settings["panel_opacity"]
    rgba = opacity < 100
    if rgba:
        paint: dict[str, Any] = {
            name: _with_opacity(settings[name], opacity)
            for name in ("background_color", "card_color", "border_color", "accent_color")
        }
        # Text stays fully opaque so a translucent panel remains readable after
        # its parent composites it over the selected background image.
        paint.update(
            {
                "text_color": settings["text_color"] + (255,),
                "label_color": settings["label_color"] + (255,),
                "muted_color": settings["muted_color"] + (255,),
                "font_size": settings["font_size"],
                "font_family": settings["font_family"],
            }
        )
        panel = Image.new("RGBA", (width, panel_height), paint["background_color"])
    else:
        paint = dict(settings)
        panel = Image.new("RGB", (width, panel_height), paint["background_color"])
    if not chosen:
        return panel

    draw = ImageDraw.Draw(panel)
    gap = max(3, min(6, width // 100))
    inner_width = width - padding * 2
    card_width = max(1, (inner_width - gap * (columns - 1)) // columns)
    content_height = panel_height - padding * 2 - gap * (rows - 1)
    base_card_height, extra = divmod(max(0, content_height), rows)
    effective_font_path = font_path or settings["font_path"] or None
    style_name = settings["metrics_style"]

    for index, key in enumerate(chosen):
        column = index % columns
        row = index // columns
        x0 = padding + column * (card_width + gap)
        row_before = row * base_card_height + min(row, extra)
        y0 = padding + row_before + row * gap
        card_height = base_card_height + (1 if row < extra else 0)
        x1 = min(width - padding, x0 + card_width)
        y1 = min(panel_height - padding, y0 + card_height)
        radius = max(3, min(8, card_height // 5))
        draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=paint["card_color"], outline=paint["border_color"])

        inset = max(3, min(7, card_height // 6))
        label = METRIC_LABELS[key]
        value = _value_for_metric(values, key)
        ratio = metric_indicator_ratio(key, value, settings["temperature_max"])
        if style_name == "Rings":
            diameter = max(12, min(card_height - inset * 2, (x1 - x0) // 3, 30))
            ring_y = y0 + max(inset, (card_height - diameter) // 2)
            ring_box = (x0 + inset, ring_y, x0 + inset + diameter, ring_y + diameter)
            _draw_ring(draw, ring_box, ratio, paint["border_color"], paint["accent_color"])
            text_x = min(x1 - inset - 1, ring_box[2] + max(3, inset // 2))
            _draw_text_pair(
                draw,
                (x0 + inset, y0 + inset, x1 - inset, y1 - inset),
                label,
                value,
                paint,
                effective_font_path,
                text_x,
            )
            continue

        bar_height = max(3, min(8 if style_name == "Bars" else 5, card_height // (5 if style_name == "Bars" else 8)))
        bar_box = (x0 + inset, y1 - inset - bar_height, x1 - inset, y1 - inset)
        _draw_progress_bar(draw, bar_box, ratio, paint["border_color"], paint["accent_color"])
        _draw_text_pair(
            draw,
            (x0 + inset, y0 + inset, x1 - inset, max(y0 + inset + 1, bar_box[1] - 2)),
            label,
            value,
            paint,
            effective_font_path,
        )
    return panel


def render_metrics_panel(
    size: tuple[int, int],
    values: Mapping[str, Any] | None,
    selected: Iterable[str] | None,
    font_path: str | os.PathLike[str] | None = None,
    style: Mapping[str, Any] | None = None,
) -> Image.Image:
    """Render selected metric strings into a compact display panel.

    The original call signature remains an RGB Cards renderer when ``style``
    is omitted. Supplying a style mapping enables configurable colors and
    fonts plus Cards, Bars, or Rings indicators. A panel opacity below 100
    returns RGBA so the caller can composite it over a background image.
    """
    if style is None:
        return _render_legacy_metrics_panel(size, values, selected, font_path)
    return _render_styled_metrics_panel(size, values, selected, font_path, style)
