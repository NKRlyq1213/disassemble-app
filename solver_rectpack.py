from __future__ import annotations
from typing import List, Dict, Optional, Tuple
import math
import time
import random

from models import PartInstance, Placement, SolveResult


# --------------------
# Public: expand
# --------------------
def expand_parts(parts: List[Dict]) -> List[PartInstance]:
    """
    parts: [{"id":..., "w":..., "h":..., "qty":..., "rotate":...}, ...]
    """
    out: List[PartInstance] = []
    for row in parts:
        pid = str(row["id"]).strip()
        w = float(row["w"])
        h = float(row["h"])
        qty = int(row["qty"])
        rot = bool(row["rotate"])
        for k in range(qty):
            out.append(
                PartInstance(
                    instance_id=f"{pid}#{k+1}",
                    part_id=pid,
                    width=w,
                    height=h,
                    rotate=rot,
                )
            )
    return out


# --------------------
# Ordering / scoring (B1)
# --------------------
DEFAULT_SORT_POOL = [
    "area_desc",
    "maxside_desc",
    "width_desc",
    "height_desc",
    "perimeter_desc",
    "random",
]


def _order_parts(parts: List[PartInstance], sort_key: str, rng: Optional[random.Random] = None) -> List[PartInstance]:
    rng = rng or random.Random(0)

    if sort_key == "random":
        out = parts.copy()
        rng.shuffle(out)
        return out

    def primary(p: PartInstance) -> float:
        if sort_key == "maxside_desc":
            return float(max(p.width, p.height))
        if sort_key == "width_desc":
            return float(p.width)
        if sort_key == "height_desc":
            return float(p.height)
        if sort_key == "perimeter_desc":
            return float(2.0 * (p.width + p.height))
        return float(p.width * p.height)  # area_desc

    return sorted(parts, key=lambda p: (-primary(p), rng.random()))


def _result_key(res: SolveResult) -> Tuple[int, int, float]:
    return (len(res.unplaced), res.sheets_used, -res.utilization)


def _better(a: SolveResult, b: SolveResult) -> SolveResult:
    return a if _result_key(a) < _result_key(b) else b


def _area_lower_bound_sheets(parts: List[PartInstance], sheet_w: float, sheet_h: float, margin: float) -> int:
    usable_w = sheet_w - 2 * margin
    usable_h = sheet_h - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        return 10**9
    usable_area = usable_w * usable_h
    total_area = sum(p.width * p.height for p in parts)
    if usable_area <= 0:
        return 10**9
    return max(1, int(math.ceil(total_area / usable_area)))


# --------------------
# rectpack internals
# --------------------
def _available_pack_algos():
    algos = [("default", None)]
    try:
        from rectpack import (
            MaxRectsBssf, MaxRectsBaf, MaxRectsBl,
            GuillotineBssfSas, GuillotineBafSas,
            SkylineBl, SkylineMwf, SkylineMwfl,
        )
        algos.extend(
            [
                ("MaxRectsBssf", MaxRectsBssf),
                ("MaxRectsBaf", MaxRectsBaf),
                ("MaxRectsBl", MaxRectsBl),
                ("GuillotineBssfSas", GuillotineBssfSas),
                ("GuillotineBafSas", GuillotineBafSas),
                ("SkylineBl", SkylineBl),
                ("SkylineMwf", SkylineMwf),
                ("SkylineMwfl", SkylineMwfl),
            ]
        )
    except Exception:
        pass
    return algos


def _create_packer(pack_algo_cls):
    from rectpack import newPacker
    try:
        if pack_algo_cls is None:
            return newPacker(rotation=True)
        return newPacker(rotation=True, pack_algo=pack_algo_cls)
    except TypeError:
        return newPacker(rotation=True)


