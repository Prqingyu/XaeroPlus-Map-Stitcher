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
* ``preview.png``        a downscaled overview

Requires Pillow (``pip install -r requirements.txt``).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from PIL import Image

import stitch_core as core

Image.MAX_IMAGE_PIXELS = None  # see stitch_core for why

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


def parse_crop(text: str) -> tuple[int, int, int, int]:
    """Parse a ``left,top,right,bottom`` crop box (full-resolution pixels)."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise SystemExit(f"Invalid --crop {text!r}: expected 'left,top,right,bottom'")
    try:
        return tuple(int(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        raise SystemExit(f"Invalid --crop {text!r}: components must be integers") from None


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
    ap.add_argument("--no-preview", action="store_true", help="skip the downscaled preview")
    ap.add_argument("--preview-scale", type=float, default=PREVIEW_SCALE, help="preview scale factor (default: 0.15)")
    ap.add_argument("--compress-level", type=int, default=6, choices=range(0, 10), help="PNG compression level 0-9 (default: 6)")
    ap.add_argument("--crop", type=parse_crop, default=None, help="crop the output to 'left,top,right,bottom' (full-resolution map pixels)")
    args = ap.parse_args()

    input_dir = args.input_dir.resolve()
    if not input_dir.is_dir():
        ap.error(f"input directory does not exist: {input_dir}")
    output_dir = args.output.resolve() if args.output else Path(str(input_dir) + "_stitched")

    t0 = time.time()
    ts = core.discover_tiles(input_dir, args.tile_size)
    print(f"Found {ts.count} tile(s)")
    rw, rh = core.region_size(ts, args.crop)
    print(
        f"Region: {rw}x{rh}px ({rw*rh/1e6:.0f} Mpx), "
        f"grid x[{ts.min_x}..{ts.max_x}] y[{ts.min_y}..{ts.max_y}]"
    )
    print(f"Filled cells: {ts.count}/{ts.cells} ({ts.holes} hole(s) -> background)")

    canvas = core.build_canvas(ts, input_dir, 1.0, args.background,
                               progress=core.Progress(_print_progress), crop_box=args.crop)
    print(f"Pasted {ts.count} tile(s) in {time.time()-t0:.0f}s")

    preview = None
    if not args.no_preview:
        pv_scale = max(0.01, min(1.0, args.preview_scale))
        preview = core.build_canvas(ts, input_dir, pv_scale, args.background, crop_box=args.crop)

    output_dir.mkdir(parents=True, exist_ok=True)
    written = core.save_outputs(
        canvas, output_dir, args.compress_level,
        preview_image=preview,
    )
    for label, path in written.items():
        print(f"  saved {path.name} ({path.stat().st_size/1048576:.1f} MB)")

    print(f"Done in {time.time()-t0:.0f}s -> {output_dir}")


def _print_progress(current: int, total: int, stage: str) -> None:
    if stage == "build" and current % 50 == 0:
        print(f"  pasted {current}/{total}")


if __name__ == "__main__":
    main()
