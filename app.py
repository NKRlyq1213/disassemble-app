from __future__ import annotations
import json

import pandas as pd
import streamlit as st
from collections import defaultdict

from solver_rectpack import expand_parts, solve_rectpack
from viz import plot_sheet_matplotlib, fig_to_svg_bytes


# ====================
# Page
# ====================
st.set_page_config(page_title="拆料工具", layout="wide")
st.title("拆料工具 By Yu-Sheng, Chiu")


# ====================
# Helpers
# ====================
def ensure_df(x) -> pd.DataFrame:
    if isinstance(x, pd.DataFrame):
        return x
    if x is None:
        return pd.DataFrame(columns=["id", "w", "h", "qty", "rotate"])
    try:
        return pd.DataFrame(x)
    except Exception:
        return pd.DataFrame(columns=["id", "w", "h", "qty", "rotate"])


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = ensure_df(df).copy()
    for col in ["id", "w", "h", "qty", "rotate"]:
        if col not in df.columns:
            df[col] = None
    df = df[["id", "w", "h", "qty", "rotate"]]

    df["id"] = df["id"].astype(str)
    df["w"] = pd.to_numeric(df["w"], errors="coerce").astype(float)
    df["h"] = pd.to_numeric(df["h"], errors="coerce").astype(float)
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(1).astype(int)
    df["rotate"] = df["rotate"].fillna(False).astype(bool)
    return df


def validate_parts_df(df: pd.DataFrame) -> list[str]:
    errs = []
    required = ["id", "w", "h", "qty", "rotate"]
    for c in required:
        if c not in df.columns:
            errs.append(f"缺少欄位：{c}")

    if df.empty:
        errs.append("零件清單不可為空。")
        return errs

    ids = [str(x).strip() for x in df["id"].tolist()]
    if len(ids) != len(set(ids)):
        errs.append("零件 id 不可重複（每一列 id 必須唯一）。")

    for i, row in df.iterrows():
        pid = str(row["id"]).strip()
        if pid == "" or pid.lower() == "nan":
            errs.append(f"第 {i+1} 列：id 不可空白。")

        try:
            w = float(row["w"])
            h = float(row["h"])
        except Exception:
            errs.append(f"第 {i+1} 列：w/h 必須是數字。")
            continue

        if w <= 0 or h <= 0:
            errs.append(f"第 {i+1} 列：w/h 必須 > 0。")

        try:
            qty = int(row["qty"])
        except Exception:
            errs.append(f"第 {i+1} 列：qty 必須是整數。")
            continue

        if qty <= 0:
            errs.append(f"第 {i+1} 列：qty 必須 >= 1。")

    return errs


def scale_parts_df(df_mm: pd.DataFrame, mm_per_unit: float) -> pd.DataFrame:
    out = df_mm.copy()
    out["w"] = out["w"] / mm_per_unit
    out["h"] = out["h"] / mm_per_unit
    return out


def unscale_parts_df(df_ui: pd.DataFrame, mm_per_unit: float) -> pd.DataFrame:
    out = normalize_df(df_ui)
    out["w"] = out["w"] * mm_per_unit
    out["h"] = out["h"] * mm_per_unit
    return out


def scale_placements_df(df_mm: pd.DataFrame, mm_per_unit: float) -> pd.DataFrame:
    out = df_mm.copy()
    for c in ["x", "y", "width", "height"]:
        if c in out.columns:
            out[c] = out[c] / mm_per_unit
    return out


def scale_unplaced_df(df_mm: pd.DataFrame, mm_per_unit: float) -> pd.DataFrame:
    out = df_mm.copy()
    for c in ["w", "h", "width", "height"]:
        if c in out.columns:
            out[c] = out[c] / mm_per_unit
    return out


def scale_placements_list(placements, mm_per_unit: float):
    if mm_per_unit == 1.0:
        return placements
    scaled = []
    for p in placements:
        d = p.__dict__.copy()
        d["x"] = d["x"] / mm_per_unit
        d["y"] = d["y"] / mm_per_unit
        d["width"] = d["width"] / mm_per_unit
        d["height"] = d["height"] / mm_per_unit
        scaled.append(type(p)(**d))
    return scaled
def _q(x: float, step: float) -> float:
    """Quantize to reduce float noise."""
    step = float(step)
    if step <= 0:
        return float(x)
    return round(float(x) / step) * step