def _rects_to_result(rects, rid_map, locked_rids, margin: float, kerf: float):
    placements: List[Placement] = []
    placed_instance_ids: set[str] = set()
    used_bins: set[int] = set()
    invalid_locked: List[PartInstance] = []

    for b, x, y, w_eff, h_eff, rid in rects:
        p = rid_map[int(rid)]

        out_w = max(0.0, float(w_eff) - kerf)
        out_h = max(0.0, float(h_eff) - kerf)

        rotated = False
        if abs(out_w - p.width) < 1e-6 and abs(out_h - p.height) < 1e-6:
            rotated = False
        elif abs(out_w - p.height) < 1e-6 and abs(out_h - p.width) < 1e-6:
            rotated = True
        else:
            rotated = False

        if (int(rid) in locked_rids) and rotated:
            invalid_locked.append(p)
            continue

        placements.append(
            Placement(
                sheet_index=int(b),
                instance_id=p.instance_id,
                part_id=p.part_id,
                x=float(x) + margin,
                y=float(y) + margin,
                width=out_w,
                height=out_h,
                rotated=rotated,
            )
        )
        placed_instance_ids.add(p.instance_id)
        used_bins.add(int(b))

    unplaced = [p for p in rid_map.values() if p.instance_id not in placed_instance_ids]
    if invalid_locked:
        invalid_ids = {p.instance_id for p in invalid_locked}
        unplaced = [p for p in unplaced if p.instance_id not in invalid_ids] + invalid_locked

    note: Optional[str] = None
    if invalid_locked:
        note = "部分 rotate=False 的零件被求解器旋轉，已標記為 unplaced（若要完全嚴格需改 OR-Tools 或關閉全域 rotation）。"

    return placements, unplaced, used_bins, note


def _pack_once(
    parts_sorted: List[PartInstance],
    sheet_w: float,
    sheet_h: float,
    sheet_qty: int,   # 0=不限
    kerf: float,
    margin: float,
    pack_algo_name: str,
    pack_algo_cls,
    deadline: Optional[float] = None,
) -> SolveResult:
    from rectpack import PackingMode

    usable_w = sheet_w - 2 * margin
    usable_h = sheet_h - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        raise ValueError("margin 太大，導致可用區域寬或高 <= 0。")

    # kerf trick：bin + kerf / rect + kerf
    bin_w = usable_w + kerf
    bin_h = usable_h + kerf

    def _time_up() -> bool:
        return (deadline is not None) and (time.perf_counter() > deadline)

    packer = _create_packer(pack_algo_cls)
    try:
        packer.mode = PackingMode.Offline
    except Exception:
        pass

    def add_bins(count: int):
        packer.add_bin(bin_w, bin_h, count=count)

    rid_map: Dict[int, PartInstance] = {}
    locked_rids: set[int] = set()

    for rid, p in enumerate(parts_sorted, start=1):
        packer.add_rect(p.width + kerf, p.height + kerf, rid=rid)
        rid_map[rid] = p
        if not p.rotate:
            locked_rids.add(rid)

    total_eff_area = sum((p.width + kerf) * (p.height + kerf) for p in parts_sorted)
    bin_area = bin_w * bin_h
    lb = max(1, int(math.ceil(total_eff_area / bin_area))) if bin_area > 0 else 1

    if sheet_qty == 0:
        max_bins = len(parts_sorted)
        bins_try = lb
        best: Optional[SolveResult] = None

        while True:
            if _time_up() and best is not None:
                return best

            packer2 = _create_packer(pack_algo_cls)
            packer2.add_bin(bin_w, bin_h, count=bins_try)

            rid_map2: Dict[int, PartInstance] = {}
            locked2: set[int] = set()
            for rid, p in enumerate(parts_sorted, start=1):
                packer2.add_rect(p.width + kerf, p.height + kerf, rid=rid)
                rid_map2[rid] = p
                if not p.rotate:
                    locked2.add(rid)

            packer2.pack()
            rects = packer2.rect_list()

            placements, unplaced, used_bins, note = _rects_to_result(
                rects=rects,
                rid_map=rid_map2,
                locked_rids=locked2,
                margin=margin,
                kerf=kerf,
            )

            sheets_used = (max(used_bins) + 1) if used_bins else 0
            total_part_area = sum(pl.width * pl.height for pl in placements)
            total_sheet_area = sheets_used * sheet_w * sheet_h if sheets_used > 0 else 0.0
            utilization = (total_part_area / total_sheet_area) if total_sheet_area > 0 else 0.0

            res = SolveResult(
                sheet_width=sheet_w,
                sheet_height=sheet_h,
                kerf=kerf,
                sheets_used=sheets_used,
                utilization=utilization,
                placements=placements,
                unplaced=unplaced,
                solver=f"rectpack/{pack_algo_name}/bins={bins_try}",
                note=note,
            )

            best = res if best is None else _better(res, best)

            if len(unplaced) == 0:
                return best
            if bins_try >= max_bins:
                return best
            bins_try = min(max_bins, int(bins_try * 1.5) + 1)

    else:
        add_bins(int(sheet_qty))
        packer.pack()
        rects = packer.rect_list()

        placements, unplaced, used_bins, note = _rects_to_result(
            rects=rects,
            rid_map=rid_map,
            locked_rids=locked_rids,
            margin=margin,
            kerf=kerf,
        )

        sheets_used = (max(used_bins) + 1) if used_bins else 0
        total_part_area = sum(pl.width * pl.height for pl in placements)
        total_sheet_area = sheets_used * sheet_w * sheet_h if sheets_used > 0 else 0.0
        utilization = (total_part_area / total_sheet_area) if total_sheet_area > 0 else 0.0

        return SolveResult(
            sheet_width=sheet_w,
            sheet_height=sheet_h,
            kerf=kerf,
            sheets_used=sheets_used,
            utilization=utilization,
            placements=placements,
            unplaced=unplaced,
            solver=f"rectpack/{pack_algo_name}",
            note=note,
        )


