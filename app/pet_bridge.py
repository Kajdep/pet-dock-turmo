# SPDX-License-Identifier: GPL-3.0-or-later
"""Pet Dock 0.2.1: live companion capture, optional metrics, and a USB display."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageDraw, ImageGrab, ImageTk

from capture import (enable_dpi_awareness, list_windows, virtual_desktop_rect,
                     LatestFrame, WindowCapture, AreaCapture, compose_frame, crop_frame)
from display_io import DisplayConnection, list_displays
from framing import detect_content_crop, CropTracker
from metrics import MetricCollector, metrics_panel_height, render_metrics_panel
from window_guard import DesktopPetGuard

VERSION = '0.2.1'
BASE = Path(__file__).resolve().parent.parent
DATA = BASE / 'data'
DATA.mkdir(exist_ok=True)
logging.basicConfig(filename=DATA / 'bridge.log', level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')


def output_size(orientation):
    return (480, 320) if 'landscape' in orientation.lower() else (320, 480)


def compose_output(image, size, settings):
    selected = settings.get('metrics_selected', [])
    panel_height = metrics_panel_height(size, selected)
    w, h = size
    pet_height = max(1, h-panel_height)
    scale = min(1.0, max(.4, float(settings.get('pet_scale', .9))))
    fit_size = (max(1, round(w*scale)), max(1, round(pet_height*scale)))
    source = crop_frame(image, settings.get('crop'))
    if settings.get('fit_visible', True) and not settings.get('ignore_alpha', False):
        alpha = source.convert('RGBA').getchannel('A')
        bounds = alpha.point([0 if value < 20 else 255 for value in range(256)]).getbbox()
        if bounds is not None:
            l, t, r, b = bounds
            source = source.crop((max(0, l-2), max(0, t-2), min(source.width, r+2), min(source.height, b+2)))
    pet = compose_frame(source, fit_size,
                        ignore_alpha=settings.get('ignore_alpha', False),
                        background=settings.get('background', '#10141b'),
                        pixel_art=settings.get('pixel_art', False))
    output = Image.new('RGB', size, settings.get('background', '#10141b'))
    output.paste(pet, ((w-pet.width)//2, (pet_height-pet.height)//2))
    if panel_height:
        panel = render_metrics_panel(size, settings.get('metrics_values', {}), selected)
        output.paste(panel, (0, h-panel_height))
    return output


class ImageView(ttk.Frame):
    def __init__(self, parent, width, height, selectable=False, callback=None):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, width=width, height=height, bg='#10141b', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.image = None
        self.photo = None
        self.viewport = (0, 0, 1, 1)
        self.bounds = None
        self.anchor = None
        self.crop = None
        self.callback = callback
        self.selectable = selectable
        self.canvas.bind('<Configure>', lambda _: self.redraw())
        if selectable:
            self.canvas.bind('<ButtonPress-1>', self.press)
            self.canvas.bind('<B1-Motion>', self.drag)
            self.canvas.bind('<ButtonRelease-1>', self.release)

    def show(self, image, viewport=None):
        self.image = image
        self.viewport = viewport or (0, 0, 1, 1)
        self.redraw()

    def redraw(self):
        if self.image is None:
            return
        l, t, r, b = self.viewport
        box = (int(l*self.image.width), int(t*self.image.height),
               max(int(l*self.image.width)+1, int(r*self.image.width)),
               max(int(t*self.image.height)+1, int(b*self.image.height)))
        visible = self.image.crop(box)
        cw, ch = max(10, self.canvas.winfo_width()), max(10, self.canvas.winfo_height())
        scale = min(cw / visible.width, ch / visible.height)
        w, h = max(1, round(visible.width * scale)), max(1, round(visible.height * scale))
        image = visible.convert('RGBA')
        bg = Image.new('RGBA', image.size, '#222834')
        bg.alpha_composite(image)
        self.photo = ImageTk.PhotoImage(bg.convert('RGB').resize((w, h), Image.Resampling.LANCZOS))
        x, y = (cw - w) // 2, (ch - h) // 2
        self.bounds = (x, y, w, h)
        self.canvas.delete('frame')
        self.canvas.create_image(x, y, anchor='nw', image=self.photo, tags='frame')
        self.draw_crop()

    def point(self, event):
        if not self.bounds:
            return None
        x, y, w, h = self.bounds
        l, t, r, b = self.viewport
        return (l + max(0, min(1, (event.x-x)/w))*(r-l),
                t + max(0, min(1, (event.y-y)/h))*(b-t))

    def press(self, event):
        self.anchor = self.point(event)

    def drag(self, event):
        p = self.point(event)
        if self.anchor and p:
            self.crop = (min(self.anchor[0], p[0]), min(self.anchor[1], p[1]),
                         max(self.anchor[0], p[0]), max(self.anchor[1], p[1]))
            self.draw_crop()

    def release(self, event):
        self.drag(event)
        if self.crop and (self.crop[2] - self.crop[0] < .01 or self.crop[3] - self.crop[1] < .01):
            self.crop = None
        self.anchor = None
        if self.callback:
            self.callback(self.crop)
        self.draw_crop()

    def draw_crop(self):
        self.canvas.delete('crop')
        if self.crop and self.bounds:
            x, y, w, h = self.bounds
            l, t, r, b = self.crop
            vl, vt, vr, vb = self.viewport
            l, t, r, b = max(l, vl), max(t, vt), min(r, vr), min(b, vb)
            if l >= r or t >= b:
                return
            self.canvas.create_rectangle(x + (l-vl)/(vr-vl)*w, y + (t-vt)/(vb-vt)*h,
                                         x + (r-vl)/(vr-vl)*w, y + (b-vt)/(vb-vt)*h,
                                         outline='#70c6ff', width=2, tags='crop')


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f'Pet Dock {VERSION} · Turmo')
        self.root.geometry('1080x800')
        self.root.minsize(950, 730)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.frames = LatestFrame()
        self.capture = None
        self.capture_started = 0
        self.windows = []
        self.displays = []
        self.area_rect = None
        self.crop = None
        self.auto_crop = None
        self.crop_tracker = CropTracker()
        self.last_detection_sequence = -1
        self.area_picker = None
        self.picker_pending = False
        self.picker_token = 0
        self.metrics = MetricCollector(interval=1.0)
        self.metrics_active = False
        self.metric_values = {}
        self.last_metrics_poll = 0.0
        self.desktop_guard = DesktopPetGuard(DATA / 'desktop-pet-restore.json')
        self.offscreen_blank_since = None
        self.settings_lock = threading.Lock()
        self.render_settings = {'crop': None, 'ignore_alpha': False, 'pixel_art': False, 'background': '#10141b'}
        self.events = queue.Queue()
        self.output_thread = None
        self.output_stop = threading.Event()
        self.closing = False
        self.last_preview_sequence = -1
        self.settings_generation = 0
        self.last_settings_generation = -1
        self.load_settings()
        self.make_ui()
        self.refresh_sources()
        self.refresh_displays()
        self.root.after(100, self.tick)
        self.root.report_callback_exception = self.callback_error

    def load_settings(self):
        try:
            self.saved = json.loads((DATA / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.saved = {}

    def make_ui(self):
        from ui_layout import build_ui
        build_ui(self, ImageView)

    def callback_error(self, typ, value, tb):
        logging.error('UI error', exc_info=(typ, value, tb))
        if self.desktop_guard.hidden:
            self.restore_desktop_pet()
        messagebox.showerror('Bridge error', f'{value}\n\nDetails are in data\\bridge.log.')

    def status_message(self, text):
        self.status.set(text)
        logging.info(text)

    def refresh_sources(self):
        if self.capture:
            self.status_message('Stop capture before changing the source.')
            return
        try:
            self.windows = list_windows()
            self.window_choice['values'] = [w.label for w in self.windows]
            if self.windows:
                self.window_choice.current(0)
        except Exception as exc:
            self.status_message(str(exc))

    def refresh_displays(self):
        try:
            self.displays = list_displays()
            self.port_choice['values'] = [p['label'] for p in self.displays]
            if self.displays:
                self.port_choice.current(0)
            else:
                self.port_choice.set('No matching COM display')
        except Exception as exc:
            self.status_message(f'USB discovery failed: {exc}')

    def mode_changed(self):
        self.stop_all()
        self.cancel_area_picker()
        is_area = self.source_mode.get() == 'Area'
        self.frame_mode.set('Manual crop / full window' if is_area else 'Auto: pet + bubble')
        self.crop = None
        self.auto_crop = None
        self.crop_tracker.reset()
        self.update_render()
        self.window_choice.configure(state='disabled' if is_area else 'readonly')
        self.area_button.configure(state='normal' if is_area else 'disabled')

    def pick_area(self):
        if self.area_picker is not None and self.area_picker.winfo_exists():
            self.area_picker.lift()
            return
        if self.picker_pending:
            return
        self.stop_all()
        self.source_mode.set('Area')
        self.window_choice.configure(state='disabled')
        self.area_button.configure(state='disabled')
        self.picker_pending = True
        self.picker_token += 1
        token = self.picker_token
        self.status_message('Taking a desktop snapshot… The app stays visible. Move it away from the pet if it covers the area you want.')
        def snapshot():
            try:
                rect = virtual_desktop_rect()
                shot = ImageGrab.grab(all_screens=True, include_layered_windows=True)
                self.events.put(('area_ready', (token, rect, shot)))
            except Exception as exc:
                self.events.put(('area_error', (token, str(exc))))
        threading.Thread(target=snapshot, daemon=True, name='desktop-picker-snapshot').start()

    def cancel_area_picker(self):
        self.picker_token += 1
        self.picker_pending = False
        popup = self.area_picker
        self.area_picker = None
        if popup is not None:
            try:
                popup.grab_release()
                popup.destroy()
            except tk.TclError:
                pass

    def show_area_picker(self, rect, shot):
        popup = None
        try:
            popup = tk.Toplevel(self.root)
            self.area_picker = popup
            popup.title('Choose the visible pet area — drag a rectangle, then Use area')
            popup.geometry('1000x700')
            popup.minsize(700, 500)
            # Do not withdraw the owner or mark this transient to a hidden owner:
            # Windows may hide both windows in that case, leaving no visible UI.
            ttk.Label(popup, text='Drag around the pet and bubble. This fallback mirrors visible desktop pixels, including anything covering the pet.', padding=10, wraplength=950).pack(anchor='w')
            row = ttk.Frame(popup, padding=10)
            row.pack(side='bottom', fill='x')
            view = ImageView(popup, 950, 540, selectable=True)
            view.pack(fill='both', expand=True, padx=10)
            view.show(shot)
            def finish(use):
                if use and not view.crop:
                    messagebox.showinfo('Choose an area', 'Drag a non-empty rectangle around the pet first.', parent=popup)
                    return
                if use:
                    l, t, r, b = view.crop
                    x, y, xr, yb = rect
                    left, top = round(x+l*(xr-x)), round(y+t*(yb-y))
                    right, bottom = round(x+r*(xr-x)), round(y+b*(yb-y))
                    self.area_rect = (left, top, max(left+1, right), max(top+1, bottom))
                    self.crop = None
                    self.auto_crop = None
                    self.frame_mode.set('Manual crop / full window')
                    self.crop_tracker.reset()
                    self.update_render()
                self.cancel_area_picker()
                self.area_button.configure(state='normal')
                if use:
                    self.start_preview()
            ttk.Button(row, text='Use area', command=lambda: finish(True)).pack(side='right')
            ttk.Button(row, text='Cancel', command=lambda: finish(False)).pack(side='right', padx=8)
            popup.protocol('WM_DELETE_WINDOW', lambda: finish(False))
            popup.bind('<Escape>', lambda _: finish(False))
            popup.deiconify()
            popup.lift()
            popup.after(50, lambda: popup.focus_set() if popup.winfo_exists() else None)
        except Exception as exc:
            logging.exception('Desktop area picker failed')
            self.cancel_area_picker()
            self.area_button.configure(state='normal')
            self.status_message(f'Desktop area picker failed: {exc}')
            messagebox.showerror('Desktop picker', str(exc), parent=self.root)

    def start_preview(self):
        self.stop_all()
        self.frames = LatestFrame()
        self.last_preview_sequence = -1
        self.crop = None
        self.auto_crop = None
        self.crop_tracker.reset()
        self.last_detection_sequence = -1
        self.source_view.crop = None
        self.update_render()
        try:
            if self.source_mode.get() == 'Area':
                if not self.area_rect:
                    self.pick_area()
                    return
                cap = AreaCapture(self.area_rect, self.frames, lambda text: self.events.put(('capture_error', text)))
            else:
                idx = self.window_choice.current()
                if idx < 0 or idx >= len(self.windows):
                    raise RuntimeError('No pet window selected. Enable the Codex pet, then Refresh.')
                win = self.windows[idx]
                cap = WindowCapture(win.hwnd, self.frames, lambda text: self.events.put(('capture_error', text)),
                                    hide_border=self.hide_border.get())
            self.capture = cap
            self.capture_started = time.monotonic()
            cap.start()
            note = ''
            if isinstance(cap, WindowCapture) and self.hide_border.get() and not cap.border_hidden:
                note = ' Windows kept its yellow capture indicator; the desktop-area fallback has no WGC border.'
            self.status_message('Capturing. Auto framing removes empty margins; drag the Source preview to override. Confirm the Turmo preview, then Start screen.'+note)
        except Exception as exc:
            if self.capture:
                try:
                    self.capture.stop()
                except Exception:
                    logging.exception('Capture cleanup')
            self.capture = None
            self.status_message(f'Capture failed: {exc}')
            messagebox.showerror('Capture failed', f'{exc}\n\nIf native capture is blank or unsupported, try Desktop area (fallback).')

    def set_crop(self, crop):
        self.crop = crop
        if crop is not None:
            self.frame_mode.set('Manual crop / full window')
            self.auto_crop = None
        elif self.source_mode.get() == 'Window':
            self.frame_mode.set('Auto: pet + bubble')
            self.auto_crop = None
            self.crop_tracker.reset()
            self.last_detection_sequence = -1
        self.source_view.crop = crop
        self.source_view.draw_crop()
        self.update_render()

    def framing_changed(self):
        self.crop = None
        self.auto_crop = None
        self.crop_tracker.reset()
        self.last_detection_sequence = -1
        self.update_render()

    def update_render(self):
        with self.settings_lock:
            self.render_settings = {'crop': self.crop or self.auto_crop, 'ignore_alpha': self.ignore_alpha.get(),
                                    'pixel_art': self.pixel_art.get(), 'background': '#10141b',
                                    'pet_scale': self.pet_scale.get(),
                                    'fit_visible': self.fit_visible.get(), 'orientation': self.orientation.get(),
                                    'metrics_selected': [key for key, var in self.metric_vars.items() if self.metrics_enabled.get() and var.get()],
                                    'metrics_values': dict(self.metric_values)}
            self.settings_generation += 1

    def orientation_changed(self):
        self.update_render()
        self.refresh_previews(force=True)
        if self.output_thread and self.output_thread.is_alive():
            self.status_message('Applying orientation after the current USB transfer, then sending a full redraw…')

    def metrics_changed(self):
        enabled = self.metrics_enabled.get()
        was_active = self.metrics_active
        self.metrics_active = enabled
        # Layout publication and preview resizing must not wait on sensor work.
        self.update_render()
        self.refresh_previews(force=True)
        try:
            if enabled and not was_active:
                self.metrics.start()
            elif not enabled and was_active:
                self.metrics.stop(wait=False)
        except Exception:
            logging.exception('Metric collector lifecycle failed; screen layout was already updated')

    def reset_pet_size(self):
        self.pet_scale.set(.9)
        self.update_render()
        self.refresh_previews(force=True)

    def refresh_previews(self, image=None, sequence=None, force=False):
        if image is None:
            image, sequence, _ = self.frames.get()
        if image is None:
            return
        if not force and sequence == self.last_preview_sequence and self.settings_generation == self.last_settings_generation:
            return
        with self.settings_lock:
            settings = dict(self.render_settings)
            generation = self.settings_generation
        if self.source_view.anchor is None:
            self.source_view.crop = settings['crop']
            viewport = settings['crop'] if self.zoom_source.get() else None
        else:
            viewport = self.source_view.viewport
        self.source_view.show(image, viewport=viewport)
        self.lcd_view.show(compose_output(image, output_size(self.orientation.get()), settings))
        self.last_preview_sequence = sequence
        self.last_settings_generation = generation

    def start_screen(self):
        if self.output_thread and self.output_thread.is_alive():
            return
        if self.capture is None:
            self.start_preview()
        if self.capture is None:
            return
        deadline = time.monotonic() + 8
        def when_ready():
            if self.closing or self.capture is None:
                return
            if self.frames.get()[0] is not None:
                self.start_output()
            elif time.monotonic() < deadline:
                self.root.after(100, when_ready)
        when_ready()

    def desktop_pet_changed(self):
        if not self.offscreen_pet.get():
            self.restore_desktop_pet()
            return
        if not isinstance(self.capture, WindowCapture) or self.frames.get()[0] is None:
            self.offscreen_pet.set(False)
            messagebox.showinfo('Capture first', 'Start native Capture preview first. Desktop-area capture cannot follow a pet moved off-screen.')
            return
        try:
            self.desktop_guard.hide(self.capture.hwnd)
            self.offscreen_blank_since = None
            self.status_message('Pet moved off-screen experimentally. Watch the live Turmo preview; Restore desktop pet brings it back. Some clients stop rendering off-screen.')
        except Exception as exc:
            self.offscreen_pet.set(False)
            self.status_message(f'Could not move the desktop pet: {exc}')
            messagebox.showerror('Desktop pet', str(exc))

    def restore_desktop_pet(self):
        try:
            self.desktop_guard.restore()
            self.offscreen_pet.set(False)
            self.offscreen_blank_since = None
        except Exception as exc:
            logging.exception('Desktop pet restore failed')
            self.status_message(f'Desktop pet restore failed: {exc}. Use Restore-Desktop-Pet.cmd if needed.')

    def selected_display(self):
        idx = self.port_choice.current()
        if idx < 0 or idx >= len(self.displays):
            raise RuntimeError('No matching USB display. Plug it in, close Formbig, then Refresh.')
        return self.displays[idx]['device']

    def display_args(self):
        brightness = int(self.brightness.get())
        fps = int(self.fps.get())
        if not 1 <= brightness <= 100 or not 1 <= fps <= 20:
            raise ValueError('Brightness must be 1–100 and FPS target 1–20.')
        return dict(port=self.selected_display(), revision=self.revision.get(),
                    orientation=self.orientation.get(), brightness=brightness,
                    efficient_updates=self.efficient_updates.get(), compatible_pacing=self.compatible_pacing.get()), fps

    def start_output(self, testing=False):
        if self.output_thread and self.output_thread.is_alive():
            self.status_message('Screen is already running. Orientation applies live; stop before changing protocol or USB port.')
            return
        if not testing and (not self.capture or self.frames.get()[0] is None):
            messagebox.showinfo('Preview first', 'Start Preview capture and confirm that you can see your pet before sending it.')
            return
        try:
            args, fps = self.display_args()
        except Exception as exc:
            messagebox.showerror('USB setup', str(exc))
            return
        self.output_stop = threading.Event()
        stop = self.output_stop
        self.send_button.configure(state='disabled')
        self.test_button.configure(state='disabled')
        self.orientation_box.configure(state='readonly')
        self.status_message('Opening USB display… Close Formbig if the port is busy.')
        self.save_settings()
        def run():
            connection = None
            try:
                connection = DisplayConnection(**args)
                connection.open()
                if stop.is_set():
                    return
                size = connection.size
                if testing:
                    pattern = Image.new('RGB', size, '#10141b')
                    draw = ImageDraw.Draw(pattern)
                    colors = ['#ff4040', '#40ff40', '#4080ff', '#ffffff']
                    for i, color in enumerate(colors):
                        draw.rectangle((i*size[0]//4, 0, (i+1)*size[0]//4-1, size[1]-1), fill=color)
                    connection.send(pattern)
                    self.events.put(('status', 'Test sent: the display should show red, green, blue, and white vertical bands. If blank/garbled, Stop USB, unplug/replug, then try protocol B.'))
                    return
                self.events.put(('status', f'Live USB mirror started · {size[0]}×{size[1]} · target {fps} FPS (actual speed depends on the display).'))
                sent = 0
                started = time.monotonic()
                previous_key = None
                previous_layout = None
                busy_since = None
                while not stop.is_set():
                    begin = time.monotonic()
                    if not connection.wait_ready(timeout=.5):
                        if busy_since is None:
                            busy_since = begin
                        elif time.monotonic()-busy_since > 8:
                            raise RuntimeError('Serial queue did not become ready for 8 seconds. Check the cable/port, then restart the screen.')
                        self.events.put(('performance', 'USB queue busy · waiting instead of accumulating old frames'))
                        stop.wait(.01)
                        continue
                    busy_since = None
                    if stop.is_set():
                        break
                    image, sequence, captured_at = self.frames.get()
                    capture_age_ms = max(0, (time.monotonic()-captured_at)*1000)
                    with self.settings_lock:
                        settings = dict(self.render_settings)
                        generation = self.settings_generation
                    wanted_orientation = settings.get('orientation', args['orientation'])
                    if wanted_orientation != connection.orientation:
                        connection.set_orientation(wanted_orientation)
                        size = connection.size
                        previous_key = None
                        self.events.put(('status', f'Orientation applied: {wanted_orientation} · {size[0]}×{size[1]} · full redraw'))
                    key = (sequence, generation)
                    layout = (wanted_orientation, tuple(settings.get('metrics_selected', [])), settings.get('fit_visible', True))
                    if image is not None and key != previous_key:
                        if layout != previous_layout:
                            connection.request_full_redraw()
                        connection.send(compose_output(image, size, settings))
                        previous_key = key
                        previous_layout = layout
                        stats = connection.last_send_stats
                        if stats.get('bytes', 0):
                            sent += 1
                            self.events.put(('performance', f'Capture age {capture_age_ms:.0f} ms · USB submit {stats["send_ms"]:.0f} ms · {stats["bytes"]/1024:.1f} KiB in {stats["regions"]} region(s)'))
                    if begin - started >= 4:
                        rate = sent / max(.001, begin - started)
                        self.events.put(('status', f'Screen active · {rate:.1f} transmitted updates/sec · target {fps}. Orientation applies live; stop before changing protocol.'))
                        sent, started = 0, begin
                    stop.wait(max(0.005, 1 / fps - (time.monotonic() - begin)))
            except Exception as exc:
                logging.exception('USB output error')
                self.events.put(('usb_error', str(exc)))
            finally:
                try:
                    if connection is not None:
                        connection.close()
                except Exception:
                    logging.exception('USB close error')
                self.events.put(('usb_done', None))
        self.output_thread = threading.Thread(target=run, daemon=True, name='turmo-usb-output')
        self.output_thread.start()

    def test_display(self):
        self.start_output(testing=True)

    def stop_output(self):
        self.output_stop.set()
        if self.output_thread and self.output_thread.is_alive():
            self.status_message('Stopping USB output…')

    def stop_all(self):
        self.stop_output()
        if self.desktop_guard.hidden:
            self.restore_desktop_pet()
        if self.capture:
            try:
                self.capture.stop()
            except Exception:
                logging.exception('Capture stop error')
            self.capture = None
        self.status_message('Stopped. The last LCD image remains; reopen Formbig to restore its normal theme.')

    def tick(self):
        if self.closing:
            return
        while True:
            try:
                event, data = self.events.get_nowait()
            except queue.Empty:
                break
            if event == 'status':
                self.status_message(data)
            elif event == 'performance':
                self.performance_text.set(data)
            elif event == 'area_ready':
                token, rect, shot = data
                if token == self.picker_token and self.source_mode.get() == 'Area':
                    self.picker_pending = False
                    self.show_area_picker(rect, shot)
            elif event == 'area_error':
                token, error = data
                if token == self.picker_token:
                    self.picker_pending = False
                    self.area_button.configure(state='normal')
                    self.status_message(f'Desktop snapshot failed: {error}')
                    messagebox.showerror('Desktop snapshot', error, parent=self.root)
            elif event == 'capture_error':
                self.stop_all()
                self.status_message(data)
            elif event == 'usb_error':
                if self.desktop_guard.hidden:
                    self.restore_desktop_pet()
                self.status_message(f'USB failed: {data}')
                messagebox.showerror('USB display error', f'{data}\n\nClose Formbig, unplug/replug the screen, and retry. If the test pattern is wrong, try the other protocol. No driver replacement is needed.')
            elif event == 'usb_done':
                self.send_button.configure(state='normal')
                self.test_button.configure(state='normal')
                self.orientation_box.configure(state='readonly')
        now = time.monotonic()
        if self.metrics_active and now-self.last_metrics_poll >= 1:
            values = self.metrics.snapshot()
            self.last_metrics_poll = now
            if values != self.metric_values:
                self.metric_values = values
                self.update_render()
        image, seq, captured_at = self.frames.get()
        if self.desktop_guard.hidden and captured_at and now-captured_at > 8:
            self.restore_desktop_pet()
            self.status_message('Native frames stopped after moving off-screen, so the desktop pet was restored. This client may require a visible or virtual display.')
        if self.desktop_guard.hidden and image is not None and not self.ignore_alpha.get():
            if image.convert('RGBA').getextrema()[3][1] < 20:
                if self.offscreen_blank_since is None:
                    self.offscreen_blank_since = now
                elif now-self.offscreen_blank_since > 2:
                    self.restore_desktop_pet()
                    self.status_message('Off-screen capture became transparent/blank, so the desktop pet was restored.')
            else:
                self.offscreen_blank_since = None
        if image is not None and seq != self.last_detection_sequence and self.source_view.anchor is None:
            mode = self.frame_mode.get()
            if mode.startswith('Auto:'):
                try:
                    candidate = detect_content_crop(image, mode='Pet only' if mode == 'Auto: pet only' else 'Pet + bubble')
                    crop = self.crop_tracker.update(candidate, image.size)
                    if crop != self.auto_crop:
                        self.auto_crop = crop
                        self.update_render()
                except Exception:
                    logging.exception('Automatic pet framing failed; manual crop remains available')
            self.last_detection_sequence = seq
        self.refresh_previews(image, seq)
        if self.capture and image is None and time.monotonic() - self.capture_started > 8:
            self.stop_all()
            self.status_message('No native frames arrived. Try another Codex window, or use Desktop area (fallback).')
        self.root.after(100, self.tick)

    def save_settings(self):
        try:
            saved = {'revision': self.revision.get(), 'orientation': self.orientation.get(),
                     'brightness': self.brightness.get(), 'fps': self.fps.get(), 'pixel_art': self.pixel_art.get(),
                     'framing': self.frame_mode.get(), 'zoom_source': self.zoom_source.get(),
                     'hide_border': self.hide_border.get(), 'compatible_pacing': self.compatible_pacing.get(),
                     'pet_scale': self.pet_scale.get(), 'fit_visible': self.fit_visible.get()}
            (DATA / 'settings.json').write_text(json.dumps(saved, indent=2) + '\n', encoding='utf-8')
        except Exception:
            logging.exception('Save settings')

    def diagnostics(self):
        try:
            data = {'version': VERSION, 'python': sys.version, 'platform': sys.platform,
                    'displays': list_displays(),
                    'windows': [{'hwnd': hex(w.hwnd), 'title': w.title, 'process': w.process,
                                 'size': [w.width, w.height], 'layered': w.layered} for w in list_windows()],
                    'source_mode': self.source_mode.get(), 'revision': self.revision.get(),
                    'orientation': self.orientation.get(), 'status': self.status.get(),
                    'framing': self.frame_mode.get(), 'manual_crop': self.crop, 'auto_crop': self.auto_crop,
                    'picker_pending': self.picker_pending, 'desktop_pet_offscreen': self.desktop_guard.hidden,
                    'hide_capture_border': self.hide_border.get(), 'compatible_pacing': self.compatible_pacing.get()}
            path = DATA / 'diagnostics.json'
            path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
            messagebox.showinfo('Diagnostics saved', f'Saved to:\n{path}\n\nNo screenshots, pet assets, chat contents, or API keys are saved. Window titles and process names are included; inspect the file before sharing.')
        except Exception as exc:
            messagebox.showerror('Diagnostics', str(exc))

    def close(self):
        self.closing = True
        self.cancel_area_picker()
        self.stop_all()
        if self.metrics_active:
            self.metrics.stop()
            self.metrics_active = False
        self.save_settings()
        if self.output_thread:
            self.output_thread.join(timeout=3)
        self.root.destroy()


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    if os.name != 'nt':
        messagebox.showerror('Windows required', 'This bridge runs on a Windows 10/11 PC connected to your Turmo screen.')
        root.destroy()
        return
    App(root)
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        logging.exception('Startup failed')
        try:
            messagebox.showerror('Bridge startup failed', traceback.format_exc())
        except Exception:
            pass
        raise
