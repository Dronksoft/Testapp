#!/usr/bin/env python3
"""
=============================================================================
  GoldSense  v2.0.0  --  Merchant Inventory Inspector  (Vision-AI Edition)
  Self-contained, single-file application.
=============================================================================

  WHAT CHANGED FROM v1
  --------------------
  v1 walked a fixed calibrated grid cell-by-cell using OCR regex.
  v2 is fundamentally different:

  STAGE 1 -- Shelf Detection (OpenCV blob analysis)
    Screenshot the entire trade window.
    Find every "item blob" -- the reddish-brown bordered item squares that
    contrast against the dark grey background -- using HSV colour masking
    and contour detection.  No grid calibration needed.

  STAGE 2 -- AI Stat Reading (moondream2, local, no API key)
    For each detected blob:
      a) Move cursor to blob centre -> screenshot the tooltip that pops up.
      b) Ask the local vision model:
           "Does this tooltip show a flat bonus to gold found (not percent)?
            Reply with just the integer, e.g. 14, or 0 if not present."
      c) If GF > 0:
           Hold ALT -> screenshot comparison tooltip.
           Ask:  "Left item flat gold find?  Right item flat gold find?
                  Reply: LEFT=<n> RIGHT=<n>"
           If shelf >= equipped OR equipped slot has no GF -> pause for you.

  WHY AI?
  -------
  The game's background, item art, font and tooltip layout change between
  patches and mod versions.  A regex over OCR output breaks every time.
  A small vision-language model describes what it *sees*, so it stays
  correct even when pixel-level appearance drifts.

  MODEL
  -----
  moondream2 (vikhyatk/moondream2) -- ~1.7 GB download once to
  ~/.cache/huggingface/.  CPU-capable; GPU accelerated if available.
  Fallback: if the model is unavailable, RapidOCR + regex is used
  automatically so the tool is never left completely non-functional.

  CONTROLS
  --------
  F6  Begin / Halt        F7  Next (pass current hit)
  F8  Hold / Resume       ESC Emergency halt

  SAFETY
  ------
  GoldSense NEVER Shift+Clicks.  All purchases are made manually by you.
=============================================================================
"""

import sys, os, re, time, json, queue, logging, threading, datetime
import traceback
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Any

# ---------------------------------------------------------------------------
#  Dependency gate
# ---------------------------------------------------------------------------
_MISSING: list = []
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext
except ImportError:
    _MISSING.append("tkinter (stdlib -- reinstall Python with tk support)")

try:
    from PIL import Image, ImageGrab, ImageDraw
    import numpy as np
except ImportError:
    _MISSING.append("Pillow / numpy")

try:
    import cv2
except ImportError:
    _MISSING.append("opencv-python")

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.02
except ImportError:
    _MISSING.append("pyautogui")

try:
    import keyboard
except ImportError:
    _MISSING.append("keyboard")

if _MISSING:
    msg = "GoldSense -- missing dependencies:\n\n" + "\n".join(f"  {m}" for m in _MISSING)
    msg += "\n\nRun INSTALL.bat -> option 1 to set up the environment."
    try:
        import tkinter as tk; from tkinter import messagebox
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("GoldSense -- Missing Packages", msg); r.destroy()
    except Exception:
        print(msg)
    sys.exit(1)

# ---------------------------------------------------------------------------
#  AI backend  (lazy-loaded on first use)
# ---------------------------------------------------------------------------
_AI_BACKEND = None
_MD_MODEL   = None
_MD_TOKENIZER = None

def _load_ai_backend(log):
    global _AI_BACKEND, _MD_MODEL, _MD_TOKENIZER
    if _AI_BACKEND is not None:
        return _AI_BACKEND
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch
        log.info("Loading moondream2 vision model (first run downloads ~1.7 GB)...")
        _MD_TOKENIZER = AutoTokenizer.from_pretrained(
            "vikhyatk/moondream2", trust_remote_code=True, revision="2025-01-09")
        _MD_MODEL = AutoModelForCausalLM.from_pretrained(
            "vikhyatk/moondream2", trust_remote_code=True, revision="2025-01-09",
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map="auto" if torch.cuda.is_available() else None,
            low_cpu_mem_usage=True)
        _MD_MODEL.eval()
        log.info("moondream2 loaded OK -- using AI backend.")
        _AI_BACKEND = "moondream"
        return "moondream"
    except Exception as exc:
        log.warning("moondream2 unavailable (%s) -- falling back to OCR.", exc)
    try:
        from rapidocr_onnxruntime import RapidOCR
        _AI_BACKEND = "ocr"
        log.info("OCR fallback backend active.")
        return "ocr"
    except Exception as exc2:
        log.error("No AI or OCR backend available: %s", exc2)
        _AI_BACKEND = "none"
        return "none"