def _build_trial_plan(
    rng: random.Random,
    configs: List[Tuple[str, str, object]],
    best_of_trials: int,
) -> List[Tuple[str, str, object]]:
    if best_of_trials <= 0:
        return []
    if best_of_trials <= len(configs):
        return rng.sample(configs, k=best_of_trials)

    plan: List[Tuple[str, str, object]] = []
    remain = best_of_trials
    while remain > 0:
        round_cfg = configs.copy()
        rng.shuffle(round_cfg)
        take = min(remain, len(round_cfg))
        plan.extend(round_cfg[:take])
        remain -= take
    return plan


# ============================================================
# B2.1: candidate points + best-fit (cross sheet)
# ============================================================
def _rect_overlap(ax: float, ay: float, aw: float, ah: float, bx: float, by: float, bw: float, bh: float) -> bool:
    return (ax < bx + bw) and (ax + aw > bx) and (ay < by + bh) and (ay + ah > by)


def _build_effective_rects(sheet_placements: List[Placement], margin: float, kerf: float) -> List[Tuple[float, float, float, float]]:
    eff = []
    for pl in sheet_placements:
        x = float(pl.x) - margin
        y = float(pl.y) - margin
        eff.append((x, y, float(pl.width) + kerf, float(pl.height) + kerf))
    return eff


def _candidate_points_grid(
    eff_rects: List[Tuple[float, float, float, float]],
    limit_points: int = 25000,
) -> List[Tuple[float, float]]:
    xs = {0.0}
    ys = {0.0}
    for x, y, w, h in eff_rects:
        xs.add(float(x))
        xs.add(float(x + w))
        ys.add(float(y))
        ys.add(float(y + h))

    xs_sorted = sorted(xs)
    ys_sorted = sorted(ys)

    pts: List[Tuple[float, float]] = []
    for yy in ys_sorted:
        for xx in xs_sorted:
            pts.append((xx, yy))
            if len(pts) >= limit_points:
                return pts
    return pts


def _place_score(px: float, py: float, w_eff: float, h_eff: float, bin_w_eff: float, bin_h_eff: float) -> Tuple[float, float, float, float]:
    slack_w = max(0.0, bin_w_eff - (px + w_eff))
    slack_h = max(0.0, bin_h_eff - (py + h_eff))
    return (slack_w * slack_h, slack_w + slack_h, py, px)


