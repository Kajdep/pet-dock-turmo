# SPDX-License-Identifier: GPL-3.0-or-later
"""One-shot local recovery helper for DesktopPetGuard.

This is not an autorun component or remote service.  It is spawned for one
active desktop-pet placement, polls roughly once per second, and exits when the
state file is removed by normal restoration.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import time

from window_guard import DesktopPetGuard, _read_state


def _same_process(pid: int, creation_time: float) -> bool:
    try:
        import psutil  # Bundled with the portable Windows runtime.
        process = psutil.Process(pid)
        return process.is_running() and abs(float(process.create_time()) - float(creation_time)) < 0.01
    except Exception:
        return False


def watch(state_path: Path, poll_seconds: float = 1.0) -> None:
    """Wait for normal state removal or restore after the dock process dies."""
    state_path = Path(state_path)
    while True:
        state = _read_state(state_path)
        if state is None:
            return
        dock_pid = state.get("dock_pid")
        create_time = state.get("dock_create_time")
        if not isinstance(dock_pid, int) or not isinstance(create_time, (int, float)):
            # A valid guard always writes these fields.  Do not attempt to move
            # a window from malformed state.
            return
        if not _same_process(dock_pid, float(create_time)):
            for _ in range(10):
                try:
                    DesktopPetGuard(state_path).restore()
                except Exception:
                    pass
                if not state_path.exists():
                    return
                time.sleep(poll_seconds)
            # Keep a matching target's record for the emergency restore command
            # rather than losing its original position after a failed restore.
            return
        time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local desktop-pet restore watchdog")
    parser.add_argument("--state", required=True, type=Path, help="Guard JSON state path")
    args = parser.parse_args(argv)
    watch(args.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
