#!/usr/bin/env python3
"""
=============================================================================
  GoldSense  v2.1.0  --  Merchant Inventory Inspector  (Vision-AI Edition)
  Self-contained, single-file application.
=============================================================================

  ARCHITECTURE  (v2.1 rewrite)
  -----------------------------
  The old grid-walker relied on a calibrated cell map.  It broke whenever
  the shop window moved, resized, or the game updated its UI.

  v2.1 works purely from screenshots and a lightweight local LLM:

  STAGE 1 -- Item discovery (OpenCV)
    Grab a screenshot of the scan region (or full screen).
    Items sit inside reddish/brownish bordered squares on a dark background.
    HSV masking + contour detection finds every such square and returns its
    centre coordinate.  No grid calibration required.

  STAGE 2 -- Stat reading (moondream2 VLM, local, no API key)
    For each discovered item:
      a) Move cursor to item centre, wait for tooltip to appear.
      b) Screenshot the tooltip region.
      c) Ask the LLM:
           "Does this tooltip show a FLAT (not %) bonus to gold found?
            Reply with only the integer, e.g. 14, or 0 if not present."
      d) If GF > 0:
           Hold ALT to show the comparison panel (selected vs equipped).
           Screenshot the comparison.
           Ask the LLM:
             "LEFT item flat gold find?  RIGHT item flat gold find?
              Reply: LEFT=<n> RIGHT=<n>"
           If shelf_gf >= equipped_gf OR no equipped gf -> pause for operator.

  WHY A VISION MODEL?
  -------------------
  The Hell 4 mod, patches, and different NPC shops change:
    - background colours and textures
    - item art / border colours
    - tooltip font and layout
  Hard-coded OCR regex breaks on every update.  A VLM reads what it *sees*
  and describes it in natural language, so it stays correct across changes.

  MODEL
  -----
  moondream2 (vikhyatk/moondream2, revision 2025-01-09)
  ~1.7 GB one-time download to ~/.cache/huggingface/
  GPU-accelerated if CUDA is available; CPU-capable otherwise (~4-8 s/query).
  Fallback: RapidOCR + regex if the model cannot be loaded.

  CONTROLS
  --------
  F6  Begin / Halt        F7  Next (pass current hit)
  F8  Hold / Resume       ESC Emergency halt

  SAFETY
  ------
  GoldSense NEVER Shift+Clicks.  All purchases are manual.
=============================================================================
"""

import sys, os, re, time, json, queue, logging, threading, datetime, traceback
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

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
    from PIL import Image, ImageGrab, ImageDraw, ImageFont
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
    pyautogui.PAUSE    = 0.02
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
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("GoldSense -- Missing Packages", msg); r.destroy()
    except Exception:
        print(msg)
    sys.exit(1)

# ---------------------------------------------------------------------------
#  AI backend  (lazy-loaded on first query)
# ---------------------------------------------------------------------------
_AI_BACKEND   = None   # "moondream" | "ocr" | "none"
_MD_MODEL     = None
_MD_TOKENIZER = None


def _load_ai_backend(log):
    global _AI_BACKEND, _MD_MODEL, _MD_TOKENIZER
    if _AI_BACKEND is not None:
        return _AI_BACKEND

    # --- try moondream2 ---
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch
        log.info("Loading moondream2 (first run downloads ~1.7 GB to HuggingFace cache) ...")
        _MD_TOKENIZER = AutoTokenizer.from_pretrained(
            "vikhyatk/moondream2", trust_remote_code=True, revision="2025-01-09")
        _MD_MODEL = AutoModelForCausalLM.from_pretrained(
            "vikhyatk/moondream2", trust_remote_code=True, revision="2025-01-09",
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map="auto"         if torch.cuda.is_available() else None,
            low_cpu_mem_usage=True)
        _MD_MODEL.eval()
        log.info("moondream2 ready  (device=%s)",
                 "cuda" if torch.cuda.is_available() else "cpu")
        _AI_BACKEND = "moondream"
        return "moondream"
    except Exception as exc:
        log.warning("moondream2 unavailable (%s) -- trying OCR fallback.", exc)

    # --- fallback: RapidOCR ---
    try:
        from rapidocr_onnxruntime import RapidOCR as _R   # noqa: F401
        _AI_BACKEND = "ocr"
        log.info("OCR fallback backend active.")
        return "ocr"
    except Exception as exc2:
        log.error("No vision backend available: %s", exc2)
        _AI_BACKEND = "none"
        return "none"


def _ask_vision(pil_img: Image.Image, prompt: str, log) -> str:
    """Send image + prompt to the active backend; return raw text answer."""
    backend = _load_ai_backend(log)

    if backend == "moondream":
        try:
            enc    = _MD_MODEL.encode_image(pil_img)
            answer = _MD_MODEL.query(enc, prompt)["answer"].strip()
            log.debug("VLM  [%s] -> %r", prompt[:60], answer)
            return answer
        except Exception as exc:
            log.warning("VLM query error: %s", exc)
            return ""

    if backend == "ocr":
        try:
            from rapidocr_onnxruntime import RapidOCR
            ocr = RapidOCR()
            result, _ = ocr(np.array(pil_img))
            text = " ".join(r[1] for r in result) if result else ""
            log.debug("OCR raw: %r", text[:120])
            return text
        except Exception as exc:
            log.warning("OCR error: %s", exc)
            return ""

    return ""


