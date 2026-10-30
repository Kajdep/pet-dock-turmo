"""Headless Tk regression checks; Windows/native/USB calls are simulated."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
import time
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import tkinter as tk
from tkinter import ttk
from PIL import Image
import pet_bridge
from gui_test_support import isolated_app_state
from capture import WindowInfo, WindowCapture


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def button(popup, text):
    return next(x for x in descendants(popup) if isinstance(x, ttk.Button) and x.cget('text') == text)


class FakeAreaCapture:
    def __init__(self, rect, frames, on_error):
        self.rect, self.frames = rect, frames
        self.started = False
    def start(self):
        self.started = True
        self.frames.put(Image.new('RGBA', (100, 100), '#123456'))
    def stop(self):
        pass


root = tk.Tk()
ports = [{'device': 'COM38', 'label': 'COM38 · USB 1A86:5722', 'vid': 0x1A86, 'pid': 0x5722, 'serial_number': 'USB35INCHIPSV2'}]
windows = [WindowInfo(123, 'ChatGPT', 'ChatGPT.exe', 879, 1080, True)]
checks = 0
with isolated_app_state(pet_bridge), patch.object(pet_bridge, 'list_windows', return_value=windows), patch.object(pet_bridge, 'list_displays', return_value=ports):
    app = pet_bridge.App(root)
    root.update()
    source = Image.new('RGBA', (879, 1080), (0, 0, 0, 0))
    source.paste(Image.new('RGBA', (30, 100), '#d09b70'), (680, 820))
    app.frames.put(source)
    app.tick()
    root.update()
    assert app.auto_crop is not None
    assert app.auto_crop[2] - app.auto_crop[0] < .3
    assert app.auto_crop[3] - app.auto_crop[1] < .3
    assert app.source_view.viewport == app.auto_crop
    checks += 1
    print('PASS: automatic framing removes the huge transparent margins')

    view = app.source_view
    bounds = view.bounds
    viewport = view.viewport
    point = view.point(SimpleNamespace(x=bounds[0], y=bounds[1]))
    assert abs(point[0]-viewport[0]) < 1e-8 and abs(point[1]-viewport[1]) < 1e-8
    expected = (viewport[0]+.1*(viewport[2]-viewport[0]), viewport[1]+.1*(viewport[3]-viewport[1]),
                viewport[0]+.9*(viewport[2]-viewport[0]), viewport[1]+.9*(viewport[3]-viewport[1]))
    view.press(SimpleNamespace(x=bounds[0]+.1*bounds[2], y=bounds[1]+.1*bounds[3]))
    view.release(SimpleNamespace(x=bounds[0]+.9*bounds[2], y=bounds[1]+.9*bounds[3]))
    assert all(abs(x-y) < 1e-8 for x, y in zip(app.crop, expected))
    assert app.frame_mode.get() == 'Manual crop / full window'
    checks += 1
    print('PASS: manual cropping on a zoomed preview maps to original source coordinates')

    app.set_crop(None)
    app.tick()
    assert app.frame_mode.get().startswith('Auto:') and app.auto_crop is not None
    checks += 1
    print('PASS: Reset crop restores automatic framing')

    before = app.lcd_view.image.copy()
    app.metrics_enabled.set(True)
    app.metric_values = {'cpu': '42%', 'ram': '57%', 'gpu': 'N/A'}
    app.update_render()
    app.tick()
    assert app.lcd_view.image.size == before.size
    assert app.render_settings['metrics_selected'] == ['cpu', 'ram', 'gpu']
    assert app.lcd_view.image.tobytes() != before.tobytes()
    app.metrics_enabled.set(False)
    app.update_render()
    app.tick()
    assert not app.render_settings['metrics_selected']
    checks += 1
    print('PASS: optional metrics alter only the composed LCD output and can be disabled')

    class FakeGuard:
        hidden = False
        restored = 0
        def hide(self, hwnd):
            self.hidden = True
        def restore(self):
            self.hidden = False
            self.restored += 1
    real_guard = app.desktop_guard
    guard = FakeGuard()
    app.desktop_guard = guard
    app.capture = WindowCapture(123, app.frames, Mock())
    app.offscreen_pet.set(True)
    app.desktop_pet_changed()
    assert guard.hidden
    app.frames.timestamp = time.monotonic()-9
    app.tick()
    assert not guard.hidden and not app.offscreen_pet.get()
    checks += 1
    print('PASS: stalled off-screen capture restores the desktop pet')
    guard.hidden = True
    app.offscreen_pet.set(True)
    app.offscreen_blank_since = time.monotonic()-3
    app.frames.put(Image.new('RGBA', source.size, (0, 0, 0, 0)))
    app.tick()
    assert not guard.hidden
    checks += 1
    print('PASS: blank off-screen capture restores the desktop pet')
    app.capture.stop()
    app.capture = None
    app.desktop_guard = real_guard

    app.source_mode.set('Area')
    app.mode_changed()
    assert app.frame_mode.get() == 'Manual crop / full window'
    snapshot = Image.new('RGB', (3840, 1080), '#303030')
    with patch.object(pet_bridge, 'virtual_desktop_rect', return_value=(-1920, 0, 1920, 1080)), patch.object(pet_bridge.ImageGrab, 'grab', return_value=snapshot) as grab:
        app.pick_area()
        app.pick_area()
        deadline = time.monotonic() + 2
        while app.picker_pending and time.monotonic() < deadline:
            app.tick()
            root.update()
            time.sleep(.01)
        assert root.state() != 'withdrawn'
        popup = app.area_picker
        assert popup is not None and popup.winfo_exists()
        assert popup.state() != 'withdrawn'
        assert grab.call_count == 1
        checks += 1
        print('PASS: repeated picker clicks create one visible picker and do not hide the app')
        view = next(x for x in descendants(popup) if isinstance(x, pet_bridge.ImageView))
        view.crop = (.25, .25, .5, .5)
        with patch.object(pet_bridge, 'AreaCapture', FakeAreaCapture):
            button(popup, 'Use area').invoke()
        assert app.area_rect == (-960, 270, 0, 540)
        assert isinstance(app.capture, FakeAreaCapture) and app.capture.started
        assert app.area_picker is None and root.state() != 'withdrawn'
        checks += 1
        print('PASS: desktop selection handles negative monitor coordinates and starts capture')

    app.stop_all()
    app.show_area_picker((-1920, 0, 1920, 1080), snapshot)
    popup = app.area_picker
    root.update()
    button(popup, 'Cancel').invoke()
    assert app.area_picker is None and root.state() != 'withdrawn'
    checks += 1
    print('PASS: cancel closes only the picker, not the main app')

    with patch.object(pet_bridge, 'virtual_desktop_rect', return_value=(0, 0, 1920, 1080)), patch.object(pet_bridge.ImageGrab, 'grab', side_effect=OSError('simulated capture failure')), patch.object(pet_bridge.messagebox, 'showerror') as error:
        app.pick_area()
        deadline = time.monotonic() + 2
        while app.picker_pending and time.monotonic() < deadline:
            app.tick()
            root.update()
            time.sleep(.01)
        assert not app.picker_pending and root.state() != 'withdrawn'
        assert error.called
        checks += 1
        print('PASS: snapshot failure is reported while the app remains visible')

    finished = threading.Event()
    def slow_stop():
        time.sleep(.2)
        finished.set()
    control = Mock()
    control.stop.side_effect = slow_stop
    capture = WindowCapture(123, app.frames, Mock())
    capture.control = control
    started = time.monotonic()
    capture.stop()
    assert time.monotonic()-started < .1
    assert finished.wait(1)
    checks += 1
    print('PASS: native shutdown does not block the UI thread')
    app.close()
print(f'UI regressions: {checks}/{checks} passed (mocked capture and discovery).')
