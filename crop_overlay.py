#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interactive photo-editor-style crop overlay for the XaeroPlus Map Stitcher GUI.

:class:`CropOverlay` is a ``tk.Canvas`` that displays an image and lets the
user drag a crop rectangle over it:

* **9 handles** — 4 corners + 4 edge mid-points + a centre handle. Corners and
  edges resize the selection; the centre (or dragging anywhere inside) moves it.
* drag inside the rectangle to move it,
* drag outside to start a new selection,
* optional aspect-ratio lock (free / original / 1:1 / 4:3 / 16:9).

Coordinates
-----------
The selection is tracked in *source* image pixels (the image passed to
:meth:`set_image`), independent of how the image is displayed. If the canvas
is resized, only the on-screen scaling changes; the selection keeps meaning
the same region of the image. Use :meth:`source_size` to map source pixels to
map pixels.
"""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageTk

HANDLE = 7          # hit radius / half-size of the square handles, in canvas px
MIN_SIZE = 12       # minimum selection edge, in source pixels
DIM_COLOR = "#000000"
SELECT_COLOR = "#e11"
HANDLE_FILL = "#ffffff"
CENTER_FILL = "#e11"

# aspect preset name -> ratio (width/height); None = free
_ASPECT_RATIOS = {"original": None, "1:1": 1.0, "4:3": 4.0 / 3.0, "16:9": 16.0 / 9.0}

# corner handle -> (fixed x index, fixed y index) using (x0,y0)=(0,0),(x1,y1)=(1,1)
_CORNER_FIXED = {"nw": (1, 1), "ne": (0, 1), "sw": (1, 0), "se": (0, 0)}
# edge handle -> the corner we treat it as when the aspect is locked
_EDGE_AS_CORNER = {"n": "nw", "s": "se", "w": "sw", "e": "ne"}


def aspect_value(name: str, src_w: int, src_h: int) -> float | None:
    """Return the aspect ratio (w/h) for a preset name, or ``None`` for free."""
    if name == "free":
        return None
    if name == "original":
        return src_w / src_h if src_h else 1.0
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
        self._src: Image.Image | None = None
        self._src_w = self._src_h = 0
        self._disp_scale = 1.0
        self._ix = self._iy = 0            # displayed image top-left in canvas px
        self._rect = (0, 0, 0, 0)          # selection in source px
        self._aspect_name = "free"
        self._active = False
        self._drag = None

        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", self._on_configure)

    # ------------------------------------------------------------- public

    def set_image(self, pil: Image.Image) -> None:
        """Display ``pil`` (source coords) and reset the selection to full."""
        self._src = pil
        self._src_w, self._src_h = pil.size
        self._update_display()
        self._rect = (0, 0, self._src_w, self._src_h)
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
        """Return the selection as ``(left, top, right, bottom)`` source pixels."""
        return tuple(int(v) for v in self._rect)

    def set_rect(self, rect: tuple[int, int, int, int]) -> None:
        """Set the selection directly (source pixels), clamped to the image."""
        x0, y0, x1, y1 = rect
        self._rect = (x0, y0, x1, y1)
        self._clamp_rect()
        self._redraw()
        self._notify()

    def reset(self) -> None:
        self._rect = (0, 0, self._src_w, self._src_h)
        self._redraw()
        self._notify()

    @property
    def is_active(self) -> bool:
        return self._active

    def source_size(self) -> tuple[int, int]:
        """Size of the source image in source pixels (used to map to the map)."""
        return self._src_w, self._src_h

    # ----------------------------------------------------------- internals

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change(self.get_rect())

    def _on_configure(self, _event) -> None:
        if self._src is not None:
            self._update_display()
            self._redraw()

    def _update_display(self) -> None:
        """Re-scale the displayed image to the current canvas size."""
        if self._src is None:
            return
        bw, bh = self.winfo_width(), self.winfo_height()
        if bw <= 1 or bh <= 1:
            bw, bh = self._box
        scale = min(bw / self._src_w, bh / self._src_h)
        self._disp_scale = scale
        dw, dh = max(1, round(self._src_w * scale)), max(1, round(self._src_h * scale))
        resized = self._src.resize((dw, dh), Image.Resampling.BILINEAR)
        self._photo = ImageTk.PhotoImage(resized)
        self._ix = (bw - dw) // 2
        self._iy = (bh - dh) // 2

    def _to_canvas(self, sx: float, sy: float) -> tuple[int, int]:
        return int(self._ix + sx * self._disp_scale), int(self._iy + sy * self._disp_scale)

    def _to_src(self, cx: int, cy: int) -> tuple[float, float]:
        return (cx - self._ix) / self._disp_scale, (cy - self._iy) / self._disp_scale

    def _current_aspect(self) -> float | None:
        return aspect_value(self._aspect_name, self._src_w, self._src_h)

    def _handle_positions(self) -> list[tuple[str, float, float]]:
        x0, y0, x1, y1 = self._rect
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return [
            ("nw", x0, y0), ("n", mx, y0), ("ne", x1, y0),
            ("w", x0, my), ("center", mx, my), ("e", x1, my),
            ("sw", x0, y1), ("s", mx, y1), ("se", x1, y1),
        ]

    def _hit_test(self, cx: int, cy: int):
        """Canvas-coord hit test -> ``("handle", name)`` / ``("inside", None)`` / ``("outside", None)``."""
        for name, hx, hy in self._handle_positions():
            px, py = self._to_canvas(hx, hy)
            if abs(cx - px) <= HANDLE and abs(cy - py) <= HANDLE:
                return "handle", name
        p0 = self._to_canvas(*self._rect[:2])
        p1 = self._to_canvas(*self._rect[2:])
        if p0[0] <= cx <= p1[0] and p0[1] <= cy <= p1[1]:
            return "inside", None
        return "outside", None

    def _press(self, event) -> None:
        if not self._active:
            return
        kind, name = self._hit_test(event.x, event.y)
        if kind == "handle":
            if name == "center":
                self._drag = {"mode": "move", "start": self._to_src(event.x, event.y), "rect": self._rect}
                self.configure(cursor="fleur")
                return
            if self._current_aspect() is not None and name in _EDGE_AS_CORNER:
                name = _EDGE_AS_CORNER[name]
            anchor = self._fixed_corner(name)
            self._drag = {"mode": "handle", "name": name, "anchor": anchor, "rect": self._rect}
            self._update_cursor(name)
        elif kind == "inside":
            self._drag = {"mode": "move", "start": self._to_src(event.x, event.y), "rect": self._rect}
            self.configure(cursor="fleur")
        else:
            self._drag = {"mode": "new", "start": self._to_src(event.x, event.y)}
            self.configure(cursor="crosshair")

    def _motion(self, event) -> None:
        if not self._active or self._drag is None:
            return
        sx, sy = self._to_src(event.x, event.y)
        d = self._drag
        if d["mode"] == "new":
            self._rect = self._norm(d["start"][0], d["start"][1], sx, sy)
        elif d["mode"] == "move":
            dx, dy = sx - d["start"][0], sy - d["start"][1]
            x0, y0, x1, y1 = d["rect"]
            self._rect = self._norm(x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        else:  # handle
            self._rect = self._handle_rect(d, sx, sy)
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
        x0, y0, x1, y1 = self._rect
        fx, fy = _CORNER_FIXED[name]
        return (x1 if fx else x0), (y1 if fy else y0)

    def _handle_rect(self, drag, sx: float, sy: float):
        name = drag["name"]
        aspect = self._current_aspect()
        if aspect is not None:
            ax, ay = drag["anchor"]
            return self._corner_resize(ax, ay, sx, sy, aspect)
        x0, y0, x1, y1 = drag["rect"]
        if name == "n":
            return (x0, sy, x1, y1)
        if name == "s":
            return (x0, y0, x1, sy)
        if name == "w":
            return (sx, y0, x1, y1)
        if name == "e":
            return (x0, y0, sx, y1)
        ax, ay = drag["anchor"]
        return self._norm(ax, ay, sx, sy)

    def _corner_resize(self, ax: float, ay: float, sx: float, sy: float, aspect: float):
        """Rect of the given aspect anchored at fixed corner ``(ax, ay)``."""
        sign_x = 1 if sx >= ax else -1
        sign_y = 1 if sy >= ay else -1
        aw, ah = abs(sx - ax), abs(sy - ay)
        if aw / aspect >= ah:
            w, h = aw, aw / aspect
        else:
            h, w = ah, ah * aspect
        return self._norm(ax, ay, ax + sign_x * w, ay + sign_y * h)

    @staticmethod
    def _norm(x0, y0, x1, y1) -> tuple[float, float, float, float]:
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def _clamp_rect(self) -> None:
        x0, y0, x1, y1 = self._rect
        x0 = max(0.0, min(x0, self._src_w))
        x1 = max(0.0, min(x1, self._src_w))
        y0 = max(0.0, min(y0, self._src_h))
        y1 = max(0.0, min(y1, self._src_h))
        if x1 - x0 < MIN_SIZE:
            x1 = min(self._src_w, x0 + MIN_SIZE)
        if y1 - y0 < MIN_SIZE:
            y1 = min(self._src_h, y0 + MIN_SIZE)
        self._rect = (round(x0), round(y0), round(x1), round(y1))

    def _fit_to_aspect(self) -> None:
        aspect = self._current_aspect()
        if aspect is None or self._src_w == 0:
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
        for dx0, dy0, dx1, dy1 in (
            (0, 0, self._src_w, y0),              # top
            (0, y1, self._src_w, self._src_h),    # bottom
            (0, y0, x0, y1),                      # left
            (x1, y0, self._src_w, y1),            # right
        ):
            if dx1 > dx0 and dy1 > dy0:
                self.create_rectangle(c(dx0, dy0), c(dx1, dy1), fill=DIM_COLOR, stipple="gray25", outline="")
        # selection border
        self.create_rectangle(c(x0, y0), c(x1, y1), outline=SELECT_COLOR, width=2)
        # handles: 4 corners + 4 edges (squares) + centre (dot)
        for name, hx, hy in self._handle_positions():
            px, py = c(hx, hy)
            if name == "center":
                self.create_oval(px - 3, py - 3, px + 3, py + 3, fill=CENTER_FILL, outline="")
            else:
                self.create_rectangle(
                    px - HANDLE, py - HANDLE, px + HANDLE, py + HANDLE,
                    fill=HANDLE_FILL, outline=SELECT_COLOR, width=1,
                )