def _best_placement_in_sheet(
    inst: PartInstance,
    sheet_eff_rects: List[Tuple[float, float, float, float]],
    usable_w: float,
    usable_h: float,
    kerf: float,
    margin: float,
    sheet_index: int,
    deadline: Optional[float] = None,
    limit_points: int = 25000,
) -> Optional[Tuple[Placement, Tuple[float, float, float, float]]]:
    if deadline is not None and time.perf_counter() > deadline:
        return None

    bin_w_eff = usable_w + kerf
    bin_h_eff = usable_h + kerf

    pts = _candidate_points_grid(sheet_eff_rects, limit_points=limit_points)

    orientations: List[Tuple[float, float, bool]] = [(inst.width, inst.height, False)]
    if inst.rotate and (inst.width != inst.height):
        orientations.append((inst.height, inst.width, True))

    best: Optional[Tuple[Placement, Tuple[float, float, float, float]]] = None

    for (w, h, rotated) in orientations:
        w_eff = w + kerf
        h_eff = h + kerf

        for (px, py) in pts:
            if deadline is not None and time.perf_counter() > deadline:
                return best

            if px < 0 or py < 0:
                continue
            if px + w_eff > bin_w_eff + 1e-9 or py + h_eff > bin_h_eff + 1e-9:
                continue

            ok = True
            for (rx, ry, rw, rh) in sheet_eff_rects:
                if _rect_overlap(px, py, w_eff, h_eff, rx, ry, rw, rh):
                    ok = False
                    break
            if not ok:
                continue

            score = _place_score(px, py, w_eff, h_eff, bin_w_eff, bin_h_eff)
            pl = Placement(
                sheet_index=sheet_index,
                instance_id=inst.instance_id,
                part_id=inst.part_id,
                x=px + margin,
                y=py + margin,
                width=float(w),
                height=float(h),
                rotated=rotated,
            )

            if best is None or score < best[1]:
                best = (pl, score)

    return best


def _best_placement_any_sheet(
    inst: PartInstance,
    sheets: List[List[Placement]],
    usable_w: float,
    usable_h: float,
    kerf: float,
    margin: float,
    deadline: Optional[float],
    limit_points: int = 25000,
) -> Optional[Placement]:
    best: Optional[Tuple[Placement, Tuple[float, float, float, float]]] = None

    for si in range(len(sheets)):
        if deadline is not None and time.perf_counter() > deadline:
            break

        eff_rects = _build_effective_rects(sheets[si], margin=margin, kerf=kerf)
        cand = _best_placement_in_sheet(
            inst=inst,
            sheet_eff_rects=eff_rects,
            usable_w=usable_w,
            usable_h=usable_h,
            kerf=kerf,
            margin=margin,
            sheet_index=si,
            deadline=deadline,
            limit_points=limit_points,
        )
        if cand is None:
            continue

        if best is None or cand[1] < best[1]:
            best = cand

    return best[0] if best is not None else None


# ============================================================
# B2.2: Compact (returns new placements; Placement frozen safe)
# ============================================================
def _rect_overlap_allow_touch(ax: float, ay: float, aw: float, ah: float,
                             bx: float, by: float, bw: float, bh: float) -> bool:
    return (ax < bx + bw) and (ax + aw > bx) and (ay < by + bh) and (ay + ah > by)


def _compact_one_sheet(
    sheet_pls: List[Placement],
    usable_w: float,
    usable_h: float,
    kerf: float,
    margin: float,
    iters: int = 8,
) -> List[Placement]:
    if not sheet_pls:
        return []

    rects = []
    for pl in sheet_pls:
        rects.append({
            "src": pl,
            "x": float(pl.x) - margin,
            "y": float(pl.y) - margin,
            "w": float(pl.width) + kerf,
            "h": float(pl.height) + kerf,
        })

    bin_w = usable_w + kerf
    bin_h = usable_h + kerf

    def overlaps(i: int, nx: float, ny: float) -> bool:
        ai = rects[i]
        for j, bj in enumerate(rects):
            if j == i:
                continue
            if _rect_overlap_allow_touch(nx, ny, ai["w"], ai["h"], bj["x"], bj["y"], bj["w"], bj["h"]):
                return True
        return False

    for _ in range(iters):
        moved = False
        order = sorted(range(len(rects)), key=lambda i: (rects[i]["y"], rects[i]["x"]))

        for i in order:
            ai = rects[i]

            # left push
            left_bound = 0.0
            for j, bj in enumerate(rects):
                if j == i:
                    continue
                y_overlap = (ai["y"] < bj["y"] + bj["h"]) and (ai["y"] + ai["h"] > bj["y"])
                if not y_overlap:
                    continue
                if bj["x"] + bj["w"] <= ai["x"] + 1e-9:
                    left_bound = max(left_bound, bj["x"] + bj["w"])

            nx = max(0.0, left_bound)
            nx = min(nx, bin_w - ai["w"])
            if nx < ai["x"] - 1e-9 and not overlaps(i, nx, ai["y"]):
                ai["x"] = nx
                moved = True

            # down push
            down_bound = 0.0
            for j, bj in enumerate(rects):
                if j == i:
                    continue
                x_overlap = (ai["x"] < bj["x"] + bj["w"]) and (ai["x"] + ai["w"] > bj["x"])
                if not x_overlap:
                    continue
                if bj["y"] + bj["h"] <= ai["y"] + 1e-9:
                    down_bound = max(down_bound, bj["y"] + bj["h"])

            ny = max(0.0, down_bound)
            ny = min(ny, bin_h - ai["h"])
            if ny < ai["y"] - 1e-9 and not overlaps(i, ai["x"], ny):
                ai["y"] = ny
                moved = True

        if not moved:
            break

    out: List[Placement] = []
    for r in rects:
        src: Placement = r["src"]
        out.append(
            Placement(
                sheet_index=src.sheet_index,
                instance_id=src.instance_id,
                part_id=src.part_id,
                x=r["x"] + margin,
                y=r["y"] + margin,
                width=src.width,
                height=src.height,
                rotated=src.rotated,
            )
        )
    return out


