"""Headless Tk smoke test (Linux CI); does not test Windows capture or real USB."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import tkinter as tk
from PIL import Image, ImageGrab
import pet_bridge
from gui_test_support import isolated_app_state
from capture import WindowInfo

root = tk.Tk()
ports = [{'device': 'COM7', 'label': 'COM7 · USB 1A86:5722', 'vid': 0x1A86, 'pid': 0x5722, 'serial_number': 'USB35INCHIPSV2'}]
windows = [WindowInfo(123, 'Codex', 'Codex.exe', 760, 440, True)]
with isolated_app_state(pet_bridge), patch.object(pet_bridge, 'list_windows', return_value=windows), patch.object(pet_bridge, 'list_displays', return_value=ports):
    app = pet_bridge.App(root)
    app.orientation.set('Landscape')
    app.orientation_changed()
    root.update()
    assert app.window_choice.current() == 0
    assert app.port_choice.current() == 0
    assert app.selected_display() == 'COM7'
    source = Image.new('RGBA', (760, 440), (0, 0, 0, 0))
    source.paste(Image.new('RGBA', (100, 120), '#70c6ff'), (480, 250))
    app.frames.put(source)
    app.set_crop((.5, .2, .9, .95))
    app.tick()
    root.update()
    assert app.lcd_view.image.size == (480, 320)
    app.orientation.set('Portrait')
    app.update_render()
    app.tick()
    root.update()
    assert app.lcd_view.image.size == (320, 480)
    app.orientation.set('Landscape')
    app.update_render()
    app.tick()
    root.update()
    output = Path(__file__).resolve().parents[1] / 'build/ui-smoke.png'
    output.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab().save(output)
    app.set_crop(None)
    assert app.render_settings['crop'] is None
    app.stop_all()
    app.close()
print('UI smoke test passed (mocked Windows and USB discovery).')
