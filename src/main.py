#!/usr/bin/env python3
"""
=============================================================================
  TH4 Shop-Bot  v1.1.0  --  The Hell 4 Gold-Find Item Scanner
  Self-contained, self-hosted, single-file application.
=============================================================================
  HOW IT WORKS
  ------------
  1.  You open Griswold's shop in The Hell 4.
  2.  Run this script. An overlay window appears top-left.
  3.  Press START (or F6) to begin scanning.
  4.  The bot moves the mouse across every shop grid tile. For each tile:
        a) Normal hover -> capture tooltip -> quick scan for gold-find text.
        b) If gold-find text found -> hold ALT -> capture the side-by-side
           COMPARISON tooltip the game shows.
        c) Parse BOTH the shop item AND the equipped item.
        d) Only stop if: shop_flat_gf >= equipped_flat_gf
           OR equipped slot is on IGNORE LIST
           OR item has BOTH flat + % gold find (RARE -- mandatory dismiss).
  5.  When a qualifying hit is found:
        - Mouse stays on that tile.
        - Overlay shows detected values.
        - Bot PAUSES and waits for you.
  6.  You decide: Buy (Shift+Click yourself) or Skip.
  7.  Press CONTINUE (overlay button or F7) to resume.
  8.  After all tiles are scanned the bot presses R to refresh and loops.

  IGNORE LIST
  -----------
  Items on the ignore list bypass the equipped-item comparison entirely.
  Use for uniques you never want to replace (e.g. Ring of Celestial Castles).

  KEY CONTROLS (global hotkeys)
  F6   --  Start / Stop toggle
  F7   --  Continue / Skip after a find
  F8   --  Pause / Resume
  ESC  --  Emergency stop

  SAFETY
  ------
  The bot NEVER Shift+Clicks.  All buys are done manually by you.

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
from typing import Optional, List, Tuple

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
    pyautogui.PAUSE = 0.0
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
    APP_NAME        = "TH4 Shop-Bot"
    APP_VERSION     = "1.1.0"

    # Shop grid geometry (pixels)
    SHOP_ORIGIN_X   = 40
    SHOP_ORIGIN_Y   = 108
    TILE_W          = 29
    TILE_H          = 29
    SHOP_COLS       = 10
    SHOP_ROWS       = 14

    # Tooltip capture
    HOVER_DELAY_MS  = 180
    ALT_DELAY_MS    = 220
    TOOLTIP_OFFSET_X = -320
    TOOLTIP_OFFSET_Y = -380
    TOOLTIP_W       = 560
    TOOLTIP_H       = 440

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

    MIN_GOLD_FIND   = 1

    # Comparison tooltip split markers
    EQUIPPED_MARKERS = ["equipped", "currently equipped", "worn", "eq:"]
    SELECTED_MARKERS = ["selected", "shop item", "buy price", "shift click to buy"]

    # Ignore list -- partial name match, case-insensitive
    IGNORE_LIST: List[str] = [
        "ring of celestial castles",
        "ring of the sun",
    ]

    # Refresh
    REFRESH_KEY      = "r"
    REFRESH_DELAY_MS = 600

    # Hotkeys
    HOTKEY_START_STOP = "f6"
    HOTKEY_CONTINUE   = "f7"
    HOTKEY_PAUSE      = "f8"
    HOTKEY_EMERGENCY  = "escape"

    # Paths
    LOG_DIR    = Path(__file__).resolve().parent.parent / "logs"
    STATE_FILE = Path(__file__).resolve().parent.parent / "_tools" / "botstate.json"

    # Overlay
    OVERLAY_X     = 20
    OVERLAY_Y     = 20
    OVERLAY_ALPHA = 0.90


# ---------------------------------------------------------------------------
#  STATE PERSISTENCE
# ---------------------------------------------------------------------------

def _load_state() -> None:
    try:
        if Config.STATE_FILE.exists():
            with open(Config.STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key in ("SHOP_ORIGIN_X", "SHOP_ORIGIN_Y", "TILE_W", "TILE_H",
                        "SHOP_COLS", "SHOP_ROWS", "HOVER_DELAY_MS",
                        "ALT_DELAY_MS", "MIN_GOLD_FIND", "IGNORE_LIST"):
                if key in data:
                    setattr(Config, key, data[key])
    except Exception:
        pass


def _save_state() -> None:
    try:
        Config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {k: getattr(Config, k) for k in
                ("SHOP_ORIGIN_X", "SHOP_ORIGIN_Y", "TILE_W", "TILE_H",
                 "SHOP_COLS", "SHOP_ROWS", "HOVER_DELAY_MS",
                 "ALT_DELAY_MS", "MIN_GOLD_FIND", "IGNORE_LIST")}
        with open(Config.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  LOGGING
# ---------------------------------------------------------------------------

Config.LOG_DIR.mkdir(parents=True, exist_ok=True)
_run_ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
_log_file = Config.LOG_DIR / f"shopbot_run_{_run_ts}.txt"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(_log_file), encoding="utf-8"),
    ],
)
log = logging.getLogger("th4bot")
log.info(f"TH4 Shop-Bot {Config.APP_VERSION} starting -- log: {_log_file}")
_load_state()


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
class ShopFind:
    col:             int
    row:             int
    shop_item:       ParsedItem
    equipped_item:   Optional[ParsedItem]
    is_dual_gf:      bool
    timestamp:       str = field(
        default_factory=lambda: datetime.datetime.now().isoformat())
    screenshot_path: Optional[str] = None

    @property
    def gold_value(self) -> int:
        return self.shop_item.flat_gf


@dataclass
class ScanStats:
    run_number:    int   = 0
    tiles_scanned: int   = 0
    finds:         int   = 0
    dual_finds:    int   = 0
    best_flat:     int   = 0
    best_pct:      int   = 0
    start_time:    float = field(default_factory=time.time)

    def elapsed_str(self) -> str:
        e = int(time.time() - self.start_time)
        return f"{e // 60:02d}:{e % 60:02d}"


# ---------------------------------------------------------------------------
#  OCR ENGINE
# ---------------------------------------------------------------------------

class OCREngine:
    def __init__(self) -> None:
        self._rapid = None
        try:
            self._rapid = RapidOCR()
            log.info("RapidOCR initialised OK.")
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
            log.debug(f"RapidOCR read error: {exc}")
            return ""

    def _read_winrt(self, img: "Image.Image") -> str:
        try:
            import asyncio
            import winocr
            loop = asyncio.new_event_loop()
            res = loop.run_until_complete(winocr.recognize_pil(img, "en"))
            loop.close()
            return res.text if res else ""
        except Exception as exc:
            log.debug(f"WinRT OCR error: {exc}")
            return ""


# ---------------------------------------------------------------------------
#  SCREEN CAPTURE
# ---------------------------------------------------------------------------

class ScreenCapture:
    def grab_region(self, x: int, y: int, w: int, h: int) -> "Image.Image":
        try:
            return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        except Exception:
            return pyautogui.screenshot(region=(x, y, w, h))

    def grab_tooltip(self, cx: int, cy: int) -> "Image.Image":
        ox = max(0, cx + Config.TOOLTIP_OFFSET_X)
        oy = max(0, cy + Config.TOOLTIP_OFFSET_Y)
        return self.grab_region(ox, oy, Config.TOOLTIP_W, Config.TOOLTIP_H)


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
                val = int(m.group(1))
                tail = text[m.end(1):m.end(1) + 10]
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
    """Split ALT comparison tooltip into (selected_text, equipped_text)."""
    text_lower = full_text.lower()
    sel_pos = eq_pos = -1
    for m in Config.SELECTED_MARKERS:
        idx = text_lower.find(m)
        if idx != -1:
            sel_pos = idx
            break
    for m in Config.EQUIPPED_MARKERS:
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
    mid = max(1, len(lines) // 2)
    log.debug("split_comparison: line-count fallback")
    return "\n".join(lines[:mid]), "\n".join(lines[mid:])


def is_ignored(item: ParsedItem) -> bool:
    name_lower = item.name.lower()
    for entry in Config.IGNORE_LIST:
        if entry.lower() in name_lower:
            log.debug(f"  Ignore-list hit: '{item.name}' matches '{entry}'")
            return True
    return False


# ---------------------------------------------------------------------------
#  SHOP SCANNER CORE
# ---------------------------------------------------------------------------

class ShopScanner:
    def __init__(self, ocr: OCREngine, capture: ScreenCapture) -> None:
        self._ocr     = ocr
        self._capture = capture
        self._running = False
        self._paused  = False
        self._wait_ev = threading.Event()
        self._stop_ev = threading.Event()

        self.on_find:    Optional[callable] = None
        self.on_tile:    Optional[callable] = None
        self.on_refresh: Optional[callable] = None
        self.on_status:  Optional[callable] = None
        self.on_stopped: Optional[callable] = None

        self.stats = ScanStats()

    # --- public API --------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_ev.clear()
        self._wait_ev.clear()
        self.stats = ScanStats()
        threading.Thread(target=self._scan_loop, daemon=True).start()
        log.info("Scanner started.")

    def stop(self) -> None:
        self._running = False
        self._stop_ev.set()
        self._wait_ev.set()
        log.info("Scanner stop requested.")

    def pause_toggle(self) -> None:
        self._paused = not self._paused
        if self._paused:
            log.info("Paused.")
            self._status("Paused -- press F8 to resume.")
        else:
            log.info("Resumed.")
            self._wait_ev.set()

    def user_continue(self) -> None:
        log.info("User: Continue.")
        self._wait_ev.set()

    # --- internals ---------------------------------------------------------

    def _status(self, msg: str) -> None:
        log.debug(f"STATUS: {msg}")
        if self.on_status:
            self.on_status(msg)

    def _wait_if_paused(self) -> None:
        while self._paused and self._running:
            time.sleep(0.1)

    def _scan_loop(self) -> None:
        try:
            while self._running:
                self.stats.run_number += 1
                log.info(f"=== Scan run #{self.stats.run_number} ===")
                if self.on_refresh:
                    self.on_refresh(self.stats.run_number)
                self._scan_all_tiles()
                if not self._running:
                    break
                self._do_refresh()
        except Exception as exc:
            log.error(f"Scanner crashed: {exc}", exc_info=True)
        finally:
            self._running = False
            if self.on_stopped:
                self.on_stopped()

    def _scan_all_tiles(self) -> None:
        ox = Config.SHOP_ORIGIN_X
        oy = Config.SHOP_ORIGIN_Y
        tw = Config.TILE_W
        th = Config.TILE_H

        for row in range(Config.SHOP_ROWS):
            for col in range(Config.SHOP_COLS):
                if not self._running or self._stop_ev.is_set():
                    return
                self._wait_if_paused()

                cx = ox + col * tw + tw // 2
                cy = oy + row * th + th // 2

                log.debug(f"Tile [{col:2d},{row:2d}] cursor=({cx},{cy})")
                pyautogui.moveTo(cx, cy, duration=0.0)
                time.sleep(Config.HOVER_DELAY_MS / 1000.0)

                img_plain  = self._capture.grab_tooltip(cx, cy)
                text_plain = self._ocr.read(img_plain)
                log.debug(
                    f"  OCR plain: {text_plain[:120].replace(chr(10),' | ')}"
                )

                if self.on_tile:
                    self.on_tile(col, row, text_plain)
                self.stats.tiles_scanned += 1

                if not re.search(r"gold", text_plain, re.IGNORECASE):
                    continue

                quick = parse_item_text(text_plain)
                if quick.flat_gf < Config.MIN_GOLD_FIND and quick.pct_gf == 0:
                    continue

                log.info(
                    f"  Pre-filter hit [{col},{row}]:"
                    f" flat={quick.flat_gf} pct={quick.pct_gf}"
                )
                self._status(
                    f"Pre-filter [{col},{row}] -- taking ALT screenshot..."
                )

                keyboard.press("alt")
                time.sleep(Config.ALT_DELAY_MS / 1000.0)
                img_alt  = self._capture.grab_tooltip(cx, cy)
                keyboard.release("alt")
                text_alt = self._ocr.read(img_alt)
                log.debug(
                    f"  OCR alt: {text_alt[:200].replace(chr(10),' | ')}"
                )

                shop_raw, equip_raw = split_comparison_tooltip(text_alt)
                shop_item  = parse_item_text(shop_raw)
                equip_item = parse_item_text(equip_raw)

                if shop_item.flat_gf == 0 and shop_item.pct_gf == 0:
                    shop_item = quick
                    log.debug("  ALT parse empty -- using plain text.")

                log.info(
                    f"  Shop:  flat={shop_item.flat_gf}"
                    f"  pct={shop_item.pct_gf}"
                    f"  name='{shop_item.name}'"
                )
                log.info(
                    f"  Equip: flat={equip_item.flat_gf}"
                    f"  pct={equip_item.pct_gf}"
                    f"  name='{equip_item.name}'"
                )

                is_dual = (
                    shop_item.flat_gf >= Config.MIN_GOLD_FIND
                    and shop_item.pct_gf > 0
                )
                should_stop, reason = self._should_stop(
                    shop_item, equip_item, is_dual
                )

                if not should_stop:
                    log.info(f"  Filtered: {reason}")
                    self._status(f"Tile [{col},{row}] filtered: {reason}")
                    continue

                log.info(f"  *** STOP [{col},{row}]: {reason}")
                self.stats.finds += 1
                if is_dual:
                    self.stats.dual_finds += 1
                if shop_item.flat_gf > self.stats.best_flat:
                    self.stats.best_flat = shop_item.flat_gf
                if shop_item.pct_gf > self.stats.best_pct:
                    self.stats.best_pct = shop_item.pct_gf

                ts   = datetime.datetime.now().strftime("%H%M%S_%f")[:9]
                path = str(
                    Config.LOG_DIR
                    / f"find_{ts}_col{col}_row{row}.png"
                )
                try:
                    img_alt.save(path)
                except Exception:
                    path = None

                find = ShopFind(
                    col=col, row=row,
                    shop_item=shop_item,
                    equipped_item=equip_item,
                    is_dual_gf=is_dual,
                    screenshot_path=path,
                )
                if self.on_find:
                    self.on_find(find)

                self._wait_ev.clear()
                self._status(f"WAITING -- {reason}")
                self._wait_ev.wait()
                self._wait_ev.clear()

    def _should_stop(
        self,
        shop: ParsedItem,
        equip: ParsedItem,
        is_dual: bool,
    ) -> Tuple[bool, str]:
        if shop.flat_gf < Config.MIN_GOLD_FIND and not is_dual:
            return (
                False,
                f"flat GF {shop.flat_gf} below minimum {Config.MIN_GOLD_FIND}",
            )

        if is_dual:
            return (
                True,
                (
                    f"DUAL GF ITEM! +{shop.flat_gf} flat AND"
                    f" +{shop.pct_gf}% ('{shop.name}') -- MUST MANUALLY DISMISS"
                ),
            )

        equip_ignored = is_ignored(equip)
        equip_has_gf  = equip.flat_gf >= Config.MIN_GOLD_FIND

        if equip_ignored or not equip_has_gf:
            return (
                True,
                (
                    f"equipped slot ignored/unset -- shop +{shop.flat_gf}"
                    f" meets minimum ('{shop.name}')"
                ),
            )

        if shop.flat_gf >= equip.flat_gf:
            return (
                True,
                (
                    f"shop +{shop.flat_gf} >= equipped +{equip.flat_gf}"
                    f" ('{shop.name}' vs '{equip.name}')"
                ),
            )

        return (
            False,
            f"shop +{shop.flat_gf} < equipped +{equip.flat_gf} -- skip",
        )

    def _do_refresh(self) -> None:
        log.info("Refreshing shop...")
        self._status("Refreshing shop...")
        keyboard.press_and_release(Config.REFRESH_KEY)
        time.sleep(Config.REFRESH_DELAY_MS / 1000.0)


# ---------------------------------------------------------------------------
#  OVERLAY GUI
# ---------------------------------------------------------------------------

class OverlayWindow:
    C = {
        "bg":         "#1a1a2e",
        "bg_panel":   "#16213e",
        "accent":     "#e94560",
        "gold":       "#f5a623",
        "green":      "#00c48c",
        "text":       "#e0e0e0",
        "muted":      "#888888",
        "find_bg":    "#2d1a00",
        "dual_bg":    "#3d0a2e",
        "dual_bdr":   "#ff00aa",
        "btn_bg":     "#0f3460",
    }
    FM = ("Courier New", 9)
    FL = ("Segoe UI", 9)
    FB = ("Segoe UI", 12, "bold")
    FT = ("Segoe UI", 8)

    def __init__(self, scanner: ShopScanner) -> None:
        self._scanner = scanner
        self._q:      queue.Queue = queue.Queue()
        self._root:   Optional[tk.Tk] = None
        self._live    = True
        self._current_find: Optional[ShopFind] = None

    def run(self) -> None:
        self._build()
        self._root.after(100, self._pump)
        self._root.mainloop()

    # --- build UI ----------------------------------------------------------

    def _build(self) -> None:
        r = tk.Tk()
        self._root = r
        r.title(f"{Config.APP_NAME} v{Config.APP_VERSION}")
        r.configure(bg=self.C["bg"])
        r.attributes("-topmost", True)
        r.attributes("-alpha", Config.OVERLAY_ALPHA)
        r.geometry(f"468x820+{Config.OVERLAY_X}+{Config.OVERLAY_Y}")
        r.resizable(True, True)
        r.protocol("WM_DELETE_WINDOW", self._on_close)

        # Header
        hdr = tk.Frame(r, bg=self.C["bg_panel"], pady=6)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=f"{Config.APP_NAME}  v{Config.APP_VERSION}",
            font=self.FB,
            fg=self.C["gold"],
            bg=self.C["bg_panel"],
        ).pack()
        self._status_lbl = tk.Label(
            hdr, text="Idle",
            font=self.FL, fg=self.C["muted"], bg=self.C["bg_panel"],
        )
        self._status_lbl.pack()

        # Stats row
        sf = tk.Frame(r, bg=self.C["bg"], pady=4)
        sf.pack(fill="x", padx=6)
        self._sv: dict = {}
        for key, lbl in [
            ("run", "Run #"), ("tiles", "Tiles"), ("finds", "Finds"),
            ("dual", "Dual GF"), ("best_f", "Best +Flat"),
            ("best_p", "Best %"), ("elapsed", "Time"),
        ]:
            cf = tk.Frame(sf, bg=self.C["bg_panel"], padx=4, pady=3)
            cf.pack(side="left", expand=True, fill="x", padx=1)
            tk.Label(
                cf, text=lbl, font=self.FT,
                fg=self.C["muted"], bg=self.C["bg_panel"],
            ).pack()
            v = tk.StringVar(value="0")
            self._sv[key] = v
            tk.Label(
                cf, textvariable=v,
                font=("Segoe UI", 10, "bold"),
                fg=self.C["gold"], bg=self.C["bg_panel"],
            ).pack()

        # Current tile
        tf = tk.Frame(r, bg=self.C["bg"])
        tf.pack(fill="x", padx=6, pady=1)
        tk.Label(
            tf, text="Tile:",
            font=self.FT, fg=self.C["muted"], bg=self.C["bg"],
        ).pack(side="left")
        self._tile_var = tk.StringVar(value="--")
        tk.Label(
            tf, textvariable=self._tile_var,
            font=self.FM, fg=self.C["text"], bg=self.C["bg"],
        ).pack(side="left", padx=4)

        # Find panel
        self._find_frame = tk.Frame(
            r, bg=self.C["find_bg"], relief="groove", bd=2
        )
        self._find_frame.pack(fill="x", padx=6, pady=4)
        self._find_lbl = tk.Label(
            self._find_frame, text="No finds yet.",
            font=("Segoe UI", 10, "bold"),
            fg=self.C["gold"], bg=self.C["find_bg"],
            wraplength=430, justify="left", pady=6, padx=6,
        )
        self._find_lbl.pack(fill="x")
        self._cmp_lbl = tk.Label(
            self._find_frame, text="",
            font=self.FM, fg=self.C["text"], bg=self.C["find_bg"],
            wraplength=430, justify="left", padx=6,
        )
        self._cmp_lbl.pack(fill="x")

        # Dual GF banner (hidden by default)
        self._dual_frame = tk.Frame(
            r, bg=self.C["dual_bg"], relief="groove", bd=3
        )
        self._dual_lbl = tk.Label(
            self._dual_frame, text="",
            font=("Segoe UI", 11, "bold"),
            fg=self.C["dual_bdr"], bg=self.C["dual_bg"],
            wraplength=430, justify="center", pady=8, padx=6,
        )
        self._dual_lbl.pack(fill="x")

        # Primary buttons
        bs = dict(
            font=("Segoe UI", 10, "bold"),
            relief="flat", padx=8, pady=4, cursor="hand2",
        )
        bf1 = tk.Frame(r, bg=self.C["bg"], pady=4)
        bf1.pack(fill="x", padx=6)
        self._btn_start = tk.Button(
            bf1, text="  START  (F6)",
            bg=self.C["green"], fg="white",
            command=self._toggle_start, **bs,
        )
        self._btn_start.pack(side="left", expand=True, fill="x", padx=2)
        self._btn_cont = tk.Button(
            bf1, text="  CONTINUE  (F7)",
            bg=self.C["btn_bg"], fg=self.C["muted"],
            state="disabled", command=self._continue, **bs,
        )
        self._btn_cont.pack(side="left", expand=True, fill="x", padx=2)

        # Secondary buttons
        bf2 = tk.Frame(r, bg=self.C["bg"])
        bf2.pack(fill="x", padx=6, pady=2)
        for txt, cmd in [
            ("PAUSE (F8)", self._pause),
            ("Calibrate",  self._open_calibrate),
            ("Ignore List", self._open_ignore),
            ("Open Log",   self._open_log),
        ]:
            tk.Button(
                bf2, text=txt, bg=self.C["btn_bg"],
                fg=self.C["text"], command=cmd, **bs,
            ).pack(side="left", expand=True, fill="x", padx=2)

        # Hotkey hint
        hk = tk.Frame(r, bg=self.C["bg_panel"], pady=2)
        hk.pack(fill="x", padx=6, pady=2)
        tk.Label(
            hk,
            text="F6=Start/Stop   F7=Continue   F8=Pause   ESC=Emergency",
            font=self.FT, fg=self.C["muted"], bg=self.C["bg_panel"],
        ).pack()

        # Live log
        tk.Label(
            r, text="Live Log",
            font=self.FT, fg=self.C["muted"], bg=self.C["bg"],
        ).pack(anchor="w", padx=8)
        self._log_box = scrolledtext.ScrolledText(
            r, height=10, font=self.FM,
            bg="#0d0d1a", fg=self.C["text"],
            state="disabled", relief="flat",
        )
        self._log_box.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        # Find history
        tk.Label(
            r, text="Find History",
            font=self.FT, fg=self.C["muted"], bg=self.C["bg"],
        ).pack(anchor="w", padx=8)
        self._hist_box = scrolledtext.ScrolledText(
            r, height=6, font=self.FM,
            bg="#0d0d1a", fg=self.C["gold"],
            state="disabled", relief="flat",
        )
        self._hist_box.pack(
            fill="both", expand=False, padx=6, pady=(0, 6)
        )

        # Wire scanner callbacks
        self._scanner.on_find    = lambda f: self._q.put(("find", f))
        self._scanner.on_tile    = lambda c, ro, t: self._q.put(("tile", (c, ro, t)))
        self._scanner.on_refresh = lambda n: self._q.put(("refresh", n))
        self._scanner.on_status  = lambda s: self._q.put(("status", s))
        self._scanner.on_stopped = lambda: self._q.put(("stopped",))

        self._reg_hotkeys()
        self._root.after(500, self._tick_stats)

    # --- hotkeys -----------------------------------------------------------

    def _reg_hotkeys(self) -> None:
        for key, fn in [
            (Config.HOTKEY_START_STOP, self._toggle_start),
            (Config.HOTKEY_CONTINUE,   self._continue),
            (Config.HOTKEY_PAUSE,      self._pause),
            (Config.HOTKEY_EMERGENCY,  self._emergency),
        ]:
            try:
                keyboard.add_hotkey(key, fn, suppress=False)
            except Exception as exc:
                log.warning(f"Hotkey {key} failed: {exc}")

    # --- event pump --------------------------------------------------------

    def _pump(self) -> None:
        try:
            while True:
                msg  = self._q.get_nowait()
                kind = msg[0]
                if kind == "find":
                    self._on_find(msg[1])
                elif kind == "tile":
                    c, ro, _ = msg[1]
                    self._tile_var.set(f"[col={c}, row={ro}]")
                elif kind == "refresh":
                    self._log(f"=== Shop refreshed (run #{msg[1]}) ===", "gold")
                elif kind == "status":
                    self._set_status(msg[1])
                elif kind == "stopped":
                    self._btn_start.config(
                        text="  START  (F6)", bg=self.C["green"]
                    )
                    self._btn_cont.config(
                        state="disabled",
                        bg=self.C["btn_bg"],
                        fg=self.C["muted"],
                    )
                    self._set_status("Stopped")
                    self._hide_dual_banner()
        except queue.Empty:
            pass
        if self._live:
            self._root.after(50, self._pump)

    # --- find display ------------------------------------------------------

    def _on_find(self, f: ShopFind) -> None:
        self._current_find = f
        si = f.shop_item
        ei = f.equipped_item

        if f.is_dual_gf:
            header = (
                f"DUAL GF ITEM!  +{si.flat_gf} flat & +{si.pct_gf}%"
                f"  -- MUST MANUALLY DISMISS"
            )
            self._find_frame.config(bg=self.C["dual_bg"])
            self._find_lbl.config(
                bg=self.C["dual_bg"], fg=self.C["dual_bdr"], text=header
            )
            self._show_dual_banner(si)
        else:
            header = f"+{si.flat_gf} Gold Find  --  '{si.name}'"
            self._find_frame.config(bg="#4a2a00")
            self._find_lbl.config(bg="#4a2a00", fg=self.C["gold"], text=header)
            self._hide_dual_banner()

        lines = [f"  Shop:     +{si.flat_gf} flat  +{si.pct_gf}%  [{si.name[:40]}]"]
        if ei:
            tag = " (IGNORED)" if is_ignored(ei) else ""
            lines.append(
                f"  Equipped: +{ei.flat_gf} flat  +{ei.pct_gf}%"
                f"  [{ei.name[:40]}]{tag}"
            )
        self._cmp_lbl.config(
            text="\n".join(lines),
            bg=self._find_frame["bg"],
        )

        cont_txt = "  SKIP  (F7)" if f.is_dual_gf else "  CONTINUE  (F7)"
        self._btn_cont.config(
            state="normal",
            bg=self.C["dual_bdr"] if f.is_dual_gf else self.C["accent"],
            fg="white",
            text=cont_txt,
        )

        kind_tag = "DUAL" if f.is_dual_gf else "FIND"
        hist = (
            f"{f.timestamp[11:19]}  [{kind_tag}]  "
            f"+{si.flat_gf}flat  +{si.pct_gf}%  "
            f"[{f.col:2d},{f.row:2d}]  {si.name[:30]}\n"
        )
        self._append_hist(hist)
        self._log(header, "accent" if f.is_dual_gf else "gold")
        self._set_status(
            "WAITING -- DUAL GF -- MUST DISMISS"
            if f.is_dual_gf else
            "WAITING -- press F7 to skip"
        )

    def _show_dual_banner(self, si: ParsedItem) -> None:
        self._dual_lbl.config(
            text=(
                f"DUAL GOLD-FIND ITEM\n"
                f"+{si.flat_gf} flat  AND  +{si.pct_gf}% percent\n"
                f"This is RARE -- you MUST manually dismiss!\n"
                f"Buy it with Shift+Click or press F7 to skip."
            )
        )
        self._dual_frame.pack(fill="x", padx=6, pady=2)

    def _hide_dual_banner(self) -> None:
        try:
            self._dual_frame.pack_forget()
        except Exception:
            pass

    # --- button callbacks --------------------------------------------------

    def _toggle_start(self) -> None:
        if self._scanner._running:
            self._scanner.stop()
            self._btn_start.config(
                text="  START  (F6)", bg=self.C["green"]
            )
        else:
            self._scanner.start()
            self._btn_start.config(
                text="  STOP  (F6)", bg=self.C["accent"]
            )
            self._log("Scanner started.", "green")
            self._hide_dual_banner()

    def _continue(self) -> None:
        f = self._current_find
        if f and f.is_dual_gf:
            if not messagebox.askyesno(
                "Skip DUAL GF item?",
                (
                    f"Are you sure you want to SKIP this dual Gold-Find item?\n"
                    f"+{f.shop_item.flat_gf} flat AND +{f.shop_item.pct_gf}%\n\n"
                    f"Press NO to go back and buy it."
                ),
                default="no",
            ):
                return
        self._scanner.user_continue()
        self._btn_cont.config(
            state="disabled",
            text="  CONTINUE  (F7)",
            bg=self.C["btn_bg"],
            fg=self.C["muted"],
        )
        self._hide_dual_banner()
        self._find_frame.config(bg=self.C["find_bg"])
        self._find_lbl.config(bg=self.C["find_bg"])
        self._cmp_lbl.config(bg=self.C["find_bg"], text="")
        self._log("User: Continue.", "muted")

    def _pause(self) -> None:
        self._scanner.pause_toggle()

    def _emergency(self) -> None:
        log.warning("EMERGENCY STOP!")
        self._scanner.stop()
        self._btn_start.config(text="  START  (F6)", bg=self.C["green"])
        self._hide_dual_banner()
        self._set_status("EMERGENCY STOP")

    def _open_calibrate(self) -> None:
        CalibrationWindow(self._root)

    def _open_ignore(self) -> None:
        IgnoreListWindow(self._root)

    def _open_log(self) -> None:
        try:
            os.startfile(str(_log_file))
        except Exception:
            messagebox.showinfo("Log file", str(_log_file))

    # --- helpers -----------------------------------------------------------

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
        s = self._scanner.stats
        self._sv["run"].set(str(s.run_number))
        self._sv["tiles"].set(str(s.tiles_scanned))
        self._sv["finds"].set(str(s.finds))
        self._sv["dual"].set(str(s.dual_finds))
        self._sv["best_f"].set(f"+{s.best_flat}")
        self._sv["best_p"].set(f"+{s.best_pct}%")
        self._sv["elapsed"].set(s.elapsed_str())
        if self._live:
            self._root.after(500, self._tick_stats)

    def _on_close(self) -> None:
        self._live = False
        self._scanner.stop()
        keyboard.unhook_all()
        _save_state()
        log.info("Overlay closed.")
        self._root.destroy()


# ---------------------------------------------------------------------------
#  CALIBRATION WINDOW
# ---------------------------------------------------------------------------

class CalibrationWindow:
    def __init__(self, parent: tk.Tk) -> None:
        w = tk.Toplevel(parent)
        w.title("Shop Grid Calibration")
        w.configure(bg="#1a1a2e")
        w.geometry("520x640")
        w.attributes("-topmost", True)

        fg    = "#e0e0e0"
        muted = "#888888"
        gold  = "#f5a623"
        bg    = "#1a1a2e"
        bs    = dict(bg="#0f3460", fg="white", relief="flat",
                     font=("Segoe UI", 9))

        tk.Label(
            w, text="Grid Calibration",
            font=("Segoe UI", 13, "bold"),
            fg=gold, bg=bg,
        ).pack(pady=(10, 2))

        instr = (
            "1.  Open Griswold's shop in-game.\n"
            "2.  Hover over TOP-LEFT tile (col 0, row 0) -> Capture TL\n"
            "3.  Hover over tile ONE COLUMN RIGHT (col 1, row 0) -> Capture W\n"
            "4.  Hover over tile ONE ROW DOWN (col 0, row 1) -> Capture H\n"
            "5.  Set columns/rows count for your shop.\n"
            "6.  Click Apply & Close when done.\n\n"
            "TIP: Increase hover/ALT delay if tooltips don't appear in time."
        )
        tk.Label(
            w, text=instr, justify="left",
            font=("Segoe UI", 9), fg=fg, bg=bg, wraplength=470,
        ).pack(padx=12, pady=4)

        tk.Frame(w, bg="#333355", height=1).pack(fill="x", padx=12, pady=4)

        self._tl_x = Config.SHOP_ORIGIN_X
        self._tl_y = Config.SHOP_ORIGIN_Y
        self._tw   = Config.TILE_W
        self._th   = Config.TILE_H
        self._tl_var: Optional[tk.StringVar] = None
        self._tw_var: Optional[tk.StringVar] = None
        self._th_var: Optional[tk.StringVar] = None

        def cap_row(
            lbl_text: str,
            cap_fn: callable,
            cap_lbl: str,
            var_init: str,
            var_attr: str,
        ) -> None:
            fr = tk.Frame(w, bg=bg)
            fr.pack(fill="x", padx=12, pady=2)
            tk.Label(
                fr, text=lbl_text, width=26, anchor="w",
                fg=muted, bg=bg, font=("Segoe UI", 9),
            ).pack(side="left")
            v = tk.StringVar(value=var_init)
            setattr(self, var_attr, v)
            tk.Label(
                fr, textvariable=v,
                fg=gold, bg=bg, font=("Courier New", 9),
            ).pack(side="left", padx=4)
            tk.Button(fr, text=cap_lbl, command=cap_fn, **bs).pack(side="right")

        cap_row(
            "Top-Left origin:", self._cap_tl, "Capture TL",
            f"X={self._tl_x}, Y={self._tl_y}", "_tl_var",
        )
        cap_row(
            "Tile width:", self._cap_tw, "Capture W",
            f"{self._tw} px", "_tw_var",
        )
        cap_row(
            "Tile height:", self._cap_th, "Capture H",
            f"{self._th} px", "_th_var",
        )

        tk.Frame(w, bg="#333355", height=1).pack(fill="x", padx=12, pady=8)

        self._spinboxes: dict = {}
        for attr, lbl_text, from_, to_, step in [
            ("SHOP_COLS",     "Shop columns:",           1, 20,   1),
            ("SHOP_ROWS",     "Shop rows:",               1, 30,   1),
            ("MIN_GOLD_FIND", "Min Gold Find value:",     1, 9999, 1),
            ("HOVER_DELAY_MS", "Hover delay (ms):",      50, 2000, 10),
            ("ALT_DELAY_MS",  "ALT compare delay (ms):", 50, 2000, 10),
        ]:
            fr = tk.Frame(w, bg=bg)
            fr.pack(fill="x", padx=12, pady=2)
            tk.Label(
                fr, text=lbl_text, width=26, anchor="w",
                fg=muted, bg=bg, font=("Segoe UI", 9),
            ).pack(side="left")
            sp = tk.Spinbox(
                fr, from_=from_, to=to_, width=6, increment=step,
                bg="#0d0d1a", fg=gold,
                insertbackground="white", font=("Courier New", 10),
            )
            sp.delete(0, "end")
            sp.insert(0, str(getattr(Config, attr)))
            sp.pack(side="left", padx=4)
            self._spinboxes[attr] = sp

        tk.Button(
            w, text="Apply & Close",
            command=lambda: self._apply(w),
            bg="#00c48c", fg="white", relief="flat",
            font=("Segoe UI", 11, "bold"), pady=6,
        ).pack(pady=14)

    def _cap_tl(self) -> None:
        x, y = pyautogui.position()
        self._tl_x, self._tl_y = x, y
        self._tl_var.set(f"X={x}, Y={y}")

    def _cap_tw(self) -> None:
        x, _ = pyautogui.position()
        self._tw = abs(x - self._tl_x)
        self._tw_var.set(f"{self._tw} px")

    def _cap_th(self) -> None:
        _, y = pyautogui.position()
        self._th = abs(y - self._tl_y)
        self._th_var.set(f"{self._th} px")

    def _apply(self, win: tk.Toplevel) -> None:
        Config.SHOP_ORIGIN_X = self._tl_x
        Config.SHOP_ORIGIN_Y = self._tl_y
        Config.TILE_W        = max(1, self._tw)
        Config.TILE_H        = max(1, self._th)
        for attr, sp in self._spinboxes.items():
            try:
                setattr(Config, attr, int(sp.get()))
            except ValueError:
                pass
        log.info(
            f"Calibration applied: origin=("
            f"{Config.SHOP_ORIGIN_X},{Config.SHOP_ORIGIN_Y}) "
            f"tile=({Config.TILE_W}x{Config.TILE_H}) "
            f"grid={Config.SHOP_COLS}x{Config.SHOP_ROWS}"
        )
        _save_state()
        win.destroy()


# ---------------------------------------------------------------------------
#  IGNORE LIST WINDOW
# ---------------------------------------------------------------------------

class IgnoreListWindow:
    def __init__(self, parent: tk.Tk) -> None:
        w = tk.Toplevel(parent)
        w.title("Ignore List")
        w.configure(bg="#1a1a2e")
        w.geometry("430x510")
        w.attributes("-topmost", True)

        fg    = "#e0e0e0"
        muted = "#888888"
        gold  = "#f5a623"
        bg    = "#1a1a2e"

        tk.Label(
            w, text="Ignore List",
            font=("Segoe UI", 13, "bold"),
            fg=gold, bg=bg,
        ).pack(pady=(10, 2))
        tk.Label(
            w,
            text=(
                "Items on this list bypass the equipped-item comparison.\n"
                "The bot stops for ANY flat +GF when the slot matches.\n"
                "Uses partial name match (case-insensitive).\n"
                "Example: 'ring of celestial' matches all variants."
            ),
            font=("Segoe UI", 9), fg=muted, bg=bg,
            wraplength=390, justify="left",
        ).pack(padx=12, pady=4)

        lf = tk.Frame(w, bg=bg)
        lf.pack(fill="both", expand=True, padx=12, pady=4)
        sb = tk.Scrollbar(lf)
        sb.pack(side="right", fill="y")
        self._lb = tk.Listbox(
            lf, yscrollcommand=sb.set, selectmode="single",
            bg="#0d0d1a", fg=gold,
            font=("Courier New", 10), activestyle="none",
        )
        self._lb.pack(fill="both", expand=True)
        sb.config(command=self._lb.yview)
        for entry in Config.IGNORE_LIST:
            self._lb.insert("end", entry)

        ef = tk.Frame(w, bg=bg)
        ef.pack(fill="x", padx=12, pady=4)
        self._entry = tk.Entry(
            ef, bg="#0d0d1a", fg=gold,
            insertbackground="white", font=("Courier New", 10),
        )
        self._entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Button(
            ef, text="Add", command=self._add,
            bg="#0f3460", fg="white", relief="flat",
            font=("Segoe UI", 9),
        ).pack(side="left")

        bf = tk.Frame(w, bg=bg)
        bf.pack(fill="x", padx=12, pady=4)
        tk.Button(
            bf, text="Remove Selected", command=self._remove,
            bg="#e94560", fg="white", relief="flat",
            font=("Segoe UI", 9),
        ).pack(side="left", padx=2)
        tk.Button(
            bf, text="Save & Close",
            command=lambda: self._save(w),
            bg="#00c48c", fg="white", relief="flat",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="right", padx=2)

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
        Config.IGNORE_LIST = [
            self._lb.get(i) for i in range(self._lb.size())
        ]
        log.info(f"Ignore list saved: {Config.IGNORE_LIST}")
        _save_state()
        win.destroy()


# ---------------------------------------------------------------------------
#  MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=" * 60)
    log.info(f"  {Config.APP_NAME}  v{Config.APP_VERSION}")
    log.info(f"  Log: {_log_file}")
    log.info(f"  Ignore list: {Config.IGNORE_LIST}")
    log.info("=" * 60)

    ocr     = OCREngine()
    capture = ScreenCapture()
    scanner = ShopScanner(ocr, capture)
    overlay = OverlayWindow(scanner)
    overlay.run()
    log.info("Bye!")


if __name__ == "__main__":
    main()
