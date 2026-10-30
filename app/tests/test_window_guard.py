from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import restore_watchdog
import window_guard
from window_guard import DesktopPetGuard, WS_EX_LAYERED


class FakeWinAPI:
    def __init__(self, *, layered=True, owner_pid=5000, move_ok=True, constrain=False):
        self.exists = True
        self.owner_pid = owner_pid
        self.style = WS_EX_LAYERED if layered else 0
        self.window_rect = (100, 200, 979, 1280)  # 879 x 1080 pet overlay
        self.bounds = (-1280, 0, 1920, 1080)
        self.move_ok = move_ok
        self.constrain = constrain
        self.moves = []

    def is_window(self, hwnd):
        return self.exists

    def exstyle(self, hwnd):
        return self.style

    def process_id(self, hwnd):
        return self.owner_pid

    def rect(self, hwnd):
        return self.window_rect

    def virtual_bounds(self):
        return self.bounds

    def set_position(self, hwnd, x, y, width, height):
        self.moves.append((hwnd, x, y, width, height))
        if not self.move_ok:
            return False
        if self.constrain and x >= self.bounds[2]:
            x = self.bounds[2] - 1
        self.window_rect = (x, y, x + width, y + height)
        return True


class WindowGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_path = Path(self.directory.name) / "desktop-pet-state.json"

    def test_hide_moves_pointer_sized_layered_hwnd_and_restores_exact_position(self):
        api = FakeWinAPI()
        hwnd = 0x123456788765  # catches accidental 32-bit HWND truncation
        guard = DesktopPetGuard(self.state_path)
        with patch("window_guard._winapi", return_value=api), \
             patch("window_guard._dock_identity", return_value=(777, 12.5)), \
             patch("window_guard.subprocess.Popen") as popen:
            guard.hide(hwnd)
            self.assertTrue(guard.hidden)
            self.assertEqual(api.moves, [(hwnd, 1984, 64, 879, 1080)])
            self.assertEqual(window_guard._POSITION_FLAGS,
                             window_guard.SWP_NOSIZE | window_guard.SWP_NOZORDER |
                             window_guard.SWP_NOACTIVATE | window_guard.SWP_NOOWNERZORDER)
            command = popen.call_args.args[0]
            self.assertEqual(command[0], sys.executable)
            self.assertEqual(command[-2:], ["--state", str(self.state_path)])
            self.assertTrue(command[1].endswith("restore_watchdog.py"))
            self.assertTrue(popen.call_args.kwargs["close_fds"])
            state = json.loads(self.state_path.read_text())
            self.assertEqual(state["hwnd"], hwnd)
            self.assertEqual(state["owner_pid"], 5000)
            self.assertEqual(state["rect"], [100, 200, 979, 1280])
            self.assertEqual((state["dock_pid"], state["dock_create_time"]), (777, 12.5))

            guard.restore()

        self.assertFalse(guard.hidden)
        self.assertEqual(api.moves[-1], (hwnd, 100, 200, 879, 1080))
        self.assertFalse(self.state_path.exists())

    def test_nonlayered_window_is_rejected_before_state_or_helper(self):
        api = FakeWinAPI(layered=False)
        with patch("window_guard._winapi", return_value=api), patch("window_guard.subprocess.Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "layered"):
                DesktopPetGuard(self.state_path).hide(123)
        popen.assert_not_called()
        self.assertFalse(self.state_path.exists())
        self.assertEqual(api.moves, [])

    def test_helper_start_failure_leaves_window_unmoved(self):
        api = FakeWinAPI()
        with patch("window_guard._winapi", return_value=api), \
             patch("window_guard._dock_identity", return_value=(7, 1.0)), \
             patch("window_guard.subprocess.Popen", side_effect=OSError("no helper")):
            with self.assertRaisesRegex(RuntimeError, "watchdog"):
                DesktopPetGuard(self.state_path).hide(123)
        self.assertEqual(api.moves, [])
        self.assertFalse(self.state_path.exists())

    def test_constrained_offscreen_placement_rolls_back_and_errors(self):
        api = FakeWinAPI(constrain=True)
        with patch("window_guard._winapi", return_value=api), \
             patch("window_guard._dock_identity", return_value=(7, 1.0)), \
             patch("window_guard.subprocess.Popen"):
            with self.assertRaisesRegex(RuntimeError, "outside the virtual desktop"):
                DesktopPetGuard(self.state_path).hide(123)
        self.assertEqual(api.moves[0], (123, 1984, 64, 879, 1080))
        self.assertEqual(api.moves[-1], (123, 100, 200, 879, 1080))
        self.assertFalse(self.state_path.exists())

    def test_restore_never_moves_reused_hwnd_owned_by_other_process(self):
        self.state_path.write_text(json.dumps({
            "version": 1, "hwnd": 99, "owner_pid": 5000,
            "rect": [100, 200, 979, 1280], "dock_pid": 7, "dock_create_time": 1.0,
        }))
        api = FakeWinAPI(owner_pid=6000)
        with patch("window_guard._winapi", return_value=api):
            DesktopPetGuard(self.state_path).restore()
        self.assertEqual(api.moves, [])
        self.assertFalse(self.state_path.exists())

    def test_failed_setwindowpos_rolls_back_and_errors(self):
        api = FakeWinAPI(move_ok=False)
        with patch("window_guard._winapi", return_value=api), \
             patch("window_guard._dock_identity", return_value=(7, 1.0)), \
             patch("window_guard.subprocess.Popen"):
            with self.assertRaisesRegex(RuntimeError, "SetWindowPos"):
                DesktopPetGuard(self.state_path).hide(123)
        # Initial failure followed by a best-effort restore attempt.
        self.assertEqual(api.moves, [(123, 1984, 64, 879, 1080), (123, 100, 200, 879, 1080)])
        self.assertFalse(self.state_path.exists())


class RestoreWatchdogTests(unittest.TestCase):
    def test_watchdog_restores_on_dock_death_then_removes_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps({
                "version": 1, "hwnd": 99, "owner_pid": 5000,
                "rect": [1, 2, 3, 4], "dock_pid": 7, "dock_create_time": 1.0,
            }))
            guard = Mock()
            guard.restore.side_effect = state_path.unlink
            with patch("restore_watchdog._same_process", return_value=False), \
                 patch("restore_watchdog.DesktopPetGuard", return_value=guard) as guard_cls:
                restore_watchdog.watch(state_path, poll_seconds=0)
            guard_cls.assert_called_once_with(state_path)
            guard.restore.assert_called_once_with()
            self.assertFalse(state_path.exists())

    def test_failed_restoration_retries_and_retains_recovery_record(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / 'state.json'
            state_path.write_text(json.dumps({
                'version': 1, 'hwnd': 99, 'owner_pid': 5000,
                'rect': [1, 2, 3, 4], 'dock_pid': 7, 'dock_create_time': 1.0,
            }))
            guard = Mock()
            guard.restore.side_effect = RuntimeError('restore refused')
            with patch('restore_watchdog._same_process', return_value=False), patch('restore_watchdog.DesktopPetGuard', return_value=guard):
                restore_watchdog.watch(state_path, poll_seconds=0)
            self.assertEqual(guard.restore.call_count, 10)
            self.assertTrue(state_path.exists())

    def test_watchdog_exits_normally_when_normal_restore_deleted_state(self):
        with tempfile.TemporaryDirectory() as directory:
            restore_watchdog.watch(Path(directory) / "not-present.json", poll_seconds=0)


if __name__ == "__main__":
    unittest.main()
