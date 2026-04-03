#!/usr/bin/env python3
"""
=============================================================================
  GoldSense  v1.3.0  --  Merchant Inventory Inspector
  Self-contained, self-hosted, single-file application.
=============================================================================
  HOW IT WORKS
  ------------
  1.  Open the merchant's trade window in-game.
  2.  Run this script. An overlay window appears.
  3.  Click "Calibrate" and drag a rectangle over the ENTIRE merchant
      grid -- top-left corner to bottom-right corner.
      Cell size is derived automatically from the drag area + grid counts.
  4.  Press BEGIN (or F6) to start scanning the inventory.
  5.  Before hovering, GoldSense captures a screenshot of the full grid
      and detects which cells are occupied via pixel-colour hashing.
      Contiguous same-coloured regions are grouped into item "blobs".
      Each unique blob is visited ONCE at its centroid -- multi-tile
      items are only read a single time, not once per cell.
  6.  For each unique blob:
        a) Move cursor to blob centroid -> wait -> capture tooltip.
           The capture region is centred around the cursor, not a fixed
           offset, and adjusts automatically near screen edges.
        b) Quick scan for gold-find text.
        c) If found -> hold ALT -> capture comparison tooltip.
        d) Parse BOTH the shelf item AND the currently worn item.
        e) Pause when: shelf_flat_gf >= worn_flat_gf
                       OR worn slot is on the PASS LIST
                       OR item has BOTH flat + % gold find (DUAL -- rare).
  7.  When a qualifying item is spotted:
        - Cursor stays on that blob.
        - Overlay shows detected values.
        - Inspector PAUSES and waits for your decision.
  8.  Buy (Shift+Click yourself) or pass.
  9.  Press NEXT (overlay button or F7) to resume.
  10. After all blobs are checked the inspector presses R to restock
      and loops from step 5.

  PASS LIST
  ---------
  Items on the pass list bypass the worn-item comparison entirely.
  Useful for slots you intend to keep regardless of stats.

  KEY CONTROLS (global hotkeys)
  F6   --  Begin / Halt toggle
  F7   --  Next / Pass after a hit
  F8   --  Hold / Resume
  ESC  --  Immediate halt

  SAFETY
  ------
  The inspector NEVER Shift+Clicks.  All purchases are done manually by you.

  SETUP
  -----
  Run INSTALL.bat first, then option 2 to launch.
=============================================================================
"""

import sys
import os
import re
import time
import json
import queue
import logging
import threading
import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Set

# ---------------------------------------------------------------------------
#  Dependency check
# ---------------------------------------------------------------------------
_missing: List[str] = []
try:
    from PIL import Image, ImageGrab
except ImportError:
    _missing.append("Pillow>=10.3.0")
try:
    import numpy as np
except ImportError:
    _missing.append("numpy>=1.26.0")
try:
    import keyboard
except ImportError:
    _missing.append("keyboard>=0.13.5")
try:
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE    = 0.0
except ImportError:
    _missing.append("pyautogui>=0.9.54")
try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    _missing.append("rapidocr-onnxruntime>=1.3.22")
try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox
except ImportError:
    _missing.append("tkinter (built-in -- check Python installation)")

if _missing:
    print("=" * 60)
    print("  MISSING PACKAGES -- run INSTALL.bat first!")
    print("=" * 60)
    for p in _missing:
        print(f"    pip install {p}")
    print()
    input("Press ENTER to exit...")
    sys.exit(1)


# ---------------------------------------------------------------------------
#  CONFIG
# ---------------------------------------------------------------------------

class Config:
    APP_NAME    = "GoldSense"
    APP_VERSION = "1.3.1"  # bumped for logging hardening

    # Shelf grid geometry (pixels) -- overwritten by calibration
    SHELF_ORIGIN_X = 40
    SHELF_ORIGIN_Y = 108
    CELL_W         = 29
    CELL_H         = 29
    SHELF_COLS     = 10
    SHELF_ROWS     = 14

    # Tooltip capture -- cursor-local, screen-edge aware
    HOVER_DELAY_MS = 180
    ALT_DELAY_MS   = 220
    TOOLTIP_W      = 450      # capture width  (px)
    TOOLTIP_H      = 350      # capture height (px)
    # Preferred anchor: tooltip appears above-left of cursor in TH4.
    # The capture box is positioned so the cursor sits near its
    # bottom-right quadrant, giving maximum room for the tooltip text.
    TOOLTIP_ANCHOR_X = 380    # cursor offset from left edge of capture box
    TOOLTIP_ANCHOR_Y = 290    # cursor offset from top  edge of capture box

    # Blob detection (unique-item deduplication)
    # Cells whose centre pixel hash matches an already-seen hash are skipped.
    # EMPTY_LUMA_THRESH: cells darker than this are treated as empty slots.
    EMPTY_LUMA_THRESH  = 22   # 0-255; increase if dark items are being skipped
    BLOB_SAMPLE_RADIUS = 3    # px radius around cell centre sampled for hashing

    # Gold-find detection patterns
    FLAT_GF_PATTERNS = [
        r"\+\s*(\d+)(?:\s*-\s*\d+)?\s*(?:to\s+)?gold\s*find",
        r"\+\s*(\d+)(?:\s*-\s*\d+)?\s*gold\s+found",
        r"gold\s*find\s*[:\+]\s*(\d+)",
        r"\+\s*(\d+)\s*(?:to\s+)?(?:extra\s+)?gold",
    ]
    PCT_GF_PATTERNS = [
        r"\+\s*(\d+)\s*%\s*(?:to\s+)?gold\s*find",
        r"\+\s*(\d+)\s*%\s*gold\s+found",
        r"(\d+)\s*%\s*(?:extra\s+)?gold\s*find",
        r"(\d+)\s*%\s*(?:to\s+)?gold\s*found",
    ]

    MIN_GOLD_FIND = 1

    # Comparison tooltip split markers
    WORN_MARKERS  = ["equipped", "currently equipped", "worn", "eq:"]
    SHELF_MARKERS = ["selected", "shop item", "buy price", "shift click to buy"]

    # Pass list -- partial name match, case-insensitive
    PASS_LIST: List[str] = [
        "ring of celestial castles",
        "ring of the sun",
    ]

    # Restock
    RESTOCK_KEY      = "r"
    RESTOCK_DELAY_MS = 600

    # Hotkeys
    HOTKEY_BEGIN_HALT = "f6"
    HOTKEY_NEXT       = "f7"
    HOTKEY_HOLD       = "f8"
    HOTKEY_HALT       = "escape"

    # Paths
    LOG_DIR    = Path(__file__).resolve().parent.parent / "logs"
    PREFS_FILE = Path(__file__).resolve().parent.parent / "_tools" / "prefs.json"

    # Overlay
    OVERLAY_X     = 20
    OVERLAY_Y     = 20
    OVERLAY_ALPHA = 0.90


# ---------------------------------------------------------------------------
#  STATE PERSISTENCE
# ---------------------------------------------------------------------------