def _ask_vision(pil_img, prompt, log):
    """Send a PIL image + text prompt to whichever backend is active."""
    backend = _load_ai_backend(log)
    if backend == "moondream":
        try:
            enc = _MD_MODEL.encode_image(pil_img)
            answer = _MD_MODEL.query(enc, prompt)["answer"].strip()
            log.debug("moondream answer: %r", answer)
            return answer
        except Exception as exc:
            log.warning("moondream query failed: %s", exc)
            return ""
    if backend == "ocr":
        try:
            from rapidocr_onnxruntime import RapidOCR
            ocr = RapidOCR()
            arr = np.array(pil_img)
            result, _ = ocr(arr)
            text = " ".join(r[1] for r in result) if result else ""
            log.debug("OCR fallback text: %r", text)
            return text
        except Exception as exc:
            log.warning("OCR fallback failed: %s", exc)
            return ""
    return ""


# ---------------------------------------------------------------------------
#  Config
# ---------------------------------------------------------------------------
@dataclass
class Config:
    # blob detection
    BLOB_HUE_LO:  int = 0
    BLOB_HUE_HI:  int = 25
    BLOB_SAT_LO:  int = 60
    BLOB_VAL_LO:  int = 60
    MIN_BLOB_AREA: int = 500
    MAX_BLOB_AREA: int = 12000

    # timing (ms)
    HOVER_DELAY_MS:  int = 200
    ALT_DELAY_MS:    int = 280
    MOVE_DELAY_MS:   int = 60

    # tooltip crop
    TOOLTIP_OFFSET_X: int = 20
    TOOLTIP_OFFSET_Y: int = -10
    TOOLTIP_W:  int = 460
    TOOLTIP_H:  int = 360

    # scan region (0,0,0,0 = full screen)
    SCAN_LEFT:   int = 0
    SCAN_TOP:    int = 0
    SCAN_RIGHT:  int = 0
    SCAN_BOTTOM: int = 0

    MIN_GF: int = 1
    RESTOCK_KEY: str = "r"
    PASS_LIST: List[str] = field(default_factory=list)
    LOG_DIR: str = "logs"
    PREFS_FILE: str = "_tools/prefs.json"


# ---------------------------------------------------------------------------
#  Logging
# ---------------------------------------------------------------------------
def _setup_logging(cfg):
    log_dir = Path(cfg.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"session_{stamp}.log"
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt); fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt); ch.setLevel(logging.DEBUG)
    log = logging.getLogger("GoldSense")
    log.setLevel(logging.DEBUG)
    log.addHandler(fh); log.addHandler(ch)
    log.info("Session log: %s", log_file)
    return log


# ---------------------------------------------------------------------------
#  Prefs
# ---------------------------------------------------------------------------
def load_prefs(cfg, log):
    pf = Path(cfg.PREFS_FILE)
    if not pf.exists(): return
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
        for k in ["hover_delay_ms","alt_delay_ms","scan_left","scan_top",
                  "scan_right","scan_bottom","pass_list","blob_hue_lo",
                  "blob_hue_hi","blob_sat_lo","blob_val_lo"]:
            attr = k.upper()
            if k in data and hasattr(cfg, attr):
                setattr(cfg, attr, data[k])
        log.info("Prefs loaded from %s", pf)
    except Exception as exc:
        log.warning("Could not load prefs: %s", exc)


def save_prefs(cfg, log):
    pf = Path(cfg.PREFS_FILE)
    pf.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "hover_delay_ms":  cfg.HOVER_DELAY_MS,
        "alt_delay_ms":    cfg.ALT_DELAY_MS,
        "scan_left":       cfg.SCAN_LEFT,
        "scan_top":        cfg.SCAN_TOP,
        "scan_right":      cfg.SCAN_RIGHT,
        "scan_bottom":     cfg.SCAN_BOTTOM,
        "pass_list":       cfg.PASS_LIST,
        "blob_hue_lo":     cfg.BLOB_HUE_LO,
        "blob_hue_hi":     cfg.BLOB_HUE_HI,
        "blob_sat_lo":     cfg.BLOB_SAT_LO,
        "blob_val_lo":     cfg.BLOB_VAL_LO,
    }
    pf.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info("Prefs saved to %s", pf)


# ---------------------------------------------------------------------------
#  Screenshot helpers
# ---------------------------------------------------------------------------
def screenshot_region(left, top, right, bottom):
    return ImageGrab.grab(bbox=(left, top, right, bottom))


def screenshot_full(cfg):
    if cfg.SCAN_RIGHT > cfg.SCAN_LEFT and cfg.SCAN_BOTTOM > cfg.SCAN_TOP:
        return screenshot_region(cfg.SCAN_LEFT, cfg.SCAN_TOP,
                                  cfg.SCAN_RIGHT, cfg.SCAN_BOTTOM)
    return ImageGrab.grab()