def layout_signature(sheet_placements, *, quant_mm: float = 0.001, ignore_part_id: bool = False):
    """
    將一張板材上的 placements 轉成「可比較的指紋」。
    quant_mm: mm 量化步長（建議 0.001~0.1 視你的輸入精度）
    ignore_part_id: True 表示忽略零件 id，只看幾何
    """
    items = []
    for p in sheet_placements:
        x = _q(getattr(p, "x"), quant_mm)
        y = _q(getattr(p, "y"), quant_mm)
        w = _q(getattr(p, "width"), quant_mm)
        h = _q(getattr(p, "height"), quant_mm)
        r = bool(getattr(p, "rotated", False))

        if ignore_part_id:
            pid = None
        else:
            pid = getattr(p, "part_id", None) or getattr(p, "id", None) or getattr(p, "instance_id", None)

        items.append((pid, x, y, w, h, r))

    items.sort()
    return tuple(items)


def group_sheets_by_layout(all_placements, sheets_used: int, *, quant_mm: float = 0.001, ignore_part_id: bool = False):
    """
    回傳 groups: list[dict]，每個 dict 含：
      - sheet_indices: 同排列的板材 index list
      - placements: 代表用 placements（取第一張）
      - rep_sheet: 代表 sheet index
    """
    by_sheet = [[] for _ in range(sheets_used)]
    for p in all_placements:
        si = int(getattr(p, "sheet_index"))
        if 0 <= si < sheets_used:
            by_sheet[si].append(p)

    bucket = defaultdict(list)
    for si in range(sheets_used):
        sig = layout_signature(by_sheet[si], quant_mm=quant_mm, ignore_part_id=ignore_part_id)
        bucket[sig].append(si)

    groups = []
    for sig, sis in bucket.items():
        sis_sorted = sorted(sis)
        rep_si = sis_sorted[0]
        groups.append(
            {
                "key": sig,
                "sheet_indices": sis_sorted,
                "placements": by_sheet[rep_si],
                "rep_sheet": rep_si,
            }
        )

    groups.sort(key=lambda g: (-len(g["sheet_indices"]), g["rep_sheet"]))
    return groups


# ====================
# Unit state (internal mm)
# ====================
st.session_state.setdefault("sheet_w_mm", 3000.0)
st.session_state.setdefault("sheet_h_mm", 1200.0)
st.session_state.setdefault("kerf_mm", 3.0)
st.session_state.setdefault("margin_mm", 0.0)

with st.sidebar:
    st.header("單位")
    unit = st.selectbox("輸入/顯示單位", ["mm", "cm"], index=0, key="unit")

mm_per_unit = 1.0 if unit == "mm" else 10.0
unit_label = unit

st.session_state.setdefault("sheet_w_ui", st.session_state["sheet_w_mm"] / mm_per_unit)
st.session_state.setdefault("sheet_h_ui", st.session_state["sheet_h_mm"] / mm_per_unit)
st.session_state.setdefault("kerf_ui", st.session_state["kerf_mm"] / mm_per_unit)
st.session_state.setdefault("margin_ui", st.session_state["margin_mm"] / mm_per_unit)

prev_unit = st.session_state.get("_prev_unit", unit)
prev_mm_per_unit = 1.0 if prev_unit == "mm" else 10.0

if prev_unit != unit:
    st.session_state["sheet_w_mm"] = float(st.session_state["sheet_w_ui"]) * prev_mm_per_unit
    st.session_state["sheet_h_mm"] = float(st.session_state["sheet_h_ui"]) * prev_mm_per_unit
    st.session_state["kerf_mm"] = float(st.session_state["kerf_ui"]) * prev_mm_per_unit
    st.session_state["margin_mm"] = float(st.session_state["margin_ui"]) * prev_mm_per_unit

    editor_val = st.session_state.get("parts_editor")
    if editor_val is not None:
        df_ui = ensure_df(editor_val)
        st.session_state["parts_df_draft_mm"] = unscale_parts_df(df_ui, prev_mm_per_unit)

    st.session_state["sheet_w_ui"] = st.session_state["sheet_w_mm"] / mm_per_unit
    st.session_state["sheet_h_ui"] = st.session_state["sheet_h_mm"] / mm_per_unit
    st.session_state["kerf_ui"] = st.session_state["kerf_mm"] / mm_per_unit
    st.session_state["margin_ui"] = st.session_state["margin_mm"] / mm_per_unit
    st.session_state.pop("parts_editor", None)

