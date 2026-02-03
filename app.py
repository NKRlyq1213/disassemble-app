from __future__ import annotations
import json

import pandas as pd
import streamlit as st

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


# ====================
# Sidebar inputs
# ====================
with st.sidebar:
    st.header("板材設定")
    sheet_w = st.number_input("板材寬 W", min_value=1.0, value=3000.0, step=1.0)
    sheet_h = st.number_input("板材高 H", min_value=1.0, value=1200.0, step=1.0)
    sheet_qty = st.number_input("板材片數（0=不限）", min_value=0, value=0, step=1)

    st.header("切割設定")
    kerf = st.number_input("kerf（零件間間距）", min_value=0.0, value=3.0, step=0.5)
    margin = st.number_input("margin（板材外框留邊）", min_value=0.0, value=0.0, step=1.0)

    st.header("求解器設定")
    sort_key = st.selectbox("排序方式（best-of 會試其他排序）", ["area_desc", "maxside_desc"], index=0)
    multistart = st.checkbox("best-of", value=True)

    st.header("繪圖設定")
    invert_y = st.checkbox("y 軸反轉", value=True)
    show_labels = st.checkbox("顯示零件標籤", value=True)


# ====================
# State: Saved vs Draft
# ====================
default_df = pd.DataFrame(
    [
        {"id": "P1", "w": 3000, "h": 650, "qty": 1, "rotate": False},
        {"id": "P2", "w": 800, "h": 400, "qty": 2, "rotate": True},
    ]
)

# Saved：按「計算」才更新（你要的「記憶表格」）
if "parts_df_saved" not in st.session_state:
    st.session_state["parts_df_saved"] = normalize_df(default_df)

# Draft：畫面上正在編輯，不應被 rerun 覆蓋
if "parts_df_draft" not in st.session_state:
    st.session_state["parts_df_draft"] = st.session_state["parts_df_saved"].copy()

# 防止狀態被寫壞成 list/dict
st.session_state["parts_df_saved"] = normalize_df(st.session_state["parts_df_saved"])
st.session_state["parts_df_draft"] = normalize_df(st.session_state["parts_df_draft"])


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
        st.session_state["parts_df_saved"] = normalize_df(default_df)
        st.session_state["parts_df_draft"] = st.session_state["parts_df_saved"].copy()
        st.session_state.pop("result", None)
        st.rerun()


# ====================
# Form: edit (no rollback) -> submit (commit + compute)
# ====================
st.subheader("零件清單（表格輸入）")

with st.form("parts_form", clear_on_submit=False):
    parts_df_edit = st.data_editor(
        st.session_state["parts_df_draft"],
        num_rows="dynamic",
        use_container_width=True,
        key="parts_editor",
    )
    submitted = st.form_submit_button("計算", type="primary")

if submitted:
    # 1) 先把提交內容寫回 Draft（就算驗證失敗也不會丟失輸入）
    parts_df_commit = normalize_df(parts_df_edit)
    st.session_state["parts_df_draft"] = parts_df_commit.copy()

    # 2) margin 防呆：可用區域必須 > 0
    usable_w = sheet_w - 2 * margin
    usable_h = sheet_h - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        st.error("margin 太大，導致可用區域寬或高 <= 0。請降低 margin。")
    else:
        # 3) 驗證資料表
        errs = validate_parts_df(parts_df_commit)
        if errs:
            st.error("輸入有誤：\n- " + "\n- ".join(errs))
        else:
            # 4) 記憶表格（Commit 到 Saved）
            st.session_state["parts_df_saved"] = parts_df_commit.copy()
            st.session_state["last_margin"] = float(margin)

            # 5) 用 Saved 開始計算
            parts_raw = st.session_state["parts_df_saved"].to_dict("records")
            instances = expand_parts(parts_raw)

            # 單件可行性檢查：以「扣掉 margin 的可用區域」判斷；kerf 不算進單件
            too_big = []
            for p in instances:
                fits = (p.width <= usable_w and p.height <= usable_h) or (p.rotate and p.height <= usable_w and p.width <= usable_h)
                if not fits:
                    too_big.append(p.instance_id)

            if too_big:
                st.error("以下零件在扣除 margin 後的可用區域中無法排入（無論是否旋轉）：\n- " + "\n- ".join(too_big))
            else:
                try:
                    result = solve_rectpack(
                        parts=instances,
                        sheet_w=sheet_w,
                        sheet_h=sheet_h,
                        sheet_qty=int(sheet_qty),
                        kerf=float(kerf),
                        sort_key=sort_key,
                        margin=float(margin),
                        multistart=bool(multistart),
                    )
                    st.session_state["result"] = result
                except TypeError as e:
                    st.error(
                        "solve_rectpack 目前不支援 margin/multistart 參數。\n"
                        "請更新 solver_rectpack.py 使 solve_rectpack(..., margin=..., multistart=...) 可用。\n\n"
                        f"原始錯誤：{e}"
                    )
                except Exception as e:
                    st.error(str(e))


