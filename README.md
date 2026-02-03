DISASSEMBLE / SHEET NESTING TOOL (Streamlit + Modular Python)

This project is a lightweight sheet-nesting / cutting-plan helper built with Streamlit.
You enter part sizes + quantities, sheet size + stock count, and cutting constraints
(kerf spacing and outer margin). The app computes a packing layout using rectpack
(heuristic 2D bin packing), then outputs placements (coordinates) and a visual layout
(SVG export).

----------------------------------------------------------------------
FEATURES
----------------------------------------------------------------------
- Parts list input: id / width (w) / height (h) / quantity (qty) / rotate allowed
- Sheet settings: sheet width/height and available sheet count (0 = unlimited)
- Cutting settings:
  - kerf: minimum spacing between parts (represents blade width / cutting gap)
  - margin: outer safety margin from the sheet boundary
- Compute:
  - estimated number of sheets required
  - placements (x, y, w, h, rotation, sheet index)
  - utilization metrics
- Export:
  - placements.csv
  - result.json
  - per-sheet layout SVG

----------------------------------------------------------------------
PROJECT STRUCTURE
----------------------------------------------------------------------
app.py
  Streamlit UI and user workflow.

models.py
  Dataclasses for parts / placements / results.

solver_rectpack.py
  rectpack-based solver (heuristic packing). Optionally supports:
  - best-of / multi-start (try multiple strategies and choose the best)
  - margin handling (packing inside the usable area)

solver_ortools.py
  (Optional / future) OR-Tools CP-SAT solver for stricter constraints and
  better optimality guarantees.

viz.py
  Matplotlib drawing utilities + SVG export.

requirements.txt
  Python dependencies.

----------------------------------------------------------------------
INSTALLATION & RUN
----------------------------------------------------------------------
Recommended Python: 3.10+ (3.11 is OK)

1) Create and activate a virtual environment:

   python -m venv .venv

   macOS/Linux:
     source .venv/bin/activate

   Windows (PowerShell):
     .venv\Scripts\Activate.ps1

2) Install dependencies:

   python -m pip install -U pip
   python -m pip install -r requirements.txt

3) Run the app:

   python -m streamlit run app.py

----------------------------------------------------------------------
USAGE
----------------------------------------------------------------------
PARTS TABLE COLUMNS
- id (string): Part identifier. Each row id must be unique.
- w (number): Part width.
- h (number): Part height.
- qty (int): Quantity (>= 1).
- rotate (bool): Whether 90-degree rotation is allowed for this part.

SHEET SETTINGS
- Sheet W/H: Raw sheet dimensions.
- Sheet qty: 0 = unlimited, otherwise a fixed maximum number of sheets.

CUTTING SETTINGS (IMPORTANT)
- kerf:
  Minimum spacing BETWEEN parts.
  Intended to model blade width / toolpath clearance.
  In this project, kerf is treated as "part-to-part spacing", not an enlargement of
  a single part for feasibility checks.

- margin:
  Outer boundary clearance. Parts must stay inside the usable area:
    usable_w = sheet_w - 2*margin
    usable_h = sheet_h - 2*margin
  If margin is too large such that usable_w <= 0 or usable_h <= 0, packing is invalid.

SOLVER NOTES
- rectpack is heuristic: it is fast and practical, but does not guarantee global optimality.
- If you enable "best-of" / multi-start, the solver may run multiple heuristics and
  choose the best result (usually fewer sheets, or higher utilization).

----------------------------------------------------------------------
OUTPUTS
----------------------------------------------------------------------
- placements.csv
  One row per placed part instance, including:
  sheet_index, instance_id, part_id, x, y, width, height, rotated

- result.json
  Full payload including parameters, utilization metrics, placements, unplaced list.

- sheet_{index}.svg
  Vector layout for each sheet.

----------------------------------------------------------------------
LIMITATIONS / FAQ
----------------------------------------------------------------------
Why isn't the result perfectly optimal?
- rectpack uses heuristics. For guaranteed optimality or stricter constraints,
  implement solver_ortools.py (CP-SAT).

Does rotate=False guarantee a part will never rotate?
- A strict guarantee may require a constraint solver (OR-Tools). Heuristic packers
  sometimes need extra handling to enforce per-part rotation rules.

----------------------------------------------------------------------
ROADMAP (SUGGESTED NEXT STEPS)
----------------------------------------------------------------------
- Best-of: try multiple packing strategies (sorting + pack algorithms) and keep the best.
- OR-Tools CP-SAT solver: strict constraints + better optimization.
- Export DXF (ezdxf): import directly into CAD/CAM workflows.
- Cut-list export: aggregate by part_id for workshop reporting.

----------------------------------------------------------------------
LICENSE
----------------------------------------------------------------------
See LICENSE.
