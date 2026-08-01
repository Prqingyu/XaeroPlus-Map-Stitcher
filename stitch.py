#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XaeroPlus Map Stitcher — stitch XaeroPlus (XaroPlus) map-export tiles into one image.

This tool is written for and verified against the map export of XaeroPlus
(https://github.com/rfresh2/XaeroPlus), a third-party add-on for Xaero's World
Map. See README.md for the full tile-format documentation.

Expected input
--------------
A directory of PNG tiles produced by XaeroPlus' map export, named as::

    <relx>_<rely>_x<mcx>_z<mcz>.png

``relx``/``rely`` are the tile's relative grid coordinates, ``mcx``/``mcz`` are
the Minecraft block coordinates (reported in the log for reference). Every tile
is 1024 x 1024 (1 pixel = 1 block) by default. Tiles may cover a sparse,
non-rectangular region; missing cells are painted with the background colour.

Example::

    python stitch.py ./map_exports_2026-08-01 -o ./output

Outputs
-------
* ``full_stitched.png``  the complete stitched map
* ``quad_*.png``         four quadrant crops for easy viewing
* ``preview.png``        a downscaled overview

Requires Pillow (``pip install -r requirements.txt``).
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from PIL import Image

# The stitched canvas alone can be hundreds of megapixels, which exceeds
# Pillow's decompression-bomb guard. The guard exists to protect against
# hostile files; here the canvas size is fully controlled by the input.
Image.MAX_IMAGE_PIXELS = None

TILE_PATTERN = re.compile(r"^(\d+)_(\d+)_x-?\d+_z-?\d+\.png$", re.IGNORECASE)
PREVIEW_SCALE = 0.15


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


def discover_tiles(input_dir: Path) -> dict[tuple[int, int], Path]:
    """Map relative grid coordinates to tile file paths."""
    tiles: dict[tuple[int, int], Path] = {}
    for path in sorted(input_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".png":
            continue
        m = TILE_PATTERN.match(path.name)
        if not m:
            print(f"[skip] filename did not match pattern: {path.name}")
            continue
        tiles[(int(m.group(1)), int(m.group(2)))] = path
    return tiles


def stitch(
    input_dir: Path,
    output_dir: Path,
    tile_size: int,
    background: tuple[int, int, int],
    with_quadrants: bool,
    with_preview: bool,
    preview_scale: float,
    compress_level: int,
) -> None:
    t0 = time.time()

    tiles = discover_tiles(input_dir)
    if not tiles:
        raise SystemExit("No tiles found in input directory.")
    print(f"Found {len(tiles)} tile(s)")

    xs = [x for x, _ in tiles]
    ys = [y for _, y in tiles]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    width = (max_x - min_x + 1) * tile_size
    height = (max_y - min_y + 1) * tile_size
    cells = (max_x - min_x + 1) * (max_y - min_y + 1)
    print(f"Canvas: {width}x{height}px ({width*height/1e6:.0f} Mpx), grid x[{min_x}..{max_x}] y[{min_y}..{max_y}]")
    print(f"Filled cells: {len(tiles)}/{cells} ({cells - len(tiles)} hole(s) -> background)")

    canvas = Image.new("RGB", (width, height), background)
    for i, ((rx, ry), path) in enumerate(sorted(tiles.items()), 1):
        with Image.open(path) as im:
            if im.mode != "RGB":
                im = im.convert("RGB")
            canvas.paste(im, ((rx - min_x) * tile_size, (ry - min_y) * tile_size))
        if i % 50 == 0:
            print(f"  pasted {i}/{len(tiles)}  ({time.time()-t0:.0f}s)")
    print(f"Pasted {len(tiles)} tile(s) in {time.time()-t0:.0f}s")

    output_dir.mkdir(parents=True, exist_ok=True)

    full = output_dir / "full_stitched.png"
    print("Saving full image (may take a while)...")
    canvas.save(full, compress_level=compress_level)
    print(f"  saved {full.name} ({full.stat().st_size/1048576:.1f} MB)")

    if with_quadrants:
        midx, midy = width // 2, height // 2
        for name, box in (
            ("NW", (0, 0, midx, midy)),
            ("NE", (midx, 0, width, midy)),
            ("SW", (0, midy, midx, height)),
            ("SE", (midx, midy, width, height)),
        ):
            qp = output_dir / f"quad_{name}.png"
            canvas.crop(box).save(qp, compress_level=compress_level)
            print(f"  saved {qp.name} ({qp.stat().st_size/1048576:.1f} MB)")

    if with_preview:
        pv = canvas.resize(
            (int(width * preview_scale), int(height * preview_scale)),
            Image.Resampling.BILINEAR,
        )
        pvp = output_dir / "preview.png"
        pv.save(pvp, optimize=True)
        print(f"  saved {pvp.name} ({pvp.stat().st_size/1048576:.1f} MB)")

    print(f"Done in {time.time()-t0:.0f}s -> {output_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="stitch.py",
        description="Stitch XaeroPlus (XaroPlus) map-export PNG tiles into a single large image.",
        epilog="Tile names must match <relx>_<rely>_x<mcx>_z<mcz>.png; see the module docstring.",
    )
    ap.add_argument("input_dir", type=Path, help="directory containing the map-export PNG tiles")
    ap.add_argument(
        "-o", "--output", type=Path, default=None,
        help="output directory (default: '<input_dir>_stitched' next to the input)",
    )
    ap.add_argument("--tile-size", type=int, default=1024, help="tile edge length in pixels (default: 1024)")
    ap.add_argument("--background", type=parse_background, default=(0, 0, 0), help="colour for holes, 'R,G,B' (default: 0,0,0)")
    ap.add_argument("--no-quadrants", action="store_true", help="skip the four quadrant crops")
    ap.add_argument("--no-preview", action="store_true", help="skip the downscaled preview")
    ap.add_argument("--preview-scale", type=float, default=PREVIEW_SCALE, help="preview scale factor (default: 0.15)")
    ap.add_argument("--compress-level", type=int, default=6, choices=range(0, 10), help="PNG compression level 0-9 (default: 6)")
    args = ap.parse_args()

    input_dir = args.input_dir.resolve()
    if not input_dir.is_dir():
        ap.error(f"input directory does not exist: {input_dir}")
    output_dir = args.output.resolve() if args.output else Path(str(input_dir) + "_stitched")

    stitch(
        input_dir,
        output_dir,
        tile_size=args.tile_size,
        background=args.background,
        with_quadrants=not args.no_quadrants,
        with_preview=not args.no_preview,
        preview_scale=args.preview_scale,
        compress_level=args.compress_level,
    )


if __name__ == "__main__":
    main()