def screenshot_tooltip(cx, cy, cfg):
    l = cx + cfg.TOOLTIP_OFFSET_X
    t = cy + cfg.TOOLTIP_OFFSET_Y
    r = l + cfg.TOOLTIP_W
    b = t + cfg.TOOLTIP_H
    sw, sh = pyautogui.size()
    if r > sw: l = sw - cfg.TOOLTIP_W; r = sw
    if b > sh: t = sh - cfg.TOOLTIP_H; b = sh
    if l < 0:  l = 0; r = cfg.TOOLTIP_W
    if t < 0:  t = 0; b = cfg.TOOLTIP_H
    return screenshot_region(l, t, r, b)


# ---------------------------------------------------------------------------
#  Blob detection
# ---------------------------------------------------------------------------
@dataclass
class ItemBlob:
    cx: int
    cy: int
    x: int
    y: int
    w: int
    h: int
    area: int


def find_item_blobs(cfg, log):
    """
    Screenshot the scan region, find reddish-brown item squares via
    HSV colour masking + contour analysis.
    Returns list of ItemBlob sorted left-to-right, top-to-bottom.
    """
    shot = screenshot_full(cfg)
    arr  = cv2.cvtColor(np.array(shot.convert("RGB")), cv2.COLOR_RGB2HSV)

    lo = np.array([cfg.BLOB_HUE_LO, cfg.BLOB_SAT_LO, cfg.BLOB_VAL_LO])
    hi = np.array([cfg.BLOB_HUE_HI, 255, 255])
    mask = cv2.inRange(arr, lo, hi)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    off_x = cfg.SCAN_LEFT if cfg.SCAN_RIGHT > cfg.SCAN_LEFT else 0
    off_y = cfg.SCAN_TOP  if cfg.SCAN_BOTTOM > cfg.SCAN_TOP  else 0

    blobs = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < cfg.MIN_BLOB_AREA or area > cfg.MAX_BLOB_AREA:
            continue
        bx, by, bw, bh = cv2.boundingRect(cnt)
        cx = off_x + bx + bw // 2
        cy = off_y + by + bh // 2
        blobs.append(ItemBlob(
            cx=cx, cy=cy,
            x=off_x + bx, y=off_y + by,
            w=bw, h=bh, area=int(area)))

    blobs.sort(key=lambda b: (b.y // 20, b.x))
    log.info("Blob detection: %d item(s) found.", len(blobs))
    return blobs


# ---------------------------------------------------------------------------
#  AI analysis helpers
# ---------------------------------------------------------------------------
_FLAT_GF_RE   = re.compile(r'\b(\d+)\b')
_COMPARE_RE   = re.compile(r'LEFT\s*=\s*(\d+).*?RIGHT\s*=\s*(\d+)', re.I | re.S)


def _parse_int(text, default=0):
    m = _FLAT_GF_RE.search(text)
    return int(m.group(1)) if m else default


def ask_flat_gf(img, log):
    prompt = (
        "Look at this game item tooltip screenshot. "
        "Does it show a flat (not percentage) bonus to gold found? "
        "Reply with only the integer number, e.g. '14', or '0' if none."
    )
    raw = _ask_vision(img, prompt, log)
    val = _parse_int(raw, 0)
    log.debug("ask_flat_gf -> raw=%r parsed=%d", raw, val)
    return val


def ask_item_name(img, log):
    prompt = (
        "Look at this game item tooltip screenshot. "
        "What is the item's name (the coloured line at the very top)? "
        "Reply with only the item name, nothing else."
    )
    raw = _ask_vision(img, prompt, log)
    log.debug("ask_item_name -> %r", raw)
    return raw.strip()


def ask_compare_gf(img, log):
    """
    Ask the AI about the ALT comparison tooltip (two items side-by-side).
    Returns (shelf_gf, equipped_gf).
    """
    prompt = (
        "This screenshot shows two game item tooltips side by side for comparison. "
        "For each tooltip, find the flat (not percent) bonus to gold found. "
        "Reply in exactly this format: LEFT=<number> RIGHT=<number>  "
        "Use 0 if not present. Example: LEFT=14 RIGHT=0"
    )
    raw = _ask_vision(img, prompt, log)
    log.debug("ask_compare_gf -> raw=%r", raw)
    m = _COMPARE_RE.search(raw)
    if m:
        return int(m.group(1)), int(m.group(2))
    nums = _FLAT_GF_RE.findall(raw)
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    if len(nums) == 1:
        return int(nums[0]), 0
    return 0, 0


# ---------------------------------------------------------------------------
#  Hit record
# ---------------------------------------------------------------------------
@dataclass
class HitRecord:
    lap: int
    blob_idx: int
    item_name: str
    shelf_gf: int
    equipped_gf: int
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().strftime("%H:%M:%S"))
    tooltip_path: str = ""
    compare_path: str = ""
    decision: str = ""   # "buy" | "pass" | "pending"


# ---------------------------------------------------------------------------
#  Inspector engine
# ---------------------------------------------------------------------------
class Inspector:
    def __init__(self, cfg, log, ui_queue):
        self.cfg = cfg
        self.log = log
        self.ui_q = ui_queue
        self._state = "halted"
        self._lock  = threading.Lock()
        self._thread = None
        self.lap = 0
        self.blobs_found = 0
        self.visited = 0
        self.hits = []
        self._current_hit = None
        self._log_dir = Path(cfg.LOG_DIR)
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def _set(self, state):
        with self._lock:
            self._state = state
        self._push("state", state)

    def _get(self):
        with self._lock:
            return self._state

    def _push(self, kind, payload=None):
        self.ui_q.put_nowait({"kind": kind, "payload": payload})

    def begin(self):
        if self._get() not in ("halted",): return
        self._set("running")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def halt(self):
        self._set("halted")
        self.log.info("Halt requested.")

    def hold(self):
        s = self._get()
        if s == "running":   self._set("holding")
        elif s in ("holding", "paused_hit"): self._set("running")

    def next_item(self):
        if self._get() == "paused_hit" and self._current_hit:
            self._current_hit.decision = "pass"
            self._push("hit_resolved", self._current_hit)
            self._set("running")

    def _run(self):
        self.log.info("Scan started.")
        self._push("log", "Scan started.")
        cfg = self.cfg
        try:
            while self._get() not in ("halted",):
                self._wait_not_holding()
                if self._get() == "halted": break

                self.lap += 1
                self._push("lap", self.lap)
                self._push("log", f"=== Shelf restocked (lap #{self.lap}) ===")
                self.log.info("=== Lap %d -- detecting blobs ===", self.lap)

                blobs = find_item_blobs(cfg, self.log)
                self.blobs_found = len(blobs)
                self._push("blobs", self.blobs_found)

                if not blobs:
                    self.log.warning("No blobs detected -- check scan region / blob tuning.")
                    self._push("log", "No items detected. Check calibration.")
                    time.sleep(1.0)
                    self._restock()
                    continue

                for idx, blob in enumerate(blobs):
                    if self._get() == "halted": break
                    self._wait_not_holding()
                    if self._get() == "halted": break

                    self.visited += 1
                    self._push("visited", self.visited)
                    self._push("item_pos", f"[{idx+1}/{len(blobs)}]")
                    self.log.debug("Blob %d/%d centre=(%d,%d) area=%d",
                                   idx+1, len(blobs), blob.cx, blob.cy, blob.area)

                    pyautogui.moveTo(blob.cx, blob.cy, duration=0.04)
                    time.sleep(cfg.HOVER_DELAY_MS / 1000)

                    tip_img  = screenshot_tooltip(blob.cx, blob.cy, cfg)
                    tip_path = self._save_img(tip_img, f"tip_L{self.lap}_B{idx}")

                    item_name = ask_item_name(tip_img, self.log)
                    shelf_gf  = ask_flat_gf(tip_img, self.log)

                    self.log.info("Blob %d: name=%r flat_gf=%d", idx+1, item_name, shelf_gf)
                    self._push("log", f"  [{idx+1}] {item_name!r} -> flat GF={shelf_gf}")

                    if shelf_gf < cfg.MIN_GF:
                        continue

                    keyboard.press("alt")
                    time.sleep(cfg.ALT_DELAY_MS / 1000)
                    cmp_img  = screenshot_tooltip(blob.cx, blob.cy, cfg)
                    keyboard.release("alt")
                    cmp_path = self._save_img(cmp_img, f"cmp_L{self.lap}_B{idx}")

                    shelf_gf2, equipped_gf = ask_compare_gf(cmp_img, self.log)
                    if shelf_gf2 > 0:
                        shelf_gf = shelf_gf2

                    self.log.info("Compare: shelf=%d equipped=%d", shelf_gf, equipped_gf)
                    self._push("log",
                        f"  [{idx+1}] Compare: shelf={shelf_gf} equipped={equipped_gf}")

                    on_pass_list = any(
                        p.lower() in item_name.lower()
                        for p in cfg.PASS_LIST if p)

                    should_pause = (
                        on_pass_list
                        or equipped_gf == 0
                        or shelf_gf >= equipped_gf
                    )

                    if should_pause:
                        hr = HitRecord(
                            lap=self.lap, blob_idx=idx,
                            item_name=item_name,
                            shelf_gf=shelf_gf,
                            equipped_gf=equipped_gf,
                            tooltip_path=tip_path,
                            compare_path=cmp_path,
                            decision="pending",
                        )
                        self.hits.append(hr)
                        self._current_hit = hr
                        self._push("hits_count", len(self.hits))
                        self._push("hit", hr)
                        self._set("paused_hit")
                        self.log.info("HIT paused: %s (shelf=%d worn=%d)",
                                      item_name, shelf_gf, equipped_gf)

                        while self._get() == "paused_hit":
                            time.sleep(0.1)

                        self._current_hit = None

                if self._get() != "halted":
                    self._push("log", "Shelf exhausted -- restocking.")
                    self._restock()

        except Exception as exc:
            self.log.critical("Inspector crash: %s", exc, exc_info=True)
            self._push("log", f"CRASH: {exc}")
            self._push("crash", traceback.format_exc())
        finally:
            self._set("halted")
            self._push("log", "Inspector stopped.")
            self.log.info("Inspector stopped.")

    def _restock(self):
        pyautogui.press(self.cfg.RESTOCK_KEY)
        time.sleep(0.6)

    def _wait_not_holding(self):
        while self._get() == "holding":
            time.sleep(0.1)

    def _save_img(self, img, name):
        p = self._log_dir / f"{name}.png"
        img.save(p)
        return str(p)


# ---------------------------------------------------------------------------
#  Region select overlay
# ---------------------------------------------------------------------------
class RegionSelectOverlay:
    def __init__(self, parent_tk, on_done):
        self._on_done = on_done
        self._start = None
        self._rect  = None

        self.win = tk.Toplevel(parent_tk)
        self.win.attributes("-fullscreen", True)
        self.win.attributes("-alpha", 0.35)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="black")
        self.win.overrideredirect(True)

        self.canvas = tk.Canvas(self.win, cursor="crosshair", bg="black",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>",   self._on_press)
        self.canvas.bind("<B1-Motion>",       self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.win.bind("<Escape>", lambda e: self._cancel())

        self.canvas.create_text(
            self.canvas.winfo_screenwidth() // 2, 40,
            text="Drag to select the trade/shop window area.  ESC to cancel.",
            fill="white", font=("Segoe UI", 18, "bold"))

    def _on_press(self, e):
        self._start = (e.x, e.y)
        if self._rect: self.canvas.delete(self._rect)

    def _on_drag(self, e):
        if not self._start: return
        if self._rect: self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(
            *self._start, e.x, e.y,
            outline="#00ff88", width=2, fill="")

    def _on_release(self, e):
        if not self._start: return
        x1 = min(self._start[0], e.x); y1 = min(self._start[1], e.y)
        x2 = max(self._start[0], e.x); y2 = max(self._start[1], e.y)
        self.win.destroy()
        if x2 - x1 > 20 and y2 - y1 > 20:
            self._on_done(x1, y1, x2, y2)

    def _cancel(self):
        self.win.destroy()
        self._on_done(None, None, None, None)


# ---------------------------------------------------------------------------
#  Calibration window
# ---------------------------------------------------------------------------
class CalibrationWindow:
    def __init__(self, parent, cfg, log, on_close):
        self.cfg = cfg; self.log = log
        self.win = tk.Toplevel(parent)
        self.win.title("GoldSense \u2013 Calibrate")
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        self._on_close = on_close

        f = tk.Frame(self.win, padx=10, pady=8)
        f.pack(fill="both", expand=True)

        tk.Label(f, text="Scan Region  (0 = full screen)",
                 font=("Segoe UI", 9, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 2))
        for i, (lbl, attr) in enumerate([
                ("Left",  "SCAN_LEFT"), ("Top",   "SCAN_TOP"),
                ("Right", "SCAN_RIGHT"), ("Bot",  "SCAN_BOTTOM")]):
            tk.Label(f, text=lbl).grid(row=1, column=i*2, sticky="e", padx=(4, 1))
            var = tk.IntVar(value=getattr(cfg, attr))
            setattr(self, f"_var_{attr}", var)
            tk.Spinbox(f, textvariable=var, from_=0, to=9999, width=6).grid(
                row=1, column=i*2+1, padx=(0, 4))

        tk.Button(f, text="\u25a6  Select Region by Drag", command=self._drag_region,
                  bg="#2a6a3a", fg="white", relief="flat", padx=6).grid(
            row=2, column=0, columnspan=4, sticky="ew", pady=4)

        tk.Label(f, text="Item Blob Detection",
                 font=("Segoe UI", 9, "bold")).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(6, 2))
        blob_params = [
            ("Hue Lo",  "BLOB_HUE_LO",  0,  179),
            ("Hue Hi",  "BLOB_HUE_HI",  0,  179),
            ("Sat Lo",  "BLOB_SAT_LO",  0,  255),
            ("Val Lo",  "BLOB_VAL_LO",  0,  255),
        ]
        for i, (lbl, attr, lo, hi) in enumerate(blob_params):
            r, c = divmod(i, 2)
            tk.Label(f, text=lbl).grid(row=4+r, column=c*2, sticky="e", padx=(4, 1))
            var = tk.IntVar(value=getattr(cfg, attr))
            setattr(self, f"_var_{attr}", var)
            tk.Spinbox(f, textvariable=var, from_=lo, to=hi, width=6).grid(
                row=4+r, column=c*2+1, padx=(0, 4))

        tk.Label(f, text="Min Area").grid(row=6, column=0, sticky="e", padx=(4,1))
        self._var_MIN = tk.IntVar(value=cfg.MIN_BLOB_AREA)
        tk.Spinbox(f, textvariable=self._var_MIN, from_=50, to=50000, width=7).grid(row=6, column=1)
        tk.Label(f, text="Max Area").grid(row=6, column=2, sticky="e", padx=(4,1))
        self._var_MAX = tk.IntVar(value=cfg.MAX_BLOB_AREA)
        tk.Spinbox(f, textvariable=self._var_MAX, from_=50, to=200000, width=7).grid(row=6, column=3)

        tk.Label(f, text="Timing (ms)",
                 font=("Segoe UI", 9, "bold")).grid(
            row=7, column=0, columnspan=4, sticky="w", pady=(6, 2))
        for i, (lbl, attr) in enumerate([
                ("Hover", "HOVER_DELAY_MS"), ("Alt",  "ALT_DELAY_MS"),
                ("Move",  "MOVE_DELAY_MS")]):
            r, c = divmod(i, 2)
            tk.Label(f, text=lbl).grid(row=8+r, column=c*2, sticky="e", padx=(4,1))
            var = tk.IntVar(value=getattr(cfg, attr))
            setattr(self, f"_var_{attr}", var)
            tk.Spinbox(f, textvariable=var, from_=50, to=2000, width=6).grid(
                row=8+r, column=c*2+1, padx=(0,4))

        tk.Button(f, text="\u25b6  Test Detection (snapshot)", command=self._test_detect,
                  bg="#336699", fg="white", relief="flat", padx=6).grid(
            row=10, column=0, columnspan=4, sticky="ew", pady=4)
        self._result_lbl = tk.Label(f, text="", fg="#44bb44")
        self._result_lbl.grid(row=11, column=0, columnspan=4)

        tk.Button(f, text="Apply & Close", command=self._apply,
                  bg="#1a6b1a", fg="white", relief="flat", padx=8).grid(
            row=12, column=0, columnspan=4, sticky="ew", pady=4)

    def _drag_region(self):
        self.win.withdraw()
        time.sleep(0.3)
        def on_done(l, t, r, b):
            self.win.deiconify()
            if l is None: return
            self._var_SCAN_LEFT.set(l); self._var_SCAN_TOP.set(t)
            self._var_SCAN_RIGHT.set(r); self._var_SCAN_BOTTOM.set(b)
            self._result_lbl.config(text=f"Region: ({l},{t}) \u2013 ({r},{b})")
        RegionSelectOverlay(self.win, on_done)

    def _test_detect(self):
        self._read_vars()
        blobs = find_item_blobs(self.cfg, self.log)
        self._result_lbl.config(
            text=f"Detected {len(blobs)} item blob(s)."
                 + (" \u2713" if blobs else "  -- adjust HSV / area params"))

    def _read_vars(self):
        for attr in ["SCAN_LEFT","SCAN_TOP","SCAN_RIGHT","SCAN_BOTTOM",
                     "BLOB_HUE_LO","BLOB_HUE_HI","BLOB_SAT_LO","BLOB_VAL_LO",
                     "HOVER_DELAY_MS","ALT_DELAY_MS","MOVE_DELAY_MS"]:
            var = getattr(self, f"_var_{attr}", None)
            if var is not None: setattr(self.cfg, attr, var.get())
        self.cfg.MIN_BLOB_AREA = self._var_MIN.get()
        self.cfg.MAX_BLOB_AREA = self._var_MAX.get()

    def _apply(self):
        self._read_vars()
        save_prefs(self.cfg, self.log)
        self._close()

    def _close(self):
        self.win.destroy()
        self._on_close()


# ---------------------------------------------------------------------------
#  Pass-list editor
# ---------------------------------------------------------------------------
class PassListWindow:
    def __init__(self, parent, cfg, log):
        self.cfg = cfg; self.log = log
        self.win = tk.Toplevel(parent)
        self.win.title("GoldSense \u2013 Pass List")
        self.win.resizable(False, False)
        tk.Label(self.win,
                 text="Items on this list are always flagged regardless of stats.",
                 padx=10, pady=5).pack()
        self.lb = tk.Listbox(self.win, width=45, height=12)
        self.lb.pack(padx=10)
        for item in cfg.PASS_LIST:
            self.lb.insert(tk.END, item)
        ef = tk.Frame(self.win)
        ef.pack(padx=10, pady=4, fill="x")
        self.entry = tk.Entry(ef)
        self.entry.pack(side="left", fill="x", expand=True)
        tk.Button(ef, text="Add",    command=self._add).pack(side="left", padx=2)
        tk.Button(ef, text="Remove", command=self._remove).pack(side="left")
        tk.Button(self.win, text="Save & Close", command=self._save,
                  bg="#1a6b1a", fg="white", relief="flat").pack(pady=4)

    def _add(self):
        v = self.entry.get().strip()
        if v: self.lb.insert(tk.END, v); self.entry.delete(0, tk.END)

    def _remove(self):
        sel = self.lb.curselection()
        if sel: self.lb.delete(sel[0])

    def _save(self):
        self.cfg.PASS_LIST = list(self.lb.get(0, tk.END))
        save_prefs(self.cfg, self.log)
        self.win.destroy()


# ---------------------------------------------------------------------------
#  Main overlay / UI
# ---------------------------------------------------------------------------
DARK_BG  = "#1a1a1a"
MID_BG   = "#242424"
LIGHT_FG = "#d4d0c8"
ACC_GRN  = "#22bb55"
ACC_RED  = "#cc3333"
ACC_ORG  = "#dd9922"
BTN_FONT = ("Segoe UI", 9, "bold")
LBL_FONT = ("Segoe UI", 9)
LOG_FONT = ("Consolas", 8)


class GoldSenseApp:
    def __init__(self, root):
        self.root = root
        root.title("GoldSense v2.0.0")
        root.configure(bg=DARK_BG)
        root.resizable(False, False)
        root.attributes("-topmost", True)

        self.cfg  = Config()
        self.log  = _setup_logging(self.cfg)
        load_prefs(self.cfg, self.log)

        self.ui_q = queue.Queue()
        self.insp = Inspector(self.cfg, self.log, self.ui_q)

        self._build_ui()
        self._register_hotkeys()
        root.after(120, self._poll_queue)

    def _build_ui(self):
        root = self.root
        hdr = tk.Frame(root, bg="#111", pady=4)
        hdr.pack(fill="x")
        tk.Label(hdr, text="GoldSense  v2.0.0", bg="#111", fg="#e8c84a",
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=10)
        self._state_lbl = tk.Label(hdr, text="Halted", bg="#111", fg=ACC_RED,
                                   font=("Segoe UI", 9, "bold"))
        self._state_lbl.pack(side="right", padx=10)

        sf = tk.Frame(root, bg=MID_BG, pady=3)
        sf.pack(fill="x", padx=2, pady=(2, 0))
        self._stat_vars = {}
        for lbl, key, w in [
                ("Lap",      "lap",     4),
                ("Blobs",    "blobs",   5),
                ("Visited",  "visited", 6),
                ("Hits",     "hits",    4),
                ("Best +Flat","best",   8)]:
            tk.Label(sf, text=lbl, bg=MID_BG, fg="#888",
                     font=("Segoe UI", 7)).pack(side="left", padx=(6,1))
            v = tk.StringVar(value="0")
            self._stat_vars[key] = v
            tk.Label(sf, textvariable=v, bg=MID_BG, fg=LIGHT_FG,
                     font=("Segoe UI", 8, "bold"), width=w).pack(side="left")

        self._item_lbl = tk.Label(root, text="Item: \u2013", bg=DARK_BG,
                                  fg="#aaaaaa", font=LBL_FONT, anchor="w")
        self._item_lbl.pack(fill="x", padx=8, pady=(4, 0))

        self._hit_banner = tk.Label(root, text="No hits yet.", bg="#1f3020",
                                    fg="#88ff99", font=("Segoe UI", 9, "bold"),
                                    pady=5, relief="groove")
        self._hit_banner.pack(fill="x", padx=4, pady=3)

        bf = tk.Frame(root, bg=DARK_BG)
        bf.pack(fill="x", padx=4, pady=2)
        self._btn_begin = self._btn(bf, "BEGIN  (F6)", self._on_begin,
                                    ACC_GRN, "white", width=14)
        self._btn_begin.pack(side="left", padx=2, expand=True, fill="x")
        self._btn_next  = self._btn(bf, "NEXT  (F7)", self._on_next,
                                    "#2255aa", "white", width=10)
        self._btn_next.pack(side="left", padx=2, expand=True, fill="x")

        bf2 = tk.Frame(root, bg=DARK_BG)
        bf2.pack(fill="x", padx=4, pady=(0, 2))
        self._btn_hold = self._btn(bf2, "HOLD  (F8)", self._on_hold, "#555", LIGHT_FG, width=10)
        self._btn_hold.pack(side="left", padx=2, expand=True, fill="x")
        self._btn(bf2, "Calibrate", self._open_calib, "#336699", "white", width=10).pack(
            side="left", padx=2, expand=True, fill="x")
        self._btn(bf2, "Pass List", self._open_pass,  "#555566", LIGHT_FG, width=10).pack(
            side="left", padx=2, expand=True, fill="x")
        self._btn(bf2, "Open Log",  self._open_log,   "#333",    LIGHT_FG, width=10).pack(
            side="left", padx=2, expand=True, fill="x")

        tk.Label(root, text="F6=Begin/Halt  F7=Next  F8=Hold  ESC=Halt",
                 bg=DARK_BG, fg="#555", font=("Segoe UI", 7)).pack(pady=(0, 2))

        lf = tk.LabelFrame(root, text=" Activity Log ", bg=DARK_BG, fg="#666",
                            font=("Segoe UI", 8))
        lf.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._log_box = scrolledtext.ScrolledText(
            lf, bg="#0d0d0d", fg="#888877", font=LOG_FONT,
            height=8, state="disabled", wrap="word")
        self._log_box.pack(fill="both", expand=True)

        hf = tk.LabelFrame(root, text=" Hit History ", bg=DARK_BG, fg="#666",
                            font=("Segoe UI", 8))
        hf.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._hit_box = scrolledtext.ScrolledText(
            hf, bg="#0d0d0d", fg="#88ff99", font=LOG_FONT,
            height=5, state="disabled", wrap="word")
        self._hit_box.pack(fill="both", expand=True)

    @staticmethod
    def _btn(parent, text, cmd, bg, fg, width=8):
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg, relief="flat",
                         font=BTN_FONT, width=width,
                         activebackground=bg, activeforeground=fg,
                         bd=0, padx=4, pady=4)

    def _on_begin(self):
        s = self.insp._get()
        if s == "halted":  self.insp.begin()
        else:              self.insp.halt()

    def _on_next(self):  self.insp.next_item()
    def _on_hold(self):  self.insp.hold()

    def _open_calib(self):
        if not getattr(self, "_calib_open", False):
            self._calib_open = True
            CalibrationWindow(self.root, self.cfg, self.log,
                              on_close=lambda: setattr(self, "_calib_open", False))

    def _open_pass(self):  PassListWindow(self.root, self.cfg, self.log)

    def _open_log(self):
        log_dir = Path(self.cfg.LOG_DIR)
        if sys.platform == "win32":  os.startfile(str(log_dir))
        else:
            import subprocess; subprocess.Popen(["xdg-open", str(log_dir)])

    def _register_hotkeys(self):
        try:
            keyboard.add_hotkey("f6",  self._on_begin,  suppress=False)
            keyboard.add_hotkey("f7",  self._on_next,   suppress=False)
            keyboard.add_hotkey("f8",  self._on_hold,   suppress=False)
            keyboard.add_hotkey("esc", self.insp.halt,  suppress=False)
        except Exception as exc:
            self.log.warning("Hotkey registration failed: %s", exc)

    def _poll_queue(self):
        try:
            while True:
                msg = self.ui_q.get_nowait()
                kind, payload = msg["kind"], msg["payload"]
                if kind == "state":
                    lbl_map = {
                        "halted":     ("Halted",    ACC_RED),
                        "running":    ("Running",   ACC_GRN),
                        "holding":    ("Holding",   ACC_ORG),
                        "paused_hit": ("HIT FOUND", "#ff88ff"),
                    }
                    txt, col = lbl_map.get(payload, ("?", LIGHT_FG))
                    self._state_lbl.config(text=txt, fg=col)
                    if payload == "halted":
                        self._btn_begin.config(text="BEGIN  (F6)", bg=ACC_GRN)
                    elif payload == "running":
                        self._btn_begin.config(text="HALT  (F6)", bg=ACC_RED)
                elif kind == "lap":         self._stat_vars["lap"].set(str(payload))
                elif kind == "blobs":       self._stat_vars["blobs"].set(str(payload))
                elif kind == "visited":     self._stat_vars["visited"].set(str(payload))
                elif kind == "hits_count":  self._stat_vars["hits"].set(str(payload))
                elif kind == "item_pos":    self._item_lbl.config(text=f"Item: {payload}")
                elif kind == "hit":
                    hr = payload
                    best = max(int(self._stat_vars["best"].get().lstrip("+") or 0), hr.shelf_gf)
                    self._stat_vars["best"].set(f"+{best}")
                    banner = (
                        f"HIT: {hr.item_name}\n"
                        f"Shelf GF: +{hr.shelf_gf}   Equipped: "
                        + (f"+{hr.equipped_gf}" if hr.equipped_gf else "none")
                    )
                    self._hit_banner.config(text=banner, bg="#2a1a3a", fg="#ff88ff")
                    self._append_hit_box(
                        f"[{hr.timestamp}] Lap {hr.lap} Item {hr.blob_idx+1}: "
                        f"{hr.item_name}  shelf={hr.shelf_gf} worn={hr.equipped_gf}\n")
                elif kind == "hit_resolved":
                    self._hit_banner.config(
                        text="Passed \u2013 resuming scan.", bg="#1f2a1f", fg="#88ff99")
                elif kind == "log":   self._append_log(str(payload))
                elif kind == "crash":
                    self._append_log("CRASH -- see log file for details.")
                    messagebox.showerror("GoldSense Crash", str(payload)[:600])
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _append_log(self, text):
        self._log_box.config(state="normal")
        self._log_box.insert(tk.END, text + "\n")
        self._log_box.see(tk.END)
        self._log_box.config(state="disabled")

    def _append_hit_box(self, text):
        self._hit_box.config(state="normal")
        self._hit_box.insert(tk.END, text)
        self._hit_box.see(tk.END)
        self._hit_box.config(state="disabled")


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------
def main():
    root = tk.Tk()
    app  = GoldSenseApp(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        app.insp.halt()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.getLogger("GoldSense").critical("Fatal: %s", exc, exc_info=True)
        raise