st.session_state["_prev_unit"] = unit


# ====================
# Sidebar inputs (UI unit)
# ====================
with st.sidebar:
    st.header("板材設定")
    sheet_w_ui = st.number_input("板材寬 W", min_value=0.001, step=1.0, key="sheet_w_ui")
    sheet_h_ui = st.number_input("板材高 H", min_value=0.001, step=1.0, key="sheet_h_ui")
    sheet_qty = st.number_input("板材片數（0=不限）", min_value=0, value=0, step=1)

    st.header("切割設定")
    kerf_ui = st.number_input("kerf（零件間間距）", min_value=0.0, step=0.1, key="kerf_ui")
    margin_ui = st.number_input("margin（板材外框留邊）", min_value=0.0, step=0.1, key="margin_ui")

    st.header("求解器設定")
    sort_key = st.selectbox("排序方式（best-of 會試其他排序）", ["area_desc", "maxside_desc"], index=0)

    # --- B1：best-of 控制項 ---
    multistart = st.checkbox("best-of（B1）", value=True, key="multistart")

    best_of_trials = st.slider(
        "best-of 試驗次數",
        min_value=1,
        max_value=300,
        value=60,
        step=1,
        disabled=not multistart,
        key="best_of_trials",
    )

    time_limit_s = st.number_input(
        "best-of 時間上限（秒）",
        min_value=0.1,
        value=5.0,
        step=0.5,
        disabled=not multistart,
        key="time_limit_s",
    )

    seed = st.number_input(
        "隨機種子 seed",
        min_value=0,
        value=0,
        step=1,
        disabled=not multistart,
        key="seed",
    )

    try_pack_algos = st.checkbox(
        "嘗試多種 pack 算法（若 rectpack 支援）",
        value=True,
        disabled=not multistart,
        key="try_pack_algos",
    )

    # --- B2：repair 控制項 ---
    st.divider()
    repair = st.checkbox("修補填洞（B2 repair）", value=True, key="repair")
    repair_time_limit_s = st.number_input(
        "B2 修補時間上限（秒）",
        min_value=0.1,
        value=1.5,
        step=0.5,
        disabled=not repair,
        key="repair_time_limit_s",
    )
    repair_fill_unplaced = st.checkbox(
        "B2：先塞入未排入（fill unplaced）",
        value=True,
        disabled=not repair,
        key="repair_fill_unplaced",
    )
    repair_reduce_last_sheet = st.checkbox(
        "B2：嘗試搬空最後一張板（reduce last sheet）",
        value=True,
        disabled=not repair,
        key="repair_reduce_last_sheet",
    )
    # --- B2.3：eliminate any sheet ---
    st.divider()
    st.subheader("B2.3：刪除板材（Bin Elimination）")

    eliminate_any_sheet = st.checkbox(
        "B2.3：嘗試刪除任意板材（含第 1 片）",
        value=True,
        disabled=not repair,
        key="eliminate_any_sheet",
        help="會先 compact，再嘗試將某片板材的零件搬到其他板材，若搬得完就刪掉那片（片數-1）。",
    )

    eliminate_policy = st.selectbox(
        "刪除策略（policy）",
        [
            "first_then_loose",   # 先第 0 片，再由鬆到緊
            "loose_only",         # 只由鬆到緊（通常最有效率）
            "first_only",         # 只嘗試第 0 片
            "last_only",          # 只嘗試最後一片（等價於你原本 reduce last sheet 的概念）
        ],
        index=1,
        disabled=(not repair) or (not eliminate_any_sheet),
        key="eliminate_policy",
        help="first=第 1 片（index 0）。loose=面積較小/較鬆的板材，較容易被吸收而刪除。",
    )

    eliminate_max_rounds = st.number_input(
        "最多刪除迭代輪數（max_rounds）",
        min_value=1,
        value=50,
        step=1,
        disabled=(not repair) or (not eliminate_any_sheet),
        key="eliminate_max_rounds",
        help="每輪會先 compact，再依策略嘗試刪除某片；成功刪掉一片就會重新開始下一輪。",
    )

    st.header("繪圖設定")
    invert_y = st.checkbox("y 軸反轉", value=True)
    show_labels = st.checkbox("顯示零件標籤", value=True)

