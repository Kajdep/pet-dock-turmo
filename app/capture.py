# SPDX-License-Identifier: GPL-3.0-or-later
"""Local-only native Windows capture, with an explicit desktop-area fallback."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
import logging
import threading
import time
from typing import Callable

import numpy as np
from PIL import Image, ImageGrab


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process: str
    width: int
    height: int
    layered: bool

    @property
    def label(self) -> str:
        name = self.title or '(untitled)'
        flag = ' · transparent' if self.layered else ''
        return f'{name} · {self.process} · {self.width}×{self.height}{flag} · 0x{self.hwnd:X}'


def enable_dpi_awareness() -> None:
    if os.name != 'nt':
        return
    try:
        user = ctypes.WinDLL('user32', use_last_error=True)
        fn = user.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = wintypes.BOOL
        fn(ctypes.c_void_p(-4))
    except (OSError, AttributeError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            ctypes.windll.user32.SetProcessDPIAware()


def _winapi():
    if os.name != 'nt':
        raise RuntimeError('Live pet capture requires Windows 10/11.')
    u = ctypes.WinDLL('user32', use_last_error=True)
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsWindowVisible.restype = wintypes.BOOL
    u.IsWindow.argtypes = [wintypes.HWND]
    u.IsWindow.restype = wintypes.BOOL
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowRect.restype = wintypes.BOOL
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    get_style = u.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else u.GetWindowLongW
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = ctypes.c_ssize_t
    return u, k, get_style


def list_windows() -> list[WindowInfo]:
    u, k, get_style = _winapi()
    result = []
    own_pid = os.getpid()
    cbtype = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        rect = wintypes.RECT()
        if not u.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < 16 or h < 16:
            return True
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        buf = ctypes.create_unicode_buffer(u.GetWindowTextLengthW(hwnd) + 1)
        u.GetWindowTextW(hwnd, buf, len(buf))
        title = buf.value
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, len(cls))
        process = f'PID {pid.value}'
        handle = k.OpenProcess(0x1000, False, pid.value)
        if handle:
            try:
                path = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(path))
                if k.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    process = path.value.replace('\\', '/').rsplit('/', 1)[-1]
            finally:
                k.CloseHandle(handle)
        if title or 'codex' in process.lower() or cls.value.startswith('Chrome_WidgetWin'):
            result.append(WindowInfo(int(hwnd), title, process, w, h, bool(get_style(hwnd, -20) & 0x80000)))
        return True

    cb = cbtype(callback)
    u.EnumWindows.argtypes = [cbtype, wintypes.LPARAM]
    u.EnumWindows.restype = wintypes.BOOL
    if not u.EnumWindows(cb, 0):
        raise OSError('Could not enumerate desktop windows.')
    # Heuristic ranking only: the user must confirm the preview before USB output.
    result.sort(key=lambda x: (not any(name in (x.title + x.process).lower() for name in ('codex', 'chatgpt')), not x.layered, x.width * x.height))
    return result


def virtual_desktop_rect() -> tuple[int, int, int, int]:
    u, _, _ = _winapi()
    x, y = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
    return x, y, x + u.GetSystemMetrics(78), y + u.GetSystemMetrics(79)


def bgra_to_rgba(buffer: np.ndarray) -> Image.Image:
    """Copy the native BGRA buffer; WGC uses premultiplied alpha."""
    if buffer.ndim != 3 or buffer.shape[2] != 4:
        raise ValueError('Expected a BGRA capture frame.')
    arr = np.ascontiguousarray(buffer[:, :, [2, 1, 0, 3]])
    alpha = arr[:, :, 3].astype(np.float32)
    partial = (alpha > 0) & (alpha < 255)
    if np.any(partial):
        rgb = arr[:, :, :3].astype(np.float32)
        scale = np.ones(alpha.shape, dtype=np.float32)
        scale[partial] = 255.0 / alpha[partial]
        arr[:, :, :3] = np.clip(np.rint(rgb * scale[:, :, None]), 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


class LatestFrame:
    """One latest frame, not an ever-growing video queue."""
    def __init__(self):
        self.lock = threading.Lock()
        self.image: Image.Image | None = None
        self.sequence = 0
        self.timestamp = 0.0

    def put(self, image: Image.Image) -> None:
        with self.lock:
            self.image = image
            self.sequence += 1
            self.timestamp = time.monotonic()

    def get(self):
        with self.lock:
            return self.image, self.sequence, self.timestamp


def crop_frame(image: Image.Image, crop=None) -> Image.Image:
    """Apply the user's normalized selection once, without mutating the capture."""
    if crop:
        l, t, r, b = crop
        if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
            raise ValueError('Crop must be a non-empty normalized rectangle.')
        box = (int(l * image.width), int(t * image.height),
               max(int(l * image.width) + 1, int(r * image.width)),
               max(int(t * image.height) + 1, int(b * image.height)))
        image = image.crop(box)
    return image


