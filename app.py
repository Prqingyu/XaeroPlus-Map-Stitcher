#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry point for the packaged EXE.

Launches the XaeroPlus Map Stitcher GUI. (The CLI remains available through
``python stitch.py``.)
"""
from __future__ import annotations


def main() -> None:
    import stitcher_gui
    stitcher_gui.main()


if __name__ == "__main__":
    main()
