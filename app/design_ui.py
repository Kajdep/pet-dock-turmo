# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, scrollable screen-designer controls; no network or sensor work."""
import tkinter as tk
from tkinter import ttk, filedialog, colorchooser, messagebox
from screen_style import DEFAULT_STYLE, PRESETS, normalize_style, import_image, import_font, asset_path


class ScreenDesigner:
    def __init__(self, app, parent, data_dir):
        self.app, self.data_dir = app, data_dir
        self.pending_save = None
        style = normalize_style(app.saved.get('design', {}))
        self.variables = {}
        for key, value in style.items():
            cls = tk.DoubleVar if key in ('pet_x', 'pet_y') else tk.IntVar if isinstance(value, int) else tk.StringVar
            self.variables[key] = cls(value=value)
        self.background_label = tk.StringVar(value='Custom image loaded' if asset_path(data_dir, style['background_image']) else 'Solid color (no image)')
        self.font_label = tk.StringVar(value='Custom font loaded' if asset_path(data_dir, style['font_path']) else 'Using selected system font')
        self.color_buttons = {}
        self.build(parent)

    def settings(self):
        values = {}
        for key, var in self.variables.items():
            try:
                values[key] = var.get()
            except tk.TclError:
                pass
        return normalize_style(values)

    def changed(self, *_):
        self.app.update_render()
        self.app.refresh_previews(force=True)
        if self.pending_save:
            self.app.root.after_cancel(self.pending_save)
        self.pending_save = self.app.root.after(500, self.save)

    def save(self):
        self.pending_save = None
        self.app.save_settings()

    def apply(self, style):
        style = normalize_style(style)
        for key, value in style.items():
            self.variables[key].set(value)
        self.background_label.set('Custom image loaded' if asset_path(self.data_dir, style['background_image']) else 'Solid color (no image)')
        self.font_label.set('Custom font loaded' if asset_path(self.data_dir, style['font_path']) else 'Using selected system font')
        self.update_colors()
        self.changed()

    def update_colors(self):
        for key, button in self.color_buttons.items():
            button.configure(text=self.variables[key].get(), bg=self.variables[key].get())

    def preset(self, name):
        current = self.settings()
        # Palette changes keep the user's own picture, font, and pet placement.
        for key in DEFAULT_STYLE:
            if key.endswith('_color'):
                current[key] = PRESETS[name].get(key, DEFAULT_STYLE[key])
        self.apply(current)

    def color(self, key):
        selected = colorchooser.askcolor(self.variables[key].get(), parent=self.app.root, title='Choose '+key.replace('_', ' '))[1]
        if selected:
            self.variables[key].set(selected)
            self.update_colors()
            self.changed()

    def choose_background(self):
        path = filedialog.askopenfilename(parent=self.app.root, title='Choose a background image',
                                         filetypes=[('Images', '*.png *.jpg *.jpeg *.webp *.bmp'), ('All files', '*.*')])
        if not path:
            return
        try:
            relative = import_image(path, self.data_dir)
            self.variables['background_image'].set(relative)
            self.background_label.set('Custom image loaded (local copy)')
            self.changed()
        except Exception as exc:
            messagebox.showerror('Background image', str(exc), parent=self.app.root)

    def remove_background(self):
        self.variables['background_image'].set('')
        self.background_label.set('Solid color (no image)')
        self.changed()

    def choose_font(self):
        path = filedialog.askopenfilename(parent=self.app.root, title='Choose a font you are licensed to use',
                                         filetypes=[('TrueType / OpenType', '*.ttf *.otf')])
        if not path:
            return
        try:
            self.variables['font_path'].set(import_font(path, self.data_dir))
            self.variables['font_family'].set('Custom')
            self.font_label.set('Custom font loaded (local copy)')
            self.changed()
        except Exception as exc:
            messagebox.showerror('Font', str(exc), parent=self.app.root)

    def clear_font(self):
        self.variables['font_path'].set('')
        self.variables['font_family'].set('Segoe UI')
        self.font_label.set('Using selected system font')
        self.changed()

    def build(self, parent):
        def section(text):
            ttk.Label(parent, text=text, style='Section.TLabel').pack(anchor='w', pady=(12, 8))
        def hint(text):
            ttk.Label(parent, text=text, style='Hint.TLabel', wraplength=280).pack(anchor='w', pady=(3, 8))
        def choose(label, key, choices):
            row = ttk.Frame(parent)
            row.pack(fill='x', pady=4)
            ttk.Label(row, text=label).pack(side='left')
            box = ttk.Combobox(row, textvariable=self.variables[key], values=choices, state='readonly', width=14)
            box.pack(side='right')
            box.bind('<<ComboboxSelected>>', self.changed)
            return box
        def slider(label, var, low, high, percent=False):
            row = ttk.Frame(parent)
            row.pack(fill='x', pady=(6, 0))
            ttk.Label(row, text=label).pack(side='left')
            value = tk.StringVar()
            def update(*_):
                try:
                    v = float(var.get())
                    value.set(f'{v*100:.0f}%' if percent else f'{v:.0f}')
                except tk.TclError:
                    pass
            var.trace_add('write', update)
            update()
            ttk.Label(row, textvariable=value, style='Hint.TLabel').pack(side='right')
            ttk.Scale(parent, from_=low, to=high, variable=var, command=self.changed).pack(fill='x', pady=(3, 6))

        section('Screen design')
        hint('Changes preview live and are saved locally. No image or font is uploaded.')
        palette = tk.StringVar(value='Midnight')
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=4)
        box = ttk.Combobox(row, textvariable=palette, values=list(PRESETS), state='readonly', width=15)
        box.pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='Apply palette', command=lambda: self.preset(palette.get())).pack(side='right', padx=(6, 0))

        section('Background')
        ttk.Button(parent, text='Choose background image…', command=self.choose_background).pack(fill='x')
        ttk.Label(parent, textvariable=self.background_label, style='Hint.TLabel', wraplength=280).pack(anchor='w', pady=5)
        ttk.Button(parent, text='Remove background image', command=self.remove_background).pack(fill='x', pady=(0, 5))
        choose('Image fit', 'background_fit', ['Contain', 'Cover'])
        hint('Contain keeps the whole picture; Cover fills the screen and crops its edges. PNG transparency is supported.')
        slider('Image dimming (%)', self.variables['background_dim'], 0, 100)
        hint('A background shows through native capture transparency. Desktop-area capture or Ignore alpha stays opaque.')

        section('Pet size & placement')
        slider('Pet size', self.app.pet_scale, .1, 1.0, True)
        slider('Horizontal position', self.variables['pet_x'], 0, 1, True)
        slider('Vertical position', self.variables['pet_y'], 0, 1, True)
        ttk.Button(parent, text='Center pet / reset size to 90%', command=self.center_pet).pack(fill='x', pady=5)
        hint('Placement is within the free area above/below metrics. Size does not change your source crop.')

        section('Computer stats')
        choose('Indicators', 'metrics_style', ['Cards', 'Bars', 'Rings'])
        choose('Stats position', 'metrics_position', ['Bottom', 'Top'])
        slider('Panel opacity (%)', self.variables['panel_opacity'], 0, 100)
        slider('Temperature scale max °C', self.variables['temperature_max'], 40, 150)
        hint('Percentages and VRAM use their measured range. Temperature gauges use 0 to this maximum. Network and clock remain text; N/A never shows fake usage.')

        section('Colors')
        for key, label in [('background_color', 'Background'), ('card_color', 'Stat cards'), ('accent_color', 'Bars / rings'),
                           ('text_color', 'Values'), ('label_color', 'Labels'), ('border_color', 'Borders / tracks')]:
            row = ttk.Frame(parent)
            row.pack(fill='x', pady=3)
            ttk.Label(row, text=label).pack(side='left')
            button = tk.Button(row, width=11, command=lambda key=key: self.color(key), fg='#ffffff', relief='flat', borderwidth=1)
            button.pack(side='right')
            self.color_buttons[key] = button
        self.update_colors()

        section('Stats font')
        choose('System font', 'font_family', ['Segoe UI', 'Arial', 'Consolas', 'Tahoma', 'Verdana', 'Custom'])
        slider('Preferred size (pixels)', self.variables['font_size'], 8, 24)
        ttk.Button(parent, text='Choose TTF / OTF font…', command=self.choose_font).pack(fill='x', pady=(4, 5))
        ttk.Label(parent, textvariable=self.font_label, style='Hint.TLabel', wraplength=280).pack(anchor='w')
        ttk.Button(parent, text='Clear custom font', command=self.clear_font).pack(fill='x', pady=6)
        hint('Text shrinks to fit small cards. Missing fonts fall back safely. Fonts change the stats, not the captured pet or its bubble.')
        ttk.Separator(parent).pack(fill='x', pady=12)
        ttk.Button(parent, text='Reset screen design', command=lambda: self.apply(DEFAULT_STYLE)).pack(fill='x')
        hint('Reset does not change capture, USB settings, or delete your local assets.')

    def center_pet(self):
        self.variables['pet_x'].set(.5)
        self.variables['pet_y'].set(.5)
        self.app.pet_scale.set(.9)
        self.changed()
