from __future__ import annotations
from typing import List, Dict, Optional, Tuple
import math

from models import PartInstance, Placement, SolveResult


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


def _order_parts(parts: List[PartInstance], sort_key: str) -> List[PartInstance]:
    if sort_key == "maxside_desc":
        return sorted(parts, key=lambda p: max(p.width, p.height), reverse=True)
    # default: area_desc
    return sorted(parts, key=lambda p: p.width * p.height, reverse=True)


def _available_pack_algos():
    """
    嘗試取得 rectpack 內建 pack_algo 類別。
    不同版本可能不完整，所以用 try/catch 安全降級。
    """
    algos = []
    try:
        from rectpack import (
            MaxRectsBssf, MaxRectsBaf, MaxRectsBl,
            GuillotineBssfSas, GuillotineBafSas,
            SkylineBl, SkylineMwf, SkylineMwfl,
        )
        algos = [
            ("MaxRectsBssf", MaxRectsBssf),
            ("MaxRectsBaf", MaxRectsBaf),
            ("MaxRectsBl", MaxRectsBl),
            ("GuillotineBssfSas", GuillotineBssfSas),
            ("GuillotineBafSas", GuillotineBafSas),
            ("SkylineBl", SkylineBl),
            ("SkylineMwf", SkylineMwf),
            ("SkylineMwfl", SkylineMwfl),
        ]
    except Exception:
        # 若版本不支援這些類別，就只用 rectpack 預設
        algos = [("default", None)]
    return algos


def _create_packer(pack_algo_cls):
    from rectpack import newPacker

    # 不同版本 newPacker 參數可能不完全一致，因此用 try/fallback
    try:
        if pack_algo_cls is None:
            return newPacker(rotation=True)
        return newPacker(rotation=True, pack_algo=pack_algo_cls)
    except TypeError:
        # 版本不吃 pack_algo 參數 → 只能用預設
        return newPacker(rotation=True)


def _pack_once(
    parts_sorted: List[PartInstance],
    sheet_w: float,
    sheet_h: float,
    sheet_qty: int,   # 0=不限
    kerf: float,
    margin: float,
    pack_algo_name: str,
    pack_algo_cls,
) -> SolveResult:
    """
    單次嘗試：固定排序 + 固定 pack_algo（若支援）
    """
    from rectpack import PackingMode

    usable_w = sheet_w - 2 * margin
    usable_h = sheet_h - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        raise ValueError("margin 太大，導致可用區域寬或高 <= 0。")

    # kerf：使用 (bin + kerf) / (rect + kerf) 讓貼邊不會被 kerf 擋住
    bin_w = usable_w + kerf
    bin_h = usable_h + kerf

    # 建 packer
    packer = _create_packer(pack_algo_cls)
    try:
        # 有些版本可指定 offline mode；不行就忽略
        packer.mode = PackingMode.Offline
    except Exception:
        pass

    # === bins 數量策略 ===
    # 若不限片數：我們用逐步加大 bins 的方式，避免一開始丟 len(parts) 造成太慢
    def add_bins(count: int):
        packer.add_bin(bin_w, bin_h, count=count)

    # rects
    rid_map: Dict[int, PartInstance] = {}
    locked_rids: set[int] = set()

    for rid, p in enumerate(parts_sorted, start=1):
        w_eff = p.width + kerf
        h_eff = p.height + kerf
        packer.add_rect(w_eff, h_eff, rid=rid)
        rid_map[rid] = p
        if not p.rotate:
            locked_rids.add(rid)

    # 動態決定 bins 數
    total_eff_area = sum((p.width + kerf) * (p.height + kerf) for p in parts_sorted)
    bin_area = bin_w * bin_h
    lb = max(1, int(math.ceil(total_eff_area / bin_area))) if bin_area > 0 else 1

    if sheet_qty == 0:
        # 不限：從下界開始，若放不完就加大 bins 再 pack（重建 packer 最穩）
        # 注意：rectpack 的 pack 後不適合直接再改 bins/rects；所以我們用「重跑」方式。
        # 這裡為了單次 pack_once 的乾淨性：用一個小迴圈重建 packer。
        max_bins = len(parts_sorted)
        bins_try = lb

        best: Optional[SolveResult] = None

        while True:
            # 重建 packer（確保狀態乾淨）
            packer2 = _create_packer(pack_algo_cls)
            try:
                packer2.add_bin(bin_w, bin_h, count=bins_try)
            except Exception:
                # 退回最保守：一次只加 bins_try 個
                packer2.add_bin(bin_w, bin_h, count=bins_try)

            # 再加 rects
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

            # 若全放下就停
            if len(unplaced) == 0:
                return best

            # bins 已經到上限還放不下
            if bins_try >= max_bins:
                return best

            # 增加 bins（成長倍率）
            bins_try = min(max_bins, int(bins_try * 1.5) + 1)

    else:
        # 有限 bins
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


