# HBNS Map Stitcher

A small command-line tool that stitches Minecraft **map-export tiles** into one large image.

If you export a Minecraft map as a directory of equal-sized PNG tiles (one per map region), this tool reconstructs the full map: it lays the tiles out on a grid from their filenames, paints any missing cells with a background colour, and writes the result as a single PNG — plus four quadrant crops and a small preview for quick viewing.

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

Example:

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

## Tile naming and coordinates

Tiles must be PNG files named as:

```
<relx>_<rely>_x<mcx>_z<mcz>.png
```

- `relx` / `rely` — the tile's position in the grid. `relx` grows eastward (right) and `rely` grows southward (down), so the final image is oriented with north up.
- `mcx` / `mcz` — the Minecraft world coordinates of the tile. Reported in the log for reference; not used for layout.

One step in the relative grid equals `tile_size` pixels. Tiles need not cover a full rectangle — sparse exports are supported, and missing cells are painted with the background colour.

Example: `34_42_x-10240_z2048.png` sits at relative `(34, 42)`, and relative `(35, 42)` would lie `1024` px to its right.

## Outputs

| File | Description |
|---|---|
| `full_stitched.png` | The complete stitched map |
| `quad_NW.png` / `quad_NE.png` / `quad_SW.png` / `quad_SE.png` | Four quadrant crops for easy viewing |
| `preview.png` | A downscaled overview |

## Performance notes

A 19×24 grid of 1024×1024 tiles (≈478 megapixels) stitches in roughly 40 seconds on a typical machine and holds a ~1.4 GB RGB canvas in memory, so make sure you have a few gigabytes of free RAM. Because the canvas alone can be hundreds of megapixels, the tool disables Pillow's decompression-bomb guard; the canvas size is fully controlled by your own input files.

## License

[MIT](LICENSE) © 2026 HBNS_RS contributors