def compose_frame(image: Image.Image, size: tuple[int, int], crop=None,
                  ignore_alpha=False, background='#10141b', pixel_art=False) -> Image.Image:
    """Fixed normalized crop and aspect-preserving fit onto the LCD stage."""
    image = crop_frame(image, crop)
    source = image.convert('RGBA')
    if ignore_alpha:
        source.putalpha(255)
    w, h = size
    scale = min(w / source.width, h / source.height)
    fit = (max(1, min(w, round(source.width * scale))), max(1, min(h, round(source.height * scale))))
    source = source.resize(fit, Image.Resampling.NEAREST if pixel_art else Image.Resampling.LANCZOS)
    stage = Image.new('RGBA', size, background)
    stage.alpha_composite(source, ((w - fit[0]) // 2, (h - fit[1]) // 2))
    return stage.convert('RGB')


class WindowCapture:
    def __init__(self, hwnd: int, frames: LatestFrame, on_error: Callable[[str], None], hide_border: bool = True):
        self.hwnd, self.frames, self.on_error = hwnd, frames, on_error
        self.hide_border = hide_border
        self.border_hidden = False
        self.control = None
        self.capture = None
        self.stopping = threading.Event()
        self.last_frame_at = 0.0

    def start(self):
        u, _, _ = _winapi()
        if not u.IsWindow(self.hwnd):
            raise RuntimeError('The selected window closed. Refresh the list and select it again.')
        from windows_capture import WindowsCapture
        def on_frame_arrived(frame, capture_control):
            if self.stopping.is_set():
                capture_control.stop()
                return
            try:
                now = time.monotonic()
                if now - self.last_frame_at < 0.04:
                    return
                self.last_frame_at = now
                self.frames.put(bgra_to_rgba(frame.frame_buffer))
            except Exception as exc:
                self.on_error(f'Window capture failed: {exc}')
                capture_control.stop()

        def on_closed():
            if not self.stopping.is_set():
                self.on_error('The captured window closed. Reopen the pet and select it again.')

        def start_session(border):
            self.capture = WindowsCapture(cursor_capture=False, draw_border=border,
                                          secondary_window=None, minimum_update_interval=None,
                                          window_hwnd=self.hwnd)
            self.capture.event(on_frame_arrived)
            self.capture.event(on_closed)
            return self.capture.start_free_threaded()
        try:
            self.control = start_session(False if self.hide_border else None)
            self.border_hidden = self.hide_border
        except Exception:
            if not self.hide_border:
                raise
            logging.warning('Windows rejected borderless capture; retrying system-default capture', exc_info=True)
            self.control = start_session(None)
            self.border_hidden = False

    def stop(self):
        self.stopping.set()
        control = self.control
        self.control = None
        if control:
            def shutdown():
                try:
                    control.stop()
                except Exception:
                    logging.exception('Native window capture shutdown failed')
            threading.Thread(target=shutdown, daemon=True, name='native-capture-stop').start()


class AreaCapture:
    """Explicit fallback: captures visible pixels, including any overlapping windows."""
    def __init__(self, rect, frames, on_error):
        self.rect, self.frames, self.on_error = rect, frames, on_error
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        def run():
            try:
                while not self.stopping.is_set():
                    image = ImageGrab.grab(bbox=self.rect, all_screens=True, include_layered_windows=True)
                    self.frames.put(image.convert('RGBA'))
                    self.stopping.wait(0.10)
            except Exception as exc:
                self.on_error(f'Desktop capture failed: {exc}')
        self.thread = threading.Thread(target=run, daemon=True, name='desktop-area-capture')
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=1)