# UI -> internal(mm)
st.session_state["sheet_w_mm"] = float(sheet_w_ui) * mm_per_unit
st.session_state["sheet_h_mm"] = float(sheet_h_ui) * mm_per_unit
st.session_state["kerf_mm"] = float(kerf_ui) * mm_per_unit
st.session_state["margin_mm"] = float(margin_ui) * mm_per_unit

sheet_w_mm = st.session_state["sheet_w_mm"]
sheet_h_mm = st.session_state["sheet_h_mm"]
kerf_mm = st.session_state["kerf_mm"]
margin_mm = st.session_state["margin_mm"]


# ====================
# State: Saved vs Draft (internal mm)
# ====================

default_df_mm = pd.DataFrame(
    [
        {"id": "P1", "w": 2250.0, "h": 600.0, "qty": 12, "rotate":  True},
        {"id": "P2", "w": 1181.0, "h": 682.0, "qty": 12, "rotate":  True},
        {"id": "P3", "w": 1970.0, "h": 585.0, "qty": 12, "rotate":  True},
        {"id": "P4", "w": 2340.0, "h": 806.0, "qty": 12, "rotate":  True},
        {"id": "P5", "w": 910.0, "h": 806.0, "qty": 12, "rotate":  True},
    ]
)


st.session_state.setdefault("parts_df_saved_mm", normalize_df(default_df_mm))
st.session_state.setdefault("parts_df_draft_mm", st.session_state["parts_df_saved_mm"].copy())
st.session_state["parts_df_saved_mm"] = normalize_df(st.session_state["parts_df_saved_mm"])
st.session_state["parts_df_draft_mm"] = normalize_df(st.session_state["parts_df_draft_mm"])


# ====================
# Utility buttons
# ====================
c0, c1, c2 = st.columns([1, 1, 6])
with c0:
    if st.button("清除結果"):
        st.session_state.pop("result", None)
        st.rerun()
with c1:
    if st.button("重置表格"):
        st.session_state["parts_df_saved_mm"] = normalize_df(default_df_mm)
        st.session_state["parts_df_draft_mm"] = st.session_state["parts_df_saved_mm"].copy()
        st.session_state.pop("result", None)
        st.rerun()


# ====================
# Form: edit -> submit (commit + compute)
# ====================
st.subheader(f"零件清單（表格輸入）— 單位：{unit_label}")

parts_df_draft_ui = scale_parts_df(st.session_state["parts_df_draft_mm"], mm_per_unit)

with st.form("parts_form", clear_on_submit=False):
    parts_df_edit_ui = st.data_editor(
        parts_df_draft_ui,
        num_rows="dynamic",
        width="stretch",
        key="parts_editor",
        column_config={
            "w": st.column_config.NumberColumn("w", step=0.1, format="%.3f"),
            "h": st.column_config.NumberColumn("h", step=0.1, format="%.3f"),
            "qty": st.column_config.NumberColumn("qty", step=1, format="%d"),
            "rotate": st.column_config.CheckboxColumn("rotate"),
        },
    )
    submitted = st.form_submit_button("計算", type="primary")

if submitted:
    parts_df_commit_mm = unscale_parts_df(parts_df_edit_ui, mm_per_unit)
    st.session_state["parts_df_draft_mm"] = parts_df_commit_mm.copy()

    usable_w_mm = sheet_w_mm - 2 * margin_mm
    usable_h_mm = sheet_h_mm - 2 * margin_mm
    if usable_w_mm <= 0 or usable_h_mm <= 0:
        st.error("margin 太大，導致可用區域寬或高 <= 0。請降低 margin。")
    else:
        errs = validate_parts_df(parts_df_commit_mm)
        if errs:
            st.error("輸入有誤：\n- " + "\n- ".join(errs))
        else:
            st.session_state["parts_df_saved_mm"] = parts_df_commit_mm.copy()
            st.session_state["last_margin_mm"] = float(margin_mm)

            parts_raw = st.session_state["parts_df_saved_mm"].to_dict("records")
            instances = expand_parts(parts_raw)

            too_big = []
            for p in instances:
                fits = (p.width <= usable_w_mm and p.height <= usable_h_mm) or (
                    p.rotate and p.height <= usable_w_mm and p.width <= usable_h_mm
                )
                if not fits:
                    too_big.append(p.instance_id)

            if too_big:
                st.error("以下零件在扣除 margin 後的可用區域中無法排入（無論是否旋轉）：\n- " + "\n- ".join(too_big))
            else:
                try:
                    result = solve_rectpack(
                        parts=instances,
                        sheet_w=sheet_w_mm,
                        sheet_h=sheet_h_mm,
                        sheet_qty=int(sheet_qty),
                        kerf=float(kerf_mm),
                        sort_key=sort_key,
                        margin=float(margin_mm),
                        multistart=bool(multistart),
                        best_of_trials=int(best_of_trials),
                        time_limit_s=float(time_limit_s),
                        seed=int(seed),
                        try_pack_algos=bool(try_pack_algos),
                        # --- B2 ---
                        repair=bool(repair),
                        repair_time_limit_s=float(repair_time_limit_s),
                        repair_fill_unplaced=bool(repair_fill_unplaced),
                        repair_reduce_last_sheet=bool(repair_reduce_last_sheet),
                        eliminate_any_sheet=bool(eliminate_any_sheet),
                        eliminate_policy=str(eliminate_policy),
                        eliminate_max_rounds=int(eliminate_max_rounds)
                    )
                    st.session_state["result"] = result
                except Exception as e:
                    st.error(str(e))


