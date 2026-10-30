# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental off-screen placement for a layered desktop-pet window.

This intentionally does *not* hide, minimize, fade, cloak, or otherwise alter a
window's rendering state.  Windows Graphics Capture is not guaranteed to
continue updating for a window placed outside the virtual desktop; callers must
keep their normal capture-stall recovery path.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from typing import Any

WS_EX_LAYERED = 0x00080000
GWL_EXSTYLE = -20
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
_POSITION_FLAGS = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER


class _WindowsAPI:
    """A deliberately small, pointer-width-correct user32 wrapper."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Desktop pet off-screen placement requires Windows.")
        user32 = ctypes.WinDLL("user32", use_last_error=True)

        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int

        # GetWindowLongPtrW is essential on 64-bit Python: a HWND/ex-style
        # retrieval must not be truncated through a 32-bit restype.
        get_long = getattr(user32, "GetWindowLongPtrW", None)
        if get_long is None:  # 32-bit Windows only.
            get_long = user32.GetWindowLongW
            get_long.restype = ctypes.c_long
        else:
            get_long.restype = ctypes.c_ssize_t
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]

        self.user32 = user32
        self._get_long = get_long

    @staticmethod
    def _error(action: str) -> RuntimeError:
        code = ctypes.get_last_error()
        detail = ctypes.FormatError(code).strip() if code else "unknown Win32 error"
        return RuntimeError(f"{action} failed ({code}: {detail}).")

    def is_window(self, hwnd: int) -> bool:
        return bool(self.user32.IsWindow(hwnd))

    def rect(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise self._error("GetWindowRect")
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    def process_id(self, hwnd: int) -> int:
        pid = wintypes.DWORD()
        if not self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            raise self._error("GetWindowThreadProcessId")
        return int(pid.value)

    def exstyle(self, hwnd: int) -> int:
        # Zero can be a valid style.  It is enough here because a zero result
        # simply fails the layered-window safety check.
        return int(self._get_long(hwnd, GWL_EXSTYLE))

    def virtual_bounds(self) -> tuple[int, int, int, int]:
        left = int(self.user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        top = int(self.user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        width = int(self.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = int(self.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        if width <= 0 or height <= 0:
            raise RuntimeError("Could not determine the virtual desktop bounds.")
        return left, top, left + width, top + height

    def set_position(self, hwnd: int, x: int, y: int, width: int, height: int) -> bool:
        return bool(self.user32.SetWindowPos(hwnd, 0, x, y, width, height, _POSITION_FLAGS))


def _winapi() -> _WindowsAPI:
    return _WindowsAPI()


def _atomic_write(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    rect = loaded.get("rect")
    if (
        loaded.get("version") != 1
        or not isinstance(loaded.get("hwnd"), int)
        or loaded["hwnd"] <= 0
        or not isinstance(loaded.get("owner_pid"), int)
        or loaded["owner_pid"] <= 0
        or not isinstance(rect, list)
        or len(rect) != 4
        or not all(isinstance(value, int) for value in rect)
    ):
        return None
    return loaded


def _remove_state(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # A future watchdog pass may still recover the window.  Restoration is
        # intentionally best-effort and must remain safe during app shutdown.
        pass


def _dock_identity() -> tuple[int, float]:
    """Return PID and creation time so a reused PID cannot keep a stale guard."""
    try:
        import psutil  # Provided by the portable Windows runtime.
        process = psutil.Process(os.getpid())
        return os.getpid(), float(process.create_time())
    except Exception as exc:
        raise RuntimeError("Desktop pet off-screen placement requires bundled psutil.") from exc


class DesktopPetGuard:
    """Move one layered pet window just beyond the virtual desktop.

    ``hide`` is an intentionally conservative name retained for the UI: it
    only moves a qualifying window.  It does not make any assertion about
    whether Windows Graphics Capture continues to update off-screen.
    """

    def __init__(self, state_path: Path):
        self.state_path = Path(state_path)
        self._hidden = False
        self._state: dict[str, Any] | None = None

    @property
    def hidden(self) -> bool:
        return self._hidden

    @staticmethod
    def _watchdog_command() -> list[str]:
        return [sys.executable, str(Path(__file__).with_name("restore_watchdog.py")), "--state"]

    def _spawn_watchdog(self) -> None:
        command = self._watchdog_command() + [str(self.state_path)]
        kwargs: dict[str, Any] = {"close_fds": True}
        if os.name == "nt":
            # Do not create an additional console when the packaged interpreter
            # is python.exe rather than pythonw.exe.
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen(command, **kwargs)
        except OSError as exc:
            raise RuntimeError("Could not start the local restore watchdog; window was not moved.") from exc

    def hide(self, hwnd: int) -> None:
        """Persist recovery state, start the watchdog, then place ``hwnd`` off-screen.

        Raises ``RuntimeError`` for every unsafe precondition or placement
        failure.  A failed verification restores the original top-left position
        before reporting the error.
        """
        if self._hidden:
            return
        if not isinstance(hwnd, int) or hwnd <= 0:
            raise RuntimeError("A valid native window handle is required.")

        # Do not overwrite a previous crash-recovery record.  A user who opens
        # the app again can call restore() to recover it; hide attempts that
        # safe recovery first.
        if self.state_path.exists():
            self.restore()
            if self.state_path.exists():
                raise RuntimeError("A previous desktop-pet recovery record still needs restoration.")

        api = _winapi()
        if not api.is_window(hwnd):
            raise RuntimeError("The selected window no longer exists.")
        if not (api.exstyle(hwnd) & WS_EX_LAYERED):
            raise RuntimeError("Only layered pet overlays may be moved off-screen.")

        original = api.rect(hwnd)
        owner_pid = api.process_id(hwnd)
        dock_pid, dock_create_time = _dock_identity()
        left, top, right, bottom = original
        _, virtual_top, virtual_right, _ = api.virtual_bounds()
        state: dict[str, Any] = {
            "version": 1,
            "hwnd": int(hwnd),
            "owner_pid": owner_pid,
            "rect": [left, top, right, bottom],
            "dock_pid": dock_pid,
            "dock_create_time": dock_create_time,
            "created_at": time.time(),
        }

        # State is durable before the helper starts, and the helper starts
        # before SetWindowPos.  If helper creation fails, nothing has moved.
        _atomic_write(self.state_path, state)
        try:
            self._spawn_watchdog()
        except Exception:
            _remove_state(self.state_path)
            raise

        width, height = right - left, bottom - top
        target_x, target_y = virtual_right + 64, virtual_top + 64
        try:
            if not api.set_position(hwnd, target_x, target_y, width, height):
                raise RuntimeError("SetWindowPos could not move the pet overlay.")
            actual = api.rect(hwnd)
            # The horizontal location is deliberately outside the entire virtual
            # desktop.  Verify it rather than trusting a constrained placement.
            if actual[0] < virtual_right:
                raise RuntimeError("Windows did not place the pet overlay outside the virtual desktop.")
        except Exception as exc:
            self._restore_state(state, api)
            _remove_state(self.state_path)
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"Could not place the pet overlay off-screen: {exc}") from exc

        self._state = state
        self._hidden = True

    def _restore_state(self, state: dict[str, Any], api: Any) -> bool:
        """Restore only if this exact HWND still belongs to its recorded PID."""
        hwnd = int(state["hwnd"])
        if not api.is_window(hwnd):
            return False
        try:
            if api.process_id(hwnd) != int(state["owner_pid"]):
                return False
            left, top, right, bottom = (int(value) for value in state["rect"])
            return bool(api.set_position(hwnd, left, top, right - left, bottom - top))
        except Exception:
            return False

    def restore(self) -> None:
        """Best-effort, idempotent restore; never manipulates a reused HWND."""
        state = self._state or _read_state(self.state_path)
        if state is None:
            self._hidden = False
            self._state = None
            return
        try:
            restored = self._restore_state(state, _winapi())
        except Exception:
            restored = False
        # A missing/reused target is also safe to forget: no other process was
        # changed.  Keep state only when a matching target remains but Windows
        # rejected SetWindowPos, so another later recovery attempt is possible.
        if restored:
            _remove_state(self.state_path)
            self._hidden = False
            self._state = None
        else:
            try:
                api = _winapi()
                stale = not api.is_window(int(state["hwnd"])) or api.process_id(int(state["hwnd"])) != int(state["owner_pid"])
            except Exception:
                stale = False
            if stale:
                _remove_state(self.state_path)
                self._hidden = False
                self._state = None
            else:
                self._hidden = True
                self._state = state
                raise RuntimeError('Windows refused to restore the pet position. The recovery record is retained; retry Restore desktop pet or exit the dock for watchdog recovery.')
