# TH4 Shop-Bot

**The Hell 4 Mod** -- Automated Gold-Find Item Scanner for Griswold's Shop

A self-contained, portable Python app that scans the shop grid in **The Hell 4** (Diablo 1 mod), looking for items with **+X flat to Gold Found** affixes. When a qualifying find is detected the bot pauses, keeps the mouse on the item tile, and waits for you to buy or skip.

---

## Quick Start

1. **Clone or download** this repository.
2. **Double-click `INSTALL.bat`** -> choose **option 1 -- Install**.
   - Downloads portable Miniconda (~100 MB, one-time) if needed.
   - Creates `env\` Python 3.11 environment.
   - Installs all packages.
3. **Start The Hell 4** and enter Griswold's shop (Trade / Repair).
4. **Choose option 2 -- Launch** from INSTALL.bat (or double-click `run.bat`).
5. Overlay appears. Click **Calibrate** and set your shop grid coordinates.
6. Click **Ignore List** to add any uniques you want to exclude from comparison.
7. Press **START** (or **F6**) and let the bot scan!

---

## Controls

| Key | Action |
|-----|--------|
| **F6** | Start / Stop scanning toggle |
| **F7** | Continue after a find (skip current item) |
| **F8** | Pause / Resume |
| **ESC** | Emergency stop |

All buttons are also available in the overlay window.

---

## How It Works

```
For each tile in the shop grid:
  1. Move mouse to tile centre
  2. Wait for tooltip (configurable hover delay)
  3. Screenshot tooltip -- quick gold-find scan via OCR
  4. If gold-find text found:
       a. Hold ALT to show comparison tooltip
       b. Screenshot the side-by-side comparison
       c. Parse BOTH shop item AND equipped item
       d. Apply filtering logic (see below)
After all tiles scanned:
  -> Press R to refresh shop
  -> Repeat
```

### Filtering Logic

| Condition | Action |
|-----------|--------|
| Shop item flat GF >= equipped flat GF | **STOP** -- alert user |
| Equipped item is on ignore list | **STOP** -- alert (any flat GF value) |
| Equipped slot shows no flat GF | **STOP** -- alert (no comparison) |
| Item has BOTH flat GF AND % GF | **STOP** -- MANDATORY DISMISS (rare!) |
| Shop item flat GF < equipped flat GF | Skip silently |

---

## Ignore List

Items on the ignore list bypass the equipped-item comparison entirely. Useful for unique rings/amulets you keep regardless of GF stats.

- Manage via **Ignore List** button in the overlay.
- Partial name match, case-insensitive.
- Example: `ring of celestial castles` matches any item with that substring.
- Saved automatically to `_tools\botstate.json`.

---

## Dual Gold-Find Items

Some rare items have **both flat +GF AND % GF**. When detected:

- A **magenta banner** appears in the overlay.
- The Skip button shows a **confirmation dialog** to prevent accidental skips.
- These items always require **manual dismissal**.

---

## Calibration

The bot must know where your shop grid is on screen.

1. Open the shop in-game.
2. Click **Calibrate** in the overlay.
3. Hover over:
   - **Top-left tile** (col 0, row 0) -> Capture TL
   - **One column right** (col 1, row 0) -> Capture W
   - **One row down** (col 0, row 1) -> Capture H
4. Set column/row counts and timing delays.
5. Click **Apply & Close**.

Calibration is saved between runs.

---

## Configuration (Config class in src/main.py)

| Setting | Default | Description |
|---------|---------|-------------|
| `SHOP_ORIGIN_X/Y` | 40, 108 | Top-left pixel of shop grid |
| `TILE_W / TILE_H` | 29, 29 | Tile size in pixels |
| `SHOP_COLS/ROWS` | 10, 14 | Grid dimensions |
| `HOVER_DELAY_MS` | 180 | Wait for normal tooltip (ms) |
| `ALT_DELAY_MS` | 220 | Wait for ALT comparison tooltip (ms) |
| `MIN_GOLD_FIND` | 1 | Minimum flat +GF value to consider |
| `IGNORE_LIST` | [...] | Names that bypass equipped comparison |
| `REFRESH_KEY` | "r" | Key pressed to refresh shop |

---

## Safety

- The bot **never Shift+Clicks** -- no accidental buys.
- You buy items **manually** with Shift+Click yourself.
- Press **ESC** for emergency stop at any time.
- All runs logged to `logs\` with find screenshots.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Bot scans wrong area | Calibrate -- re-set grid origin |
| Tooltip not captured | Increase `HOVER_DELAY_MS` |
| Comparison doesn't appear | Increase `ALT_DELAY_MS` |
| OCR misses text | Try windowed/borderless mode |
| Hotkeys not working | Run as Administrator |
| `ModuleNotFoundError` | INSTALL.bat -> option 4 Repair |
| Comparison split wrong | Check log; adjust `EQUIPPED_MARKERS` in Config |

---

## Folder Layout

```
TH4-Shop-Bot\
+-- INSTALL.bat        <- Setup & management menu
+-- run.bat            <- Quick launcher
+-- README.md
+-- requirements.txt
+-- src\
    +-- main.py        <- Entire application (single file)
+-- env\               <- Python environment (INSTALL.bat creates this)
+-- _conda\            <- Local Miniconda (INSTALL.bat creates this)
+-- _tools\
    +-- botstate.json  <- Saved calibration + ignore list
+-- logs\              <- Run logs and find screenshots
```
