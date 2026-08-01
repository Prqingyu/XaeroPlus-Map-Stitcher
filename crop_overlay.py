#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interactive photo-editor-style crop overlay for the XaeroPlus Map Stitcher GUI.

:class:`CropOverlay` is a ``tk.Canvas`` that displays an image and lets the
user drag a crop rectangle over it:

* drag a corner or edge handle to resize,
* drag inside the rectangle to move it,
* drag outside to start a new selection,
* optional aspect-ratio lock (free / original / 1:1 / 4:3 / 16:9).

The crop rectangle is tracked in *image* pixel coordinates; the caller maps
those to map coordinates using the preview's scale factor.
"""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageTk

HANDLE = 7          # half-size of the square handles, in pixels
MIN_SIZE = 12       # minimum selection edge, in image pixels
DIM_COLOR = "#000000"
SELECT_COLOR = "#e11"
HANDLE_FILL = "#ffffff"

# aspect preset name -> ratio (width/height); None = free
_ASPECT_RATIOS = {"original": None, "1:1": 1.0, "4:3": 4.0 / 3.0, "16:9": 16.0 / 9.0}

# corner handle -> (fixed x index, fixed y index) using (x0,y0)=(0,0),(x1,y1)=(1,1)
_CORNER_FIXED = {"nw": (1, 1), "ne": (0, 1), "sw": (1, 0), "se": (0, 0)}
# edge handle -> the corner we treat it as when the aspect is locked
_EDGE_AS_CORNER = {"n": "nw", "s": "se", "w": "sw", "e": "ne"}


def aspect_value(name: str, image_w: int, image_h: int) -> float | None:
    """Return the aspect ratio (w/h) for a preset name, or ``None`` for free."""
    if name == "free":
        return None
    if name == "original":
        return image_w / image_h if image_h else 1.0
    return _ASPECT_RATIOS.get(name)


class CropOverlay(tk.Canvas):
    """Canvas showing an image with a draggable crop rectangle overlay."""

    def __init__(self, master, box_w: int, box_h: int, on_change=None, **kwargs):
        super().__init__(
            master,
            width=box_w,
            height=box_h,
            highlightthickness=0,
            bg="#202020",
            **kwargs,
        )
        self._box = (box_w, box_h)
        self._on_change = on_change
        self._photo: ImageTk.PhotoImage | None = None
        self._img_w = self._img_h = 0
        self._ix = self._iy = 0          # image top-left in canvas coords
        self._rect = (0, 0, 0, 0)        # selection in image coords
        self._aspect_name = "free"
        self._active = False             # crop mode on -> mouse enabled + overlay drawn
        self._drag = None

        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)

    # ------------------------------------------------------------- public

    def set_image(self, pil: Image.Image) -> None:
        """Display ``pil`` scaled to fit the canvas and reset the selection."""
        w, h = pil.size
        fit = min(self._box[0] / w, self._box[1] / h)
        tw, th = max(1, int(w * fit)), max(1, int(h * fit))
        resized = pil.resize((tw, th), Image.Resampling.BILINEAR)
        self._photo = ImageTk.PhotoImage(resized)
        self._img_w, self._img_h = tw, th
        self._ix, self._iy = (self._box[0] - tw) // 2, (self._box[1] - th) // 2
        self._rect = (0, 0, tw, th)
        self._redraw()

    def set_active(self, active: bool) -> None:
        """Enable/disable crop mode (mouse handling + overlay)."""
        self._active = active
        self._redraw()
        self.configure(cursor="crosshair" if active else "")

    def set_aspect(self, name: str) -> None:
        self._aspect_name = name
        if self._rect[2] - self._rect[0] > 0 and self._rect[3] - self._rect[1] > 0:
            self._fit_to_aspect()
            self._redraw()
            self._notify()

    def get_rect(self) -> tuple[int, int, int, int]:
        """Return the selection as ``(left, top, right, bottom)`` image pixels."""
        return tuple(int(v) for v in self._rect)

    def set_rect(self, rect: tuple[int, int, int, int]) -> None:
        """Set the selection directly (image pixels), clamped to the image."""
        x0, y0, x1, y1 = rect
        self._rect = (x0, y0, x1, y1)
        self._clamp_rect()
        self._redraw()
        self._notify()

    def reset(self) -> None:
        self._rect = (0, 0, self._img_w, self._img_h)
        self._redraw()
        self._notify()

    @property
    def is_active(self) -> bool:
        return self._active

    def image_size(self) -> tuple[int, int]:
        """Size of the displayed image in canvas pixels (after fit-to-box).

        Crop coordinates live in this space; callers must map to the full map
        using ``image_w / full_map_width``.
        """
        return self._img_w, self._img_h

    # ----------------------------------------------------------- internals

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change(self.get_rect())

    def _to_canvas(self, x: float, y: float) -> tuple[int, int]:
        return int(self._ix + x), int(self._iy + y)

    def _to_img(self, cx: int, cy: int) -> tuple[int, int]:
        return cx - self._ix, cy - self._iy

    def _current_aspect(self) -> float | None:
        return aspect_value(self._aspect_name, self._img_w, self._img_h)

    def _handle_positions(self) -> list[tuple[str, float, float]]:
        x0, y0, x1, y1 = self._rect
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return [
            ("nw", x0, y0), ("n", mx, y0), ("ne", x1, y0),
            ("w", x0, my), ("e", x1, my),
            ("sw", x0, y1), ("s", mx, y1), ("se", x1, y1),
        ]

    def _hit_test(self, px: int, py: int):
        """Return ``("handle", name)`` / ``("inside", None)`` / ``("outside", None)``."""
        x0, y0, x1, y1 = self._rect
        for name, hx, hy in self._handle_positions():
            if abs(px - hx) <= HANDLE and abs(py - hy) <= HANDLE:
                return "handle", name
        if x0 <= px <= x1 and y0 <= py <= y1:
            return "inside", None
        return "outside", None

    def _press(self, event) -> None:
        if not self._active:
            return
        px, py = self._to_img(event.x, event.y)
        kind, name = self._hit_test(px, py)
        if kind == "handle":
            if self._current_aspect() is not None and name in _EDGE_AS_CORNER:
                name = _EDGE_AS_CORNER[name]
            anchor = self._fixed_corner(name)
            self._drag = {"mode": "handle", "name": name, "anchor": anchor, "rect": self._rect}
            self._update_cursor(name)
        elif kind == "inside":
            self._drag = {"mode": "move", "start": (px, py), "rect": self._rect}
            self.configure(cursor="fleur")
        else:
            self._drag = {"mode": "new", "start": (px, py)}
            self.configure(cursor="crosshair")

    def _motion(self, event) -> None:
        if not self._active or self._drag is None:
            return
        px, py = self._to_img(event.x, event.y)
        d = self._drag
        if d["mode"] == "new":
            self._rect = self._norm(d["start"][0], d["start"][1], px, py)
        elif d["mode"] == "move":
            dx, dy = px - d["start"][0], py - d["start"][1]
            x0, y0, x1, y1 = d["rect"]
            self._rect = self._norm(x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        else:  # handle
            self._rect = self._handle_rect(d, px, py)
            self._update_cursor(d["name"])
        self._clamp_rect()
        self._redraw()
        self._notify()

    def _release(self, _event) -> None:
        if not self._active:
            return
        self._drag = None
        if self._active:
            self.configure(cursor="crosshair")

    def _fixed_corner(self, name: str) -> tuple[int, int]:
        """Return the image-coordinate corner that stays fixed for this handle."""
        x0, y0, x1, y1 = self._rect
        fx, fy = _CORNER_FIXED[name]
        return (x1 if fx else x0), (y1 if fy else y0)

    def _handle_rect(self, drag, px: int, py: int):
        name = drag["name"]
        aspect = self._current_aspect()
        if aspect is not None:
            # treat every handle as a corner resize from its fixed corner
            ax, ay = drag["anchor"]
            rect = self._corner_resize(ax, ay, px, py, aspect)
            return rect
        x0, y0, x1, y1 = drag["rect"]
        if name == "n":
            return (x0, py, x1, y1)
        if name == "s":
            return (x0, y0, x1, py)
        if name == "w":
            return (px, y0, x1, y1)
        if name == "e":
            return (x0, y0, px, y1)
        # corners: free form between the fixed corner and the pointer
        ax, ay = drag["anchor"]
        return self._norm(ax, ay, px, py)

    def _corner_resize(self, ax: float, ay: float, px: float, py: float, aspect: float):
        """Rect of the given aspect anchored at fixed corner ``(ax, ay)``."""
        sx = 1 if px >= ax else -1
        sy = 1 if py >= ay else -1
        aw, ah = abs(px - ax), abs(py - ay)
        if aw / aspect >= ah:
            w, h = aw, aw / aspect
        else:
            h, w = ah, ah * aspect
        return self._norm(ax, ay, ax + sx * w, ay + sy * h)

    @staticmethod
    def _norm(x0, y0, x1, y1) -> tuple[float, float, float, float]:
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def _clamp_rect(self) -> None:
        x0, y0, x1, y1 = self._rect
        x0 = max(0.0, min(x0, self._img_w))
        x1 = max(0.0, min(x1, self._img_w))
        y0 = max(0.0, min(y0, self._img_h))
        y1 = max(0.0, min(y1, self._img_h))
        if x1 - x0 < MIN_SIZE:
            x1 = min(self._img_w, x0 + MIN_SIZE)
        if y1 - y0 < MIN_SIZE:
            y1 = min(self._img_h, y0 + MIN_SIZE)
        self._rect = (round(x0), round(y0), round(x1), round(y1))

    def _fit_to_aspect(self) -> None:
        """Re-shape the current selection to the active aspect (if locked)."""
        aspect = self._current_aspect()
        if aspect is None or self._img_w == 0:
            return
        x0, y0, x1, y1 = self._rect
        w, h = x1 - x0, y1 - y0
        if h <= 0 or w <= 0:
            return
        if w / h > aspect:
            nh = w / aspect
            y0 -= (nh - h) / 2
            y1 = y0 + nh
        else:
            nw = h * aspect
            x0 -= (nw - w) / 2
            x1 = x0 + nw
        self._rect = (x0, y0, x1, y1)
        self._clamp_rect()

    def _update_cursor(self, name: str) -> None:
        cursors = {
            "nw": "nw_resize", "se": "nw_resize",
            "ne": "ne_resize", "sw": "ne_resize",
            "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
            "w": "sb_h_double_arrow", "e": "sb_h_double_arrow",
        }
        self.configure(cursor=cursors.get(name, "crosshair"))

    def _redraw(self) -> None:
        self.delete("all")
        if self._photo is None:
            return
        self.create_image(self._ix, self._iy, anchor="nw", image=self._photo)
        if not self._active:
            return
        x0, y0, x1, y1 = self._rect
        c = self._to_canvas
        # dim the area outside the selection
        dims = [
            (0, 0, self._img_w, y0),          # top
            (0, y1, self._img_w, self._img_h),  # bottom
            (0, y0, x0, y1),                  # left
            (x1, y0, self._img_w, y1),        # right
        ]
        for dx0, dy0, dx1, dy1 in dims:
            if dx1 > dx0 and dy1 > dy0:
                self.create_rectangle(c(dx0, dy0), c(dx1, dy1), fill=DIM_COLOR, stipple="gray25", outline="")
        # selection border
        self.create_rectangle(c(x0, y0), c(x1, y1), outline=SELECT_COLOR, width=2)
        # handles
        for _name, hx, hy in self._handle_positions():
            self.create_rectangle(
                c(hx - HANDLE, hy - HANDLE), c(hx + HANDLE, hy + HANDLE),
                fill=HANDLE_FILL, outline=SELECT_COLOR, width=1,
            )