def _rects_to_result(rects, rid_map, locked_rids, margin: float, kerf: float):
    """
    rectpack rect_list() 常見格式：(bin, x, y, w, h, rid)
    我們的 kerf trick 讓 w/h 包含 +kerf，所以輸出時要扣回去。
    且座標要 +margin 位移。
    """
    placements: List[Placement] = []
    placed_instance_ids: set[str] = set()
    used_bins: set[int] = set()
    invalid_locked: List[PartInstance] = []

    for b, x, y, w_eff, h_eff, rid in rects:
        p = rid_map[int(rid)]

        out_w = max(0.0, float(w_eff) - kerf)
        out_h = max(0.0, float(h_eff) - kerf)

        # 判斷是否旋轉（用輸出尺寸對照原尺寸）
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
        note = "部分 rotate=False 的零件被求解器旋轉，已標記為 unplaced（若要完全嚴格可改 OR-Tools）。"

    return placements, unplaced, used_bins, note


def _better(a: SolveResult, b: SolveResult) -> SolveResult:
    """
    選擇較好的結果：
    1) unplaced 越少越好
    2) sheets_used 越少越好
    3) utilization 越高越好
    """
    key_a = (len(a.unplaced), a.sheets_used, -a.utilization)
    key_b = (len(b.unplaced), b.sheets_used, -b.utilization)
    return a if key_a < key_b else b


def solve_rectpack(
    parts: List[PartInstance],
    sheet_w: float,
    sheet_h: float,
    sheet_qty: int,
    kerf: float,
    sort_key: str = "area_desc",
    margin: float = 0.0,
    multistart: bool = True,
) -> SolveResult:
    """
    對外介面不改（app.py 可繼續叫 solve_rectpack），但內部支援：
    - margin 留邊
    - multistart：多策略挑最佳
    """
    # 基本防呆
    if margin < 0:
        raise ValueError("margin 不可為負。")
    if kerf < 0:
        raise ValueError("kerf 不可為負。")

    # 先做單件可行性（不含 kerf；kerf 只算零件間距）
    # margin 則是硬限制：可用區域要能容納零件
    usable_w = sheet_w - 2 * margin
    usable_h = sheet_h - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        raise ValueError("margin 太大，導致可用區域寬或高 <= 0。")

    for p in parts:
        fits = (p.width <= usable_w and p.height <= usable_h) or (p.rotate and p.height <= usable_w and p.width <= usable_h)
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

    # multistart 候選集
    parts_sorted_default = _order_parts(parts, sort_key)

    if not multistart:
        return _pack_once(
            parts_sorted=parts_sorted_default,
            sheet_w=sheet_w,
            sheet_h=sheet_h,
            sheet_qty=sheet_qty,
            kerf=kerf,
            margin=margin,
            pack_algo_name="default",
            pack_algo_cls=None,
        )

    algos = _available_pack_algos()

    # 排序策略也一起試（提高成功率與品質）
    sort_keys_to_try = ["area_desc", "maxside_desc"]
    best: Optional[SolveResult] = None

    for sk in sort_keys_to_try:
        parts_sorted = _order_parts(parts, sk)
        for algo_name, algo_cls in algos:
            res = _pack_once(
                parts_sorted=parts_sorted,
                sheet_w=sheet_w,
                sheet_h=sheet_h,
                sheet_qty=sheet_qty,
                kerf=kerf,
                margin=margin,
                pack_algo_name=f"{algo_name}+{sk}",
                pack_algo_cls=algo_cls,
            )
            best = res if best is None else _better(res, best)

    # 在 solver 字串留下 winning 訊息
    best = SolveResult(
        sheet_width=best.sheet_width,
        sheet_height=best.sheet_height,
        kerf=best.kerf,
        sheets_used=best.sheets_used,
        utilization=best.utilization,
        placements=best.placements,
        unplaced=best.unplaced,
        solver=best.solver + " (best-of)",
        note=best.note,
    )
    return best
