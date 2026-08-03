# XaeroPlus Map Stitcher

> **XaeroPlus 分片地图整合工具** — 将 XaeroPlus(XaroPlus)导出的分片地图瓦片拼接成一张大图。
>
> A command-line **and GUI** tool that stitches **XaeroPlus (XaroPlus) map exports** into one large image.

[XaeroPlus](https://github.com/rfresh2/XaeroPlus) (often written "XaroPlus") is a third-party add-on for [Xaero's World Map](https://www.curseforge.com/minecraft/mc-mods/xaeros-world-map), created by [rfresh2](https://github.com/rfresh2) and **not affiliated with** the original mod author xaero96. When you export a world from XaeroPlus' world map, it writes the explored region as a directory of equal-sized PNG tiles.

This tool takes that directory and reconstructs the full map: it lays the tiles out on a grid read from their filenames, paints any missing cells with a background colour, and writes the result as a single PNG — plus a small preview for quick viewing.

## Requirements

- Python 3.9+
- [Pillow](https://python-pillow.org)
- [CustomTkinter](https://customtkinter.github.io/) (GUI only)

## Installation

Clone the repository and install the dependencies for what you need:

```bash
git clone https://github.com/Prqingyu/XaeroPlus-Map-Stitcher.git
cd XaeroPlus-Map-Stitcher

# CLI only (Pillow)
pip install -r requirements.txt

# GUI (adds CustomTkinter)
pip install -r requirements-gui.txt
```

## Usage

```bash
python stitch.py <input_dir> [-o <output_dir>] [options]
```

Example — point it at a XaeroPlus export folder:

```bash
python stitch.py "C:\minecraft\map exports\2026-08-01_20.38.43"
```

By default the output is written to `<input_dir>_stitched/`; use `-o` to choose another location.

### Options

| Option | Description | Default |
|---|---|---|
| `-o, --output` | Output directory | `<input_dir>_stitched` |
| `--tile-size` | Edge length of one tile, in pixels | `1024` |
| `--background` | Colour for missing cells, as `R,G,B` | `0,0,0` |
| `--no-preview` | Skip the downscaled preview | `False` |
| `--preview-scale` | Preview scale factor | `0.15` |
| `--compress-level` | PNG compression level, `0`–`9` | `6` |
| `--crop` | Crop the output to `left,top,right,bottom` (full-resolution map pixels) | `None` |

## Minimal version (`stitch_simple.py`)

Just want the core stitch — no GUI, no size/resolution controls, always at the
original resolution? Use the single-file [`stitch_simple.py`](stitch_simple.py),
which depends only on Pillow:

```bash
pip install Pillow
python stitch_simple.py <input_dir> [-o <output_dir>]
```

It reads the same XaeroPlus tile directory, builds the full-resolution canvas,
and writes `full_stitched.png` plus a small `preview.png`.

## GUI

A graphical front-end built with [CustomTkinter](https://customtkinter.github.io/).
After `pip install -r requirements-gui.txt`, run:

```bash
python stitcher_gui.py
```

Features:

- **Input / output pickers** — select the XaeroPlus export directory and an output location. By default the output folder is created *next to* the input as `<input>_stitched`; use **Browse** to place it anywhere.
- **Linked output controls** — the two modes stay in sync, and **both default to the original full resolution** (downscaling only happens when you pick a smaller width or enter a target size):
  - *By resolution*: set the output width (or drag the scale slider); the resulting file size is estimated live.
  - *By file size*: leave the target empty for the original resolution, or enter a size in MB and the tool iteratively solves for the matching resolution.
- **Compression level** 0–9. PNG is lossless — the level only trades encode time against file size (0 = fastest/largest, 9 = slowest/smallest, 6 = default balance).
- **Statistics area** — tile count, input resolution, grid & holes, output resolution, estimated size.
- **Preview box** — a fixed ~1 MB full-map overview, generated automatically after loading the input. Like a photo viewer, it **zooms with the mouse wheel and pans by dragging**.
- **Interactive cropping** — press **裁切** to open a dedicated crop window that combines a large map canvas with a data panel. The map canvas is photo-editor-style: **9 handles** — an **edge-midpoint handle moves that one edge only**, a **corner handle moves the two adjacent edges together**, the centre handle (or dragging inside) moves the whole selection, and dragging outside starts a new selection. It also zooms (wheel) and pans (middle-drag), with aspect-ratio presets (自由 / original / 1:1 / 4:3 / 16:9). The crop limits the stitched output region and the statistics update live. The same region can be set from the CLI with `--crop`.
- Background-thread stitching with a progress bar and a **Cancel** button.

> **On estimates:** pre-run size estimates are approximate. Large flat regions (e.g. ocean) compress dramatically better at full resolution, so estimates for very large outputs tend to run high. The *by file size* mode does **not** depend on the estimate — it measures the real PNG output and converges to the target within ~5%.

## Building a Windows EXE

A single-file GUI executable can be built with [PyInstaller](https://pyinstaller.org/):

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "XaeroPlus-Map-Stitcher" \
    --collect-all customtkinter app.py
```

The result is `dist/XaeroPlus-Map-Stitcher.exe` (~30 MB). The generated
`XaeroPlus-Map-Stitcher.spec` is kept in the repo so the build is reproducible.
The EXE launches the GUI; the CLI remains available via `python stitch.py`.

## XaeroPlus export format

This tool is written for and verified against **XaeroPlus' map export** (the map image export of XaeroPlus, a.k.a. XaroPlus). Its tiles follow this layout:

- One PNG per tile, **1024 × 1024 pixels, 8-bit RGB**, non-interlaced — **1 pixel = 1 block**, so each tile covers a 1024 × 1024 block region.
- The filename encodes the tile's position:

  ```
  <relx>_<rely>_x<mcx>_z<mcz>.png
  ```

  | Part | Meaning |
  |---|---|
  | `relx`, `rely` | The tile's position in the export grid. `relx` grows eastward (to the right in the final image); `rely` grows southward (downward). The final image is therefore oriented **north-up**. |
  | `mcx`, `mcz` | The Minecraft block coordinates of the tile (reported in the log for reference; **not** used for layout). Negative values are written without a `+`, e.g. `x-10240`. |

- One step in the relative grid equals 1024 blocks and 1024 pixels. Example: `34_42_x-10240_z2048.png` sits at grid position `(34, 42)`; grid position `(35, 42)` lies 1024 px to its right.

- Exports are **sparse**: only regions that have been explored or loaded are written, so the grid is usually **not a full rectangle**. Missing cells are painted with the background colour (default black).

Sample filenames from the development dataset:

```
34_42_x-10240_z2048.png
35_35_x-9216_z-5120.png
45_47_x1024_z7168.png
52_43_x8192_z3072.png
```

## Outputs

| File | Description |
|---|---|
| `full_stitched.png` | The complete stitched map |
| `preview.png` | A downscaled overview |

## Performance notes

A 19×24 grid of 1024×1024 tiles (≈478 megapixels) stitches in roughly 40 seconds on a typical machine and holds a ~1.4 GB RGB canvas in memory, so make sure you have a few gigabytes of free RAM. Because the canvas alone can be hundreds of megapixels, the tool disables Pillow's decompression-bomb guard; the canvas size is fully controlled by your own input files.

## License

[MIT](LICENSE) © 2026 XaeroPlus Map Stitcher contributors