# ---------------------------------------------------------------------------
#  Config
# ---------------------------------------------------------------------------
@dataclass
class Config:
    # --- blob detection (HSV) ---
    BLOB_HUE_LO:   int  = 0       # red-brown lower hue
    BLOB_HUE_HI:   int  = 25      # red-brown upper hue
    BLOB_SAT_LO:   int  = 55
    BLOB_VAL_LO:   int  = 55
    MIN_BLOB_AREA: int  = 400
    MAX_BLOB_AREA: int  = 14000

    # --- timing (ms) ---
    HOVER_DELAY_MS:   int = 220   # time to wait for tooltip to appear
    ALT_DELAY_MS:     int = 300   # time to wait for ALT comparison panel
    MOVE_DELAY_MS:    int = 60
    RESTOCK_DELAY_MS: int = 650   # wait after pressing R

    # --- tooltip screenshot crop ---
    TOOLTIP_OFFSET_X: int = 18    # px to the right of item centre
    TOOLTIP_OFFSET_Y: int = -15   # px above item centre
    TOOLTIP_W:        int = 480
    TOOLTIP_H:        int = 380

    # --- scan region (0,0,0,0 = full screen) ---
    SCAN_LEFT:   int = 0
    SCAN_TOP:    int = 0
    SCAN_RIGHT:  int = 0
    SCAN_BOTTOM: int = 0

    # --- misc ---
    RESTOCK_KEY: str  = "r"
    PASS_LIST:   List[str] = field(default_factory=list)
    LOG_DIR:     str  = "logs"
    PREFS_FILE:  str  = "_tools/prefs.json"
    SAVE_SCREENSHOTS: bool = True   # save every tooltip screenshot to log dir


