# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservative automatic framing for transparent pet captures.

``detect_content_crop`` returns a normalized ``(left, top, right, bottom)``
crop.  It deliberately uses alpha whenever there is a real transparent area;
this is the reliable signal exposed by layered-window WGC captures.  For an
opaque capture it will only crop when all four corner regions agree on a
nearly uniform RGB background.  A busy desktop or an image mostly occupied by
foreground is left alone (``None``) rather than guessed at.

The ``Pet only`` choice is a geometry heuristic, not semantic recognition.  It
prefers a connected component that is lower, taller relative to its width, and
more solid than the other visible components.  It works well for a humanoid
pet under a speech bubble, but callers should retain a manual crop control.

No OpenCV/scipy dependency is used.  Components are measured on a bounded
BOX-downsampled mask, so even a 4K capture has predictable cost.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable, Sequence

import numpy as np
from PIL import Image


Crop = tuple[float, float, float, float]

# This keeps the Python flood fill bounded while retaining a 30x100-pixel pet
# as a visibly sized component even in a 4K capture.
_MAX_MASK_SIDE = 768
_ALPHA_THRESHOLD = 20


@dataclass(frozen=True)
class _Component:
    """One connected component, with coordinates expressed in source pixels."""

    left: int
    top: int
    right: int
    bottom: int
    pixels: int
    sample_box_area: int
    source_area: float

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def fill(self) -> float:
        return self.pixels / max(1, self.sample_box_area)


def detect_content_crop(image: Image.Image, mode: str = "Pet + bubble") -> Crop | None:
    """Find a padded normalized crop around meaningful visible content.

    Args:
        image: Any Pillow image.  It is converted to RGBA without modifying
            the caller's image.
        mode: ``"Pet + bubble"`` unions all meaningful visible components.
            ``"Pet only"`` selects the most lower, vertical, solid component.

    Returns:
        A normalized ``(left, top, right, bottom)`` crop, or ``None`` when
        there is no reliable foreground.  Returning ``None`` is intentional
        for complex fully-opaque captures: an arbitrary desktop crop is worse
        than leaving the current framing alone.

    The crop includes at least 12 pixels and roughly 20 percent of the content
    extent as breathing room on each axis, preserving small idle animations.
    """
    selected_mode = _normalise_mode(mode)
    rgba = image.convert("RGBA")
    width, height = rgba.size
    if width <= 0 or height <= 0:
        return None

    pixels = np.asarray(rgba, dtype=np.uint8)
    alpha = pixels[:, :, 3]

    # Antialiasing alone should not select alpha mode.  A real layered-window
    # margin has alpha near zero over a measurable region.
    transparent_fraction = float(np.count_nonzero(alpha < _ALPHA_THRESHOLD)) / alpha.size
    if transparent_fraction >= 0.005:
        mask = alpha >= _ALPHA_THRESHOLD
    else:
        mask = _opaque_foreground_mask(pixels)
        if mask is None:
            return None

    components = _meaningful_components(mask)
    if not components:
        return None

    if selected_mode == "Pet only":
        selected = _choose_pet_component(components, width, height)
        bounds = (selected.left, selected.top, selected.right, selected.bottom)
    else:
        bounds = (
            min(component.left for component in components),
            min(component.top for component in components),
            max(component.right for component in components),
            max(component.bottom for component in components),
        )

    return _padded_normalized_crop(bounds, (width, height))


def _normalise_mode(mode: str) -> str:
    if not isinstance(mode, str):
        raise ValueError("mode must be 'Pet + bubble' or 'Pet only'")
    cleaned = " ".join(mode.strip().lower().split())
    aliases = {
        "pet + bubble": "Pet + bubble",
        "pet+bubble": "Pet + bubble",
        "pet only": "Pet only",
    }
    try:
        return aliases[cleaned]
    except KeyError as exc:
        raise ValueError("mode must be 'Pet + bubble' or 'Pet only'") from exc


