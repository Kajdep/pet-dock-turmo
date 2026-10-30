# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated, local-only appearance settings and cached background assets."""
from __future__ import annotations
from functools import lru_cache
import hashlib
import math
from pathlib import Path
import re

from PIL import Image, ImageOps

DEFAULT_STYLE = {
    'background_color': '#10141b', 'background_image': '', 'background_fit': 'Contain',
    'background_dim': 0, 'card_color': '#1b2531', 'border_color': '#2b3948',
    'text_color': '#eef5ff', 'label_color': '#9fb0c2', 'accent_color': '#70c6ff',
    'muted_color': '#7f8790', 'font_family': 'Segoe UI', 'font_path': '', 'font_size': 14,
    'metrics_style': 'Cards', 'panel_opacity': 100, 'metrics_position': 'Bottom',
    'pet_x': .5, 'pet_y': .5, 'temperature_max': 100,
}
PRESETS = {
    'Midnight': {},
    'Forest': {'background_color': '#0b1915', 'card_color': '#15332a', 'border_color': '#295141',
               'text_color': '#effff6', 'label_color': '#99c4ad', 'accent_color': '#65df9d'},
    'Amber': {'background_color': '#1b130c', 'card_color': '#33251a', 'border_color': '#61472a',
              'text_color': '#fff1dc', 'label_color': '#c7ac85', 'accent_color': '#ffb64c'},
    'Violet': {'background_color': '#181124', 'card_color': '#2c2040', 'border_color': '#53396e',
               'text_color': '#f7edff', 'label_color': '#b9a1d1', 'accent_color': '#ce94ff'},
}
COLOR_KEYS = tuple(key for key in DEFAULT_STYLE if key.endswith('_color'))


def _number(value, default, low, high):
    try:
        value = float(value)
        return max(low, min(high, value)) if math.isfinite(value) else default
    except (ValueError, TypeError):
        return default


def normalize_style(style=None):
    """Accept only supported keys, finite ranges, safe relative asset names."""
    source = style if isinstance(style, dict) else {}
    result = dict(DEFAULT_STYLE)
    for key in COLOR_KEYS:
        value = source.get(key, result[key])
        if isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value):
            result[key] = value.lower()
    choices = {'background_fit': ('Contain', 'Cover'), 'metrics_style': ('Cards', 'Bars', 'Rings'),
               'metrics_position': ('Top', 'Bottom'),
               'font_family': ('Segoe UI', 'Arial', 'Consolas', 'Tahoma', 'Verdana', 'Custom')}
    for key, options in choices.items():
        if source.get(key) in options:
            result[key] = source[key]
    for key, low, high in [('font_size', 8, 24), ('background_dim', 0, 100),
                            ('panel_opacity', 0, 100), ('temperature_max', 40, 150)]:
        result[key] = round(_number(source.get(key), result[key], low, high))
    for key in ('pet_x', 'pet_y'):
        result[key] = _number(source.get(key), result[key], 0, 1)
    for key in ('background_image', 'font_path'):
        value = source.get(key, '')
        # Theme settings never reference an arbitrary filesystem path or URL.
        if isinstance(value, str) and re.fullmatch(r'assets/[a-f0-9]{64}\.(png|ttf|otf)', value):
            suffix = Path(value).suffix
            if (key == 'background_image' and suffix == '.png') or (key == 'font_path' and suffix in ('.ttf', '.otf')):
                result[key] = value
    return result


def asset_path(data_dir, relative):
    if not relative:
        return None
    base = Path(data_dir).resolve()
    path = (base / relative).resolve()
    try:
        path.relative_to(base / 'assets')
    except ValueError:
        return None
    return path if path.is_file() else None


def import_image(source, data_dir):
    """Copy the first image frame as a metadata-free PNG; never change original."""
    source = Path(source)
    if source.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Choose an image smaller than 32 MiB.')
    with Image.open(source) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError('Choose an image under 40 megapixels.')
        image = ImageOps.exif_transpose(image).convert('RGBA')
        image.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
        # Fresh pixels drop source names, EXIF, comments, and PNG metadata.
        clean = Image.new('RGBA', image.size)
        clean.paste(image)
        digest = hashlib.sha256(str(clean.size).encode() + clean.tobytes()).hexdigest()
        relative = 'assets/' + digest + '.png'
        output = Path(data_dir) / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        clean.save(output, format='PNG')
    return relative


def import_font(source, data_dir):
    from PIL import ImageFont
    source = Path(source)
    if source.suffix.lower() not in ('.ttf', '.otf') or source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Choose a TTF/OTF font under 16 MiB.')
    ImageFont.truetype(str(source), size=14)  # Validate before copying.
    content = source.read_bytes()
    relative = f'assets/{hashlib.sha256(content).hexdigest()}{source.suffix.lower()}'
    output = Path(data_dir) / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    return relative


@lru_cache(maxsize=8)
def _background_cached(filename, modified, size, fit, color, dim):
    background = Image.new('RGBA', size, color)
    try:
        with Image.open(filename) as source:
            source = source.convert('RGBA')
            if fit == 'Cover':
                placed = ImageOps.fit(source, size, Image.Resampling.LANCZOS)
            else:
                placed = ImageOps.contain(source, size, Image.Resampling.LANCZOS)
            background.alpha_composite(placed, ((size[0]-placed.width)//2, (size[1]-placed.height)//2))
    except (OSError, ValueError, Image.DecompressionBombError):
        pass
    if dim:
        background = Image.blend(background, Image.new('RGBA', size, '#000000'), dim/100)
    return background.convert('RGB')


def render_background(size, style, data_dir):
    path = asset_path(data_dir, style['background_image'])
    if path is None:
        return Image.new('RGB', size, style['background_color'])
    try:
        stat = path.stat()
    except OSError:
        return Image.new('RGB', size, style['background_color'])
    return _background_cached(str(path), stat.st_mtime_ns, tuple(size), style['background_fit'],
                              style['background_color'], style['background_dim']).copy()