# ====================
# Show result
# ====================
result = st.session_state.get("result")
if not result:
    st.info("尚未計算。請輸入零件與板材設定後按「計算」。")
else:
    last_margin_mm = float(st.session_state.get("last_margin_mm", 0.0))
    usable_w_mm = result.sheet_width - 2 * last_margin_mm
    usable_h_mm = result.sheet_height - 2 * last_margin_mm

    placements_df_mm = pd.DataFrame([p.__dict__ for p in result.placements])
    placements_df_ui = scale_placements_df(placements_df_mm, mm_per_unit)

    total_part_area = float((placements_df_mm["width"] * placements_df_mm["height"]).sum()) if not placements_df_mm.empty else 0.0
    net_area = result.sheets_used * usable_w_mm * usable_h_mm if result.sheets_used > 0 else 0.0
    net_util = (total_part_area / net_area) if net_area > 0 else 0.0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("使用板材片數", f"{result.sheets_used}")
    m2.metric("利用率（含留邊）", f"{result.utilization:.2%}")
    m3.metric("有效區利用率（扣 margin）", f"{net_util:.2%}")
    m4.metric("未排入數量", f"{len(result.unplaced)}")
    m5.metric("Solver", result.solver)

    if result.note:
        st.warning(result.note)

    st.subheader(f"Placements（座標輸出）— 單位：{unit_label}")
    st.dataframe(placements_df_ui, width="stretch")

    dc1, dc2, dc3 = st.columns(3)

    with dc1:
        st.download_button(
            f"下載 placements.csv（{unit_label}）",
            data=placements_df_ui.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"placements_{unit_label}.csv",
            mime="text/csv",
        )
        if unit != "mm":
            st.download_button(
                "下載 placements_mm.csv（mm）",
                data=placements_df_mm.to_csv(index=False).encode("utf-8-sig"),
                file_name="placements_mm.csv",
                mime="text/csv",
            )

    unplaced_df_mm = pd.DataFrame([u.__dict__ for u in result.unplaced]) if result.unplaced else pd.DataFrame()
    unplaced_df_ui = scale_unplaced_df(unplaced_df_mm, mm_per_unit) if not unplaced_df_mm.empty else unplaced_df_mm

    with dc2:
        payload = {
            "unit": unit_label,
            "mm_per_unit": mm_per_unit,
            "sheet_w": result.sheet_width / mm_per_unit,
            "sheet_h": result.sheet_height / mm_per_unit,
            "kerf": result.kerf / mm_per_unit,
            "margin": last_margin_mm / mm_per_unit,
            "sheets_used": result.sheets_used,
            "utilization_gross": result.utilization,
            "utilization_net": net_util,
            "solver": result.solver,
            "placements": placements_df_ui.to_dict("records"),
            "unplaced": (unplaced_df_ui.to_dict("records") if not unplaced_df_ui.empty else []),
            "note": result.note,
        }
        st.download_button(
            "下載 result.json",
            data=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name="result.json",
            mime="application/json",
        )

    # plot（用 UI 單位畫）
    if result.sheets_used > 0:
        st.subheader(f"板材排版圖（單位：{unit_label}）")

        view_mode = st.radio(
            "顯示模式",
            ["逐張板材", "合併相同排列"],
            horizontal=True,
            index=0,
        )

        ignore_part_id = st.checkbox(
            "判斷相同排列時忽略零件 id（只看幾何）",
            value=False,
            help="如果你有很多同尺寸零件，互換位置仍想視為同一種排列就勾選。",
        )

        # 用目前單位輸入精度，內部轉回 mm
        default_quant_ui = 0.001 if unit_label == "mm" else 0.001  # 0.001 cm = 0.01 mm
        quant_ui = st.number_input(
            f"相同判定精度（{unit_label}）",
            min_value=0.0001,
            value=float(default_quant_ui),
            step=0.0001,
            help="用來消除浮點數誤差；數值越大越容易被判定為相同。",
        )
        quant_mm = float(quant_ui) * mm_per_unit

        if view_mode == "逐張板材":
            sheet_idx = st.selectbox("選擇板材", list(range(result.sheets_used)), index=0)
            sheet_pls_mm = [p for p in result.placements if p.sheet_index == sheet_idx]
            sheet_pls_ui = scale_placements_list(sheet_pls_mm, mm_per_unit)

            fig = plot_sheet_matplotlib(
                sheet_w=result.sheet_width / mm_per_unit,
                sheet_h=result.sheet_height / mm_per_unit,
                placements=sheet_pls_ui,
                title=(
                    f"Sheet {sheet_idx} | "
                    f"W={result.sheet_width/mm_per_unit:.3f}, H={result.sheet_height/mm_per_unit:.3f} ({unit_label}) | "
                    f"margin={last_margin_mm/mm_per_unit:.3f} | kerf={result.kerf/mm_per_unit:.3f}"
                ),
                invert_y=invert_y,
                show_labels=show_labels,
                margin=last_margin_mm / mm_per_unit,
                show_grid=True,
            )

            svg_bytes = fig_to_svg_bytes(fig)
            st.pyplot(fig, clear_figure=True)

            import matplotlib.pyplot as plt
            plt.close(fig)

            with dc3:
                st.download_button(
                    "下載圖（SVG）",
                    data=svg_bytes,
                    file_name=f"sheet_{sheet_idx}_{unit_label}.svg",
                    mime="image/svg+xml",
                )

        else:
            groups = group_sheets_by_layout(
                result.placements,
                result.sheets_used,
                quant_mm=float(quant_mm),
                ignore_part_id=bool(ignore_part_id),
            )

            if not groups:
                st.info("目前沒有可顯示的排列。")
            else:
                def _fmt_group(i: int) -> str:
                    g = groups[i]
                    return f"Pattern {i+1} | x{len(g['sheet_indices'])} | sheets={g['sheet_indices']}"

                gi = st.selectbox(
                    "選擇合併排列",
                    list(range(len(groups))),
                    index=0,
                    format_func=_fmt_group,
                )
                g = groups[int(gi)]

                sheet_pls_mm = g["placements"]
                sheet_pls_ui = scale_placements_list(sheet_pls_mm, mm_per_unit)

                fig = plot_sheet_matplotlib(
                    sheet_w=result.sheet_width / mm_per_unit,
                    sheet_h=result.sheet_height / mm_per_unit,
                    placements=sheet_pls_ui,
                    title=(
                        f"Pattern {int(gi)+1} | x{len(g['sheet_indices'])} | sheets={g['sheet_indices']} ({unit_label}) | "
                        f"margin={last_margin_mm/mm_per_unit:.3f} | kerf={result.kerf/mm_per_unit:.3f}"
                    ),
                    invert_y=invert_y,
                    show_labels=show_labels,
                    margin=last_margin_mm / mm_per_unit,
                    show_grid=True,
                )

                svg_bytes = fig_to_svg_bytes(fig)
                st.pyplot(fig, clear_figure=True)

                import matplotlib.pyplot as plt
                plt.close(fig)

                sheets = g["sheet_indices"]
                if len(sheets) <= 8:
                    sheets_tag = "-".join(map(str, sheets))
                else:
                    sheets_tag = f"{sheets[0]}-...-{sheets[-1]}"

                with dc3:
                    st.download_button(
                        "下載合併圖（SVG）",
                        data=svg_bytes,
                        file_name=f"pattern_{int(gi)+1}_x{len(sheets)}_sheets_{sheets_tag}_{unit_label}.svg",
                        mime="image/svg+xml",
                    )