def _compact_all_sheets(
    sheets: List[List[Placement]],
    usable_w: float,
    usable_h: float,
    kerf: float,
    margin: float,
    iters: int = 8,
) -> List[List[Placement]]:
    out: List[List[Placement]] = []
    for s in sheets:
        if len(s) >= 2:
            out.append(_compact_one_sheet(s, usable_w, usable_h, kerf, margin, iters=iters))
        else:
            out.append(list(s))
    return out


def _renumber_sheets(sheets: List[List[Placement]]) -> List[List[Placement]]:
    """確保每個 Placement.sheet_index 與 list index 一致（Placement frozen → 產新物件）"""
    out: List[List[Placement]] = []
    for si, s in enumerate(sheets):
        ns: List[Placement] = []
        for pl in s:
            ns.append(
                Placement(
                    sheet_index=si,
                    instance_id=pl.instance_id,
                    part_id=pl.part_id,
                    x=pl.x,
                    y=pl.y,
                    width=pl.width,
                    height=pl.height,
                    rotated=pl.rotated,
                )
            )
        out.append(ns)
    return out


# ============================================================
# B2.3: Bin elimination (any sheet, first sheet included)
# ============================================================
def _sheet_part_area(s: List[Placement]) -> float:
    return float(sum(pl.width * pl.height for pl in s))


def _candidate_sheets_order(
    sheets: List[List[Placement]],
    policy: str,
) -> List[int]:
    n = len(sheets)
    if n <= 1:
        return list(range(n))

    # looseness: area smaller -> looser -> easier to eliminate
    areas = [(i, _sheet_part_area(sheets[i])) for i in range(n)]
    areas_sorted = [i for i, _ in sorted(areas, key=lambda x: x[1])]

    if policy == "first_only":
        return [0]
    if policy == "last_only":
        return [n - 1]
    if policy == "loose_only":
        return areas_sorted
    if policy == "first_then_loose":
        order = [0] + [i for i in areas_sorted if i != 0]
        return order
    if policy == "loose_then_first":
        order = areas_sorted
        if 0 in order:
            order.remove(0)
            order.insert(0, 0)
        return order
    # default
    order = [0] + [i for i in areas_sorted if i != 0]
    return order


def _try_eliminate_sheet(
    sheets: List[List[Placement]],
    target_idx: int,
    inst_by_id: Dict[str, PartInstance],
    usable_w: float,
    usable_h: float,
    kerf: float,
    margin: float,
    deadline: Optional[float],
    limit_points: int,
) -> Optional[List[List[Placement]]]:
    """
    嘗試刪掉 target_idx：把該片所有 instances 塞回其它片
    成功 → 回傳新的 sheets（少一片）；失敗 → None
    """
    if target_idx < 0 or target_idx >= len(sheets):
        return None
    if len(sheets) <= 1:
        return None
    if deadline is not None and time.perf_counter() > deadline:
        return None

    target_pls = sheets[target_idx]
    # map target placements -> instances
    target_insts: List[PartInstance] = []
    for pl in target_pls:
        inst = inst_by_id.get(pl.instance_id)
        if inst is None:
            return None
        target_insts.append(inst)

    # 其它片（深拷貝 list 結構即可；Placement frozen）
    other_sheets = [list(s) for i, s in enumerate(sheets) if i != target_idx]

    # 先大到小放回去（成功率較高）
    target_insts_sorted = sorted(target_insts, key=lambda p: p.width * p.height, reverse=True)

    for inst in target_insts_sorted:
        if deadline is not None and time.perf_counter() > deadline:
            return None

        pl_new = _best_placement_any_sheet(
            inst=inst,
            sheets=other_sheets,
            usable_w=usable_w,
            usable_h=usable_h,
            kerf=kerf,
            margin=margin,
            deadline=deadline,
            limit_points=limit_points,
        )
        if pl_new is None:
            return None
        other_sheets[int(pl_new.sheet_index)].append(pl_new)

    # 成功：重新 renumber + compact 一次（讓下輪更好消）
    other_sheets = _renumber_sheets(other_sheets)
    other_sheets = _compact_all_sheets(other_sheets, usable_w, usable_h, kerf, margin, iters=10)
    other_sheets = _renumber_sheets(other_sheets)
    return other_sheets


