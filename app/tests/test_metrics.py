from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import metrics


class MetricCollectorTests(unittest.TestCase):
    def _psutil(self, counters):
        return SimpleNamespace(
            cpu_percent=Mock(return_value=42.4),
            virtual_memory=Mock(return_value=SimpleNamespace(percent=63.6)),
            disk_usage=Mock(return_value=SimpleNamespace(percent=81.2)),
            net_io_counters=Mock(side_effect=counters),
            sensors_temperatures=Mock(
                return_value={"coretemp": [SimpleNamespace(current=54.6)]}
            ),
            cpu_freq=Mock(return_value=SimpleNamespace(current=3199.6)),
        )

    def test_snapshot_has_display_strings_and_versioned_poll(self):
        fake_psutil = self._psutil(
            [
                SimpleNamespace(bytes_sent=1_000, bytes_recv=2_000),
                SimpleNamespace(bytes_sent=3_048, bytes_recv=6_096),
            ]
        )
        collector = metrics.MetricCollector()
        self.assertEqual(collector.version, 0)
        self.assertEqual(collector.snapshot(), {key: "N/A" for key in metrics.METRIC_KEYS})

        gpu = {"gpu": "27%", "gpu_temp": "68\N{DEGREE SIGN}C", "vram": "1.5/8.0 GiB"}
        with patch.object(metrics, "psutil", fake_psutil), patch.object(
            metrics, "_query_nvidia", return_value=gpu
        ), patch.object(metrics.time, "monotonic", side_effect=[10.0, 12.0]):
            collector._collect_once()
            first = collector.snapshot()
            collector._collect_once()
            second = collector.snapshot()

        self.assertEqual(collector.version, 2)
        self.assertEqual(first["cpu"], "42%")
        self.assertEqual(first["ram"], "64%")
        self.assertEqual(first["disk"], "81%")
        self.assertEqual(first["net"], "N/A")  # A rate requires two samples.
        self.assertEqual(first["cpu_temp"], "55\N{DEGREE SIGN}C")
        self.assertEqual(first["clock"], "3.20 GHz")
        self.assertEqual(second["net"], "U 1.0K D 2.0K")
        self.assertEqual(second["gpu"], "27%")
        self.assertEqual(second["gpu_temp"], "68\N{DEGREE SIGN}C")
        self.assertEqual(second["vram"], "1.5/8.0 GiB")
        fake_psutil.disk_usage.assert_called_with("/")

    def test_network_counter_reset_is_clamped_to_zero(self):
        fake_psutil = self._psutil(
            [
                SimpleNamespace(bytes_sent=2_000, bytes_recv=4_000),
                SimpleNamespace(bytes_sent=50, bytes_recv=25),
            ]
        )
        collector = metrics.MetricCollector()
        with patch.object(metrics, "psutil", fake_psutil), patch.object(
            metrics, "_query_nvidia", return_value={key: "N/A" for key in ("gpu", "gpu_temp", "vram")}
        ), patch.object(metrics.time, "monotonic", side_effect=[3.0, 4.0]):
            collector._collect_once()
            collector._collect_once()
        self.assertEqual(collector.snapshot()["net"], "U 0B D 0B")

    def test_invalid_interval_rejected(self):
        for interval in (0, -1, float("inf"), True, "bad"):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                metrics.MetricCollector(interval)

    def test_default_disk_root_is_posix_root(self):
        self.assertEqual(metrics._default_disk_path(), "/")


class NvidiaTests(unittest.TestCase):
    def test_nvidia_query_uses_first_adapter_and_parses_partial_values(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout="27, 68, 1536, 8192\n99, 75, 2048, 16384\n",
        )
        with patch.object(metrics.subprocess, "run", return_value=completed) as run:
            values = metrics._query_nvidia()

        self.assertEqual(values, {"gpu": "27%", "gpu_temp": "68\N{DEGREE SIGN}C", "vram": "1.5/8.0 GiB"})
        run.assert_called_once_with(
            metrics._NVIDIA_COMMAND,
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )

    def test_missing_or_unavailable_nvidia_data_is_not_invented(self):
        completed = SimpleNamespace(returncode=0, stdout="N/A, N/A, N/A, 8192\n")
        with patch.object(metrics.subprocess, "run", return_value=completed):
            self.assertEqual(
                metrics._query_nvidia(),
                {"gpu": "N/A", "gpu_temp": "N/A", "vram": "N/A"},
            )
        with patch.object(metrics.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(
                metrics._query_nvidia(),
                {"gpu": "N/A", "gpu_temp": "N/A", "vram": "N/A"},
            )

    def test_windows_query_requests_no_console_creation(self):
        completed = SimpleNamespace(returncode=0, stdout="1, 2, 1024, 2048\n")
        with patch.object(metrics.os, "name", "nt"), patch.object(
            metrics.subprocess, "run", return_value=completed
        ) as run:
            metrics._query_nvidia()
        self.assertEqual(run.call_args.kwargs["creationflags"], 0)
        self.assertEqual(run.call_args.kwargs["timeout"], 1.0)


class MetricsPanelTests(unittest.TestCase):
    def test_empty_selection_reserves_no_height(self):
        self.assertEqual(metrics.metrics_panel_height((320, 480), []), 0)
        panel = metrics.render_metrics_panel((320, 480), {}, [])
        self.assertEqual(panel.mode, "RGB")
        self.assertEqual(panel.size, (320, 0))

    def test_panel_dimensions_are_compact_for_both_display_orientations(self):
        selected = list(metrics.METRIC_KEYS)
        values = {
            "cpu": "42%",
            "ram": "64%",
            "disk": "81%",
            "net": "U 1.0K D 2.0K",
            "gpu": "27%",
            "gpu_temp": "68\N{DEGREE SIGN}C",
            "vram": "1.5/8.0 GiB",
            "cpu_temp": "N/A",
            "clock": "3.20 GHz",
        }
        for size in ((320, 480), (480, 320)):
            with self.subTest(size=size):
                height = metrics.metrics_panel_height(size, selected)
                panel = metrics.render_metrics_panel(size, values, selected)
                self.assertGreater(height, 0)
                self.assertLessEqual(height, int(size[1] * 0.45))
                self.assertEqual(panel.mode, "RGB")
                self.assertEqual(panel.size, (size[0], height))

    def test_panel_ignores_unselected_and_unknown_keys(self):
        panel = metrics.render_metrics_panel(
            (320, 480),
            {"gpu": "45%", "cpu": "99%"},
            ["gpu", "not-a-metric", "gpu"],
        )
        self.assertEqual(panel.size[1], metrics.metrics_panel_height((320, 480), ["gpu"]))
        # A card is drawn, while only the requested supported key is considered.
        pixels = panel.get_flattened_data() if hasattr(panel, "get_flattened_data") else panel.getdata()
        self.assertIn((27, 37, 49), pixels)


if __name__ == "__main__":
    unittest.main()
