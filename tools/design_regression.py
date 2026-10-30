# SPDX-License-Identifier: GPL-3.0-or-later
"""Mocked Tk regression for the Screen Designer.

Run headlessly on Linux with::

    xvfb-run -a python3 tools/design_regression.py

Use ``--snapshot`` to save one inspection-only UI image to
``build/design-ui.png``.  The source preview contains generated colored blocks,
not a captured desktop/window, and metrics values are deterministic mock data.
No Windows capture, USB display, real settings directory, or sensor process is
used by this tool.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageGrab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import tkinter as tk

import design_ui
import pet_bridge
from capture import WindowInfo
from screen_style import DEFAULT_STYLE, PRESETS


_HASHED_PNG = re.compile(r"assets/[0-9a-f]{64}\.png\Z")


class MockMetrics:
    """Inert collector: designer tests must never query real hardware."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop(self, wait: bool = True) -> None:
        del wait
        self.stopped += 1

    def snapshot(self) -> dict[str, str]:
        return {}


def _source_blocks() -> Image.Image:
    """Build the only capture frame used by the UI/screenshot regression."""
    source = Image.new("RGBA", (240, 160), (0, 0, 0, 0))
    draw = ImageDraw.Draw(source)
    draw.rounded_rectangle((74, 62, 151, 144), radius=15, fill="#e08a4f")
    draw.ellipse((91, 34, 133, 79), fill="#f4b37c")
    draw.rounded_rectangle((27, 21, 142, 53), radius=10, fill="#5794d5")
    draw.rectangle((153, 103, 169, 136), fill="#82cf74")
    return source


def _blank_load(app: pet_bridge.App) -> None:
    """Ensure the regression never reads a caller's existing settings file."""
    app.saved = {}


def _pump(root: tk.Tk, milliseconds: int = 0) -> None:
    """Allow Tk variable/UI updates and, when requested, a debounced save."""
    deadline = time.monotonic() + milliseconds / 1000
    while True:
        root.update_idletasks()
        root.update()
        if time.monotonic() >= deadline:
            return
        time.sleep(0.01)


def _assert(condition: bool, text: str) -> None:
    if not condition:
        raise AssertionError(text)


def _save_snapshot(root: tk.Tk) -> Path:
    """Save a single synthetic-screen screenshot for parent inspection."""
    output = ROOT / "build" / "design-ui.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    root.update_idletasks()
    root.update()
    ImageGrab.grab().save(output)
    return output


def _assert_asset_json(data_dir: Path, imported_source: Path) -> dict[str, object]:
    saved_path = data_dir / "settings.json"
    _assert(saved_path.is_file(), "the designer debounce must write temp settings.json")
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    style = saved["design"]
    _assert(isinstance(style, dict), "saved design must be an object")
    background = style.get("background_image", "")
    _assert(isinstance(background, str) and _HASHED_PNG.fullmatch(background) is not None,
            "saved background must be a relative hash asset path")
    _assert((data_dir / background).is_file(), "saved background hash must resolve beneath temp data")
    serialized = json.dumps(saved, ensure_ascii=False)
    _assert(str(imported_source) not in serialized, "absolute source path leaked into saved JSON")
    _assert(not str(background).startswith(("/", "\\")), "asset path cannot be absolute")
    return saved