# ============================================================
# B2 repair (B2.1 + B2.2 + B2.3)
# ============================================================
def _repair_solution(
    base: SolveResult,
    instances: List[PartInstance],
    sheet_w: float,
    sheet_h: float,
    kerf: float,
    margin: float,
    repair_time_limit_s: float = 1.0,
    fill_unplaced: bool = True,
    # B2.3
    reduce_last_sheet: bool = True,
    eliminate_any_sheet: bool = True,
    eliminate_policy: str = "first_then_loose",
    eliminate_max_rounds: int = 20,
    # internal
    limit_points: int = 25000,
) -> SolveResult:
    t0 = time.perf_counter()
    deadline = t0 + float(repair_time_limit_s) if repair_time_limit_s is not None else None

    usable_w = sheet_w - 2 * margin
    usable_h = sheet_h - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        return base

    inst_by_id: Dict[str, PartInstance] = {p.instance_id: p for p in instances}

    # group placements into sheets
    sheets: List[List[Placement]] = [[] for _ in range(max(base.sheets_used, 0))]
    for pl in base.placements:
        si = int(pl.sheet_index)
        if 0 <= si < len(sheets):
            sheets[si].append(pl)

    while sheets and len(sheets[-1]) == 0:
        sheets.pop()

    before_sheets = len(sheets)
    before_unplaced = len(base.unplaced)

    # -------- (A) compact first (你要求的順序) --------
    sheets = _compact_all_sheets(sheets, usable_w, usable_h, kerf, margin, iters=10)
    sheets = _renumber_sheets(sheets)

    # -------- (B) fill unplaced --------
    unplaced_after = list(base.unplaced)

    filled_cnt = 0
    if fill_unplaced and unplaced_after and sheets:
        # 大到小優先
        order = sorted(unplaced_after, key=lambda p: p.width * p.height, reverse=True)
        remain: List[PartInstance] = []
        for inst in order:
            if deadline is not None and time.perf_counter() > deadline:
                remain.append(inst)
                continue
            pl_new = _best_placement_any_sheet(
                inst=inst,
                sheets=sheets,
                usable_w=usable_w,
                usable_h=usable_h,
                kerf=kerf,
                margin=margin,
                deadline=deadline,
                limit_points=limit_points,
            )
            if pl_new is None:
                remain.append(inst)
            else:
                sheets[int(pl_new.sheet_index)].append(pl_new)
                filled_cnt += 1

        unplaced_after = remain
        sheets = _compact_all_sheets(sheets, usable_w, usable_h, kerf, margin, iters=10)
        sheets = _renumber_sheets(sheets)

    eliminated = 0
    rounds = 0
    # (C0) 先刪最後一片（等價 last_only）
    if reduce_last_sheet and len(sheets) > 1:
        new_sheets = _try_eliminate_sheet(
            sheets=sheets,
            target_idx=len(sheets) - 1,
            inst_by_id=inst_by_id,
            usable_w=usable_w,
            usable_h=usable_h,
            kerf=kerf,
            margin=margin,
            deadline=deadline,
            limit_points=limit_points,
        )
        if new_sheets is not None:
            sheets = new_sheets    
    # -------- (C) B2.3 eliminate-any-sheet --------
    if eliminate_any_sheet and sheets:
        changed = True
        while changed:
            if deadline is not None and time.perf_counter() > deadline:
                break
            if len(sheets) <= 1:
                break
            if rounds >= eliminate_max_rounds:
                break

            changed = False
            rounds += 1

            order_idx = _candidate_sheets_order(sheets, eliminate_policy)

            for t_idx in order_idx:
                if deadline is not None and time.perf_counter() > deadline:
                    break
                if len(sheets) <= 1:
                    break
                if t_idx >= len(sheets):
                    continue

                new_sheets = _try_eliminate_sheet(
                    sheets=sheets,
                    target_idx=t_idx,
                    inst_by_id=inst_by_id,
                    usable_w=usable_w,
                    usable_h=usable_h,
                    kerf=kerf,
                    margin=margin,
                    deadline=deadline,
                    limit_points=limit_points,
                )
                if new_sheets is not None:
                    sheets = new_sheets
                    eliminated += 1
                    changed = True
                    break  # 片數變了，回到下一輪重新算候選順序

    # rebuild placements
    final_placements: List[Placement] = []
    for si, s in enumerate(sheets):
        for pl in s:
            final_placements.append(
                Placement(
                    sheet_index=si,
                    instance_id=pl.instance_id,
                    part_id=pl.part_id,
                    x=pl.x,
                    y=pl.y,
                    width=pl.width,
                    height=pl.height,
                    rotated=pl.rotated,
                )
            )

    sheets_used = len(sheets)
    total_part_area = sum(pl.width * pl.height for pl in final_placements)
    total_sheet_area = sheets_used * sheet_w * sheet_h if sheets_used > 0 else 0.0
    utilization = (total_part_area / total_sheet_area) if total_sheet_area > 0 else 0.0

    elapsed = time.perf_counter() - t0
    extra = (
        f"B2 repair: compact=on, fill_unplaced=on(+{filled_cnt}), "
        f"eliminate_any_sheet={eliminate_any_sheet}({eliminate_policy}) eliminated={eliminated}, "
        f"sheets {before_sheets}->{sheets_used}, unplaced {before_unplaced}->{len(unplaced_after)}, "
        f"time={elapsed:.2f}s"
    )
    note = (base.note + " | " + extra) if base.note else extra

    return SolveResult(
        sheet_width=sheet_w,
        sheet_height=sheet_h,
        kerf=kerf,
        sheets_used=sheets_used,
        utilization=utilization,
        placements=final_placements,
        unplaced=unplaced_after,
        solver=(base.solver + " +B2").strip(),
        note=note,
    )