def _load_prefs() -> None:
    try:
        if Config.PREFS_FILE.exists():
            with open(Config.PREFS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key in ("SHELF_ORIGIN_X", "SHELF_ORIGIN_Y", "CELL_W", "CELL_H",
                        "SHELF_COLS", "SHELF_ROWS", "HOVER_DELAY_MS",
                        "ALT_DELAY_MS", "MIN_GOLD_FIND", "PASS_LIST",
                        "EMPTY_LUMA_THRESH", "BLOB_SAMPLE_RADIUS",
                        "TOOLTIP_W", "TOOLTIP_H",
                        "TOOLTIP_ANCHOR_X", "TOOLTIP_ANCHOR_Y"):
                if key in data:
                    setattr(Config, key, data[key])
    except Exception as exc:
        logging.getLogger("goldsense").warning(
            "Failed to load prefs from %s: %s", Config.PREFS_FILE, exc, exc_info=True
        )


def _save_prefs() -> None:
    try:
        Config.PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {k: getattr(Config, k) for k in
                ("SHELF_ORIGIN_X", "SHELF_ORIGIN_Y", "CELL_W", "CELL_H",
                 "SHELF_COLS", "SHELF_ROWS", "HOVER_DELAY_MS",
                 "ALT_DELAY_MS", "MIN_GOLD_FIND", "PASS_LIST",
                 "EMPTY_LUMA_THRESH", "BLOB_SAMPLE_RADIUS",
                 "TOOLTIP_W", "TOOLTIP_H",
                 "TOOLTIP_ANCHOR_X", "TOOLTIP_ANCHOR_Y")}
        with open(Config.PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        logging.getLogger("goldsense").warning(
            "Failed to save prefs to %s: %s", Config.PREFS_FILE, exc, exc_info=True
        )


# ---------------------------------------------------------------------------
#  LOGGING
# ---------------------------------------------------------------------------

Config.LOG_DIR.mkdir(parents=True, exist_ok=True)
_run_ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
_log_file = Config.LOG_DIR / f"session_{_run_ts}.txt"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(_log_file), encoding="utf-8"),
    ],
)
log = logging.getLogger("goldsense")
log.info(f"{Config.APP_NAME} {Config.APP_VERSION} starting -- log: {_log_file}")