# ====================
# Show result
# ====================
result = st.session_state.get("result")
if not result:
    st.info("尚未計算。請輸入零件與板材設定後按「計算」。")
else:
    last_margin = float(st.session_state.get("last_margin", 0.0))
    usable_w = result.sheet_width - 2 * last_margin
    usable_h = result.sheet_height - 2 * last_margin

    placements_df = pd.DataFrame([p.__dict__ for p in result.placements])
    total_part_area = float((placements_df["width"] * placements_df["height"]).sum()) if not placements_df.empty else 0.0

    gross_area = result.sheets_used * result.sheet_width * result.sheet_height if result.sheets_used > 0 else 0.0
    net_area = result.sheets_used * usable_w * usable_h if result.sheets_used > 0 else 0.0
    net_util = (total_part_area / net_area) if net_area > 0 else 0.0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("使用板材片數", f"{result.sheets_used}")
    m2.metric("利用率（含留邊）", f"{result.utilization:.2%}")
    m3.metric("有效區利用率（扣 margin）", f"{net_util:.2%}")
    m4.metric("未排入數量", f"{len(result.unplaced)}")
    m5.metric("Solver", result.solver)

    if result.note:
        st.warning(result.note)

    st.subheader("Placements（座標輸出）")
    st.dataframe(placements_df, use_container_width=True)

    dc1, dc2, dc3 = st.columns(3)
    with dc1:
        st.download_button(
            "下載 placements.csv",
            data=placements_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="placements.csv",
            mime="text/csv",
        )
    with dc2:
        payload = {
            "sheet_w": result.sheet_width,
            "sheet_h": result.sheet_height,
            "kerf": result.kerf,
            "margin": last_margin,
            "sheets_used": result.sheets_used,
            "utilization_gross": result.utilization,
            "utilization_net": net_util,
            "solver": result.solver,
            "placements": [p.__dict__ for p in result.placements],
            "unplaced": [u.__dict__ for u in result.unplaced],
            "note": result.note,
        }
        st.download_button(
            "下載 result.json",
            data=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name="result.json",
            mime="application/json",
        )

    if result.sheets_used > 0:
        st.subheader("板材排版圖")
        sheet_idx = st.selectbox("選擇板材", list(range(result.sheets_used)), index=0)
        sheet_pls = [p for p in result.placements if p.sheet_index == sheet_idx]

        fig = plot_sheet_matplotlib(
            sheet_w=result.sheet_width,
            sheet_h=result.sheet_height,
            placements=sheet_pls,
            title=f"Sheet {sheet_idx} | W={result.sheet_width}, H={result.sheet_height} | margin={last_margin} | kerf={result.kerf}",
            invert_y=invert_y,
            show_labels=show_labels,
            margin=last_margin,
            show_grid=True,
        )
        st.pyplot(fig, clear_figure=True)

        svg_bytes = fig_to_svg_bytes(fig)
        with dc3:
            st.download_button(
                "下載圖（SVG）",
                data=svg_bytes,
                file_name=f"sheet_{sheet_idx}.svg",
                mime="image/svg+xml",
            )

    if result.unplaced:
        st.subheader("Unplaced（未排入）")
        unplaced_df = pd.DataFrame([u.__dict__ for u in result.unplaced])
        st.dataframe(unplaced_df, use_container_width=True)