# --------------------
# Public: solve_rectpack (B1 + B2)
# --------------------
def solve_rectpack(
    parts: List[PartInstance],
    sheet_w: float,
    sheet_h: float,
    sheet_qty: int,
    kerf: float,
    sort_key: str = "area_desc",
    margin: float = 0.0,
    multistart: bool = True,
    # --- B1 ---
    best_of_trials: int = 30,
    time_limit_s: Optional[float] = 3.0,
    seed: int = 0,
    sort_pool: Optional[List[str]] = None,
    try_pack_algos: bool = True,
    # --- B2 (repair) ---
    repair: bool = False,
    repair_time_limit_s: float = 1.0,
    repair_fill_unplaced: bool = True,
    repair_reduce_last_sheet: bool = True,
    # --- B2.3 ---
    eliminate_any_sheet: bool = True,
    eliminate_policy: str = "first_then_loose",   # 先第 0 片，再由鬆到緊
    eliminate_max_rounds: int = 20,
) -> SolveResult:
    if margin < 0:
        raise ValueError("margin 不可為負。")
    if kerf < 0:
        raise ValueError("kerf 不可為負。")

    usable_w = sheet_w - 2 * margin
    usable_h = sheet_h - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        raise ValueError("margin 太大，導致可用區域寬或高 <= 0。")

    # 單件可行性（不含 kerf）
    for p in parts:
        fits = (p.width <= usable_w and p.height <= usable_h) or (
            p.rotate and p.height <= usable_w and p.width <= usable_h
        )
        if not fits:
            return SolveResult(
                sheet_width=sheet_w,
                sheet_height=sheet_h,
                kerf=kerf,
                sheets_used=0,
                utilization=0.0,
                placements=[],
                unplaced=[p for p in parts],
                solver="rectpack",
                note="存在零件在扣除 margin 的可用區域中無法放入（無論是否旋轉）。",
            )

    # 不開 multistart：單次
    if not multistart:
        parts_sorted = _order_parts(parts, sort_key, rng=random.Random(seed))
        base = _pack_once(
            parts_sorted=parts_sorted,
            sheet_w=sheet_w,
            sheet_h=sheet_h,
            sheet_qty=sheet_qty,
            kerf=kerf,
            margin=margin,
            pack_algo_name=f"default+{sort_key}",
            pack_algo_cls=None,
            deadline=None,
        )
        if repair:
            return _repair_solution(
                base=base,
                instances=parts,
                sheet_w=sheet_w,
                sheet_h=sheet_h,
                kerf=kerf,
                margin=margin,
                repair_time_limit_s=repair_time_limit_s,
                fill_unplaced=repair_fill_unplaced,
                reduce_last_sheet=repair_reduce_last_sheet,
                eliminate_any_sheet=eliminate_any_sheet,
                eliminate_policy=eliminate_policy,
                eliminate_max_rounds=eliminate_max_rounds,
            )
        return base

    # ---- B1 best-of ----
    rng = random.Random(seed)

    pool = (sort_pool or DEFAULT_SORT_POOL).copy()
    if sort_key not in pool:
        pool.insert(0, sort_key)
    else:
        pool = [sort_key] + [x for x in pool if x != sort_key]

    algos = _available_pack_algos() if try_pack_algos else [("default", None)]

    configs: List[Tuple[str, str, object]] = []
    for sk in pool:
        for algo_name, algo_cls in algos:
            configs.append((sk, algo_name, algo_cls))

    plan = _build_trial_plan(rng, configs, best_of_trials)

    start = time.perf_counter()
    deadline = (start + float(time_limit_s)) if (time_limit_s is not None) else None

    lb_sheets = _area_lower_bound_sheets(parts, sheet_w, sheet_h, margin)

    best: Optional[SolveResult] = None
    best_trial_info: Optional[str] = None
    trials_run = 0

    for t, (sk, algo_name, algo_cls) in enumerate(plan, start=1):
        if deadline is not None and time.perf_counter() > deadline:
            break

        rng_trial = random.Random(seed + t * 10007)
        parts_sorted = _order_parts(parts, sk, rng=rng_trial)

        res = _pack_once(
            parts_sorted=parts_sorted,
            sheet_w=sheet_w,
            sheet_h=sheet_h,
            sheet_qty=sheet_qty,
            kerf=kerf,
            margin=margin,
            pack_algo_name=f"{algo_name}+{sk}",
            pack_algo_cls=algo_cls,
            deadline=deadline,
        )

        trials_run += 1
        if best is None or _result_key(res) < _result_key(best):
            best = res
            best_trial_info = f"trial={t}/{len(plan)} sort={sk} algo={algo_name}"

            if len(best.unplaced) == 0 and best.sheets_used == lb_sheets:
                break

    if best is None:
        parts_sorted = _order_parts(parts, sort_key, rng=rng)
        best = _pack_once(
            parts_sorted=parts_sorted,
            sheet_w=sheet_w,
            sheet_h=sheet_h,
            sheet_qty=sheet_qty,
            kerf=kerf,
            margin=margin,
            pack_algo_name=f"default+{sort_key}",
            pack_algo_cls=None,
            deadline=deadline,
        )
        best_trial_info = "fallback"

    elapsed = time.perf_counter() - start
    extra_note = f"best-of trials={trials_run}/{len(plan)} lb_sheets={lb_sheets} time={elapsed:.2f}s best=({best_trial_info})"
    merged_note = (best.note + " | " + extra_note) if best.note else extra_note

    base = SolveResult(
        sheet_width=best.sheet_width,
        sheet_height=best.sheet_height,
        kerf=best.kerf,
        sheets_used=best.sheets_used,
        utilization=best.utilization,
        placements=best.placements,
        unplaced=best.unplaced,
        solver=best.solver + " (best-of)",
        note=merged_note,
    )

    if repair:
        return _repair_solution(
            base=base,
            instances=parts,
            sheet_w=sheet_w,
            sheet_h=sheet_h,
            kerf=kerf,
            margin=margin,
            repair_time_limit_s=repair_time_limit_s,
            fill_unplaced=repair_fill_unplaced,
            eliminate_any_sheet=eliminate_any_sheet,
            eliminate_policy=eliminate_policy,
            eliminate_max_rounds=eliminate_max_rounds,
        )

    return base
