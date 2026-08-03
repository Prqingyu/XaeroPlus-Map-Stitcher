#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal single-file XaeroPlus map stitcher.

This is the stripped-down core of the tool: it stitches a directory of
XaeroPlus map-export tiles into one full-resolution PNG. No GUI, no
resolution/file-size controls, no cropping — just the stitch. It depends only
on Pillow.

Usage:
    python stitch_simple.py <input_dir> [-o <output_dir>]

Outputs ``full_stitched.png`` (the whole map) and ``preview.png`` (a small
overview) into ``<input_dir>_stitched`` by default.
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from PIL import Image

# The stitched canvas alone can be hundreds of megapixels; disable Pillow's
# decompression-bomb guard (the size is controlled by your own files).
Image.MAX_IMAGE_PIXELS = None

TILE_PATTERN = re.compile(r"^(\d+)_(\d+)_x-?\d+_z-?\d+\.png$", re.IGNORECASE)


def parse_background(text: str) -> tuple[int, int, int]:
    """Parse an ``R,G,B`` string into a colour tuple."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3:
        raise SystemExit(f"Invalid --background {text!r}: expected 'R,G,B'")
    try:
        rgb = tuple(int(p) for p in parts)
    except ValueError:
        raise SystemExit(f"Invalid --background {text!r}: components must be integers") from None
    if any(c < 0 or c > 255 for c in rgb):
        raise SystemExit(f"Invalid --background {text!r}: components must be 0-255")
    return rgb


def main() -> None:
    ap = argparse.ArgumentParser(description="Minimal XaeroPlus map-export tile stitcher.")
    ap.add_argument("input_dir", type=Path, help="directory containing the XaeroPlus export PNG tiles")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="output directory (default: '<input_dir>_stitched')")
    ap.add_argument("--background", type=parse_background, default=(0, 0, 0),
                    help="colour for missing cells, 'R,G,B' (default: 0,0,0)")
    args = ap.parse_args()

    input_dir = args.input_dir.resolve()
    if not input_dir.is_dir():
        ap.error(f"input directory does not exist: {input_dir}")
    output_dir = args.output.resolve() if args.output else Path(str(input_dir) + "_stitched")

    # ---- parse tiles from filenames
    tiles: dict[tuple[int, int], Path] = {}
    for path in sorted(input_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".png":
            continue
        m = TILE_PATTERN.match(path.name)
        if not m:
            continue
        tiles[(int(m.group(1)), int(m.group(2)))] = path
    if not tiles:
        raise SystemExit(f"no XaeroPlus tiles found in {input_dir}")

    # all tiles are the same size; take it from the first one
    with Image.open(next(iter(tiles.values()))) as probe:
        tile_size = probe.size[0]

    xs = [x for x, _ in tiles]
    ys = [y for _, y in tiles]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = (max_x - min_x + 1) * tile_size
    height = (max_y - min_y + 1) * tile_size
    print(f"Found {len(tiles)} tile(s) -> {width}x{height}px canvas")

    # ---- build the full-resolution canvas
    t0 = time.time()
    canvas = Image.new("RGB", (width, height), args.background)
    for i, ((rx, ry), path) in enumerate(sorted(tiles.items()), 1):
        box = ((rx - min_x) * tile_size, (ry - min_y) * tile_size)
        with Image.open(path) as im:
            if im.mode != "RGB":
                im = im.convert("RGB")
            canvas.paste(im, box)
        if i % 50 == 0:
            print(f"  pasted {i}/{len(tiles)}")
    print(f"Stitched in {time.time()-t0:.0f}s")

    # ---- save the full image plus a small preview
    output_dir.mkdir(parents=True, exist_ok=True)
    full = output_dir / "full_stitched.png"
    canvas.save(full, compress_level=6)
    print(f"  saved {full.name} ({full.stat().st_size/1048576:.1f} MB)")

    preview = canvas.resize((max(1, width // 8), max(1, height // 8)), Image.Resampling.BILINEAR)
    pvp = output_dir / "preview.png"
    preview.save(pvp, optimize=True)
    print(f"  saved {pvp.name} ({pvp.stat().st_size/1048576:.1f} MB)")
    print(f"Done -> {output_dir}")


if __name__ == "__main__":
    main()
