#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core stitching logic for XaeroPlus Map Stitcher (CLI + GUI).

This module is UI-agnostic: it knows nothing about argparse or tkinter. Both
``stitch.py`` (CLI) and ``stitcher_gui.py`` (GUI) import from here.

The key idea behind resolution/file-size coupling
-------------------------------------------------
PNG output size scales almost linearly with the number of pixels for a given
image, but large images compress slightly better than small ones, so we fit a
power law ``bytes_per_pixel(n) = a * n**k`` over two calibration scales (8% and
25%). That gives:

* resolution -> size: ``estimate_bytes(target_pixels)``
* size -> resolution: an iterative solver that builds at a predicted scale,
  measures the real PNG size, then adjusts the scale by ``sqrt(target/actual)``
  (size is proportional to pixel count, so the correction converges in a few
  passes).

Instead of building the full-resolution canvas and downscaling it, canvases are
built directly at the target scale by resizing each tile before pasting. Memory
therefore scales with the *output* resolution, not the input one.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

# The stitched canvas alone can be hundreds of megapixels, which exceeds
# Pillow's decompression-bomb guard. The guard exists to protect against
# hostile files; here the canvas size is fully controlled by the input.
Image.MAX_IMAGE_PIXELS = None

TILE_PATTERN = re.compile(r"^(\d+)_(\d+)_x-?\d+_z-?\d+\.png$", re.IGNORECASE)

# Two calibration scales for the bytes-per-pixel power-law fit. The 8% sample
# is cheap; the 25% sample makes the fit robust for larger outputs.
CALIBRATION_SCALES = (0.08, 0.25)


class StitchCancelled(Exception):
    """Raised when the user cancels an in-progress stitch."""


@dataclass
class TileSet:
    """A parsed map-export directory."""

    tiles: dict[tuple[int, int], Path]
    tile_size: int
    min_x: int
    max_x: int
    min_y: int
    max_y: int

    @property
    def count(self) -> int:
        return len(self.tiles)

    @property
    def cols(self) -> int:
        return self.max_x - self.min_x + 1

    @property
    def rows(self) -> int:
        return self.max_y - self.min_y + 1

    @property
    def cells(self) -> int:
        return self.cols * self.rows

    @property
    def holes(self) -> int:
        return self.cells - self.count

    @property
    def full_width(self) -> int:
        return self.cols * self.tile_size

    @property
    def full_height(self) -> int:
        return self.rows * self.tile_size

    @property
    def full_pixels(self) -> int:
        return self.full_width * self.full_height


class Progress:
    """Minimal progress callback interface.

    ``callback(current, total, stage)`` is called with stage names such as
    ``"build"``, ``"encode"``, ``"save"``. The GUI translates these into
    progress-bar updates; the CLI ignores them.
    """

    def __init__(self, callback=None):
        self._cb = callback

    def __call__(self, current: int, total: int, stage: str) -> None:
        if self._cb:
            self._cb(current, total, stage)


