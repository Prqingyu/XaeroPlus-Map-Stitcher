#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XaeroPlus Map Stitcher — GUI front-end (CustomTkinter).

Requires the GUI dependencies::

    pip install -r requirements-gui.txt   # Pillow + customtkinter

Run::

    python stitcher_gui.py

Features
--------
* Pick a XaeroPlus export directory and an output directory.
* Two linked output modes (the setting sits directly below the mode selector).
  Both default to the **original full resolution**; downscaling happens only
  when you pick a smaller width or enter a target file size.
  - by resolution  (set output width / scale, size is estimated)
  - by file size   (leave the target empty for original resolution, or enter a
    size in MB and the resolution is solved iteratively)
* PNG compression level 0-9 (lossless; affects file size / encode time only).
* A statistics area (tile count, grid, resolution, estimated size) and a
  fixed ~1 MB full-map preview. The preview zooms with the mouse wheel and
  pans by dragging, like a photo viewer.
* **Cropping** opens a dedicated crop window that combines a large map canvas
  (photo-editor-style: 9 handles, drag to move/resize, zoom & pan) with a
  data panel.
* All heavy work runs in a background thread; the UI stays responsive and the
  operation can be cancelled.
"""

from __future__ import annotations

import math
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

import stitch_core as core
from crop_overlay import CropOverlay

Image.MAX_IMAGE_PIXELS = None  # see stitch_core for why

APP_TITLE = "XaeroPlus Map Stitcher — 分片地图整合工具"
PREVIEW_TARGET_BYTES = 1_000_000  # ~1 MB overview
PREVIEW_BOX_W, PREVIEW_BOX_H = 440, 460
DEFAULT_LEVEL = 6

ASPECT_LABELS = ["自由", "原比例", "1:1", "4:3", "16:9"]


class CropWindow(ctk.CTkToplevel):
    """A dedicated crop editor: large map canvas + a data/controls panel."""

    def __init__(self, master, ts, preview_pil, scale_getter, crop_box):
        super().__init__(master)
        self.master = master
        self.ts = ts
        self._scale_getter = scale_getter
        self.title("裁切 — XaeroPlus Map Stitcher")
        self.geometry("1000x760")
        self.minsize(900, 640)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        root = ctk.CTkFrame(self)
        root.pack(fill="both", expand=True, padx=12, pady=12)

        # data + controls panel on the right
        panel = ctk.CTkFrame(root)
        panel.pack(side="right", fill="y", padx=(10, 0))
        self._build_panel(panel)

        # large map canvas on the left
        self.overlay = CropOverlay(root, 820, 700, on_change=self._on_change)
        self.overlay.pack(side="left", fill="both", expand=True)

        self.overlay.set_image(preview_pil)
        if crop_box is not None:
            s = self._src_scale()
            left, top, right, bottom = crop_box
            self.overlay.set_rect((round(left * s), round(top * s), round(right * s), round(bottom * s)))
        else:
            self.overlay.reset()
        self.overlay.set_active(True)
        self._on_change(None)

    def _build_panel(self, panel: ctk.CTkFrame) -> None:
        ctk.CTkLabel(panel, text="裁切设置", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=12, pady=(10, 4))

        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.pack(fill="x", padx=12)
        ctk.CTkLabel(row, text="宽高比:").pack(side="left")
        self.aspect_var = tk.StringVar(value="自由")
        ctk.CTkOptionMenu(
            row, values=ASPECT_LABELS, variable=self.aspect_var, command=self._on_aspect, width=92,
        ).pack(side="left", padx=6)

        ctk.CTkLabel(panel, text="滚轮:缩放 · 中键拖动:平移\n左键:拖动 9 个手柄调整裁切", justify="left",
                     text_color="gray").pack(anchor="w", padx=12, pady=(8, 0))

        ctk.CTkLabel(panel, text="数据区", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=12, pady=(14, 2))
        rows = [("tiles", "分片量(瓦片数)"), ("region", "选区(地图px)"), ("output", "输出分辨率"), ("scale", "输出比例")]
        self.labels: dict[str, ctk.CTkLabel] = {}
        for i, (key, label) in enumerate(rows):
            ctk.CTkLabel(panel, text=label + ":", anchor="w").pack(anchor="w", padx=12, pady=1)
            val = ctk.CTkLabel(panel, text="-", anchor="e")
            val.pack(fill="x", padx=12)
            self.labels[key] = val

        btns = ctk.CTkFrame(panel, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(16, 10))
        ctk.CTkButton(btns, text="应用", command=self._apply).pack(fill="x", pady=2)
        ctk.CTkButton(btns, text="重置", command=self._reset).pack(fill="x", pady=2)
        ctk.CTkButton(btns, text="取消", command=self._cancel, fg_color="gray40", hover_color="gray55").pack(fill="x", pady=2)

    # ------------------------------------------------------------ mapping

    def _src_scale(self) -> float:
        sw, _ = self.overlay.source_size()
        return sw / self.ts.full_width if sw else 1.0

    def _map_to_full(self, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        s = self._src_scale()
        x0, y0, x1, y1 = rect
        left = max(0, round(x0 / s))
        top = max(0, round(y0 / s))
        right = min(self.ts.full_width, round(x1 / s))
        bottom = min(self.ts.full_height, round(y1 / s))
        return left, top, right, bottom

    def _on_change(self, _rect) -> None:
        if not hasattr(self, "labels"):
            return
        self.labels["tiles"].configure(text=str(self.ts.count))
        left, top, right, bottom = self._map_to_full(self.overlay.get_rect())
        w, h = right - left, bottom - top
        self.labels["region"].configure(text=f"{w} × {h}")
        scale = min(1.0, max(0.01, self._scale_getter()))
        self.labels["output"].configure(text=f"{max(1, round(w*scale))} × {max(1, round(h*scale))}")
        self.labels["scale"].configure(text=f"{scale*100:.1f}%")

    def _on_aspect(self, name: str) -> None:
        mapping = {"自由": "free", "原比例": "original", "1:1": "1:1", "4:3": "4:3", "16:9": "16:9"}
        self.overlay.set_aspect(mapping.get(name, "free"))

    def _reset(self) -> None:
        self.overlay.reset()
        self._on_change(None)

    def _apply(self) -> None:
        box = self._map_to_full(self.overlay.get_rect())
        self.master._crop_applied(box)
        self.destroy()

    def _cancel(self) -> None:
        self.master._crop_closed()
        self.destroy()


class StitcherApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x760")
        self.minsize(900, 700)

        # ---- state (plain Python objects, safe to read from worker threads)
        self.ts: core.TileSet | None = None
        self.cal: core.Calibration | None = None
        self.preview_pil: Image.Image | None = None
        self.crop_box: tuple[int, int, int, int] | None = None
        self._crop_window: CropWindow | None = None

        self.queue: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None

        # ---- tk variables (main thread only)
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="resolution")
        self.width_var = tk.StringVar()
        self.scale_var = tk.DoubleVar(value=1.0)
        self.target_mb_var = tk.StringVar(value="")
        self.level_var = tk.DoubleVar(value=DEFAULT_LEVEL)

        self._build_ui()
        self.after(100, self._poll)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = ctk.CTkFrame(self)
        root.pack(fill="both", expand=True, padx=14, pady=14)

        self._add_path_row(root, 0, "输入目录", self.input_var, self._pick_input)
        self._add_path_row(root, 1, "输出目录", self.output_var, self._pick_output)

        self._build_params(root)

        middle = ctk.CTkFrame(root)
        middle.pack(fill="both", expand=True, pady=12)
        middle.grid_columnconfigure(0, weight=1)
        middle.grid_rowconfigure(0, weight=1)
        self._build_data(middle)
        self._build_preview(middle)

        self._build_bottom(root)

        self._on_mode()

    def _add_path_row(self, parent, row: int, label: str, var, browse_cb) -> None:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", pady=3)
        ctk.CTkLabel(frame, text=label, width=70, anchor="w").pack(side="left")
        ctk.CTkEntry(frame, textvariable=var).pack(side="left", fill="x", expand=True, padx=6)
        ctk.CTkButton(frame, text="浏览...", width=76, command=browse_cb).pack(side="left")

    def _build_params(self, parent) -> None:
        """Output settings block: mode selector first, then its setting, then level."""
        section = ctk.CTkFrame(parent)
        section.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(section, text="输出设置", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 0)
        )

        # ---- mode selector
        modef = ctk.CTkFrame(section, fg_color="transparent")
        modef.pack(fill="x", padx=10, pady=(6, 0))
        ctk.CTkLabel(modef, text="输出模式:").pack(side="left")
        ctk.CTkRadioButton(
            modef, text="按分辨率", variable=self.mode_var, value="resolution", command=self._on_mode
        ).pack(side="left", padx=8)
        ctk.CTkRadioButton(
            modef, text="按文件大小", variable=self.mode_var, value="size", command=self._on_mode
        ).pack(side="left", padx=8)

        # ---- resolution setting (shown directly under the mode selector)
        self.res_frame = ctk.CTkFrame(section, fg_color="transparent")
        ctk.CTkLabel(self.res_frame, text="输出宽度(px):").pack(side="left")
        self.width_entry = ctk.CTkEntry(self.res_frame, textvariable=self.width_var, width=110)
        self.width_entry.pack(side="left", padx=6)
        self.width_entry.bind("<Return>", self._on_width)
        self.width_entry.bind("<FocusOut>", self._on_width)
        self.scale_slider = ctk.CTkSlider(
            self.res_frame, from_=0.01, to=1.0, number_of_steps=99,
            variable=self.scale_var, command=self._on_scale, width=220,
        )
        self.scale_slider.pack(side="left", padx=10)
        self.res_readout = ctk.CTkLabel(self.res_frame, text="", anchor="e")
        self.res_readout.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # ---- size setting (alternate with the resolution one)
        self.size_frame = ctk.CTkFrame(section, fg_color="transparent")
        ctk.CTkLabel(self.size_frame, text="目标大小(MB):").pack(side="left")
        self.mb_entry = ctk.CTkEntry(self.size_frame, textvariable=self.target_mb_var, width=110)
        self.mb_entry.pack(side="left", padx=6)
        self.mb_entry.bind("<Return>", self._on_mb)
        self.mb_entry.bind("<FocusOut>", self._on_mb)
        ctk.CTkLabel(self.size_frame, text="(留空 = 不限制,按原始分辨率输出;填数字则自动求解分辨率)").pack(side="left", padx=8)

        # ---- compression level
        compf = ctk.CTkFrame(section, fg_color="transparent")
        compf.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkLabel(compf, text="压缩级别:").pack(side="left")
        ctk.CTkSlider(
            compf, from_=0, to=9, number_of_steps=9, variable=self.level_var,
            command=self._on_level, width=140,
        ).pack(side="left", padx=6)
        self.level_val = ctk.CTkLabel(compf, text=str(DEFAULT_LEVEL), width=24)
        self.level_val.pack(side="left")

        self.res_frame.pack(fill="x", padx=10, pady=6)
        self.size_frame.pack_forget()

    def _build_data(self, parent) -> None:
        frame = ctk.CTkFrame(parent)
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ctk.CTkLabel(frame, text="数据区", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 4)
        )
        rows = [
            ("tiles", "分片量(瓦片数)"),
            ("input_res", "输入分辨率"),
            ("grid", "网格"),
            ("holes", "空洞"),
            ("output_res", "输出分辨率"),
            ("scale", "输出比例"),
            ("est_size", "预估文件大小"),
            ("actual_size", "实际文件大小"),
        ]
        self.stat_labels: dict[str, ctk.CTkLabel] = {}
        for i, (key, label) in enumerate(rows, 1):
            ctk.CTkLabel(frame, text=label + ":", anchor="w").grid(
                row=i, column=0, sticky="ew", padx=12, pady=2
            )
            val = ctk.CTkLabel(frame, text="-", anchor="e")
            val.grid(row=i, column=1, sticky="e", padx=12)
            self.stat_labels[key] = val
        frame.grid_columnconfigure(1, weight=1)

    def _build_preview(self, parent) -> None:
        frame = ctk.CTkFrame(parent)
        frame.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(frame, text="预览(~1MB 全图概览)", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        self.crop_overlay = CropOverlay(frame, PREVIEW_BOX_W, PREVIEW_BOX_H)
        self.crop_overlay.pack(fill="both", expand=True, padx=12, pady=(2, 0))
        ctk.CTkLabel(frame, text="滚轮缩放 · 按住拖动平移", text_color="gray", anchor="center").pack(
            pady=(0, 8)
        )

    def _build_bottom(self, parent) -> None:
        bottom = ctk.CTkFrame(parent)
        bottom.pack(fill="x", pady=(0, 4))
        self.progress = ctk.CTkProgressBar(bottom, height=14)
        self.progress.pack(fill="x", padx=10, pady=(10, 4))
        self.status_var = tk.StringVar(value="就绪")
        ctk.CTkLabel(bottom, textvariable=self.status_var, anchor="w").pack(fill="x", padx=12)
        btns = ctk.CTkFrame(bottom, fg_color="transparent")
        btns.pack(fill="x", padx=10, pady=8)
        self.btn_preview = ctk.CTkButton(btns, text="生成预览", command=self._cmd_preview)
        self.btn_preview.pack(side="left", padx=(0, 8))
        self.btn_crop = ctk.CTkButton(btns, text="裁切", command=self._cmd_crop)
        self.btn_crop.pack(side="left", padx=(0, 8))
        self.btn_run = ctk.CTkButton(btns, text="开始拼接", command=self._cmd_run, state="disabled")
        self.btn_run.pack(side="left")
        self.btn_cancel = ctk.CTkButton(
            btns, text="取消", command=self._cmd_cancel, state="disabled",
            fg_color="gray40", hover_color="gray55",
        )
        self.btn_cancel.pack(side="right")

    # ------------------------------------------------------------ handlers

    def _pick_input(self) -> None:
        d = filedialog.askdirectory(title="选择 XaeroPlus 导出瓦片目录")
        if not d:
            return
        self.input_var.set(d)
        src = Path(d)
        self.output_var.set(str(Path(str(src) + "_stitched")))  # sibling folder
        self._load_input(d)

    def _pick_output(self) -> None:
        d = filedialog.askdirectory(title="选择输出目录(将在该位置创建文件夹)")
        if d:
            self.output_var.set(d)

    def _load_input(self, d: str) -> None:
        self.btn_run.configure(state="disabled")
        self.btn_preview.configure(state="disabled")
        self.btn_crop.configure(state="disabled")
        self._set_progress_indet(True, "正在解析瓦片...")
        self._start_worker(self._worker_load, d, int(self.level_var.get()))

    def _on_mode(self) -> None:
        if self.mode_var.get() == "resolution":
            self.size_frame.pack_forget()
            self.res_frame.pack(fill="x", padx=10, pady=6)
        else:
            self.res_frame.pack_forget()
            self.size_frame.pack(fill="x", padx=10, pady=6)
        self._recompute()

    def _on_scale(self, _value) -> None:
        if self.ts is not None:
            region_w, _ = core.region_size(self.ts, self.crop_box)
            scale = min(1.0, max(0.01, self.scale_var.get()))
            self.width_var.set(str(max(1, round(region_w * scale))))
        self._recompute()

    def _on_width(self, _event=None) -> None:
        if self.ts is None:
            return
        try:
            w = max(1, int(self.width_var.get()))
        except ValueError:
            return
        region_w, _ = core.region_size(self.ts, self.crop_box)
        self.width_var.set(str(w))
        self.scale_var.set(min(1.0, w / region_w))
        self._recompute()

    def _on_mb(self, _event=None) -> None:
        self._recompute()

    def _on_level(self, _value) -> None:
        lvl = int(self.level_var.get())
        self.level_val.configure(text=str(lvl))
        if self.cal is not None:
            # re-encode the stored calibration images at the new level (cheap)
            pts = [(s, core.encode_png_size(im, lvl), im) for s, _b, im in self.cal.points]
            self.cal = core.Calibration(self.cal.ts, lvl, pts)
        self._recompute()

    def _cmd_preview(self) -> None:
        if self.ts is None:
            messagebox.showwarning(APP_TITLE, "请先选择输入目录")
            return
        self.btn_preview.configure(state="disabled")
        self.btn_crop.configure(state="disabled")
        self._set_progress_indet(True, "正在生成预览...")
        self._start_worker(
            self._worker_preview, Path(self.input_var.get()), int(self.level_var.get())
        )

    # ------------------------------------------------------------- cropping

    def _cmd_crop(self) -> None:
        if self.ts is None or self.preview_pil is None:
            messagebox.showwarning(APP_TITLE, "请先加载输入目录并生成预览")
            return
        if self._crop_window is not None and self._crop_window.winfo_exists():
            self._crop_window.lift()
            self._crop_window.focus()
            return
        self.btn_run.configure(state="disabled")
        self.btn_preview.configure(state="disabled")
        self.btn_crop.configure(state="disabled")
        self._crop_window = CropWindow(
            self, self.ts, self.preview_pil,
            lambda: (self.scale_var.get() if self.mode_var.get() == "resolution" else 1.0),
            self.crop_box,
        )

    def _crop_applied(self, box: tuple[int, int, int, int]) -> None:
        self.crop_box = box
        rw, _ = core.region_size(self.ts, self.crop_box)
        self.width_var.set(str(max(1, round(rw * self.scale_var.get()))))
        self._recompute()
        self.status_var.set(f"已应用裁切: {box}")
        self._crop_closed()

    def _crop_closed(self) -> None:
        self._crop_window = None
        self.btn_run.configure(state="normal")
        self.btn_preview.configure(state="normal")
        self.btn_crop.configure(state="normal")

    # --------------------------------------------------------------- run

    def _cmd_run(self) -> None:
        if self.ts is None or not self.input_var.get():
            messagebox.showwarning(APP_TITLE, "请先选择输入目录")
            return
        out = self.output_var.get().strip()
        if not out:
            messagebox.showwarning(APP_TITLE, "请设置输出目录")
            return
        if self.mode_var.get() == "size":
            target_text = self.target_mb_var.get().strip()
            if target_text:
                try:
                    mb = float(target_text)
                    if mb <= 0:
                        raise ValueError
                except ValueError:
                    messagebox.showwarning(APP_TITLE, "目标大小必须是正数(MB),或留空按原始分辨率输出")
                    return
                target = ("size", mb)
            else:
                target = ("resolution", 1.0)  # empty -> original resolution
        else:
            target = ("resolution", min(1.0, self.scale_var.get()))

        self.btn_run.configure(state="disabled")
        self.btn_preview.configure(state="disabled")
        self.btn_crop.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.cancel_event.clear()
        self._set_progress_indet(False)
        self._start_worker(
            self._worker_run, Path(self.input_var.get()), Path(out),
            int(self.level_var.get()), target,
        )

    def _cmd_cancel(self) -> None:
        self.cancel_event.set()
        self.btn_cancel.configure(state="disabled")

    # ------------------------------------------------------------- workers

    def _worker_load(self, d: str, level: int) -> None:
        input_dir = Path(d)
        ts = core.discover_tiles(input_dir)
        self._emit({"type": "tileset", "ts": ts})
        cal = core.build_calibration(ts, input_dir, level, (0, 0, 0))
        self._emit({"type": "cal", "cal": cal})
        preview = self._build_preview_image(ts, input_dir, cal, level)
        self._emit({"type": "preview", "image": preview})
        self._emit({"type": "status", "text": "就绪"})
        self._emit({"type": "done"})

    def _worker_preview(self, input_dir: Path, level: int) -> None:
        cal = self.cal
        if cal is None or cal.level != level:
            cal = core.build_calibration(self.ts, input_dir, level, (0, 0, 0))
            self._emit({"type": "cal", "cal": cal})
        preview = self._build_preview_image(self.ts, input_dir, cal, level)
        self._emit({"type": "preview", "image": preview})
        self._emit({"type": "status", "text": "预览已生成"})
        self._emit({"type": "done"})

    def _worker_run(self, input_dir: Path, output_dir: Path, level: int, target) -> None:
        ts = self.ts
        crop_box = self.crop_box  # snapshot at start
        prog = core.Progress(lambda c, t, s: self._emit_progress(c, t, s))
        try:
            if target[0] == "resolution":
                scale = target[1]
                self._emit({"type": "status", "text": f"正在构建画布(比例 {scale*100:.1f}%)..."})
                canvas = core.build_canvas(ts, input_dir, scale, (0, 0, 0), progress=prog,
                                           cancel_flag=self.cancel_event, crop_box=crop_box)
                self._emit({"type": "status", "text": "正在编码输出..."})
                actual = core.encode_png_size(canvas, level)
            else:
                self._emit({"type": "status", "text": "正在求解目标文件大小..."})
                _scale, canvas, actual = core.solve_scale_for_size(
                    ts, input_dir, target[1] * 1048576, level, (0, 0, 0),
                    progress=prog, cancel_flag=self.cancel_event,
                    calibration=self.cal, crop_box=crop_box,
                )
            out_w, out_h = canvas.size
            self._emit({"type": "progress", "fraction": 0.95})
            self._emit({"type": "status", "text": "正在保存输出..."})
            written = core.save_outputs(
                canvas, output_dir, level,
                preview_image=self.preview_pil,
                cancel_flag=self.cancel_event,
            )
            self._emit({"type": "progress", "fraction": 1.0})
            self._emit({"type": "stats_actual", "w": out_w, "h": out_h, "bytes": actual})
            lines = [
                f"拼接完成: {out_w} × {out_h}",
                f"实际文件大小: {self._fmt_bytes(actual)}",
                f"输出目录: {output_dir}",
            ]
            for _label, p in written.items():
                lines.append(f"  {p.name}: {self._fmt_bytes(p.stat().st_size)}")
            self._emit({"type": "result", "text": "\n".join(lines)})
        except core.StitchCancelled:
            self._emit({"type": "status", "text": "已取消"})
            self._emit({"type": "done"})

    # ------------------------------------------------------------- helpers

    def _build_preview_image(self, ts: core.TileSet, input_dir: Path, cal: core.Calibration, level: int) -> Image.Image:
        """Build a ~1 MB overview by scaling the whole map from the tiles."""
        est_full = cal.estimate_bytes(ts.full_pixels, level)
        if est_full > 0:
            scale = min(1.0, max(0.005, math.sqrt(PREVIEW_TARGET_BYTES / est_full)))
        else:
            scale = 0.04
        im = core.build_canvas(ts, input_dir, scale, (0, 0, 0))
        size = core.encode_png_size(im, level)
        # one corrective pass to land near the target size
        if 0 < abs(size - PREVIEW_TARGET_BYTES) / PREVIEW_TARGET_BYTES > 0.15:
            s2 = min(1.0, max(0.005, scale * math.sqrt(PREVIEW_TARGET_BYTES / size)))
            im2 = core.build_canvas(ts, input_dir, s2, (0, 0, 0))
            if abs(core.encode_png_size(im2, level) - PREVIEW_TARGET_BYTES) < abs(size - PREVIEW_TARGET_BYTES):
                im = im2
        return im

    def _show_preview(self, pil: Image.Image) -> None:
        self.preview_pil = pil
        self.crop_overlay.set_image(pil)

    def _refresh_stats(self) -> None:
        ts = self.ts
        if ts is None:
            return
        self.stat_labels["tiles"].configure(text=str(ts.count))
        self.stat_labels["input_res"].configure(text=f"{ts.full_width} × {ts.full_height}")
        self.stat_labels["grid"].configure(text=f"{ts.cols} × {ts.rows}")
        self.stat_labels["holes"].configure(text=str(ts.holes))
        self._recompute()

    def _recompute(self) -> None:
        ts = self.ts
        if ts is None:
            return
        level = int(self.level_var.get())
        cal = self.cal
        region_w, region_h = core.region_size(ts, self.crop_box)
        if self.mode_var.get() == "resolution":
            scale = min(1.0, max(0.01, self.scale_var.get()))
            out_w = max(1, round(region_w * scale))
            out_h = max(1, round(region_h * scale))
            self.stat_labels["output_res"].configure(text=f"{out_w} × {out_h}")
            self.stat_labels["scale"].configure(text=f"{scale*100:.1f}%")
            if cal is not None:
                est = cal.estimate_bytes(out_w * out_h, level)
                self.stat_labels["est_size"].configure(text=f"{self._fmt_bytes(est)} (预估,可能偏大)")
            else:
                self.stat_labels["est_size"].configure(text="-")
            self.stat_labels["actual_size"].configure(text="-")
        else:  # by file size: show the *estimated* resolution for the target
            target_text = self.target_mb_var.get().strip()
            if not target_text:
                # unlimited -> original resolution
                self.stat_labels["output_res"].configure(text=f"{region_w} × {region_h}")
                self.stat_labels["scale"].configure(text="100%")
                self.stat_labels["est_size"].configure(text="不限制(原始分辨率)")
                self.stat_labels["actual_size"].configure(text="-")
                return
            try:
                target_b = float(target_text) * 1048576
                if target_b <= 0:
                    raise ValueError
            except ValueError:
                self.stat_labels["output_res"].configure(text="-")
                self.stat_labels["scale"].configure(text="-")
                self.stat_labels["est_size"].configure(text="目标大小无效")
                return
            if cal is not None:
                est_full = cal.estimate_bytes(region_w * region_h, level)
                if est_full > 0:
                    scale = min(1.0, math.sqrt(target_b / est_full))
                    out_w = max(1, round(region_w * scale))
                    out_h = max(1, round(region_h * scale))
                    self.stat_labels["output_res"].configure(text=f"{out_w} × {out_h}(预估)")
                    self.stat_labels["scale"].configure(text=f"{scale*100:.1f}%(预估)")
                    self.stat_labels["est_size"].configure(text=f"目标 {self.target_mb_var.get()} MB")
                else:
                    self.stat_labels["output_res"].configure(text="-")
            else:
                self.stat_labels["output_res"].configure(text="-")
                self.stat_labels["scale"].configure(text="-")
            self.stat_labels["actual_size"].configure(text="-")

    def _set_progress_indet(self, active: bool, text: str = "") -> None:
        if active:
            self.progress.configure(mode="indeterminate")
            self.progress.start()
            if text:
                self.status_var.set(text)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(0)

    def _emit_progress(self, current: int, total: int, stage: str) -> None:
        if stage == "build" and total:
            frac = 0.05 + 0.85 * (current / total)
        elif stage == "encode":
            frac = 0.90
        else:
            frac = 0.95
        self._emit({"type": "progress", "fraction": min(0.99, frac)})

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        mb = n / 1048576
        if mb >= 1024:
            return f"{mb/1024:.2f} GB"
        return f"{mb:.1f} MB"

    def _start_worker(self, fn, *args) -> None:
        self.worker = threading.Thread(target=self._run_worker_safe, args=(fn, args), daemon=True)
        self.worker.start()

    def _run_worker_safe(self, fn, args) -> None:
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 - surface any worker error to the UI
            self._emit({"type": "error", "text": str(exc)})

    def _emit(self, msg: dict) -> None:
        self.queue.put(msg)

    # --------------------------------------------------------- main loop

    def _poll(self) -> None:
        try:
            while True:
                self._apply(self.queue.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _apply(self, msg: dict) -> None:
        t = msg["type"]
        if t == "tileset":
            self.ts = msg["ts"]
            self.width_var.set(str(self.ts.full_width))
            self.scale_var.set(1.0)
            self._set_progress_indet(False)
            self._refresh_stats()
        elif t == "cal":
            self.cal = msg["cal"]
            self._refresh_stats()
        elif t == "preview":
            self.preview_pil = msg["image"]
            self._show_preview(msg["image"])
            self.status_var.set("预览已生成")
        elif t == "stats_actual":
            self.stat_labels["output_res"].configure(text=f"{msg['w']} × {msg['h']}")
            if self.ts is not None:
                region_w, _ = core.region_size(self.ts, self.crop_box)
                self.stat_labels["scale"].configure(
                    text=f"{min(1.0, msg['w']/region_w)*100:.1f}%"
                )
            self.stat_labels["est_size"].configure(text="-")
            self.stat_labels["actual_size"].configure(text=self._fmt_bytes(msg["bytes"]))
        elif t == "status":
            self.status_var.set(msg["text"])
        elif t == "progress":
            self.progress.set(msg["fraction"])
        elif t == "result":
            self._set_progress_indet(False)
            self.btn_run.configure(state="normal")
            self.btn_preview.configure(state="normal")
            self.btn_crop.configure(state="normal")
            self.btn_cancel.configure(state="disabled")
            messagebox.showinfo(APP_TITLE, msg["text"])
        elif t == "done":
            self._set_progress_indet(False)
            self.btn_run.configure(state="normal")
            self.btn_preview.configure(state="normal")
            self.btn_crop.configure(state="normal")
            self.btn_cancel.configure(state="disabled")
        elif t == "error":
            self._set_progress_indet(False)
            self.btn_run.configure(state="normal")
            self.btn_preview.configure(state="normal")
            self.btn_crop.configure(state="normal")
            self.btn_cancel.configure(state="disabled")
            messagebox.showerror(APP_TITLE, msg["text"])


def main() -> None:
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    app = StitcherApp()
    app.mainloop()


if __name__ == "__main__":
    main()