# ---------------------------------------------------------------------------
#  Logging
# ---------------------------------------------------------------------------
def _setup_logging(cfg: Config):
    log_dir = Path(cfg.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp    = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    if not log.handlers:
        log.addHandler(fh); log.addHandler(ch)
    log.info("Session log: %s", log_file)
    return log


# ---------------------------------------------------------------------------
#  Prefs persistence
# ---------------------------------------------------------------------------
_PREF_KEYS = [
    "hover_delay_ms", "alt_delay_ms", "restock_delay_ms", "move_delay_ms",
    "scan_left", "scan_top", "scan_right", "scan_bottom",
    "blob_hue_lo", "blob_hue_hi", "blob_sat_lo", "blob_val_lo",
    "min_blob_area", "max_blob_area",
    "tooltip_offset_x", "tooltip_offset_y", "tooltip_w", "tooltip_h",
    "pass_list", "save_screenshots",
]


def load_prefs(cfg: Config, log):
    pf = Path(cfg.PREFS_FILE)
    if not pf.exists():
        return
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
        for k in _PREF_KEYS:
            attr = k.upper()
            if k in data and hasattr(cfg, attr):
                setattr(cfg, attr, data[k])
        log.info("Prefs loaded from %s", pf)
    except Exception as exc:
        log.warning("Could not load prefs: %s", exc)


def save_prefs(cfg: Config, log):
    pf = Path(cfg.PREFS_FILE)
    pf.parent.mkdir(parents=True, exist_ok=True)
    data = {k: getattr(cfg, k.upper()) for k in _PREF_KEYS if hasattr(cfg, k.upper())}
    pf.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info("Prefs saved -> %s", pf)


# ---------------------------------------------------------------------------
#  Screenshot helpers
# ---------------------------------------------------------------------------

def _grab(left, top, right, bottom) -> Image.Image:
    return ImageGrab.grab(bbox=(int(left), int(top), int(right), int(bottom)))


def screenshot_full(cfg: Config) -> Image.Image:
    if cfg.SCAN_RIGHT > cfg.SCAN_LEFT and cfg.SCAN_BOTTOM > cfg.SCAN_TOP:
        return _grab(cfg.SCAN_LEFT, cfg.SCAN_TOP, cfg.SCAN_RIGHT, cfg.SCAN_BOTTOM)
    return ImageGrab.grab()


def screenshot_tooltip(cx: int, cy: int, cfg: Config) -> Image.Image:
    """Grab a region around (cx,cy) where the tooltip is expected."""
    l = cx + cfg.TOOLTIP_OFFSET_X
    t = cy + cfg.TOOLTIP_OFFSET_Y
    r = l + cfg.TOOLTIP_W
    b = t + cfg.TOOLTIP_H
    sw, sh = pyautogui.size()
    # clamp to screen
    if r > sw: l = sw - cfg.TOOLTIP_W;  r = sw
    if b > sh: t = sh - cfg.TOOLTIP_H;  b = sh
    if l < 0:  l = 0;                   r = cfg.TOOLTIP_W
    if t < 0:  t = 0;                   b = cfg.TOOLTIP_H
    return _grab(l, t, r, b)


# ---------------------------------------------------------------------------
#  Item-blob detection
# ---------------------------------------------------------------------------
@dataclass
class ItemBlob:
    cx:   int    # screen-space centre X
    cy:   int    # screen-space centre Y
    x:    int    # bounding box origin X
    y:    int    # bounding box origin Y
    w:    int
    h:    int
    area: int


def find_item_blobs(cfg: Config, log) -> List[ItemBlob]:
    """
    Screenshot the scan region and locate every item cell by detecting
    the reddish-brown bordered squares against the dark shop background.

    Returns blobs sorted reading-order (row-major, left-to-right).
    """
    shot   = screenshot_full(cfg)
    rgb    = np.array(shot.convert("RGB"))
    hsv    = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)

    # Primary mask: reddish-brown (0-25 hue, moderate sat/val)
    lo1 = np.array([cfg.BLOB_HUE_LO, cfg.BLOB_SAT_LO, cfg.BLOB_VAL_LO])
    hi1 = np.array([cfg.BLOB_HUE_HI, 255, 255])
    m1  = cv2.inRange(hsv, lo1, hi1)

    # Secondary mask: wrap-around red (170-179)
    lo2 = np.array([170, cfg.BLOB_SAT_LO, cfg.BLOB_VAL_LO])
    hi2 = np.array([179, 255, 255])
    m2  = cv2.inRange(hsv, lo2, hi2)

    mask = cv2.bitwise_or(m1, m2)

    # Morphology: close small gaps, remove stray pixels
    kernel  = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    mask    = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    kernel2 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask    = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Offset: if we grabbed a sub-region, blobs must be translated to screen coords
    off_x = cfg.SCAN_LEFT if (cfg.SCAN_RIGHT > cfg.SCAN_LEFT) else 0
    off_y = cfg.SCAN_TOP  if (cfg.SCAN_BOTTOM > cfg.SCAN_TOP) else 0

    blobs: List[ItemBlob] = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < cfg.MIN_BLOB_AREA or area > cfg.MAX_BLOB_AREA:
            continue
        bx, by, bw, bh = cv2.boundingRect(cnt)
        # reject very elongated non-square shapes (unlikely to be item cells)
        aspect = max(bw, bh) / max(min(bw, bh), 1)
        if aspect > 5.0:
            continue
        cx = off_x + bx + bw // 2
        cy = off_y + by + bh // 2
        blobs.append(ItemBlob(cx=cx, cy=cy,
                               x=off_x + bx, y=off_y + by,
                               w=bw, h=bh, area=int(area)))

    # Reading order
    blobs.sort(key=lambda b: (b.y // 25, b.x))
    log.info("Blob detection: %d item(s) found  (mask sum=%d)", len(blobs), int(mask.sum()))

    # Debug: save annotated screenshot to log dir
    if cfg.SAVE_SCREENSHOTS:
        try:
            ann = rgb.copy()
            for b in blobs:
                lx = b.x - off_x; ly = b.y - off_y
                cv2.rectangle(ann, (lx, ly), (lx+b.w, ly+b.h), (0, 255, 80), 2)
                cv2.circle(ann, (b.cx - off_x, b.cy - off_y), 4, (255, 80, 0), -1)
            stamp = datetime.datetime.now().strftime("%H%M%S_%f")
            p = Path(cfg.LOG_DIR) / f"blobs_{stamp}.png"
            Image.fromarray(ann).save(p)
            log.debug("Annotated blob screenshot: %s", p)
        except Exception as exc:
            log.debug("Could not save annotated screenshot: %s", exc)

    return blobs


# ---------------------------------------------------------------------------
#  Vision-model queries
# ---------------------------------------------------------------------------
_INT_RE      = re.compile(r'\b(\d{1,4})\b')
_COMPARE_RE  = re.compile(r'LEFT\s*=\s*(\d+)\D+RIGHT\s*=\s*(\d+)', re.I | re.S)


def _first_int(text: str, default: int = 0) -> int:
    m = _INT_RE.search(text)
    return int(m.group(1)) if m else default


def ask_item_name(img: Image.Image, log) -> str:
    prompt = (
        "Look at this game item tooltip screenshot. "
        "What is the item name shown at the very top in colour? "
        "Reply with only the item name, nothing else."
    )
    raw = _ask_vision(img, prompt, log)
    log.debug("item_name <- %r", raw)
    return raw.strip() or "Unknown Item"


def ask_flat_gf(img: Image.Image, log) -> int:
    """Return the flat (not %) gold-found bonus shown in the tooltip, or 0."""
    prompt = (
        "Look at this game item tooltip screenshot. "
        "Is there a flat bonus to gold found (a line like '+14 to Gold Found' or "
        "'+(14-18) to Gold Found', NOT a percentage)? "
        "If yes, reply with only the integer number, e.g. '14'. "
        "If not present, reply '0'."
    )
    raw = _ask_vision(img, prompt, log)
    val = _first_int(raw, 0)
    log.debug("flat_gf <- %r  parsed=%d", raw, val)
    return val


def ask_compare_gf(img: Image.Image, log) -> Tuple[int, int]:
    """
    Parse the ALT-comparison screenshot (two tooltips side by side).
    Returns (left_gf, right_gf) where left = shop item, right = equipped.
    """
    prompt = (
        "This screenshot shows two item tooltips side by side for comparison. "
        "For EACH tooltip find the flat (not %) bonus to gold found. "
        "Reply in EXACTLY this format: LEFT=<n> RIGHT=<n> "
        "Use 0 if not present. Example: LEFT=14 RIGHT=0"
    )
    raw = _ask_vision(img, prompt, log)
    log.debug("compare_gf <- %r", raw)
    m = _COMPARE_RE.search(raw)
    if m:
        return int(m.group(1)), int(m.group(2))
    nums = _INT_RE.findall(raw)
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
    lap:         int
    blob_idx:    int
    item_name:   str
    shelf_gf:    int
    equipped_gf: int
    timestamp:   str = field(
        default_factory=lambda: datetime.datetime.now().strftime("%H:%M:%S"))
    tooltip_path:  str = ""
    compare_path:  str = ""
    decision:      str = "pending"  # "buy" | "pass" | "pending"


# ---------------------------------------------------------------------------
#  Inspector engine  (runs in background thread)
# ---------------------------------------------------------------------------
class Inspector:
    def __init__(self, cfg: Config, log, ui_queue: queue.Queue):
        self.cfg  = cfg
        self.log  = log
        self.ui_q = ui_queue

        self._state   = "halted"
        self._lock    = threading.Lock()
        self._thread: Optional[threading.Thread] = None

        # stats
        self.lap         = 0
        self.blobs_found = 0
        self.visited     = 0
        self.hits: List[HitRecord] = []
        self._current_hit: Optional[HitRecord] = None

        self._log_dir = Path(cfg.LOG_DIR)
        self._log_dir.mkdir(parents=True, exist_ok=True)

    # --- state machine ---
    def _set(self, state: str):
        with self._lock:
            self._state = state
        self._push("state", state)

    def _get(self) -> str:
        with self._lock:
            return self._state

    def _push(self, kind: str, payload=None):
        self.ui_q.put_nowait({"kind": kind, "payload": payload})

    # --- public controls ---
    def begin(self):
        if self._get() != "halted":
            return
        self._set("running")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def halt(self):
        self._set("halted")
        self.log.info("Halt requested.")

    def hold(self):
        s = self._get()
        if   s == "running":               self._set("holding")
        elif s in ("holding", "paused_hit"): self._set("running")

    def next_item(self):
        if self._get() == "paused_hit" and self._current_hit:
            self._current_hit.decision = "pass"
            self._push("hit_resolved", self._current_hit)
            self._set("running")

    # --- main scan loop ---
    def _run(self):
        self.log.info("Scan started.")
        self._push("log", "Scan started.")
        cfg = self.cfg

        try:
            while self._get() != "halted":
                self._wait_not_holding()
                if self._get() == "halted":
                    break

                # ---------- new lap ----------
                self.lap += 1
                self._push("lap",  self.lap)
                self._push("log",  f"=== Shelf restocked (lap #{self.lap}) ===")
                self.log.info("=== Lap %d -- detecting item blobs ===", self.lap)

                blobs = find_item_blobs(cfg, self.log)
                self.blobs_found = len(blobs)
                self._push("blobs", self.blobs_found)
                self._push("log",   f"  {len(blobs)} item(s) detected.")

                if not blobs:
                    self.log.warning("No blobs found -- verify scan region and HSV params.")
                    self._push("log", "  No items found. Check Calibrate -> Test Detection.")
                    time.sleep(1.2)
                    self._restock()
                    continue

                # ---------- iterate blobs ----------
                for idx, blob in enumerate(blobs):
                    if self._get() == "halted":
                        break
                    self._wait_not_holding()
                    if self._get() == "halted":
                        break

                    self.visited += 1
                    self._push("visited",  self.visited)
                    self._push("item_pos", f"[{idx+1}/{len(blobs)}]")
                    self.log.debug(
                        "Blob %d/%d  cx=%d cy=%d  area=%d",
                        idx+1, len(blobs), blob.cx, blob.cy, blob.area)

                    # -- move to item, wait for tooltip --
                    pyautogui.moveTo(blob.cx, blob.cy, duration=0.05)
                    time.sleep(cfg.HOVER_DELAY_MS / 1000)

                    # -- screenshot tooltip --
                    tip_img  = screenshot_tooltip(blob.cx, blob.cy, cfg)
                    tip_path = self._save_img(tip_img, f"tip_L{self.lap}_B{idx}")

                    # -- ask LLM: item name and flat GF --
                    item_name = ask_item_name(tip_img, self.log)
                    shelf_gf  = ask_flat_gf(tip_img, self.log)

                    self.log.info("  Blob %d: %r  flat_gf=%d", idx+1, item_name, shelf_gf)
                    self._push("log", f"  [{idx+1}] {item_name!r}  flat_GF={shelf_gf}")

                    if shelf_gf <= 0:
                        continue   # no gold-find on this item -> skip

                    # -- ALT comparison --
                    try:
                        keyboard.press("alt")
                        time.sleep(cfg.ALT_DELAY_MS / 1000)
                        cmp_img  = screenshot_tooltip(blob.cx, blob.cy, cfg)
                        keyboard.release("alt")
                    except Exception as exc:
                        self.log.warning("ALT press error: %s", exc)
                        try:
                            keyboard.release("alt")
                        except Exception:
                            pass
                        cmp_img = tip_img   # fall back to tip screenshot

                    cmp_path = self._save_img(cmp_img, f"cmp_L{self.lap}_B{idx}")
                    shelf_gf2, equipped_gf = ask_compare_gf(cmp_img, self.log)

                    # prefer the comparison value (more precise range parse)
                    if shelf_gf2 > 0:
                        shelf_gf = shelf_gf2

                    self.log.info(
                        "  Compare: shelf=%d  equipped=%d",
                        shelf_gf, equipped_gf)
                    self._push("log",
                        f"  [{idx+1}] Compare -- shelf={shelf_gf}  worn={equipped_gf}")

                    # -- decide whether to pause --
                    on_pass_list = any(
                        p.strip().lower() in item_name.lower()
                        for p in cfg.PASS_LIST if p.strip())

                    should_pause = (
                        on_pass_list            # always flag passlist items
                        or equipped_gf == 0     # no equipped GF -> anything is better
                        or shelf_gf >= equipped_gf
                    )

                    if should_pause:
                        hr = HitRecord(
                            lap=self.lap,
                            blob_idx=idx,
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
                        self.log.info(
                            "  *** HIT  %s  shelf=%d  worn=%d  (lap %d) ***",
                            item_name, shelf_gf, equipped_gf, self.lap)

                        # park here until operator presses F7 (Next) or F6 (Halt)
                        while self._get() == "paused_hit":
                            time.sleep(0.08)

                        self._current_hit = None

                # ---------- lap done -> restock ----------
                if self._get() != "halted":
                    self._push("log", "  Shelf exhausted -- restocking...")
                    self._restock()

        except Exception as exc:
            self.log.critical("Inspector crash: %s", exc, exc_info=True)
            self._push("log",   f"CRASH: {exc}")
            self._push("crash", traceback.format_exc())
        finally:
            self._set("halted")
            self._push("log", "Inspector stopped.")
            self.log.info("Inspector stopped.")

    # --- helpers ---
    def _restock(self):
        pyautogui.press(self.cfg.RESTOCK_KEY)
        time.sleep(self.cfg.RESTOCK_DELAY_MS / 1000)

    def _wait_not_holding(self):
        while self._get() == "holding":
            time.sleep(0.08)

    def _save_img(self, img: Image.Image, name: str) -> str:
        if not self.cfg.SAVE_SCREENSHOTS:
            return ""
        p = self._log_dir / f"{name}.png"
        img.save(p)
        return str(p)


# ---------------------------------------------------------------------------
#  Region-select drag overlay
# ---------------------------------------------------------------------------
class RegionSelectOverlay:
    """Full-screen translucent canvas for dragging the scan region."""

    def __init__(self, parent_tk, on_done):
        self._on_done = on_done
        self._start   = None
        self._rect    = None

        self.win = tk.Toplevel(parent_tk)
        self.win.attributes("-fullscreen", True)
        self.win.attributes("-alpha", 0.30)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="black")
        self.win.overrideredirect(True)

        self.canvas = tk.Canvas(self.win, cursor="crosshair",
                                bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>",   self._on_press)
        self.canvas.bind("<B1-Motion>",        self._on_drag)
        self.canvas.bind("<ButtonRelease-1>",  self._on_release)
        self.win.bind("<Escape>", lambda e: self._cancel())

        sw = self.canvas.winfo_screenwidth()
        self.canvas.create_text(
            sw // 2, 40,
            text="Drag to select the shop / trade window area.  ESC to cancel.",
            fill="white", font=("Segoe UI", 16, "bold"))

    def _on_press(self, e):
        self._start = (e.x, e.y)
        if self._rect:
            self.canvas.delete(self._rect)

    def _on_drag(self, e):
        if not self._start:
            return
        if self._rect:
            self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(
            *self._start, e.x, e.y,
            outline="#00ff88", width=2, fill="")

    def _on_release(self, e):
        if not self._start:
            return
        x1 = min(self._start[0], e.x); y1 = min(self._start[1], e.y)
        x2 = max(self._start[0], e.x); y2 = max(self._start[1], e.y)
        self.win.destroy()
        if x2 - x1 > 20 and y2 - y1 > 20:
            self._on_done(x1, y1, x2, y2)
        else:
            self._on_done(None, None, None, None)

    def _cancel(self):
        self.win.destroy()
        self._on_done(None, None, None, None)


# ---------------------------------------------------------------------------
#  Calibration window
# ---------------------------------------------------------------------------
class CalibrationWindow:
    def __init__(self, parent, cfg: Config, log, on_close):
        self.cfg = cfg; self.log = log
        self.win = tk.Toplevel(parent)
        self.win.title("GoldSense \u2013 Calibrate")
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        self._on_close = on_close

        pad = {"padx": 4, "pady": 2}
        f = tk.Frame(self.win, padx=10, pady=8)
        f.pack(fill="both", expand=True)

        # ----- scan region -----
        tk.Label(f, text="Scan Region  (0 = full screen)",
                 font=("Segoe UI", 9, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 2))

        for i, (lbl, attr) in enumerate([
                ("Left",  "SCAN_LEFT"),  ("Top",    "SCAN_TOP"),
                ("Right", "SCAN_RIGHT"), ("Bottom", "SCAN_BOTTOM")]):
            col = i * 2
            tk.Label(f, text=lbl).grid(row=1, column=col, sticky="e", **pad)
            var = tk.IntVar(value=getattr(cfg, attr))
            setattr(self, f"_v_{attr}", var)
            tk.Spinbox(f, textvariable=var, from_=0, to=9999, width=6).grid(
                row=1, column=col+1, **pad)

        tk.Button(
            f, text="\u25a6  Select Region by Drag", command=self._drag_region,
            bg="#2a6a3a", fg="white", relief="flat", padx=6
        ).grid(row=2, column=0, columnspan=4, sticky="ew", pady=4)

        # ----- blob tuning -----
        tk.Label(f, text="Item Blob Detection (HSV)",
                 font=("Segoe UI", 9, "bold")).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(6, 2))

        blob_rows = [
            ("Hue Lo",  "BLOB_HUE_LO",  0, 179),
            ("Hue Hi",  "BLOB_HUE_HI",  0, 179),
            ("Sat Lo",  "BLOB_SAT_LO",  0, 255),
            ("Val Lo",  "BLOB_VAL_LO",  0, 255),
        ]
        for i, (lbl, attr, lo, hi) in enumerate(blob_rows):
            r, c = divmod(i, 2)
            tk.Label(f, text=lbl).grid(row=4+r, column=c*2, sticky="e", **pad)
            var = tk.IntVar(value=getattr(cfg, attr))
            setattr(self, f"_v_{attr}", var)
            tk.Spinbox(f, textvariable=var, from_=lo, to=hi, width=6).grid(
                row=4+r, column=c*2+1, **pad)

        tk.Label(f, text="Min Area").grid(row=6, column=0, sticky="e", **pad)
        self._v_MIN = tk.IntVar(value=cfg.MIN_BLOB_AREA)
        tk.Spinbox(f, textvariable=self._v_MIN, from_=50, to=50000, width=7).grid(
            row=6, column=1, **pad)
        tk.Label(f, text="Max Area").grid(row=6, column=2, sticky="e", **pad)
        self._v_MAX = tk.IntVar(value=cfg.MAX_BLOB_AREA)
        tk.Spinbox(f, textvariable=self._v_MAX, from_=50, to=200000, width=7).grid(
            row=6, column=3, **pad)

        # ----- tooltip crop -----
        tk.Label(f, text="Tooltip Screenshot Crop",
                 font=("Segoe UI", 9, "bold")).grid(
            row=7, column=0, columnspan=4, sticky="w", pady=(6, 2))
        for i, (lbl, attr) in enumerate([
                ("Off X", "TOOLTIP_OFFSET_X"), ("Off Y", "TOOLTIP_OFFSET_Y"),
                ("W",     "TOOLTIP_W"),         ("H",     "TOOLTIP_H")]):
            r, c = divmod(i, 2)
            tk.Label(f, text=lbl).grid(row=8+r, column=c*2, sticky="e", **pad)
            var = tk.IntVar(value=getattr(cfg, attr))
            setattr(self, f"_v_{attr}", var)
            tk.Spinbox(f, textvariable=var, from_=-500, to=3000, width=6).grid(
                row=8+r, column=c*2+1, **pad)

        # ----- timing -----
        tk.Label(f, text="Timing (ms)",
                 font=("Segoe UI", 9, "bold")).grid(
            row=10, column=0, columnspan=4, sticky="w", pady=(6, 2))
        for i, (lbl, attr) in enumerate([
                ("Hover",   "HOVER_DELAY_MS"),
                ("Alt",     "ALT_DELAY_MS"),
                ("Move",    "MOVE_DELAY_MS"),
                ("Restock", "RESTOCK_DELAY_MS")]):
            r, c = divmod(i, 2)
            tk.Label(f, text=lbl).grid(row=11+r, column=c*2, sticky="e", **pad)
            var = tk.IntVar(value=getattr(cfg, attr))
            setattr(self, f"_v_{attr}", var)
            tk.Spinbox(f, textvariable=var, from_=50, to=3000, width=6).grid(
                row=11+r, column=c*2+1, **pad)

        # ----- misc -----
        self._v_SAVE = tk.BooleanVar(value=cfg.SAVE_SCREENSHOTS)
        tk.Checkbutton(f, text="Save all tooltip screenshots to log folder",
                       variable=self._v_SAVE).grid(
            row=13, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # ----- buttons -----
        tk.Button(
            f, text="\u25b6  Test Detection (snapshot)",
            command=self._test_detect,
            bg="#336699", fg="white", relief="flat", padx=6
        ).grid(row=14, column=0, columnspan=4, sticky="ew", pady=4)

        self._result_lbl = tk.Label(f, text="", fg="#44bb44",
                                    font=("Segoe UI", 8))
        self._result_lbl.grid(row=15, column=0, columnspan=4)

        tk.Button(
            f, text="Apply & Close",
            command=self._apply,
            bg="#1a6b1a", fg="white", relief="flat", padx=8
        ).grid(row=16, column=0, columnspan=4, sticky="ew", pady=4)

    def _drag_region(self):
        self.win.withdraw()
        time.sleep(0.3)

        def on_done(l, t, r, b):
            self.win.deiconify()
            if l is None:
                return
            self._v_SCAN_LEFT.set(l);  self._v_SCAN_TOP.set(t)
            self._v_SCAN_RIGHT.set(r); self._v_SCAN_BOTTOM.set(b)
            self._result_lbl.config(text=f"Region set: ({l},{t}) \u2013 ({r},{b})")

        RegionSelectOverlay(self.win, on_done)

    def _test_detect(self):
        self._read_vars()
        blobs = find_item_blobs(self.cfg, self.log)
        msg = f"Detected {len(blobs)} item blob(s)."
        if blobs:
            msg += "  \u2713  (annotated screenshot saved to log folder)"
        else:
            msg += "  -- adjust HSV / area params or drag a tighter region"
        self._result_lbl.config(text=msg)

    def _read_vars(self):
        for attr in [
            "SCAN_LEFT", "SCAN_TOP", "SCAN_RIGHT", "SCAN_BOTTOM",
            "BLOB_HUE_LO", "BLOB_HUE_HI", "BLOB_SAT_LO", "BLOB_VAL_LO",
            "TOOLTIP_OFFSET_X", "TOOLTIP_OFFSET_Y", "TOOLTIP_W", "TOOLTIP_H",
            "HOVER_DELAY_MS", "ALT_DELAY_MS", "MOVE_DELAY_MS", "RESTOCK_DELAY_MS",
        ]:
            v = getattr(self, f"_v_{attr}", None)
            if v is not None:
                setattr(self.cfg, attr, v.get())
        self.cfg.MIN_BLOB_AREA      = self._v_MIN.get()
        self.cfg.MAX_BLOB_AREA      = self._v_MAX.get()
        self.cfg.SAVE_SCREENSHOTS   = self._v_SAVE.get()

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
    def __init__(self, parent, cfg: Config, log):
        self.cfg = cfg; self.log = log
        self.win = tk.Toplevel(parent)
        self.win.title("GoldSense \u2013 Pass List")
        self.win.resizable(False, False)

        tk.Label(self.win,
                 text="Items matching any name fragment below are always flagged.",
                 padx=10, pady=6, font=("Segoe UI", 8)).pack()

        self.lb = tk.Listbox(self.win, width=46, height=12,
                             font=("Consolas", 9))
        self.lb.pack(padx=10)
        for item in cfg.PASS_LIST:
            self.lb.insert(tk.END, item)

        ef = tk.Frame(self.win)
        ef.pack(padx=10, pady=4, fill="x")
        self.entry = tk.Entry(ef, font=("Consolas", 9))
        self.entry.pack(side="left", fill="x", expand=True)
        tk.Button(ef, text="Add",    command=self._add).pack(side="left", padx=2)
        tk.Button(ef, text="Remove", command=self._remove).pack(side="left")

        tk.Button(self.win, text="Save & Close", command=self._save,
                  bg="#1a6b1a", fg="white", relief="flat", pady=4).pack(pady=4)

    def _add(self):
        v = self.entry.get().strip()
        if v:
            self.lb.insert(tk.END, v)
            self.entry.delete(0, tk.END)

    def _remove(self):
        sel = self.lb.curselection()
        if sel:
            self.lb.delete(sel[0])

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
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("GoldSense v2.1.0")
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
        root.after(100, self._poll_queue)

    # ------------------------------------------------------------------ build
    def _build_ui(self):
        root = self.root

        # header
        hdr = tk.Frame(root, bg="#111", pady=4)
        hdr.pack(fill="x")
        tk.Label(hdr, text="GoldSense  v2.1.0",
                 bg="#111", fg="#e8c84a",
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=10)
        self._state_lbl = tk.Label(hdr, text="Halted",
                                   bg="#111", fg=ACC_RED,
                                   font=("Segoe UI", 9, "bold"))
        self._state_lbl.pack(side="right", padx=10)

        # stats strip
        sf = tk.Frame(root, bg=MID_BG, pady=3)
        sf.pack(fill="x", padx=2, pady=(2, 0))
        self._sv: dict = {}
        for lbl, key, w in [
                ("Lap",       "lap",     4),
                ("Blobs",     "blobs",   5),
                ("Visited",   "visited", 6),
                ("Hits",      "hits",    4),
                ("Best +Flat","best",    8),
                ("Time",      "time",    7)]:
            tk.Label(sf, text=lbl, bg=MID_BG, fg="#888",
                     font=("Segoe UI", 7)).pack(side="left", padx=(6, 1))
            v = tk.StringVar(value="0")
            self._sv[key] = v
            tk.Label(sf, textvariable=v, bg=MID_BG, fg=LIGHT_FG,
                     font=("Segoe UI", 8, "bold"), width=w).pack(side="left")

        # current item label
        self._item_lbl = tk.Label(root, text="Item: \u2013",
                                  bg=DARK_BG, fg="#aaaaaa",
                                  font=LBL_FONT, anchor="w")
        self._item_lbl.pack(fill="x", padx=8, pady=(4, 0))

        # hit banner
        self._hit_banner = tk.Label(root, text="No hits yet.",
                                    bg="#1f3020", fg="#88ff99",
                                    font=("Segoe UI", 9, "bold"),
                                    pady=5, relief="groove")
        self._hit_banner.pack(fill="x", padx=4, pady=3)

        # primary buttons
        bf = tk.Frame(root, bg=DARK_BG)
        bf.pack(fill="x", padx=4, pady=2)
        self._btn_begin = self._mk_btn(bf, "BEGIN  (F6)", self._on_begin,
                                       ACC_GRN, "white", width=14)
        self._btn_begin.pack(side="left", padx=2, expand=True, fill="x")
        self._btn_next = self._mk_btn(bf, "NEXT  (F7)", self._on_next,
                                      "#2255aa", "white", width=10)
        self._btn_next.pack(side="left", padx=2, expand=True, fill="x")

        # secondary buttons
        bf2 = tk.Frame(root, bg=DARK_BG)
        bf2.pack(fill="x", padx=4, pady=(0, 2))
        self._btn_hold = self._mk_btn(bf2, "HOLD  (F8)", self._on_hold,
                                      "#555", LIGHT_FG, width=10)
        self._btn_hold.pack(side="left", padx=2, expand=True, fill="x")
        for lbl, cmd, bg in [
            ("Calibrate",  self._open_calib, "#336699"),
            ("Pass List",  self._open_pass,  "#555566"),
            ("Open Log",   self._open_log,   "#333333"),
        ]:
            self._mk_btn(bf2, lbl, cmd, bg, LIGHT_FG, width=10).pack(
                side="left", padx=2, expand=True, fill="x")

        tk.Label(root, text="F6=Begin/Halt  F7=Next  F8=Hold  ESC=Halt",
                 bg=DARK_BG, fg="#555", font=("Segoe UI", 7)).pack(pady=(0, 2))

        # activity log
        lf = tk.LabelFrame(root, text=" Activity Log ", bg=DARK_BG, fg="#666",
                           font=("Segoe UI", 8))
        lf.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._log_box = scrolledtext.ScrolledText(
            lf, bg="#0d0d0d", fg="#888877", font=LOG_FONT,
            height=9, state="disabled", wrap="word")
        self._log_box.pack(fill="both", expand=True)

        # hit history
        hf = tk.LabelFrame(root, text=" Hit History ", bg=DARK_BG, fg="#666",
                           font=("Segoe UI", 8))
        hf.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._hit_box = scrolledtext.ScrolledText(
            hf, bg="#0d0d0d", fg="#88ff99", font=LOG_FONT,
            height=5, state="disabled", wrap="word")
        self._hit_box.pack(fill="both", expand=True)

        # clock
        self._start_ts = None
        self._tick()

    @staticmethod
    def _mk_btn(parent, text, cmd, bg, fg, width=8) -> tk.Button:
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg, relief="flat",
                         font=BTN_FONT, width=width,
                         activebackground=bg, activeforeground=fg,
                         bd=0, padx=4, pady=4)

    # ---------------------------------------------------------------- controls
    def _on_begin(self):
        s = self.insp._get()
        if s == "halted":
            self._start_ts = time.time()
            self.insp.begin()
        else:
            self.insp.halt()

    def _on_next(self):  self.insp.next_item()
    def _on_hold(self):  self.insp.hold()

    def _open_calib(self):
        if not getattr(self, "_calib_open", False):
            self._calib_open = True
            CalibrationWindow(self.root, self.cfg, self.log,
                              on_close=lambda: setattr(self, "_calib_open", False))

    def _open_pass(self):
        PassListWindow(self.root, self.cfg, self.log)

    def _open_log(self):
        p = Path(self.cfg.LOG_DIR)
        if sys.platform == "win32":
            os.startfile(str(p))
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(p)])

    def _register_hotkeys(self):
        try:
            keyboard.add_hotkey("f6",  self._on_begin,  suppress=False)
            keyboard.add_hotkey("f7",  self._on_next,   suppress=False)
            keyboard.add_hotkey("f8",  self._on_hold,   suppress=False)
            keyboard.add_hotkey("esc", self.insp.halt,  suppress=False)
        except Exception as exc:
            self.log.warning("Hotkey registration failed: %s", exc)

    # ---------------------------------------------------------------- UI poll
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
                        self._start_ts = None
                    elif payload == "running":
                        self._btn_begin.config(text="HALT  (F6)",  bg=ACC_RED)

                elif kind == "lap":        self._sv["lap"].set(str(payload))
                elif kind == "blobs":      self._sv["blobs"].set(str(payload))
                elif kind == "visited":    self._sv["visited"].set(str(payload))
                elif kind == "hits_count": self._sv["hits"].set(str(payload))
                elif kind == "item_pos":   self._item_lbl.config(text=f"Item: {payload}")

                elif kind == "hit":
                    hr: HitRecord = payload
                    try:
                        prev = int(self._sv["best"].get().lstrip("+"))
                    except ValueError:
                        prev = 0
                    best = max(prev, hr.shelf_gf)
                    self._sv["best"].set(f"+{best}")
                    worn_str = f"+{hr.equipped_gf}" if hr.equipped_gf else "none"
                    self._hit_banner.config(
                        bg="#2a1a3a", fg="#ff88ff",
                        text=f"HIT: {hr.item_name}\n"
                             f"Shelf GF: +{hr.shelf_gf}   Worn: {worn_str}")
                    self._append(self._hit_box,
                        f"[{hr.timestamp}] Lap {hr.lap} Blob {hr.blob_idx+1}: "
                        f"{hr.item_name}  shelf={hr.shelf_gf}  worn={hr.equipped_gf}\n")

                elif kind == "hit_resolved":
                    self._hit_banner.config(
                        bg="#1f2a1f", fg="#88ff99",
                        text="Passed \u2013 resuming scan.")

                elif kind == "log":
                    self._append(self._log_box, str(payload) + "\n")

                elif kind == "crash":
                    self._append(self._log_box,
                                 "CRASH -- see log file for details.\n")
                    messagebox.showerror("GoldSense Crash", str(payload)[:600])

        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _append(self, widget: scrolledtext.ScrolledText, text: str):
        widget.config(state="normal")
        widget.insert(tk.END, text)
        widget.see(tk.END)
        widget.config(state="disabled")

    def _tick(self):
        if self._start_ts is not None:
            elapsed = int(time.time() - self._start_ts)
            h, r = divmod(elapsed, 3600)
            m, s = divmod(r, 60)
            self._sv["time"].set(f"{h:02d}:{m:02d}:{s:02d}")
        self.root.after(1000, self._tick)


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