def discover_tiles(input_dir: Path, tile_size: int = 1024) -> TileSet:
    """Parse the input directory into a :class:`TileSet`.

    Raises ``ValueError`` if no tiles match the XaeroPlus naming pattern.
    """
    tiles: dict[tuple[int, int], Path] = {}
    for path in sorted(input_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".png":
            continue
        m = TILE_PATTERN.match(path.name)
        if not m:
            continue
        tiles[(int(m.group(1)), int(m.group(2)))] = path
    if not tiles:
        raise ValueError(
            f"no XaeroPlus tiles found in {input_dir} "
            f"(expected names like '<relx>_<rely>_x<mcx>_z<mcz>.png')"
        )
    xs = [x for x, _ in tiles]
    ys = [y for _, y in tiles]
    return TileSet(
        tiles=tiles,
        tile_size=tile_size,
        min_x=min(xs),
        max_x=max(xs),
        min_y=min(ys),
        max_y=max(ys),
    )


def region_size(ts: TileSet, crop_box=None) -> tuple[int, int]:
    """Return the full-resolution size ``(w, h)`` of the region to build.

    ``crop_box`` is ``(left, top, right, bottom)`` in full-resolution map
    pixels; ``None`` means the whole map.
    """
    if crop_box is None:
        return ts.full_width, ts.full_height
    left, top, right, bottom = crop_box
    return max(0, right - left), max(0, bottom - top)


def region_pixels(ts: TileSet, crop_box=None) -> int:
    """Return the full-resolution pixel count of the region to build."""
    w, h = region_size(ts, crop_box)
    return w * h


def build_canvas(
    ts: TileSet,
    input_dir: Path,
    scale: float,
    background: tuple[int, int, int] = (0, 0, 0),
    progress: Progress | None = None,
    cancel_flag=None,
    crop_box=None,
) -> Image.Image:
    """Build a canvas directly at ``scale`` by resizing each tile before pasting.

    ``crop_box`` is ``(left, top, right, bottom)`` in full-resolution map
    pixels and limits the build to that region (tiles fully outside are
    skipped); ``None`` builds the whole map. At ``scale == 1.0`` no resizing
    happens and this is equivalent to the classic full-resolution stitch.
    ``cancel_flag`` is any object with an ``is_set()`` method (e.g.
    ``threading.Event``); raises :class:`StitchCancelled` when set.
    """
    if crop_box is None:
        left, top, right, bottom = 0, 0, ts.full_width, ts.full_height
    else:
        left, top, right, bottom = crop_box
        left = max(0, min(int(left), ts.full_width))
        top = max(0, min(int(top), ts.full_height))
        right = max(0, min(int(right), ts.full_width))
        bottom = max(0, min(int(bottom), ts.full_height))
        if right <= left or bottom <= top:
            raise ValueError(f"invalid crop box: {crop_box}")

    width = max(1, round((right - left) * scale))
    height = max(1, round((bottom - top) * scale))
    canvas = Image.new("RGB", (width, height), background)

    resample = Image.Resampling.LANCZOS if scale < 0.95 else Image.Resampling.BILINEAR
    do_resize = scale != 1.0

    # only tiles that intersect the crop region
    tiles_in = []
    for (rx, ry), path in sorted(ts.tiles.items()):
        tile_l = (rx - ts.min_x) * ts.tile_size
        tile_t = (ry - ts.min_y) * ts.tile_size
        if tile_l < right and tile_t < bottom and tile_l + ts.tile_size > left and tile_t + ts.tile_size > top:
            tiles_in.append((rx, ry, path))

    for i, (rx, ry, path) in enumerate(tiles_in, 1):
        if cancel_flag is not None and cancel_flag.is_set():
            raise StitchCancelled()
        tile_l = (rx - ts.min_x) * ts.tile_size
        tile_t = (ry - ts.min_y) * ts.tile_size
        # intersection of this tile with the crop region (full-res pixels)
        ix0, iy0 = max(tile_l, left), max(tile_t, top)
        ix1, iy1 = min(tile_l + ts.tile_size, right), min(tile_t + ts.tile_size, bottom)
        with Image.open(path) as im:
            if im.mode != "RGB":
                im = im.convert("RGB")
            src = (ix0 - tile_l, iy0 - tile_t, ix1 - tile_l, iy1 - tile_t)
            im = im.crop(src)
            if do_resize:
                dw = max(1, round((ix1 - ix0) * scale))
                dh = max(1, round((iy1 - iy0) * scale))
                im = im.resize((dw, dh), resample)
            canvas.paste(im, (round((ix0 - left) * scale), round((iy0 - top) * scale)))
        if progress is not None:
            progress(i, len(tiles_in), "build")

    return canvas


def encode_png_size(im: Image.Image, compress_level: int) -> int:
    """Encode an image to PNG in memory and return the byte size."""
    buf = io.BytesIO()
    im.save(buf, format="PNG", compress_level=compress_level)
    return buf.tell()


def level_factor(level_a: int, level_b: int) -> float:
    """Rough size ratio between two PNG compression levels (1 vs 6 vs 9).

    Measured on the development dataset: level 1 is ~1.42x the size of
    level 6, level 9 is ~0.97x. Interpolate between those anchors.
    """
    anchors = {1: 1.42, 6: 1.0, 9: 0.97}
    if level_a not in anchors or level_b not in anchors:
        return 1.0
    return anchors.get(level_a, 1.0) / anchors.get(level_b, 1.0)


@dataclass
class Calibration:
    """PNG-size calibration samples used for size estimation.

    ``points`` holds ``(scale, png_bytes, image)`` for several downscaled
    builds of the map.
    """

    ts: TileSet
    level: int
    points: list[tuple[float, int, Image.Image]]

    def estimate_bytes(self, target_pixels: int, level: int) -> int:
        """Estimate the PNG size of an output with ``target_pixels``.

        Fits ``bpp(n) = a * n**k`` through the calibration points, then applies
        the compression-level ratio. Estimates are approximate for very large
        outputs (the development dataset's full-res map lands within ~±30%).
        """
        if not self.points:
            return 0
        n = [self.ts.full_pixels * s * s for s, _, _ in self.points]
        bpp = [b / max(1.0, n[i]) for i, (_, b, _) in enumerate(self.points)]
        target = max(1, target_pixels)
        if len(self.points) >= 2 and n[1] > n[0] and bpp[0] > 0 and bpp[1] > 0:
            k = math.log(bpp[1] / bpp[0]) / math.log(n[1] / n[0])
            bpp_t = bpp[0] * (target / n[0]) ** k
        else:
            bpp_t = bpp[-1]
        return round(max(1, bpp_t * target) * level_factor(level, self.level))


def build_calibration(
    ts: TileSet,
    input_dir: Path,
    compress_level: int,
    background: tuple[int, int, int] = (0, 0, 0),
    scales: tuple[float, ...] = CALIBRATION_SCALES,
) -> Calibration:
    """Build calibration samples at the given downscale factors."""
    points = []
    for s in scales:
        im = build_canvas(ts, input_dir, s, background)
        points.append((s, encode_png_size(im, compress_level), im))
    return Calibration(ts, compress_level, points)


def solve_scale_for_size(
    ts: TileSet,
    input_dir: Path,
    target_bytes: int,
    compress_level: int,
    background: tuple[int, int, int] = (0, 0, 0),
    progress: Progress | None = None,
    cancel_flag=None,
    max_iterations: int = 4,
    calibration: Calibration | None = None,
    crop_box=None,
) -> tuple[float, Image.Image, int]:
    """Find the scale whose PNG output is close to ``target_bytes``.

    Returns ``(scale, canvas, actual_bytes)``. Converges via the
    ``scale *= sqrt(target / actual)`` rule because PNG size is ~proportional
    to pixel count. The initial scale is seeded from ``calibration`` (built on
    demand if not given), so the loop usually converges in one or two passes.
    ``crop_box`` (see :func:`build_canvas`) limits the build to a region.
    """
    cal = calibration if calibration is not None and calibration.level == compress_level else build_calibration(ts, input_dir, compress_level, background)
    est_full = cal.estimate_bytes(region_pixels(ts, crop_box), compress_level)
    scale = min(1.0, math.sqrt(target_bytes / est_full)) if est_full > 0 else 0.5
    scale = max(0.01, scale)

    canvas: Image.Image | None = None
    actual = 0
    for _ in range(max_iterations):
        if cancel_flag is not None and cancel_flag.is_set():
            raise StitchCancelled()
        canvas = build_canvas(ts, input_dir, scale, background, progress, cancel_flag, crop_box=crop_box)
        actual = encode_png_size(canvas, compress_level)
        if progress is not None:
            progress(actual, target_bytes, "encode")
        if abs(actual - target_bytes) / target_bytes < 0.05:
            break
        scale = min(1.0, max(0.01, scale * math.sqrt(target_bytes / actual)))

    return scale, canvas, actual


def save_outputs(
    canvas: Image.Image,
    output_dir: Path,
    compress_level: int,
    preview_image: Image.Image | None = None,
    progress: Progress | None = None,
    cancel_flag=None,
) -> dict[str, Path]:
    """Save the canvas and an optional preview into ``output_dir``.

    Returns a mapping of label -> saved path.
    """
    if cancel_flag is not None and cancel_flag.is_set():
        raise StitchCancelled()
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    full = output_dir / "full_stitched.png"
    canvas.save(full, compress_level=compress_level)
    written["full"] = full
    if progress is not None:
        progress(1, 1, "save")

    if preview_image is not None:
        pvp = output_dir / "preview.png"
        preview_image.save(pvp, optimize=True)
        written["preview"] = pvp

    return written