def _install_global_exception_hooks() -> None:
    """Route all uncaught exceptions into the session log.

    This catches main-thread errors, worker-thread errors (where supported),
    and makes debugging "silent" crashes much easier.
    """

    def _handle_uncaught(exc_type, exc_value, exc_tb):
        log.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _handle_uncaught

    # Python 3.8+ provides threading.excepthook for worker threads.
    try:
        def _thread_excepthook(args):
            log.critical(
                "Uncaught thread exception in %s", getattr(args, "thread", None),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        threading.excepthook = _thread_excepthook  # type: ignore[attr-defined]
    except Exception as exc:
        log.warning("Failed to install threading.excepthook: %s", exc, exc_info=True)


_install_global_exception_hooks()
_load_prefs()


# ---------------------------------------------------------------------------
#  DATA CLASSES
# ---------------------------------------------------------------------------

@dataclass
class ParsedItem:
    name:     str = ""
    flat_gf:  int = 0
    pct_gf:   int = 0
    raw_text: str = ""


@dataclass
class ItemBlob:
    """
    Represents a unique item detected in the grid.

    cells   -- all (col, row) pairs belonging to this blob
    cx, cy  -- screen pixel centroid (where cursor is moved to)
    phash   -- the pixel-colour hash that identifies this blob
    """
    cells:  List[Tuple[int, int]]
    cx:     int
    cy:     int
    phash:  int

    @property
    def col(self) -> int:
        return min(c for c, _ in self.cells)

    @property
    def row(self) -> int:
        return min(r for _, r in self.cells)

    def label(self) -> str:
        c0, r0 = self.cells[0]
        if len(self.cells) == 1:
            return f"[{c0},{r0}]"
        return f"[{c0},{r0}]+{len(self.cells) - 1}"


@dataclass
class GoldHit:
    blob:         ItemBlob
    shelf_item:   ParsedItem
    worn_item:    Optional[ParsedItem]
    is_dual_gf:   bool
    timestamp:    str = field(
        default_factory=lambda: datetime.datetime.now().isoformat())
    capture_path: Optional[str] = None

    # back-compat shims used by OverlayWindow
    @property
    def col(self) -> int:
        return self.blob.col

    @property
    def row(self) -> int:
        return self.blob.row

    @property
    def gold_value(self) -> int:
        return self.shelf_item.flat_gf


@dataclass
class WalkStats:
    lap_number:    int   = 0
    items_visited: int   = 0     # unique blobs visited (was cells_visited)
    blobs_found:   int   = 0     # blobs detected in last grid snapshot
    hits:          int   = 0
    dual_hits:     int   = 0
    best_flat:     int   = 0
    best_pct:      int   = 0
    start_time:    float = field(default_factory=time.time)

    def elapsed_str(self) -> str:
        e = int(time.time() - self.start_time)
        return f"{e // 60:02d}:{e % 60:02d}"


# ---------------------------------------------------------------------------
#  SCROLL READER  (OCR)
# ---------------------------------------------------------------------------

class ScrollReader:
    def __init__(self) -> None:
        self._rapid = None
        try:
            self._rapid = RapidOCR()
            log.info("ScrollReader (RapidOCR) initialised OK.")
        except Exception as exc:
            log.warning(f"RapidOCR init failed: {exc}. Falling back to WinRT.")

    def read(self, img: "Image.Image") -> str:
        if self._rapid:
            return self._read_rapid(img)
        return self._read_winrt(img)

    def _read_rapid(self, img: "Image.Image") -> str:
        arr = np.array(img.convert("RGB"))
        try:
            result, _ = self._rapid(arr)
            if not result:
                return ""
            return "\n".join(r[1] for r in result if r and len(r) >= 2)
        except Exception as exc:
            log.debug(f"ScrollReader read error: {exc}")
            return ""

    def _read_winrt(self, img: "Image.Image") -> str:
        try:
            import asyncio
            import winocr
            loop = asyncio.new_event_loop()
            res  = loop.run_until_complete(winocr.recognize_pil(img, "en"))
            loop.close()
            return res.text if res else ""
        except Exception as exc:
            log.debug(f"WinRT fallback error: {exc}")
            return ""


# ---------------------------------------------------------------------------
#  SCREEN CAPTURE
# ---------------------------------------------------------------------------

class ScreenCapture:
    """All screen-grabbing lives here."""

    def grab_region(self, x: int, y: int, w: int, h: int) -> "Image.Image":
        try:
            return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        except Exception:
            return pyautogui.screenshot(region=(x, y, w, h))

    def grab_grid(self) -> "Image.Image":
        """Capture the full configured shelf grid area."""
        gw = Config.CELL_W * Config.SHELF_COLS
        gh = Config.CELL_H * Config.SHELF_ROWS
        return self.grab_region(
            Config.SHELF_ORIGIN_X, Config.SHELF_ORIGIN_Y, gw, gh
        )

    def grab_tooltip(self, cursor_x: int, cursor_y: int) -> "Image.Image":
        """
        Capture a tooltip-sized region anchored relative to the cursor.

        The anchor point is configured via TOOLTIP_ANCHOR_X / TOOLTIP_ANCHOR_Y:
        those values express where the cursor sits *within* the capture box.
        This puts the majority of the box above-left of the cursor, matching
        where TH4 draws its tooltips.

        The box is clamped to the virtual screen rectangle so it never goes
        off-screen (handles cursors near edges).
        """
        sw = pyautogui.size().width
        sh = pyautogui.size().height
        tw = Config.TOOLTIP_W
        th = Config.TOOLTIP_H
        ax = Config.TOOLTIP_ANCHOR_X
        ay = Config.TOOLTIP_ANCHOR_Y

        # Ideal top-left of capture box
        ideal_x = cursor_x - ax
        ideal_y = cursor_y - ay

        # Clamp to screen bounds
        x0 = max(0, min(ideal_x, sw - tw))
        y0 = max(0, min(ideal_y, sh - th))

        log.debug(
            f"grab_tooltip: cursor=({cursor_x},{cursor_y})"
            f" box=({x0},{y0},{tw},{th})"
        )
        return self.grab_region(x0, y0, tw, th)


# ---------------------------------------------------------------------------
#  GRID BLOB DETECTOR
#
#  Takes a screenshot of the full shelf grid and returns a list of
#  ItemBlob objects -- one per unique item, regardless of how many cells
#  it occupies.
#
#  Algorithm
#  ---------
#  1. For each cell (col, row), sample a small patch around the cell centre.
#  2. Compute a compact hash of the patch (mean RGB quantised to 4 levels).
#  3. Empty cells (low overall luma) are skipped.
#  4. Group adjacent cells that share the same hash via flood-fill.
#  5. Return each group as one ItemBlob whose cursor target is the
#     centroid of all constituent cells.
# ---------------------------------------------------------------------------

class GridBlobDetector:
    """
    Detects unique item blobs in a grid screenshot.

    Returns a list of ItemBlob, ordered top-left to bottom-right
    (row-major), with duplicates removed.
    """

    # Hash quantisation: each RGB channel is binned into this many levels.
    # 6 levels gives a 3-byte hash (6^3 = 216 values) -- coarse enough to
    # treat one item as the same across its cells, fine enough to tell
    # most different items apart.
    _QUANT = 6

    def detect(self, grid_img: "Image.Image") -> List[ItemBlob]:
        """
        grid_img -- PIL image of exactly the configured grid area
                    (width = CELL_W * SHELF_COLS, height = CELL_H * SHELF_ROWS)
        Returns list of ItemBlob sorted by (row, col).
        """
        arr = np.array(grid_img.convert("RGB"), dtype=np.uint16)
        cw  = Config.CELL_W
        ch  = Config.CELL_H
        cols = Config.SHELF_COLS
        rows = Config.SHELF_ROWS
        r    = max(1, Config.BLOB_SAMPLE_RADIUS)

        # Build hash map: (col, row) -> hash_int | None
        cell_hash: dict = {}
        for row in range(rows):
            for col in range(cols):
                # Centre of this cell in grid-image coordinates
                px = col * cw + cw // 2
                py = row * ch + ch // 2

                # Sample patch (clamped to image)
                x0 = max(0, px - r)
                y0 = max(0, py - r)
                x1 = min(arr.shape[1], px + r + 1)
                y1 = min(arr.shape[0], py + r + 1)
                patch = arr[y0:y1, x0:x1]   # shape (H, W, 3)

                # Mean luma -- skip empty / very dark cells
                mean_rgb = patch.mean(axis=(0, 1))   # (R, G, B)
                luma = 0.299 * mean_rgb[0] + 0.587 * mean_rgb[1] + 0.114 * mean_rgb[2]
                if luma < Config.EMPTY_LUMA_THRESH:
                    log.debug(f"  Blob detect [{col},{row}]: luma={luma:.1f} -- EMPTY")
                    cell_hash[(col, row)] = None
                    continue

                # Quantise each channel to _QUANT levels, pack into int
                q   = self._QUANT
                rq  = int(mean_rgb[0] * (q - 1) / 255)
                gq  = int(mean_rgb[1] * (q - 1) / 255)
                bq  = int(mean_rgb[2] * (q - 1) / 255)
                h   = rq * q * q + gq * q + bq
                cell_hash[(col, row)] = h
                log.debug(
                    f"  Blob detect [{col},{row}]: luma={luma:.1f}"
                    f" rgb=({mean_rgb[0]:.0f},{mean_rgb[1]:.0f},{mean_rgb[2]:.0f})"
                    f" hash={h}"
                )

        # Flood-fill: group adjacent cells with identical non-None hashes
        visited: Set[Tuple[int, int]] = set()
        blobs: List[ItemBlob] = []

        for row in range(rows):
            for col in range(cols):
                if (col, row) in visited:
                    continue
                h = cell_hash.get((col, row))
                if h is None:
                    visited.add((col, row))
                    continue

                # BFS flood-fill
                group: List[Tuple[int, int]] = []
                queue_bfs = [(col, row)]
                while queue_bfs:
                    cc, rr = queue_bfs.pop()
                    if (cc, rr) in visited:
                        continue
                    if cell_hash.get((cc, rr)) != h:
                        continue
                    visited.add((cc, rr))
                    group.append((cc, rr))
                    for nc, nr in ((cc+1,rr),(cc-1,rr),(cc,rr+1),(cc,rr-1)):
                        if 0 <= nc < cols and 0 <= nr < rows:
                            queue_bfs.append((nc, nr))

                if not group:
                    continue

                # Centroid in screen coordinates
                ox = Config.SHELF_ORIGIN_X
                oy = Config.SHELF_ORIGIN_Y
                mean_col = sum(c for c, _ in group) / len(group)
                mean_row = sum(r for _, r in group) / len(group)
                scx = int(ox + mean_col * cw + cw // 2)
                scy = int(oy + mean_row * ch + ch // 2)

                blobs.append(ItemBlob(
                    cells=sorted(group),
                    cx=scx, cy=scy,
                    phash=h,
                ))
                log.debug(
                    f"  Blob: hash={h} cells={group} centroid=({scx},{scy})"
                )

        # Sort top-left to bottom-right (row-major by first cell)
        blobs.sort(key=lambda b: (b.cells[0][1], b.cells[0][0]))
        log.info(
            f"GridBlobDetector: {len(blobs)} unique blobs detected"
            f" ({rows * cols} cells total)"
        )
        return blobs


# ---------------------------------------------------------------------------
#  ITEM PARSER
# ---------------------------------------------------------------------------

_FLAT_RE = [re.compile(p, re.IGNORECASE) for p in Config.FLAT_GF_PATTERNS]
_PCT_RE  = [re.compile(p, re.IGNORECASE) for p in Config.PCT_GF_PATTERNS]


def _best_match(patterns: list, text: str) -> int:
    best = 0
    for pat in patterns:
        for m in pat.finditer(text):
            try:
                val     = int(m.group(1))
                tail    = text[m.end(1):m.end(1) + 10]
                range_m = re.search(r"-\s*(\d+)", tail)
                if range_m:
                    val = int(range_m.group(1))
                if val > best:
                    best = val
            except (IndexError, ValueError):
                pass
    return best


def _extract_name(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not re.match(r"^[\d\+\-%\s]+$", line):
            return line[:60]
    return ""


def parse_item_text(text: str) -> ParsedItem:
    flat = _best_match(_FLAT_RE, text)
    pct  = _best_match(_PCT_RE, text)
    name = _extract_name(text)
    return ParsedItem(name=name, flat_gf=flat, pct_gf=pct, raw_text=text)


# ---------------------------------------------------------------------------
#  COMPARISON TOOLTIP SPLITTER
# ---------------------------------------------------------------------------

def split_comparison_tooltip(full_text: str) -> Tuple[str, str]:
    """Split ALT comparison tooltip into (shelf_text, worn_text)."""
    text_lower = full_text.lower()
    sel_pos = eq_pos = -1
    for m in Config.SHELF_MARKERS:
        idx = text_lower.find(m)
        if idx != -1:
            sel_pos = idx
            break
    for m in Config.WORN_MARKERS:
        idx = text_lower.find(m)
        if idx != -1:
            eq_pos = idx
            break
    if sel_pos != -1 and eq_pos != -1:
        if sel_pos < eq_pos:
            return full_text[sel_pos:eq_pos], full_text[eq_pos:]
        return full_text[sel_pos:], full_text[eq_pos:sel_pos]
    if eq_pos != -1:
        return full_text[:eq_pos], full_text[eq_pos:]
    lines = full_text.splitlines()
    mid   = max(1, len(lines) // 2)
    log.debug("split_comparison: line-count fallback")
    return "\n".join(lines[:mid]), "\n".join(lines[mid:])


def is_passed(item: ParsedItem) -> bool:
    name_lower = item.name.lower()
    for entry in Config.PASS_LIST:
        if entry.lower() in name_lower:
            log.debug(f"  Pass-list hit: '{item.name}' matches '{entry}'")
            return True
    return False


# ---------------------------------------------------------------------------
#  INVENTORY INSPECTOR  (core walk loop)
# ---------------------------------------------------------------------------

class InventoryInspector:
    def __init__(
        self,
        reader:   ScrollReader,
        capture:  ScreenCapture,
        detector: GridBlobDetector,
    ) -> None:
        self._reader   = reader
        self._capture  = capture
        self._detector = detector
        self._active   = False
        self._held     = False
        self._next_ev  = threading.Event()
        self._halt_ev  = threading.Event()

        self.on_hit:     Optional[callable] = None
        self.on_blob:    Optional[callable] = None   # (blob, text) -- replaces on_cell
        self.on_restock: Optional[callable] = None
        self.on_status:  Optional[callable] = None
        self.on_halted:  Optional[callable] = None

        self.stats = WalkStats()

    # --- public API --------------------------------------------------------

    def begin(self) -> None:
        if self._active:
            return
        self._active  = True
        self._halt_ev.clear()
        self._next_ev.clear()
        self.stats    = WalkStats()
        threading.Thread(target=self._walk_loop, daemon=True).start()
        log.info("InventoryInspector: scan started.")

    def halt(self) -> None:
        self._active = False
        self._halt_ev.set()
        self._next_ev.set()
        log.info("InventoryInspector: halt requested.")

    def hold_toggle(self) -> None:
        self._held = not self._held
        if self._held:
            log.info("Held.")
            self._status("Held -- press F8 to resume.")
        else:
            log.info("Resumed.")
            self._next_ev.set()

    def user_next(self) -> None:
        log.info("User: Next.")
        self._next_ev.set()

    # --- internals ---------------------------------------------------------

    def _status(self, msg: str) -> None:
        log.debug(f"STATUS: {msg}")
        if self.on_status:
            self.on_status(msg)

    def _wait_if_held(self) -> None:
        while self._held and self._active:
            time.sleep(0.1)

    def _walk_loop(self) -> None:
        try:
            while self._active:
                self.stats.lap_number += 1
                log.info(f"=== Scan lap #{self.stats.lap_number} ===")
                if self.on_restock:
                    self.on_restock(self.stats.lap_number)
                self._inspect_blobs()
                if not self._active:
                    break
                self._do_restock()
        except Exception as exc:
            log.error(f"Inspector crashed: {exc}", exc_info=True)
        finally:
            self._active = False
            if self.on_halted:
                self.on_halted()

    def _inspect_blobs(self) -> None:
        """Capture grid -> detect blobs -> visit each unique blob once."""
        self._status("Scanning grid for items...")

        # Move cursor away from the grid before the snapshot so it
        # doesn't occlude cells or trigger a tooltip mid-capture.
        pyautogui.moveTo(
            Config.SHELF_ORIGIN_X - 60,
            Config.SHELF_ORIGIN_Y + (Config.CELL_H * Config.SHELF_ROWS) // 2,
            duration=0.0,
        )
        time.sleep(0.05)   # let any lingering tooltip dismiss

        grid_img = self._capture.grab_grid()
        ts_snap  = datetime.datetime.now().strftime("%H%M%S_%f")[:9]
        try:
            grid_img.save(str(Config.LOG_DIR / f"grid_{ts_snap}.png"))
        except Exception:
            pass

        blobs = self._detector.detect(grid_img)
        self.stats.blobs_found = len(blobs)
        self._status(f"Detected {len(blobs)} unique items -- scanning...")

        for blob in blobs:
            if not self._active or self._halt_ev.is_set():
                return
            self._wait_if_held()
            self._inspect_one_blob(blob)

    def _inspect_one_blob(self, blob: ItemBlob) -> None:
        """Visit a single blob: hover, read tooltip, apply hit logic."""
        log.debug(
            f"Blob {blob.label()} cursor=({blob.cx},{blob.cy})"
            f" cells={blob.cells}"
        )
        pyautogui.moveTo(blob.cx, blob.cy, duration=0.0)
        time.sleep(Config.HOVER_DELAY_MS / 1000.0)

        img_plain  = self._capture.grab_tooltip(blob.cx, blob.cy)
        text_plain = self._reader.read(img_plain)
        log.debug(
            f"  Plain read: {text_plain[:120].replace(chr(10), ' | ')}"
        )

        if self.on_blob:
            self.on_blob(blob, text_plain)
        self.stats.items_visited += 1

        if not re.search(r"gold", text_plain, re.IGNORECASE):
            return

        quick = parse_item_text(text_plain)
        if quick.flat_gf < Config.MIN_GOLD_FIND and quick.pct_gf == 0:
            return

        log.info(
            f"  Pre-filter hit {blob.label()}:"
            f" flat={quick.flat_gf} pct={quick.pct_gf}"
        )
        self._status(f"Item {blob.label()} -- reading comparison...")

        keyboard.press("alt")
        time.sleep(Config.ALT_DELAY_MS / 1000.0)
        img_alt  = self._capture.grab_tooltip(blob.cx, blob.cy)
        keyboard.release("alt")
        text_alt = self._reader.read(img_alt)
        log.debug(
            f"  Compare read: {text_alt[:200].replace(chr(10), ' | ')}"
        )

        shelf_raw, worn_raw = split_comparison_tooltip(text_alt)
        shelf_item = parse_item_text(shelf_raw)
        worn_item  = parse_item_text(worn_raw)

        if shelf_item.flat_gf == 0 and shelf_item.pct_gf == 0:
            shelf_item = quick
            log.debug("  Compare read empty -- using plain text.")

        log.info(
            f"  Shelf: flat={shelf_item.flat_gf}"
            f"  pct={shelf_item.pct_gf}"
            f"  name='{shelf_item.name}'"
        )
        log.info(
            f"  Worn:  flat={worn_item.flat_gf}"
            f"  pct={worn_item.pct_gf}"
            f"  name='{worn_item.name}'"
        )

        is_dual = (
            shelf_item.flat_gf >= Config.MIN_GOLD_FIND
            and shelf_item.pct_gf > 0
        )
        should_pause, reason = self._should_pause(
            shelf_item, worn_item, is_dual
        )

        if not should_pause:
            log.info(f"  Passed: {reason}")
            self._status(f"Item {blob.label()} passed: {reason}")
            return

        log.info(f"  *** HIT {blob.label()}: {reason}")
        self.stats.hits += 1
        if is_dual:
            self.stats.dual_hits += 1
        if shelf_item.flat_gf > self.stats.best_flat:
            self.stats.best_flat = shelf_item.flat_gf
        if shelf_item.pct_gf > self.stats.best_pct:
            self.stats.best_pct = shelf_item.pct_gf

        ts   = datetime.datetime.now().strftime("%H%M%S_%f")[:9]
        path = str(
            Config.LOG_DIR
            / f"hit_{ts}_c{blob.col}_r{blob.row}.png"
        )
        try:
            img_alt.save(path)
        except Exception:
            path = None

        hit = GoldHit(
            blob=blob,
            shelf_item=shelf_item,
            worn_item=worn_item,
            is_dual_gf=is_dual,
            capture_path=path,
        )
        if self.on_hit:
            self.on_hit(hit)

        self._next_ev.clear()
        self._status(f"WAITING -- {reason}")
        self._next_ev.wait()
        self._next_ev.clear()

    def _should_pause(
        self,
        shelf:   ParsedItem,
        worn:    ParsedItem,
        is_dual: bool,
    ) -> Tuple[bool, str]:
        if shelf.flat_gf < Config.MIN_GOLD_FIND and not is_dual:
            return (
                False,
                f"flat GF {shelf.flat_gf} below minimum {Config.MIN_GOLD_FIND}",
            )

        if is_dual:
            return (
                True,
                (
                    f"DUAL GF! +{shelf.flat_gf} flat AND"
                    f" +{shelf.pct_gf}% ('{shelf.name}') -- confirm before passing"
                ),
            )

        worn_passed = is_passed(worn)
        worn_has_gf = worn.flat_gf >= Config.MIN_GOLD_FIND

        if worn_passed or not worn_has_gf:
            return (
                True,
                (
                    f"worn slot on pass list / unset -- shelf +{shelf.flat_gf}"
                    f" meets minimum ('{shelf.name}')"
                ),
            )

        if shelf.flat_gf >= worn.flat_gf:
            return (
                True,
                (
                    f"shelf +{shelf.flat_gf} >= worn +{worn.flat_gf}"
                    f" ('{shelf.name}' vs '{worn.name}')"
                ),
            )

        return (
            False,
            f"shelf +{shelf.flat_gf} < worn +{worn.flat_gf} -- pass",
        )

    def _do_restock(self) -> None:
        log.info("Restocking shelf...")
        self._status("Restocking shelf...")
        keyboard.press_and_release(Config.RESTOCK_KEY)
        time.sleep(Config.RESTOCK_DELAY_MS / 1000.0)


# ---------------------------------------------------------------------------
#  DRAG-SELECT CALIBRATION OVERLAY
# ---------------------------------------------------------------------------

class DragSelectOverlay:
    """
    Full-screen transparent drag-select canvas.
    Calls callback(x, y, w, h) on successful drag, then destroys itself.
    """

    FILL_COLOR   = "#00aaff"
    BORDER_COLOR = "#00aaff"
    BORDER_WIDTH = 2
    LABEL_COLOR  = "#ffffff"

    def __init__(
        self,
        parent: tk.Tk,
        cols:   int,
        rows:   int,
        on_done,
    ) -> None:
        self._parent  = parent
        self._cols    = cols
        self._rows    = rows
        self._on_done = on_done
        self._start_x = self._start_y = 0
        self._rect_id = self._label_id = None

        self._win = tk.Toplevel(parent)
        self._win.attributes("-fullscreen", True)
        self._win.attributes("-topmost",    True)
        self._win.attributes("-alpha",      0.35)
        self._win.configure(bg="black")
        self._win.overrideredirect(True)

        self._canvas = tk.Canvas(
            self._win, cursor="crosshair", bg="black", highlightthickness=0,
        )
        self._canvas.pack(fill="both", expand=True)
        self._canvas.create_text(
            self._canvas.winfo_screenwidth() // 2, 40,
            text=(
                "Drag to mark the ENTIRE merchant grid\n"
                "(top-left corner --> bottom-right corner)\n"
                "Release to confirm.  ESC to cancel."
            ),
            fill=self.LABEL_COLOR,
            font=("Segoe UI", 14, "bold"),
            justify="center",
        )
        self._canvas.bind("<ButtonPress-1>",   self._on_press)
        self._canvas.bind("<B1-Motion>",        self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._win.bind("<Escape>",             self._cancel)

    def _on_press(self, ev) -> None:
        self._start_x, self._start_y = ev.x, ev.y
        for tag in (self._rect_id, self._label_id):
            if tag:
                self._canvas.delete(tag)
        self._rect_id = self._label_id = None

    def _on_drag(self, ev) -> None:
        for tag in (self._rect_id, self._label_id):
            if tag:
                self._canvas.delete(tag)
        self._canvas.delete("grid_preview")
        x0, y0 = min(self._start_x, ev.x), min(self._start_y, ev.y)
        x1, y1 = max(self._start_x, ev.x), max(self._start_y, ev.y)
        w, h   = x1 - x0, y1 - y0
        self._rect_id = self._canvas.create_rectangle(
            x0, y0, x1, y1,
            outline=self.BORDER_COLOR, width=self.BORDER_WIDTH,
            fill=self.FILL_COLOR, stipple="gray25",
        )
        cw = max(1, w // self._cols)
        ch = max(1, h // self._rows)
        for c in range(1, self._cols):
            lx = x0 + c * cw
            self._canvas.create_line(lx, y0, lx, y1, fill="#ffffff", dash=(2,4), tags="grid_preview")
        for r in range(1, self._rows):
            ly = y0 + r * ch
            self._canvas.create_line(x0, ly, x1, ly, fill="#ffffff", dash=(2,4), tags="grid_preview")
        self._label_id = self._canvas.create_text(
            x0 + w // 2, y1 + 18,
            text=f"{w}x{h} px  |  cell {cw}x{ch} px",
            fill=self.LABEL_COLOR, font=("Courier New", 10),
        )

    def _on_release(self, ev) -> None:
        x0, y0 = min(self._start_x, ev.x), min(self._start_y, ev.y)
        x1, y1 = max(self._start_x, ev.x), max(self._start_y, ev.y)
        w, h   = x1 - x0, y1 - y0
        if w < 10 or h < 10:
            log.warning("DragSelectOverlay: drag too small -- ignored.")
            self._cancel(None)
            return
        win_x, win_y = self._win.winfo_rootx(), self._win.winfo_rooty()
        sx, sy = win_x + x0, win_y + y0
        log.info(f"DragSelectOverlay: screen=({sx},{sy}) size={w}x{h}")
        self._win.destroy()
        self._on_done(sx, sy, w, h)

    def _cancel(self, _ev) -> None:
        log.info("DragSelectOverlay: cancelled.")
        self._win.destroy()


# ---------------------------------------------------------------------------
#  CALIBRATION WINDOW
# ---------------------------------------------------------------------------

class CalibrationWindow:
    def __init__(self, parent: tk.Tk) -> None:
        self._parent = parent
        w = tk.Toplevel(parent)
        self._win = w
        w.title("Grid Calibration")
        w.configure(bg="#1a1a2e")
        w.geometry("520x620")
        w.attributes("-topmost", True)

        fg, muted, gold, bg, green = (
            "#e0e0e0", "#888888", "#f5a623", "#1a1a2e", "#00c48c"
        )

        tk.Label(w, text="Grid Calibration",
                 font=("Segoe UI", 13, "bold"), fg=gold, bg=bg).pack(pady=(10,2))

        instr = (
            "HOW TO CALIBRATE\n"
            "1.  Open the merchant's trade window in-game.\n"
            "2.  Set Columns and Rows below to match the grid.\n"
            "3.  Click  \"Mark Grid Area\"  -- screen dims.\n"
            "4.  Drag from TOP-LEFT of grid to BOTTOM-RIGHT.\n"
            "5.  Release -- cell size is calculated automatically.\n"
            "6.  Adjust delays and blob settings if needed, then Apply.\n\n"
            "TOOLTIP ANCHOR  (advanced)\n"
            "Anchor X/Y = where the cursor sits inside the capture box.\n"
            "Increase X to shift capture left; increase Y to shift it up.\n"
            "Default (380, 290) works well for 1920x1080."
        )
        tk.Label(w, text=instr, justify="left",
                 font=("Segoe UI", 9), fg=fg, bg=bg,
                 wraplength=480).pack(padx=12, pady=4)

        tk.Frame(w, bg="#333355", height=1).pack(fill="x", padx=12, pady=6)

        self._calib_var = tk.StringVar(value=self._calib_str())
        tk.Label(w, textvariable=self._calib_var,
                 font=("Courier New", 9), fg=gold, bg=bg,
                 justify="left").pack(padx=14, anchor="w")

        tk.Button(w, text="  Mark Grid Area  ",
                  command=self._start_drag,
                  bg=green, fg="white", relief="flat",
                  font=("Segoe UI", 10, "bold"), pady=5).pack(pady=8)

        tk.Frame(w, bg="#333355", height=1).pack(fill="x", padx=12, pady=4)

        self._spinboxes: dict = {}
        for attr, lbl_text, from_, to_, step in [
            ("SHELF_COLS",        "Columns:",               1,  20,    1),
            ("SHELF_ROWS",        "Rows:",                  1,  30,    1),
            ("MIN_GOLD_FIND",     "Min Gold Find value:",   1,  9999,  1),
            ("HOVER_DELAY_MS",    "Hover delay (ms):",     50,  2000, 10),
            ("ALT_DELAY_MS",      "Compare delay (ms):",   50,  2000, 10),
            ("TOOLTIP_W",         "Tooltip capture W:",   200,  1200, 10),
            ("TOOLTIP_H",         "Tooltip capture H:",   100,  1000, 10),
            ("TOOLTIP_ANCHOR_X",  "Tooltip anchor X:",      0,  1200,  5),
            ("TOOLTIP_ANCHOR_Y",  "Tooltip anchor Y:",      0,  1000,  5),
            ("EMPTY_LUMA_THRESH", "Empty luma threshold:",  0,   255,  1),
            ("BLOB_SAMPLE_RADIUS","Blob sample radius:",    1,    20,  1),
        ]:
            fr = tk.Frame(w, bg=bg)
            fr.pack(fill="x", padx=12, pady=1)
            tk.Label(fr, text=lbl_text, width=26, anchor="w",
                     fg=muted, bg=bg,
                     font=("Segoe UI", 9)).pack(side="left")
            sp = tk.Spinbox(fr, from_=from_, to=to_, width=6,
                            increment=step, bg="#0d0d1a", fg=gold,
                            insertbackground="white",
                            font=("Courier New", 10))
            sp.delete(0, "end")
            sp.insert(0, str(getattr(Config, attr)))
            sp.pack(side="left", padx=4)
            self._spinboxes[attr] = sp

        tk.Button(w, text="Apply & Close",
                  command=self._apply,
                  bg=green, fg="white", relief="flat",
                  font=("Segoe UI", 11, "bold"), pady=6).pack(pady=10)

    def _start_drag(self) -> None:
        try:
            cols = int(self._spinboxes["SHELF_COLS"].get())
            rows = int(self._spinboxes["SHELF_ROWS"].get())
        except ValueError:
            cols, rows = Config.SHELF_COLS, Config.SHELF_ROWS
        self._win.withdraw()
        self._parent.after(
            150,
            lambda: DragSelectOverlay(self._parent, cols, rows, self._on_drag_done),
        )

    def _on_drag_done(self, sx: int, sy: int, w: int, h: int) -> None:
        try:
            cols = int(self._spinboxes["SHELF_COLS"].get())
            rows = int(self._spinboxes["SHELF_ROWS"].get())
        except ValueError:
            cols, rows = Config.SHELF_COLS, Config.SHELF_ROWS
        Config.SHELF_ORIGIN_X = sx
        Config.SHELF_ORIGIN_Y = sy
        Config.CELL_W         = max(1, w // cols)
        Config.CELL_H         = max(1, h // rows)
        log.info(
            f"Calibration drag: origin=({sx},{sy})"
            f" area={w}x{h} cell={Config.CELL_W}x{Config.CELL_H}"
            f" grid={cols}x{rows}"
        )
        self._calib_var.set(self._calib_str())
        self._win.deiconify()

    def _calib_str(self) -> str:
        return (
            f"  Origin  : ({Config.SHELF_ORIGIN_X}, {Config.SHELF_ORIGIN_Y})\n"
            f"  Cell    : {Config.CELL_W} x {Config.CELL_H} px\n"
            f"  Grid    : {Config.SHELF_COLS} cols x {Config.SHELF_ROWS} rows\n"
            f"  Tooltip : {Config.TOOLTIP_W}x{Config.TOOLTIP_H} anchor"
            f" ({Config.TOOLTIP_ANCHOR_X},{Config.TOOLTIP_ANCHOR_Y})"
        )

    def _apply(self) -> None:
        for attr, sp in self._spinboxes.items():
            try:
                setattr(Config, attr, int(sp.get()))
            except ValueError:
                pass
        log.info(
            f"Calibration applied: origin=({Config.SHELF_ORIGIN_X},{Config.SHELF_ORIGIN_Y})"
            f" cell=({Config.CELL_W}x{Config.CELL_H})"
            f" grid={Config.SHELF_COLS}x{Config.SHELF_ROWS}"
            f" tooltip={Config.TOOLTIP_W}x{Config.TOOLTIP_H}"
            f" anchor=({Config.TOOLTIP_ANCHOR_X},{Config.TOOLTIP_ANCHOR_Y})"
            f" luma={Config.EMPTY_LUMA_THRESH}"
            f" blob_r={Config.BLOB_SAMPLE_RADIUS}"
        )
        _save_prefs()
        self._win.destroy()


# ---------------------------------------------------------------------------
#  PASS LIST WINDOW
# ---------------------------------------------------------------------------

class PassListWindow:
    def __init__(self, parent: tk.Tk) -> None:
        w = tk.Toplevel(parent)
        w.title("Pass List")
        w.configure(bg="#1a1a2e")
        w.geometry("430x510")
        w.attributes("-topmost", True)

        fg, muted, gold, bg = "#e0e0e0", "#888888", "#f5a623", "#1a1a2e"

        tk.Label(w, text="Pass List",
                 font=("Segoe UI", 13, "bold"), fg=gold, bg=bg).pack(pady=(10,2))
        tk.Label(w, text=(
            "Items on this list bypass the worn-item comparison.\n"
            "Any flat +GF on the shelf will trigger a hit when the slot matches.\n"
            "Partial name match, case-insensitive.\n"
            "Example: 'ring of celestial' matches all variants."
        ), font=("Segoe UI", 9), fg=muted, bg=bg,
            wraplength=390, justify="left").pack(padx=12, pady=4)

        lf = tk.Frame(w, bg=bg)
        lf.pack(fill="both", expand=True, padx=12, pady=4)
        sb = tk.Scrollbar(lf)
        sb.pack(side="right", fill="y")
        self._lb = tk.Listbox(lf, yscrollcommand=sb.set, selectmode="single",
                               bg="#0d0d1a", fg=gold,
                               font=("Courier New", 10), activestyle="none")
        self._lb.pack(fill="both", expand=True)
        sb.config(command=self._lb.yview)
        for entry in Config.PASS_LIST:
            self._lb.insert("end", entry)

        ef = tk.Frame(w, bg=bg)
        ef.pack(fill="x", padx=12, pady=4)
        self._entry = tk.Entry(ef, bg="#0d0d1a", fg=gold,
                                insertbackground="white",
                                font=("Courier New", 10))
        self._entry.pack(side="left", fill="x", expand=True, padx=(0,4))
        tk.Button(ef, text="Add", command=self._add,
                  bg="#0f3460", fg="white", relief="flat",
                  font=("Segoe UI", 9)).pack(side="left")

        bf = tk.Frame(w, bg=bg)
        bf.pack(fill="x", padx=12, pady=4)
        tk.Button(bf, text="Remove Selected", command=self._remove,
                  bg="#e94560", fg="white", relief="flat",
                  font=("Segoe UI", 9)).pack(side="left", padx=2)
        tk.Button(bf, text="Save & Close",
                  command=lambda: self._save(w),
                  bg="#00c48c", fg="white", relief="flat",
                  font=("Segoe UI", 10, "bold")).pack(side="right", padx=2)

    def _add(self) -> None:
        val = self._entry.get().strip()
        if val:
            self._lb.insert("end", val.lower())
            self._entry.delete(0, "end")

    def _remove(self) -> None:
        sel = self._lb.curselection()
        if sel:
            self._lb.delete(sel[0])

    def _save(self, win: tk.Toplevel) -> None:
        Config.PASS_LIST = [self._lb.get(i) for i in range(self._lb.size())]
        log.info(f"Pass list saved: {Config.PASS_LIST}")
        _save_prefs()
        win.destroy()


# ---------------------------------------------------------------------------
#  OVERLAY
# ---------------------------------------------------------------------------

class OverlayWindow:
    C = {
        "bg":       "#1a1a2e",
        "bg_panel": "#16213e",
        "accent":   "#e94560",
        "gold":     "#f5a623",
        "green":    "#00c48c",
        "text":     "#e0e0e0",
        "muted":    "#888888",
        "hit_bg":   "#2d1a00",
        "dual_bg":  "#3d0a2e",
        "dual_bdr": "#ff00aa",
        "btn_bg":   "#0f3460",
    }
    FM = ("Courier New", 9)
    FL = ("Segoe UI", 9)
    FB = ("Segoe UI", 12, "bold")
    FT = ("Segoe UI", 8)

    def __init__(self, inspector: InventoryInspector) -> None:
        self._inspector    = inspector
        self._q:           queue.Queue    = queue.Queue()
        self._root:        Optional[tk.Tk] = None
        self._live         = True
        self._current_hit: Optional[GoldHit] = None

    def run(self) -> None:
        self._build()
        self._root.after(100, self._pump)
        self._root.mainloop()

    def _build(self) -> None:
        r = tk.Tk()
        self._root = r
        r.title(f"{Config.APP_NAME} v{Config.APP_VERSION}")
        r.configure(bg=self.C["bg"])
        r.attributes("-topmost", True)
        r.attributes("-alpha", Config.OVERLAY_ALPHA)
        r.geometry(f"468x860+{Config.OVERLAY_X}+{Config.OVERLAY_Y}")
        r.resizable(True, True)
        r.protocol("WM_DELETE_WINDOW", self._on_close)

        # Route Tkinter callback errors into our logger instead of silently dying.
        def _tk_report_callback_exception(exc_type, exc_value, exc_tb):
            log.error("Tk callback exception", exc_info=(exc_type, exc_value, exc_tb))
        r.report_callback_exception = _tk_report_callback_exception

        # Header
        hdr = tk.Frame(r, bg=self.C["bg_panel"], pady=6)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"{Config.APP_NAME}  v{Config.APP_VERSION}",
                 font=self.FB, fg=self.C["gold"],
                 bg=self.C["bg_panel"]).pack()
        self._status_lbl = tk.Label(hdr, text="Idle",
                                     font=self.FL, fg=self.C["muted"],
                                     bg=self.C["bg_panel"])
        self._status_lbl.pack()

        # Stats row
        sf = tk.Frame(r, bg=self.C["bg"], pady=4)
        sf.pack(fill="x", padx=6)
        self._sv: dict = {}
        for key, lbl in [
            ("lap",     "Lap #"),
            ("blobs",   "Blobs"),
            ("items",   "Visited"),
            ("hits",    "Hits"),
            ("dual",    "Dual GF"),
            ("best_f",  "Best +Flat"),
            ("elapsed", "Time"),
        ]:
            cf = tk.Frame(sf, bg=self.C["bg_panel"], padx=4, pady=3)
            cf.pack(side="left", expand=True, fill="x", padx=1)
            tk.Label(cf, text=lbl, font=self.FT,
                     fg=self.C["muted"], bg=self.C["bg_panel"]).pack()
            v = tk.StringVar(value="0")
            self._sv[key] = v
            tk.Label(cf, textvariable=v,
                     font=("Segoe UI", 10, "bold"),
                     fg=self.C["gold"], bg=self.C["bg_panel"]).pack()

        # Current blob
        tf = tk.Frame(r, bg=self.C["bg"])
        tf.pack(fill="x", padx=6, pady=1)
        tk.Label(tf, text="Item:", font=self.FT,
                 fg=self.C["muted"], bg=self.C["bg"]).pack(side="left")
        self._cell_var = tk.StringVar(value="--")
        tk.Label(tf, textvariable=self._cell_var,
                 font=self.FM, fg=self.C["text"],
                 bg=self.C["bg"]).pack(side="left", padx=4)

        # Hit panel
        self._hit_frame = tk.Frame(r, bg=self.C["hit_bg"], relief="groove", bd=2)
        self._hit_frame.pack(fill="x", padx=6, pady=4)
        self._hit_lbl = tk.Label(
            self._hit_frame, text="No hits yet.",
            font=("Segoe UI", 10, "bold"),
            fg=self.C["gold"], bg=self.C["hit_bg"],
            wraplength=430, justify="left", pady=6, padx=6,
        )
        self._hit_lbl.pack(fill="x")
        self._cmp_lbl = tk.Label(
            self._hit_frame, text="",
            font=self.FM, fg=self.C["text"], bg=self.C["hit_bg"],
            wraplength=430, justify="left", padx=6,
        )
        self._cmp_lbl.pack(fill="x")

        # Dual GF banner
        self._dual_frame = tk.Frame(r, bg=self.C["dual_bg"], relief="groove", bd=3)
        self._dual_lbl   = tk.Label(
            self._dual_frame, text="",
            font=("Segoe UI", 11, "bold"),
            fg=self.C["dual_bdr"], bg=self.C["dual_bg"],
            wraplength=430, justify="center", pady=8, padx=6,
        )
        self._dual_lbl.pack(fill="x")

        # Primary buttons
        bs = dict(font=("Segoe UI", 10, "bold"), relief="flat",
                  padx=8, pady=4, cursor="hand2")
        bf1 = tk.Frame(r, bg=self.C["bg"], pady=4)
        bf1.pack(fill="x", padx=6)
        self._btn_begin = tk.Button(
            bf1, text="  BEGIN  (F6)",
            bg=self.C["green"], fg="white",
            command=self._toggle_begin, **bs)
        self._btn_begin.pack(side="left", expand=True, fill="x", padx=2)
        self._btn_next = tk.Button(
            bf1, text="  NEXT  (F7)",
            bg=self.C["btn_bg"], fg=self.C["muted"],
            state="disabled", command=self._next, **bs)
        self._btn_next.pack(side="left", expand=True, fill="x", padx=2)

        # Secondary buttons
        bf2 = tk.Frame(r, bg=self.C["bg"])
        bf2.pack(fill="x", padx=6, pady=2)
        for txt, cmd in [
            ("HOLD (F8)",  self._hold),
            ("Calibrate",  self._open_calibrate),
            ("Pass List",  self._open_passlist),
            ("Open Log",   self._open_log),
        ]:
            tk.Button(bf2, text=txt, bg=self.C["btn_bg"],
                      fg=self.C["text"], command=cmd, **bs
                      ).pack(side="left", expand=True, fill="x", padx=2)

        # Hotkey hint
        hk = tk.Frame(r, bg=self.C["bg_panel"], pady=2)
        hk.pack(fill="x", padx=6, pady=2)
        tk.Label(hk, text="F6=Begin/Halt   F7=Next   F8=Hold   ESC=Halt",
                 font=self.FT, fg=self.C["muted"],
                 bg=self.C["bg_panel"]).pack()

        # Live log
        tk.Label(r, text="Activity Log", font=self.FT,
                 fg=self.C["muted"], bg=self.C["bg"]).pack(anchor="w", padx=8)
        self._log_box = scrolledtext.ScrolledText(
            r, height=10, font=self.FM,
            bg="#0d0d1a", fg=self.C["text"],
            state="disabled", relief="flat")
        self._log_box.pack(fill="both", expand=True, padx=6, pady=(0,4))

        # Hit history
        tk.Label(r, text="Hit History", font=self.FT,
                 fg=self.C["muted"], bg=self.C["bg"]).pack(anchor="w", padx=8)
        self._hist_box = scrolledtext.ScrolledText(
            r, height=6, font=self.FM,
            bg="#0d0d1a", fg=self.C["gold"],
            state="disabled", relief="flat")
        self._hist_box.pack(fill="both", expand=False, padx=6, pady=(0,6))

        # Wire inspector callbacks
        self._inspector.on_hit     = lambda h:       self._q.put(("hit",     h))
        self._inspector.on_blob    = lambda b, t:    self._q.put(("blob",    (b, t)))
        self._inspector.on_restock = lambda n:       self._q.put(("restock", n))
        self._inspector.on_status  = lambda s:       self._q.put(("status",  s))
        self._inspector.on_halted  = lambda:         self._q.put(("halted",))

        self._reg_hotkeys()
        self._root.after(500, self._tick_stats)

    def _reg_hotkeys(self) -> None:
        for key, fn in [
            (Config.HOTKEY_BEGIN_HALT, self._toggle_begin),
            (Config.HOTKEY_NEXT,       self._next),
            (Config.HOTKEY_HOLD,       self._hold),
            (Config.HOTKEY_HALT,       self._emergency),
        ]:
            try:
                keyboard.add_hotkey(key, fn, suppress=False)
            except Exception as exc:
                log.warning(f"Hotkey {key} failed: {exc}")

    def _pump(self) -> None:
        try:
            while True:
                msg  = self._q.get_nowait()
                kind = msg[0]
                if kind == "hit":
                    self._on_hit(msg[1])
                elif kind == "blob":
                    blob, _ = msg[1]
                    self._cell_var.set(blob.label())
                elif kind == "restock":
                    self._log(f"=== Shelf restocked (lap #{msg[1]}) ===", "gold")
                elif kind == "status":
                    self._set_status(msg[1])
                elif kind == "halted":
                    self._btn_begin.config(text="  BEGIN  (F6)", bg=self.C["green"])
                    self._btn_next.config(state="disabled",
                                          bg=self.C["btn_bg"],
                                          fg=self.C["muted"])
                    self._set_status("Halted")
                    self._hide_dual_banner()
        except queue.Empty:
            pass
        if self._live:
            self._root.after(50, self._pump)

    def _on_hit(self, h: GoldHit) -> None:
        self._current_hit = h
        si, wi = h.shelf_item, h.worn_item
        if h.is_dual_gf:
            header = f"DUAL GF!  +{si.flat_gf} flat & +{si.pct_gf}%  -- confirm before passing"
            self._hit_frame.config(bg=self.C["dual_bg"])
            self._hit_lbl.config(bg=self.C["dual_bg"], fg=self.C["dual_bdr"], text=header)
            self._show_dual_banner(si)
        else:
            header = f"+{si.flat_gf} Gold Find  --  '{si.name}'"
            self._hit_frame.config(bg="#4a2a00")
            self._hit_lbl.config(bg="#4a2a00", fg=self.C["gold"], text=header)
            self._hide_dual_banner()

        lines = [f"  Shelf: +{si.flat_gf} flat  +{si.pct_gf}%  [{si.name[:40]}]"]
        if wi:
            tag = " (PASSED)" if is_passed(wi) else ""
            lines.append(f"  Worn:  +{wi.flat_gf} flat  +{wi.pct_gf}%  [{wi.name[:40]}]{tag}")
        lines.append(f"  Item:  {h.blob.label()}  cells={h.blob.cells}")
        self._cmp_lbl.config(text="\n".join(lines), bg=self._hit_frame["bg"])

        next_txt = "  PASS  (F7)" if h.is_dual_gf else "  NEXT  (F7)"
        self._btn_next.config(
            state="normal",
            bg=self.C["dual_bdr"] if h.is_dual_gf else self.C["accent"],
            fg="white", text=next_txt,
        )

        kind_tag = "DUAL" if h.is_dual_gf else "HIT"
        hist = (
            f"{h.timestamp[11:19]}  [{kind_tag}]  "
            f"+{si.flat_gf}flat  +{si.pct_gf}%  "
            f"{h.blob.label()}  {si.name[:30]}\n"
        )
        self._append_hist(hist)
        self._log(header, "accent" if h.is_dual_gf else "gold")
        self._set_status(
            "WAITING -- DUAL GF -- confirm before passing"
            if h.is_dual_gf else
            "WAITING -- press F7 to pass"
        )

    def _show_dual_banner(self, si: ParsedItem) -> None:
        self._dual_lbl.config(text=(
            f"DUAL GOLD-FIND ITEM\n"
            f"+{si.flat_gf} flat  AND  +{si.pct_gf}% percent\n"
            f"This is RARE -- confirm before passing!\n"
            f"Buy with Shift+Click, or press F7 to pass."
        ))
        self._dual_frame.pack(fill="x", padx=6, pady=2)

    def _hide_dual_banner(self) -> None:
        try:
            self._dual_frame.pack_forget()
        except Exception:
            pass

    def _toggle_begin(self) -> None:
        if self._inspector._active:
            self._inspector.halt()
            self._btn_begin.config(text="  BEGIN  (F6)", bg=self.C["green"])
        else:
            self._inspector.begin()
            self._btn_begin.config(text="  HALT  (F6)", bg=self.C["accent"])
            self._log("Scan started.", "green")
            self._hide_dual_banner()

    def _next(self) -> None:
        h = self._current_hit
        if h and h.is_dual_gf:
            if not messagebox.askyesno(
                "Pass on DUAL GF item?",
                (
                    f"Are you sure you want to PASS on this dual Gold-Find item?\n"
                    f"+{h.shelf_item.flat_gf} flat AND +{h.shelf_item.pct_gf}%\n\n"
                    f"Press NO to go back and buy it."
                ),
                default="no",
            ):
                return
        self._inspector.user_next()
        self._btn_next.config(state="disabled", text="  NEXT  (F7)",
                               bg=self.C["btn_bg"], fg=self.C["muted"])
        self._hide_dual_banner()
        self._hit_frame.config(bg=self.C["hit_bg"])
        self._hit_lbl.config(bg=self.C["hit_bg"])
        self._cmp_lbl.config(bg=self.C["hit_bg"], text="")
        self._log("Passed.", "muted")

    def _hold(self) -> None:
        self._inspector.hold_toggle()

    def _emergency(self) -> None:
        log.warning("HALT!")
        self._inspector.halt()
        self._btn_begin.config(text="  BEGIN  (F6)", bg=self.C["green"])
        self._hide_dual_banner()
        self._set_status("HALTED")

    def _open_calibrate(self) -> None:
        CalibrationWindow(self._root)

    def _open_passlist(self) -> None:
        PassListWindow(self._root)

    def _open_log(self) -> None:
        try:
            os.startfile(str(_log_file))
        except Exception:
            messagebox.showinfo("Session log", str(_log_file))

    def _set_status(self, msg: str) -> None:
        self._status_lbl.config(text=msg)

    def _log(self, msg: str, color: str = "text") -> None:
        ts   = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        c    = self.C.get(color, self.C["text"])
        self._log_box.config(state="normal")
        self._log_box.insert("end", line)
        tag = f"clr_{color}"
        self._log_box.tag_config(tag, foreground=c)
        self._log_box.tag_add(tag, f"end - {len(line) + 1}c", "end - 1c")
        self._log_box.see("end")
        self._log_box.config(state="disabled")

    def _append_hist(self, line: str) -> None:
        self._hist_box.config(state="normal")
        self._hist_box.insert("end", line)
        self._hist_box.see("end")
        self._hist_box.config(state="disabled")

    def _tick_stats(self) -> None:
        s = self._inspector.stats
        self._sv["lap"].set(str(s.lap_number))
        self._sv["blobs"].set(str(s.blobs_found))
        self._sv["items"].set(str(s.items_visited))
        self._sv["hits"].set(str(s.hits))
        self._sv["dual"].set(str(s.dual_hits))
        self._sv["best_f"].set(f"+{s.best_flat}")
        self._sv["elapsed"].set(s.elapsed_str())
        if self._live:
            self._root.after(500, self._tick_stats)

    def _on_close(self) -> None:
        self._live = False
        self._inspector.halt()
        keyboard.unhook_all()
        _save_prefs()
        log.info("Overlay closed.")
        self._root.destroy()


# ---------------------------------------------------------------------------
#  MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=" * 60)
    log.info(f"  {Config.APP_NAME}  v{Config.APP_VERSION}")
    log.info(f"  Session log: {_log_file}")
    log.info(f"  Pass list: {Config.PASS_LIST}")
    log.info("=" * 60)

    reader    = ScrollReader()
    capture   = ScreenCapture()
    detector  = GridBlobDetector()
    inspector = InventoryInspector(reader, capture, detector)
    overlay   = OverlayWindow(inspector)
    overlay.run()
    log.info("Goodbye.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.critical("Fatal error in main(): %s", exc, exc_info=True)
        raise
