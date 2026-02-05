DISASSEMBLE / SHEET NESTING TOOL (Streamlit + Modular Python)

This project is a lightweight sheet-nesting / cutting-plan helper built with Streamlit.
You enter part sizes + quantities, sheet size + stock count, and cutting constraints
(kerf spacing and outer margin). The app computes a packing layout using rectpack
(heuristic 2D bin packing), then outputs placements (coordinates) and a visual layout
(SVG export).

----------------------------------------------------------------------
FEATURES
----------------------------------------------------------------------
- Parts input table: size (W/H), quantity, rotation allowed
- Sheet settings: sheet width/height, number of sheets (0 = unlimited)
- Kerf (cut gap) between parts
- Margin (edge allowance) reserved around the sheet border
- Compute sheets needed and utilization
- Placements output: part coordinates + dimensions + rotated flag
- Visualization: layout plot + export SVG
- Export results:
  - placements.csv
  - result.json
  - sheet_*.svg

----------------------------------------------------------------------
Optimization (Heuristics)
----------------------------------------------------------------------
- B1 Best-of: multi-trial search across sorting strategies and (optionally) multiple rectpack algorithms
- B2 Repair:
  - Fill unplaced parts (best-fit insertion)
  - Reduce last sheet (try to move parts off the last sheet)
  - Eliminate any sheet (try to remove a chosen sheet by moving its parts to other sheets)
  - Compact layouts (tighten within each sheet)

----------------------------------------------------------------------
Visualization Convenience
----------------------------------------------------------------------
- Merge identical layouts: when multiple sheets have the same arrangement, you can group and display them as one “pattern”.

----------------------------------------------------------------------
PROJECT STRUCTURE
----------------------------------------------------------------------
.
├─ app.py                 Streamlit UI (main entry)
├─ models.py              dataclasses: PartInstance, Placement, SolveResult
├─ solver_rectpack.py     B1 best-of + B2 repair/compact/elimination
├─ viz.py                 matplotlib plotting + SVG export
├─ requirements.txt
└─ README.md

----------------------------------------------------------------------
Requirements
----------------------------------------------------------------------
- Python 3.10+ (tested on 3.11)
- Dependencies in requirements.txt

----------------------------------------------------------------------
Run Locally
----------------------------------------------------------------------
1) Create venv & install deps
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
pip install -r requirements.txt

2) Start Streamlit
streamlit run app.py

----------------------------------------------------------------------
Usage Guide (UI)
----------------------------------------------------------------------
1. Select unit: mm or cm
2. Set sheet W/H, optional sheet_qty (0 = unlimited)
3. Set kerf and margin
4. Configure solver options:
   - B1 best-of trials / time limit / seed / algorithm attempts
   - B2 repair options (fill unplaced, reduce last sheet, eliminate any sheet)
5. Edit parts in the table and click Compute
6. Review:
   - metrics: sheets used, utilization, unplaced count
   - placements table (coordinates)
   - plot (per-sheet or merged patterns)
7. Download CSV/JSON/SVG

----------------------------------------------------------------------
Algorithms & Notes (Important)
----------------------------------------------------------------------
Units
- Internal computations are in mm
- UI can switch between mm/cm (values are scaled automatically)

Kerf & Margin
- kerf: spacing between parts (applied as effective padding between rectangles)
- margin: reserved border around the sheet; parts must fit within sheet - 2*margin

Rotation constraint
- rotate=True: solver may rotate the part
- rotate=False: soft constraint under rectpack (rotation is checked after packing).
  If strict rotation constraints are required, consider an OR-Tools based solver (future work).

----------------------------------------------------------------------
Optimality
----------------------------------------------------------------------
This is a heuristic solver. It produces good layouts quickly but does not guarantee global optimality.

----------------------------------------------------------------------
Roadmap
----------------------------------------------------------------------
- OR-Tools solver with strict constraints (rotation, guillotine cuts, etc.)
- Better objective functions (min sheets first, then maximize utilization)
- Cut-list export formats (CSV for CNC / saw workflows)
- Optional material libraries / presets

Author
- Yu-Sheng (Yu-Chuan) Chiu

----------------------------------------------------------------------
Online Link
----------------------------------------------------------------------
[https://2evglapjdpvsegnpgdyqcg.streamlit.app/](https://2evglapjdpvsegnpgdyqcg.streamlit.app/)

----------------------------------------------------------------------
LICENSE
----------------------------------------------------------------------
See LICENSE.
