"""Headless live-worker orientation/metrics regression; no native capture or USB."""
import sys
from pathlib import Path
import time
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import tkinter as tk
from PIL import Image
import pet_bridge
from capture import WindowInfo


class FakeConnection:
    instances = []
    def __init__(self, orientation, **kwargs):
        self.orientation = orientation
        self.rotations = []
        self.images = []
        self.ready = True
        self.full_requested = False
        self.closed = False
        self.last_send_stats = {}
        self.instances.append(self)
    @property
    def size(self):
        return pet_bridge.output_size(self.orientation)
    def open(self):
        return self
    def wait_ready(self, timeout):
        return self.ready
    def set_orientation(self, orientation):
        assert self.ready, 'must not rotate while a transfer is queued'
        self.orientation = orientation
        self.rotations.append(orientation)
        self.full_requested = True
    def request_full_redraw(self):
        self.full_requested = True
    def send(self, image):
        assert image.size == self.size
        self.images.append((self.orientation, image.copy(), self.full_requested))
        self.last_send_stats = {'bytes': image.width*image.height*2, 'regions': 1, 'send_ms': 1., 'full_frame': self.full_requested}
        self.full_requested = False
    def close(self):
        self.closed = True


def pump(root, predicate):
    deadline = time.monotonic()+3
    while not predicate() and time.monotonic()<deadline:
        root.update()
        time.sleep(.01)
    assert predicate(), 'simulated worker did not reach the expected state'


root = tk.Tk()
ports = [{'device':'COM38','label':'COM38 test','vid':0x1a86,'pid':0x5722}]
windows = [WindowInfo(123,'ChatGPT','ChatGPT.exe',880,1080,True)]
with patch.object(pet_bridge,'list_windows',return_value=windows), patch.object(pet_bridge,'list_displays',return_value=ports), patch.object(pet_bridge,'DisplayConnection',FakeConnection):
    app = pet_bridge.App(root)
    source = Image.new('RGBA',(880,1080),(0,0,0,0))
    source.paste(Image.new('RGBA',(30,100),'#d09b70'),(700,820))
    app.frames.put(source)
    app.capture = Mock()
    app.orientation.set('Landscape')
    app.orientation_changed()
    app.start_output()
    pump(root, lambda: FakeConnection.instances and FakeConnection.instances[0].images)
    connection = FakeConnection.instances[0]
    assert str(app.orientation_box.cget('state')) == 'readonly'
    sequence = app.frames.get()[1]
    for orientation in ['Portrait','Reverse portrait','Landscape']:
        app.orientation.set(orientation)
        app.orientation_box.event_generate('<<ComboboxSelected>>')
        pump(root,lambda: connection.images[-1][0] == orientation)
        assert connection.images[-1][1].size == pet_bridge.output_size(orientation)
        assert connection.images[-1][2]
        assert app.lcd_view.image.size == pet_bridge.output_size(orientation)
    assert len(FakeConnection.instances) == 1
    print('PASS: all live orientations use the same connection, new dimensions, and a full redraw')

    connection.ready = False
    count = len(connection.rotations)
    app.orientation.set('Reverse landscape')
    app.orientation_changed()
    time.sleep(.06)
    root.update()
    assert len(connection.rotations) == count
    app.orientation.set('Portrait')
    app.orientation_changed()
    connection.ready = True
    pump(root,lambda: connection.images[-1][0] == 'Portrait')
    assert connection.rotations[-1] == 'Portrait'
    print('PASS: busy USB waits; rapid changes coalesce to the latest selection')

    app.metrics = Mock()
    app.metrics.snapshot.return_value = {}
    count = len(connection.images)
    app.metrics_enabled.set(True)
    app.metrics_changed()
    pump(root,lambda: len(connection.images)>count and connection.images[-1][2] and app.render_settings['metrics_selected'])
    baseline = pet_bridge.compose_output(source,(320,480),dict(app.render_settings,metrics_selected=[]))
    count = len(connection.images)
    app.metrics_enabled.set(False)
    app.metrics_changed()
    assert app.lcd_view.image.tobytes() == baseline.tobytes(), 'off must restore immediately without a new capture/tick'
    pump(root,lambda: len(connection.images)>count)
    assert connection.images[-1][2], 'metrics off must send a full image'
    assert connection.images[-1][1].tobytes() == baseline.tobytes()
    assert app.frames.get()[1] == sequence
    app.metrics.stop.assert_called_with(wait=False)
    print('PASS: metrics off restores full-size preview immediately and fully redraws the LCD on a static capture')
    app.stop_output()
    pump(root,lambda: not app.output_thread.is_alive())
    app.close()
print('Live-worker regression passed (mock serial hardware).')