def run(snapshot: bool = False) -> int:
    checks = 0
    root: tk.Tk | None = None
    with tempfile.TemporaryDirectory(prefix="pet-dock-design-ui-") as temporary:
        data_dir = Path(temporary) / "data"
        data_dir.mkdir()
        selected_background = Path(temporary) / "背景 screenshot source.png"
        # A non-square opaque image makes Contain/Cover behavior visible while
        # its Unicode source name verifies the file dialog never persists it.
        background = Image.new("RGB", (120, 60), "#326496")
        ImageDraw.Draw(background).rectangle((0, 0, 59, 59), fill="#b8753b")
        background.save(selected_background)

        ports = [{"device": "COM38", "label": "COM38 · simulated", "vid": 0x1A86, "pid": 0x5722}]
        windows = [WindowInfo(123, "Synthetic pet", "pet.exe", 240, 160, True)]
        with patch.object(pet_bridge, "DATA", data_dir), patch.object(
            pet_bridge, "MetricCollector", MockMetrics
        ), patch.object(pet_bridge, "list_windows", return_value=windows), patch.object(
            pet_bridge, "list_displays", return_value=ports
        ), patch.object(pet_bridge.App, "load_settings", _blank_load), patch.object(
            design_ui.filedialog, "askopenfilename", return_value=str(selected_background)
        ):
            root = tk.Tk()
            root.geometry("1120x800+0+0")
            app = pet_bridge.App(root)
            app.metrics_enabled.set(True)
            for variable in app.metric_vars.values():
                variable.set(False)
            app.metric_vars["cpu"].set(True)
            app.metric_vars["ram"].set(True)
            app.metric_vars["gpu_temp"].set(True)
            app.metric_values = {"cpu": "42%", "ram": "57%", "gpu_temp": "63°C"}
            app.metrics_changed()
            app.frames.put(_source_blocks())
            app.tick()
            _pump(root)
            sequence = app.frames.get()[1]
            _assert(sequence >= 0, "synthetic frame must be published")
            checks += 1
            print("PASS: mocked discovery, metrics, and a synthetic transparent capture initialise the real UI")

            # Exercise the actual ScreenDesigner file-dialog callback.  It is
            # patched only to return the temporary Unicode filename above.
            app.designer.choose_background()
            _pump(root)
            imported = app.designer.settings()["background_image"]
            _assert(_HASHED_PNG.fullmatch(imported) is not None, "background import must produce a hash asset")
            _assert((data_dir / imported).is_file(), "background import must stay inside temp DATA/assets")
            _assert(app.designer.background_label.get().startswith("Custom image"), "background label did not update")
            _assert(app.frames.get()[1] == sequence, "background preview change must not require a capture frame")
            checks += 1
            print("PASS: file-dialog background import is local, hashed, and refreshes a static preview")

            # Palette action must preserve a custom picture and only replace
            # palette colours.  This goes through the real preset callback.
            app.designer.preset("Forest")
            forest = app.designer.settings()
            _assert(forest["background_image"] == imported, "palette action must retain custom background")
            _assert(forest["accent_color"] == PRESETS["Forest"]["accent_color"], "Forest palette did not apply")
            _assert(app.frames.get()[1] == sequence, "palette preview unexpectedly consumed a frame")
            checks += 1
            print("PASS: palette callbacks preserve local assets and update the static preview")

            # Render all metric treatments in each display orientation.  Style
            # changes are forced previews; the frame sequence must remain fixed.
            styled_outputs: dict[tuple[str, str], bytes] = {}
            for orientation, expected_size in (("Portrait", (320, 480)), ("Landscape", (480, 320))):
                app.orientation.set(orientation)
                app.orientation_changed()
                for treatment in ("Cards", "Bars", "Rings"):
                    app.designer.apply(dict(app.designer.settings(), metrics_style=treatment, panel_opacity=100))
                    _pump(root)
                    _assert(app.frames.get()[1] == sequence, f"{orientation} {treatment} needed a new frame")
                    _assert(app.lcd_view.image.size == expected_size, f"{orientation} preview has wrong dimensions")
                    styled_outputs[(orientation, treatment)] = app.lcd_view.image.tobytes()
                _assert(len({styled_outputs[(orientation, name)] for name in ("Cards", "Bars", "Rings")}) == 3,
                        f"{orientation} metric treatments must render distinctly")
            checks += 1
            print("PASS: Cards, Bars, and Rings redraw distinctly in portrait and landscape without a new capture")

            # Change actual designer variables as the slider callbacks do, then
            # use changed() to force preview. Top/bottom, opacity, and pet
            # placement should all publish through update_render immediately.
            before = app.lcd_view.image.tobytes()
            app.designer.variables["panel_opacity"].set(43)
            app.designer.variables["metrics_position"].set("Top")
            app.designer.variables["pet_x"].set(0.12)
            app.designer.variables["pet_y"].set(0.84)
            app.pet_scale.set(0.42)
            app.designer.changed()
            _pump(root)
            settings = app.render_settings
            _assert(settings["design"]["panel_opacity"] == 43, "opacity slider value was not published")
            _assert(settings["design"]["metrics_position"] == "Top", "top metrics position was not published")
            _assert(settings["design"]["pet_x"] == 0.12 and settings["design"]["pet_y"] == 0.84,
                    "pet position sliders were not published")
            _assert(abs(float(settings["pet_scale"]) - 0.42) < 1e-8, "pet scale slider was not published")
            _assert(app.lcd_view.image.tobytes() != before, "opacity/position slider changes did not alter preview")
            _assert(app.frames.get()[1] == sequence, "slider previews must work on a static frame")
            app.designer.center_pet()
            _pump(root)
            centered = app.designer.settings()
            _assert(centered["pet_x"] == centered["pet_y"] == 0.5, "center pet did not reset positions")
            _assert(abs(app.pet_scale.get() - 0.9) < 1e-8, "center pet did not reset size to 90%")
            app.designer.variables["metrics_position"].set("Bottom")
            app.designer.changed()
            _pump(root)
            _assert(app.render_settings["design"]["metrics_position"] == "Bottom", "bottom metrics position was not published")
            checks += 1
            print("PASS: opacity, top/bottom, and pet/size slider areas refresh static previews and reset safely")

            # The deferred callback must persist once, in patched temporary
            # DATA, and the JSON can be loaded without reading user settings.
            _assert(app.designer.pending_save is not None, "designer changes must schedule a debounce save")
            _pump(root, milliseconds=650)
            saved = _assert_asset_json(data_dir, selected_background)
            _assert(saved["design"]["metrics_position"] == "Bottom", "saved JSON did not retain designer state")
            checks += 1
            print("PASS: delayed saver writes reloadable design JSON only under temporary DATA with relative assets")

            # The saved JSON is the persistence reload boundary; normalize it
            # exactly as a fresh App/ScreenDesigner session will do.
            reloaded = pet_bridge.normalize_style(saved["design"])
            _assert(reloaded["background_image"] == imported, "reloaded JSON lost background asset")
            _assert(reloaded["panel_opacity"] == 43, "reloaded JSON lost opacity")
            _assert(reloaded["pet_x"] == reloaded["pet_y"] == 0.5, "reloaded JSON lost reset placement")
            checks += 1
            print("PASS: persisted design JSON reloads with the same validated local settings")

            if snapshot:
                app.settings_tabs.select(3)  # Design tab, useful for visual parent inspection.
                snapshot_path = _save_snapshot(root)
                _assert(snapshot_path.is_file(), "requested Design-tab screenshot was not written")
                checks += 1
                print(f"PASS: synthetic Design-tab snapshot written to {snapshot_path}")

            # Exercise the real remove callback last, after JSON validation.
            app.designer.remove_background()
            _pump(root)
            _assert(app.designer.settings()["background_image"] == "", "remove background did not clear image setting")
            _assert(app.designer.background_label.get() == "Solid color (no image)", "remove background label was not reset")
            checks += 1
            print("PASS: remove-background callback clears only the selected local asset")

            app.close()
            root = None
    print(f"Design regression: {checks}/{checks} passed (mocked Tk/capture/discovery/metrics; no real data or hardware).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Mocked Pet Dock Screen Designer regression")
    parser.add_argument("--snapshot", action="store_true", help="save build/design-ui.png with the Design tab selected")
    arguments = parser.parse_args()
    return run(snapshot=arguments.snapshot)


if __name__ == "__main__":
    raise SystemExit(main())
