# XaeroPlus Map Stitcher

> **XaeroPlus 分片地图整合工具** — 将 XaeroPlus(XaroPlus)导出的分片地图瓦片拼接成一张大图。
>
> A command-line tool that stitches **XaeroPlus (XaroPlus) map exports** into one large image.

[XaeroPlus](https://github.com/rfresh2/XaeroPlus) (often written "XaroPlus") is a third-party add-on for [Xaero's World Map](https://www.curseforge.com/minecraft/mc-mods/xaeros-world-map), created by [rfresh2](https://github.com/rfresh2) and **not affiliated with** the original mod author xaero96. When you export a world from XaeroPlus' world map, it writes the explored region as a directory of equal-sized PNG tiles.

This tool takes that directory and reconstructs the full map: it lays the tiles out on a grid read from their filenames, paints any missing cells with a background colour, and writes the result as a single PNG — plus four quadrant crops and a small preview for quick viewing.

## Requirements

- Python 3.9+
- [Pillow](https://python-pillow.org)

## Installation

```bash
git clone <your-repo-url> HBNS_RS
cd HBNS_RS
pip install -r requirements.txt
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
| `--no-quadrants` | Skip the four quadrant crops | `False` |
| `--no-preview` | Skip the downscaled preview | `False` |
| `--preview-scale` | Preview scale factor | `0.15` |
| `--compress-level` | PNG compression level, `0`–`9` | `6` |

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
| `quad_NW.png` / `quad_NE.png` / `quad_SW.png` / `quad_SE.png` | Four quadrant crops for easy viewing |
| `preview.png` | A downscaled overview |

## Performance notes

A 19×24 grid of 1024×1024 tiles (≈478 megapixels) stitches in roughly 40 seconds on a typical machine and holds a ~1.4 GB RGB canvas in memory, so make sure you have a few gigabytes of free RAM. Because the canvas alone can be hundreds of megapixels, the tool disables Pillow's decompression-bomb guard; the canvas size is fully controlled by your own input files.

## License

[MIT](LICENSE) © 2026 XaeroPlus Map Stitcher contributors
