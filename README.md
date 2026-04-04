# GoldSense

**Merchant Inventory Inspector — Vision-AI Edition**

A self-contained, portable Python overlay for **The Hell 4** that automatically scouts the merchant's shelf for **flat +Gold Find** items using a two-stage AI pipeline — no fixed grid calibration needed.

---

## What's New in v2

| | v1 (grid walker) | v2 (vision-AI) |
|---|---|---|
| Item detection | Fixed calibrated grid cells | OpenCV blob detection — finds reddish-brown item squares automatically |
| Stat reading | OCR + regex | Local vision-language model (moondream2) |
| Robustness | Breaks on UI/font changes | Works regardless of background, colours, or layout changes |
| Calibration | Column/row count + pixel origin | Drag a rectangle around the shop window — done |
| GF comparison | Regex match on text | ALT screenshot fed to AI — `LEFT=n RIGHT=n` answer |

---

## Quick Start

1. **Clone or download** this repository.
2. **Double-click `INSTALL.bat`** → choose **option 1 -- Setup**.
   - Downloads portable Miniconda (~100 MB, one-time) if needed.
   - Creates `env\` with Python 3.11.
   - Installs all packages including `opencv-python`, `transformers`, and `torch`.
   - moondream2 (~1.7 GB) downloads automatically on **first run**.
3. **Start The Hell 4** and open the merchant's Trade window.
4. **Double-click `run.bat`** (or choose option 2 in INSTALL.bat).
5. Overlay appears → click **Calibrate** → **Select Region by Drag** over the shop grid → **Apply**.
6. Press **BEGIN** (or **F6**) and the scan engine starts.

> moondream2 is downloaded once into `~/.cache/huggingface/` — subsequent runs start instantly.

---

## How It Works

```
STAGE 1 — Blob Detection
  Screenshot the scan region.
  HSV colour mask isolates reddish-brown item borders against the dark background.
  Contour analysis finds each distinct item square → list of (cx, cy) centres.

STAGE 2 — AI Stat Reading  (per blob)
  Move cursor to blob centre → wait for tooltip → screenshot.
  Ask moondream2: "Flat +gold find value? Reply with just the number or 0."
  If GF > 0:
    Hold ALT → screenshot comparison tooltip (two items side-by-side).
    Ask: "LEFT=<shelf_gf> RIGHT=<equipped_gf>"
    If shelf >= equipped, OR equipped slot has no GF → PAUSE for operator.

After all blobs scanned:
  Press R to restock shelf → repeat.
```

---

## Controls

| Key | Action |
|-----|--------|
| **F6** | Begin / Halt toggle |
| **F7** | Next — pass current hit |
| **F8** | Hold / Resume |
| **ESC** | Immediate halt |

All controls also available as overlay buttons.

---

## Hit Logic

| Condition | Result |
|-----------|--------|
| Shelf flat GF ≥ worn flat GF | **Pause** |
| Worn slot shows no GF data | **Pause** (nothing to compare against) |
| Item is on Watch List | **Pause** regardless of stats |
| Shelf flat GF < worn flat GF | Pass silently |

---

## Calibration

1. Open the merchant's Trade window in-game.
2. Click **Calibrate** in the overlay.
3. Click **Select Region by Drag** — screen dims.
4. Drag from the **top-left** to **bottom-right** of the shop grid area.
5. Click **Test Detection** to verify blob count matches item count.
6. Adjust HSV parameters if detection is off, then click **Apply & Close**.

### HSV Tuning (if items are missed or false-positives appear)

The reddish-brown item borders are detected by HSV colour range.  
Default values work for standard TH4 theme — if your colours look different:

| Parameter | Default | Purpose |
|-----------|---------|--------|
| Hue Lo / Hi | 0 – 25 | Hue range of item border colour |
| Sat Lo | 60 | Minimum colour saturation |
| Val Lo | 60 | Minimum brightness |
| Min Area | 500 px² | Filters out noise dots |
| Max Area | 12000 px² | Filters out large background regions |

---

## Watch List

Items on the watch list are always surfaced regardless of GF stats.  
Useful for named items you want to be notified about regardless of rolls.

- Partial name match, case-insensitive.
- Manage via the **Watch List** button in the overlay.
- Saved to `_tools/prefs.json`.

---

## AI Backend

**Primary — moondream2** (`vikhyatk/moondream2`)
- ~1.7 GB, downloaded once via HuggingFace.
- Runs on CPU (slower) or GPU if available (fast).
- No API key, no internet required after first download.

**Fallback — RapidOCR + regex**
- Used automatically if moondream2 fails to load.
- Less robust to UI changes but always available.

---

## Folder Layout

```
GoldSense\
+-- INSTALL.bat       <- Setup & management menu
+-- run.bat           <- Quick launcher
+-- README.md
+-- requirements.txt
+-- src\
    +-- main.py       <- Entire application (single file)
+-- env\             <- Python environment  (created by INSTALL.bat)
+-- _conda\          <- Local Miniconda     (created by INSTALL.bat)
+-- _tools\
    +-- prefs.json    <- Saved calibration + watch list
+-- logs\            <- Session logs and tooltip captures
```

---

## Safety

- GoldSense **never Shift+Clicks** — no accidental purchases.
- Buy items **manually** with Shift+Click.
- Press **ESC** to halt immediately at any time.
- All sessions logged to `logs\` including tooltip and comparison screenshots.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No items detected | Calibrate → adjust HSV range, check scan region |
| Wrong item count | Tune Min/Max area in Calibrate |
| GF not detected | Try increasing Hover delay; check tooltip screenshot in `logs\` |
| ALT compare wrong | Increase Alt delay; check comparison screenshot in `logs\` |
| Model loading slow | Normal on first run; subsequent runs use cached model |
| `ModuleNotFoundError` | INSTALL.bat → option 4 Repair |
| Hotkeys unresponsive | Run as Administrator |
