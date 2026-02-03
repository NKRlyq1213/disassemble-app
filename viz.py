from __future__ import annotations
from typing import List, Optional, Dict, Tuple
import io
import hashlib

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.collections import PatchCollection

from models import Placement


def _stable_color_from_id(part_id: str) -> Tuple[float, float, float]:
    """
    將 part_id 映射到穩定的 RGB 顏色（0~1）。
    用 hash 避免每次顏色亂跳。
    """
    h = hashlib.md5(part_id.encode("utf-8")).hexdigest()
    # 取前 6 碼當 rgb（00~ff）
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    # 顏色稍微往「淡色」拉，避免太刺眼
    r = 0.25 + 0.65 * r
    g = 0.25 + 0.65 * g
    b = 0.25 + 0.65 * b
    return (r, g, b)


def plot_sheet_matplotlib(
    sheet_w: float,
    sheet_h: float,
    placements: List[Placement],
    title: str = "",
    invert_y: bool = True,
    show_labels: bool = True,
    margin: float = 0.0,               # 新增：可視化留邊
    show_grid: bool = True,            # 新增：可關掉 grid
    max_labels: int = 200,             # 新增：標籤上限（避免超多零件卡頓）
):
    # --- 動態 figsize：依板材比例決定圖大小（看起來比較「正」） ---
    base_w = 12.0
    base_h = base_w * (sheet_h / sheet_w)
    base_h = max(5.5, min(9.5, base_h))  # 限制高度範圍，避免太扁或太高

    fig, ax = plt.subplots(figsize=(base_w, base_h), dpi=120)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(0, sheet_w)
    ax.set_ylim(0, sheet_h)

    # --- 背景/網格（淡）---
    ax.set_facecolor("white")
    if show_grid:
        ax.grid(True, linewidth=0.6, alpha=0.15)
    else:
        ax.grid(False)

    # 減少座標軸干擾（保留刻度但弱化）
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_alpha(0.3)
    ax.spines["bottom"].set_alpha(0.3)
    ax.tick_params(axis="both", labelsize=10)

    # --- 板材外框 ---
    board = patches.Rectangle(
        (0, 0), sheet_w, sheet_h,
        fill=False, linewidth=2.0, edgecolor=(0, 0, 0, 0.9)
    )
    ax.add_patch(board)

    # --- margin 框（虛線）---
    if margin and margin > 0:
        usable = patches.Rectangle(
            (margin, margin),
            sheet_w - 2 * margin,
            sheet_h - 2 * margin,
            fill=False,
            linewidth=1.2,
            linestyle=(0, (6, 4)),
            edgecolor=(0.8, 0.2, 0.2, 0.7),
        )
        ax.add_patch(usable)

    # --- 零件：用 PatchCollection 更快且更好控制 ---
    rect_patches = []
    face_colors = []
    edge_colors = []
    label_candidates = []  # (cx, cy, text, area)

    for pl in placements:
        rect = patches.Rectangle((pl.x, pl.y), pl.width, pl.height)
        rect_patches.append(rect)

        c = _stable_color_from_id(pl.part_id)
        face_colors.append((*c, 0.18))     # 淡填色
        edge_colors.append((*c, 0.95))     # 外框顏色稍深

        # 標籤候選：零件太小就先不畫
        if show_labels:
            area = pl.width * pl.height
            cx = pl.x + pl.width / 2
            cy = pl.y + pl.height / 2
            label_candidates.append((cx, cy, pl.instance_id, area))

    if rect_patches:
        pc = PatchCollection(
            rect_patches,
            facecolors=face_colors,
            edgecolors=edge_colors,
            linewidths=1.1,
        )
        ax.add_collection(pc)

    # --- 標籤：只挑「面積最大的前 N 個」，且太小的矩形不畫 ---
    if show_labels and label_candidates:
        label_candidates.sort(key=lambda t: t[3], reverse=True)
        label_candidates = label_candidates[:max_labels]

        min_area_to_label = (sheet_w * sheet_h) * 0.003  # 0.3% 板材面積以下不畫標籤（可調）
        for cx, cy, text, area in label_candidates:
            if area < min_area_to_label:
                break
            ax.text(
                cx, cy, text,
                ha="center", va="center",
                fontsize=9,
                color=(0, 0, 0, 0.85),
                bbox=dict(boxstyle="round,pad=0.18", facecolor="white", alpha=0.65, linewidth=0),
            )

    # --- 左上角資訊框（取代超大 title）---
    info = title if title else f"W={sheet_w}, H={sheet_h}"
    ax.text(
        0.01, 1.01,
        info,
        transform=ax.transAxes,
        ha="left", va="bottom",
        fontsize=12,
        weight="bold",
        color=(0, 0, 0, 0.85),
    )

    if invert_y:
        ax.invert_yaxis()

    fig.tight_layout()
    return fig


def fig_to_svg_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    return buf.getvalue()