def _opaque_foreground_mask(rgba: np.ndarray) -> np.ndarray | None:
    """Return an RGB foreground mask only when the border proves a flat bg.

    This deliberately rejects ambiguous fully opaque snapshots.  Corner
    agreement prevents crop guesses for a desktop, textured app, or gradient;
    a foreground coverage limit prevents a crop when the source is already
    effectively filled by content.
    """
    height, width = rgba.shape[:2]
    rgb = rgba[:, :, :3]
    patch = max(4, min(32, width // 12, height // 12))
    if patch <= 0:
        return None

    corner_patches = (
        rgb[:patch, :patch],
        rgb[:patch, width - patch:width],
        rgb[height - patch:height, :patch],
        rgb[height - patch:height, width - patch:width],
    )
    samples = np.concatenate([part.reshape(-1, 3) for part in corner_patches], axis=0)
    background = np.median(samples, axis=0).astype(np.int16)
    sample_delta = np.max(np.abs(samples.astype(np.int16) - background), axis=1)
    corner_medians = np.array([np.median(part.reshape(-1, 3), axis=0) for part in corner_patches])

    # A true flat background has both mutually close corner medians and a
    # tight distribution in each corner.  These thresholds intentionally err
    # toward returning None for compression, gradients, or desktop imagery.
    if np.max(np.abs(corner_medians.astype(np.int16) - background)) > 12:
        return None
    corner_noise = float(np.percentile(sample_delta, 95))
    if corner_noise > 16:
        return None

    rgb_delta = np.max(np.abs(rgb.astype(np.int16) - background), axis=2)
    # Permit the tiny amount of noise observed at a flat rendered border, but
    # retain custom dark/light/pastel pet colors without color-specific rules.
    foreground = rgb_delta > max(18, int(math.ceil(corner_noise)) + 8)
    if float(np.count_nonzero(foreground)) / foreground.size > 0.70:
        return None
    return foreground


def _meaningful_components(mask: np.ndarray) -> list[_Component]:
    """Suppress isolated specks and threadlike fringe from a boolean mask."""
    if mask.ndim != 2 or not np.any(mask):
        return []

    source_height, source_width = mask.shape
    small = _downsample_mask(mask)
    components = _connected_components(small, (source_width, source_height))
    image_area = source_width * source_height
    min_area = max(100.0, image_area * 0.00002)
    min_span = max(12.0, min(source_width, source_height) * 0.01)

    meaningful: list[_Component] = []
    for component in components:
        width = component.width
        height = component.height
        if component.source_area < min_area:
            continue
        if max(width, height) < min_span:
            continue
        # A component can have fill=1.0 while still being only a one-pixel
        # vertical/horizontal capture fringe, because its bounding box is
        # equally thin.  Do not mistake that geometry for a compact sprite.
        if min(width, height) < max(2.0, min_span * 0.25):
            continue
        # A detached one-pixel line is usually an alpha fringe or a capture
        # artifact.  A real outlined bubble still has a much higher fill rate.
        if min(width, height) < 2 and component.fill < 0.50:
            continue
        if component.fill < 0.012 and component.source_area < image_area * 0.01:
            continue
        meaningful.append(component)
    return meaningful


def _downsample_mask(mask: np.ndarray) -> np.ndarray:
    """BOX-downsample a mask to a bounded size, retaining visible coverage."""
    height, width = mask.shape
    scale = min(1.0, _MAX_MASK_SIDE / max(width, height))
    out_width = max(1, int(math.ceil(width * scale)))
    out_height = max(1, int(math.ceil(height * scale)))
    if (out_width, out_height) == (width, height):
        return np.ascontiguousarray(mask, dtype=bool)

    source = Image.fromarray(np.ascontiguousarray(mask.astype(np.uint8) * 255), mode="L")
    reduced = source.resize((out_width, out_height), Image.Resampling.BOX)
    # At 4K this still retains a line several source pixels wide, while most
    # single-pixel antialias/fringe is later rejected by component geometry.
    return np.asarray(reduced, dtype=np.uint8) >= _ALPHA_THRESHOLD


def _connected_components(mask: np.ndarray, source_size: tuple[int, int]) -> list[_Component]:
    """8-connect mask pixels using an iterative, bounded pure-Python floodfill."""
    sample_height, sample_width = mask.shape
    source_width, source_height = source_size
    scale_x = source_width / sample_width
    scale_y = source_height / sample_height
    work = np.ascontiguousarray(mask, dtype=bool).copy()
    result: list[_Component] = []

    for seed_y in range(sample_height):
        seeds = np.flatnonzero(work[seed_y])
        for seed_x in seeds:
            if not work[seed_y, seed_x]:
                continue
            work[seed_y, seed_x] = False
            stack = [(seed_y, int(seed_x))]
            left = right = int(seed_x)
            top = bottom = seed_y
            count = 0

            while stack:
                y, x = stack.pop()
                count += 1
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)

                for neighbour_y in range(max(0, y - 1), min(sample_height, y + 2)):
                    row = work[neighbour_y]
                    for neighbour_x in range(max(0, x - 1), min(sample_width, x + 2)):
                        if row[neighbour_x]:
                            row[neighbour_x] = False
                            stack.append((neighbour_y, neighbour_x))

            # Coordinate conversion encloses the sampled component in source
            # pixels.  It is intentionally conservative by at most one sample.
            source_left = max(0, int(math.floor(left * scale_x)))
            source_top = max(0, int(math.floor(top * scale_y)))
            source_right = min(source_width, int(math.ceil((right + 1) * scale_x)))
            source_bottom = min(source_height, int(math.ceil((bottom + 1) * scale_y)))
            sample_box_area = (right - left + 1) * (bottom - top + 1)
            result.append(
                _Component(
                    source_left,
                    source_top,
                    source_right,
                    source_bottom,
                    count,
                    sample_box_area,
                    count * scale_x * scale_y,
                )
            )
    return result


def _choose_pet_component(
    components: Sequence[_Component], width: int, height: int) -> _Component:
    """Choose a lower, vertical, filled component; geometry only, not AI."""
    image_area = width * height

    def score(component: _Component) -> float:
        lower_position = component.bottom / height
        verticality = min(3.0, component.height / max(1.0, component.width)) / 3.0
        solidity = min(1.0, component.fill)
        # Area is deliberately a weak tie-breaker: a large wide bubble should
        # not outrank a smaller upright pet just because it has more pixels.
        area = min(1.0, math.sqrt(component.source_area / image_area) * 4.0)
        height_fraction = min(1.0, component.height / height * 3.0)
        return 1.25 * lower_position + 0.85 * verticality + 0.45 * solidity + 0.25 * area + 0.30 * height_fraction

    return max(components, key=score)


def _padded_normalized_crop(bounds: tuple[int, int, int, int], size: tuple[int, int]) -> Crop:
    left, top, right, bottom = bounds
    width, height = size
    content_width = max(1, right - left)
    content_height = max(1, bottom - top)
    margin_x = max(12, int(math.ceil(content_width * 0.20)))
    margin_y = max(12, int(math.ceil(content_height * 0.20)))
    left = max(0, left - margin_x)
    top = max(0, top - margin_y)
    right = min(width, right + margin_x)
    bottom = min(height, bottom + margin_y)
    return (left / width, top / height, right / width, bottom / height)


class CropTracker:
    """Keep an automatic crop stable while a pet poses or idles.

    ``update(candidate, size)`` accepts the normalized crop returned by
    :func:`detect_content_crop`, or a source-pixel crop when any coordinate is
    greater than one.  It always returns a normalized crop.  A larger crop is
    applied immediately, ensuring a new pose is never clipped.  A smaller crop
    must persist for ``shrink_delay`` seconds or ``stable_frames`` close frames
    before it is applied.  ``None`` keeps the last trusted crop.
    """

    def __init__(
        self,
        shrink_delay: float = 2.0,
        stable_frames: int = 6,
        *,
        clock: Callable[[], float] | None = None,
        stability_tolerance: float = 0.01,
    ) -> None:
        if shrink_delay < 0:
            raise ValueError("shrink_delay must be non-negative")
        if stable_frames < 1:
            raise ValueError("stable_frames must be at least 1")
        if stability_tolerance < 0:
            raise ValueError("stability_tolerance must be non-negative")
        self.shrink_delay = float(shrink_delay)
        self.stable_frames = int(stable_frames)
        self.stability_tolerance = float(stability_tolerance)
        self._clock = clock or time.monotonic
        self._crop: Crop | None = None
        self._pending: Crop | None = None
        self._pending_started = 0.0
        self._pending_frames = 0

    @property
    def crop(self) -> Crop | None:
        """The last accepted normalized viewport, if any."""
        return self._crop

    def reset(self) -> None:
        """Forget the accepted and pending viewports."""
        self._crop = None
        self._clear_pending()

    def update(self, candidate: Sequence[float] | None, size: tuple[int, int]) -> Crop | None:
        """Accept a candidate crop and return the current stable viewport."""
        if candidate is None:
            self._clear_pending()
            return self._crop

        normalized = self._normalize_candidate(candidate, size)
        if self._crop is None:
            self._crop = normalized
            self._clear_pending()
            return self._crop

        current = self._crop
        if _needs_expansion(current, normalized):
            self._crop = (
                min(current[0], normalized[0]),
                min(current[1], normalized[1]),
                max(current[2], normalized[2]),
                max(current[3], normalized[3]),
            )
            self._clear_pending()
            return self._crop

        if _crops_close(current, normalized, self.stability_tolerance):
            self._clear_pending()
            return self._crop

        now = self._clock()
        if self._pending is not None and _crops_close(self._pending, normalized, self.stability_tolerance):
            self._pending = normalized
            self._pending_frames += 1
        else:
            self._pending = normalized
            self._pending_started = now
            self._pending_frames = 1

        if self._pending_frames >= self.stable_frames or now - self._pending_started >= self.shrink_delay:
            self._crop = normalized
            self._clear_pending()
        return self._crop

    def _normalize_candidate(self, candidate: Sequence[float], size: tuple[int, int]) -> Crop:
        if len(candidate) != 4:
            raise ValueError("candidate crop must contain four coordinates")
        source_width, source_height = size
        if source_width <= 0 or source_height <= 0:
            raise ValueError("size must contain positive width and height")
        try:
            values = tuple(float(value) for value in candidate)
        except (TypeError, ValueError) as exc:
            raise ValueError("candidate crop must contain numeric coordinates") from exc
        if not all(math.isfinite(value) for value in values):
            raise ValueError("candidate crop coordinates must be finite")

        # A normalized crop is the normal path.  Source-pixel crops make the
        # tracker convenient for a future manual crop UI without losing its
        # resolution-independent stored viewport.
        if all(-1e-9 <= value <= 1.0 + 1e-9 for value in values):
            left, top, right, bottom = values
        else:
            left, top, right, bottom = (
                values[0] / source_width,
                values[1] / source_height,
                values[2] / source_width,
                values[3] / source_height,
            )
        left, top = max(0.0, left), max(0.0, top)
        right, bottom = min(1.0, right), min(1.0, bottom)
        if right <= left or bottom <= top:
            raise ValueError("candidate crop must have positive extent")
        return (left, top, right, bottom)

    def _clear_pending(self) -> None:
        self._pending = None
        self._pending_started = 0.0
        self._pending_frames = 0


def _needs_expansion(current: Crop, candidate: Crop) -> bool:
    return (
        candidate[0] < current[0]
        or candidate[1] < current[1]
        or candidate[2] > current[2]
        or candidate[3] > current[3]
    )


def _crops_close(first: Crop, second: Crop, tolerance: float) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(first, second))
