"""Headless metrics-toggle regression using real ttk Checkbutton commands.

Run with:
    xvfb-run -a python3 tools/metrics_toggle_regression.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw

import pet_bridge
from gui_test_support import isolated_app_state
from capture import WindowInfo


class FakeCollector:
    """No worker thread or live system probes; records App.metrics_changed calls."""

    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.values = {"cpu": "42%", "ram": "57%", "gpu": "N/A"}

    def start(self) -> None:
        self.started += 1

    def stop(self, wait=True) -> None:
        self.stopped += 1

    def snapshot(self) -> dict[str, str]:
        return dict(self.values)


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def checkbutton(root, text: str) -> ttk.Checkbutton:
    return next(
        widget
        for widget in descendants(root)
        if isinstance(widget, ttk.Checkbutton) and widget.cget("text") == text
    )


def sparse_tall_pet() -> Image.Image:
    image = Image.new("RGBA", (960, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((690, 520, 770, 860), radius=28, fill="#e8aa7c")
    draw.ellipse((700, 440, 760, 530), fill="#e8aa7c")
    draw.rounded_rectangle((560, 310, 770, 400), radius=24, fill="#89b8f0")
    return image


def pet_height(image: Image.Image) -> int:
    ys = []
    rgb = image.convert("RGB")
    for y in range(image.height):
        for x in range(image.width):
            red, green, blue = rgb.getpixel((x, y))
            if red > 180 and 100 < green < 210 and blue < 180:
                ys.append(y)
    assert ys, "the known pet color should remain visible"
    return max(ys) - min(ys) + 1


def tick(app, root) -> None:
    app.tick()
    root.update_idletasks()
    root.update()


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    ports = [{"device": "COM38", "label": "COM38 · simulated", "vid": 0x1A86, "pid": 0x5722}]
    windows = [WindowInfo(123, "ChatGPT", "ChatGPT.exe", 960, 1080, True)]
    with isolated_app_state(pet_bridge), patch.object(pet_bridge, "list_windows", return_value=windows), patch.object(
        pet_bridge, "list_displays", return_value=ports
    ):
        app = pet_bridge.App(root)
        fake = FakeCollector()
        app.metrics = fake
        app.orientation.set("Portrait")
        app.pet_scale.set(0.95)
        app.frames.put(sparse_tall_pet())
        tick(app, root)

        assert app.auto_crop is not None, "test frame must exercise auto framing"
        auto_crop = app.auto_crop
        scale = app.pet_scale.get()
        static_sequence = app.frames.get()[1]
        baseline = app.lcd_view.image.copy()
        baseline_height = pet_height(baseline)

        show = checkbutton(root, "Show metrics on the Turmo")
        choices = {
            "cpu": checkbutton(root, "CPU utilization"),
            "ram": checkbutton(root, "RAM utilization"),
            "gpu": checkbutton(root, "GPU utilization"),
            "gpu_temp": checkbutton(root, "GPU temperature"),
            "vram": checkbutton(root, "GPU memory"),
            "cpu_temp": checkbutton(root, "CPU temperature"),
            "disk": checkbutton(root, "System drive utilization"),
            "net": checkbutton(root, "Network upload / download"),
            "clock": checkbutton(root, "CPU frequency"),
        }

        # Invoke the real global Checkbutton command. This is deliberately not
        # a direct update_render call: it tests MetricCollector start/stop too.
        show.invoke()
        tick(app, root)
        assert app.metrics_active and fake.started == 1

        # Start from no cards, then invoke each actual card callback one at a
        # time. The frame sequence must remain fixed throughout the test.
        for button in choices.values():
            if button.instate(["selected"]):
                button.invoke()
                tick(app, root)
        assert app.render_settings["metrics_selected"] == []
        assert app.lcd_view.image.tobytes() == baseline.tobytes()

        for key, button in choices.items():
            button.invoke()
            tick(app, root)
            assert key in app.render_settings["metrics_selected"]
            assert 0 < pet_height(app.lcd_view.image) <= baseline_height
            assert app.auto_crop == auto_crop and app.pet_scale.get() == scale
            button.invoke()
            tick(app, root)
            assert app.render_settings["metrics_selected"] == []
            assert app.lcd_view.image.tobytes() == baseline.tobytes()

        for button in choices.values():
            button.invoke()
        tick(app, root)
        assert len(app.render_settings["metrics_selected"]) == len(choices)
        assert 0 < pet_height(app.lcd_view.image) <= baseline_height

        # Invoke the real global off callback. With no new frame, output must
        # restore exactly, as must crop and slider state.
        show.invoke()
        tick(app, root)
        assert not app.metrics_active and fake.stopped == 1
        assert app.render_settings["metrics_selected"] == []
        assert app.auto_crop == auto_crop and app.pet_scale.get() == scale
        assert app.frames.get()[1] == static_sequence
        assert app.lcd_view.image.tobytes() == baseline.tobytes()
        app.close()
    print("PASS: real Tk metrics callbacks round-trip a static auto-cropped pet exactly.")


if __name__ == "__main__":
    main()
