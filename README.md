# GoldSense

**Merchant Inventory Inspector** — Gold-Find Affinity Surfacer for The Hell 4

A self-contained, portable Python overlay that walks the merchant's shelf grid in **The Hell 4**, reading item tooltips via OCR to surface **flat +Gold Find** affixes. When a qualifying item is spotted the inspector pauses, leaves the cursor on the slot, and waits for your decision.

---

## Quick Start

1. **Clone or download** this repository.
2. **Double-click `SETUP.bat`** → choose **option 1 -- Setup**.
   - Downloads portable Miniconda (~100 MB, one-time) if needed.
   - Creates `env\` Python 3.11 environment.
   - Installs all packages.
3. **Start The Hell 4** and open the merchant's Trade / Repair screen.
4. **Choose option 2 -- Launch** from SETUP.bat (or double-click `run.bat`).
5. Overlay appears → click **Calibrate** and set your shelf grid coordinates.
6. Click **Pass List** to add any items you want to exclude from comparison.
7. Press **BEGIN** (or **F6**) and let the inspector walk the shelf!

---

## Controls

| Key | Action |
|-----|--------|
| **F6** | Begin / Halt toggle |
| **F7** | Next — pass current hit |
| **F8** | Hold / Resume |
| **ESC** | Immediate halt |

All controls are also available as buttons in the overlay.

---

## How It Works

```
For each cell in the shelf grid:
  1. Move cursor to cell centre
  2. Wait for tooltip  (configurable hover delay)
  3. Capture tooltip image → read text via OCR
  4. If gold-find text present:
       a. Hold ALT → capture comparison tooltip
       b. Read BOTH shelf item AND worn item
       c. Apply hit logic (see table below)
After all cells inspected:
  → Press R to restock shelf
  → Repeat
```

### Hit Logic

| Condition | Result |
|-----------|--------|
| Shelf flat GF ≥ worn flat GF | **Pause** — notify you |
| Worn item is on Pass List | **Pause** — any flat GF value |
| Worn slot shows no flat GF | **Pause** — nothing to compare |
| Item has BOTH flat GF AND % GF | **Pause** — confirm before passing (rare!) |
| Shelf flat GF < worn flat GF | Pass silently |

---

## Pass List

Items on the pass list bypass the worn-item comparison. Useful for named uniques you keep regardless of stats.

- Manage via the **Pass List** button in the overlay.
- Partial name match, case-insensitive.
- Example: `ring of celestial castles` matches all variants.
- Saved to `_tools\prefs.json`.

---

## Dual Gold-Find Items

Some rare items carry **both flat +GF AND % GF**. When found:

- A **magenta banner** appears in the overlay.
- The Next button shows a **confirmation dialog** to prevent accidental passes.
- Always requires manual decision.

---

## Calibration

The inspector must know where your shelf grid sits on screen.

1. Open the merchant in-game.
2. Click **Calibrate** in the overlay.
3. Hover over:
   - **Top-left cell** (col 0, row 0) → Capture TL
   - **One column right** (col 1, row 0) → Capture W
   - **One row down** (col 0, row 1) → Capture H
4. Set column / row counts and timing delays.
5. Click **Apply & Close** — saved automatically.

---

## Configuration (`Config` class in `src/main.py`)

| Setting | Default | Description |
|---------|---------|-------------|
| `SHELF_ORIGIN_X/Y` | 40, 108 | Top-left pixel of shelf grid |
| `CELL_W / CELL_H` | 29, 29 | Cell size in pixels |
| `SHELF_COLS/ROWS` | 10, 14 | Grid dimensions |
| `HOVER_DELAY_MS` | 180 | Wait for normal tooltip (ms) |
| `ALT_DELAY_MS` | 220 | Wait for comparison tooltip (ms) |
| `MIN_GOLD_FIND` | 1 | Minimum flat +GF value to surface |
| `PASS_LIST` | [...] | Names that bypass worn comparison |
| `RESTOCK_KEY` | "r" | Key to restock shelf |

---

## Safety

- The inspector **never Shift+Clicks** — no accidental purchases.
- You buy items **manually** with Shift+Click.
- Press **ESC** to halt immediately at any time.
- All sessions logged to `logs\` with hit captures.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Inspecting wrong area | Calibrate — re-set grid origin |
| Tooltip not captured | Increase `HOVER_DELAY_MS` |
| Comparison doesn't appear | Increase `ALT_DELAY_MS` |
| OCR misses text | Try windowed / borderless mode |
| Hotkeys unresponsive | Run as Administrator |
| `ModuleNotFoundError` | SETUP.bat → option 4 Repair |
| Comparison split wrong | Check session log; adjust `WORN_MARKERS` in Config |

---

## Folder Layout

```
GoldSense\
+-- SETUP.bat         <- Setup & management menu
+-- run.bat           <- Quick launcher
+-- README.md
+-- requirements.txt
+-- src\
    +-- main.py       <- Entire application (single file)
+-- env\              <- Python environment  (created by SETUP.bat)
+-- _conda\           <- Local Miniconda     (created by SETUP.bat)
+-- _tools\
    +-- prefs.json    <- Saved calibration + pass list
+-- logs\             <- Session logs and hit captures
```
