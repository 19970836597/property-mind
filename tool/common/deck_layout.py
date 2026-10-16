# -*- coding: utf-8 -*-
"""版式测量（Layout Measurement）—— 渲染之前先算尺寸

为什么独立于渲染
----------------
原先这几个函数长在 ``deck_build_tool`` 里，和真正的"画"混在一起。
抽出来之后有两个好处：

1. **可以只测量不渲染** —— 于是"这份 IR 会不会溢出"能在出图之前算出来，
   而不是等渲染完再看（本项目的视觉回归就是这么发现的排版问题）。
2. 渲染函数只负责画，测量函数只负责算，两者的 bug 不会互相掩盖。

踩过的坑（都体现在实现里）
--------------------------
* **卡片按"填满可用高度"算** → 2.1" 高的卡片内容只占 0.6"，大片空洞。
  改成按内容估算，并允许放大到可用空间的一定比例。
* **3 张卡片按 2 列排** → 第二行只有 1 张，右半页空着。
  改为按"空槽数 → 偏离偏好 → 列数"三级排序挑列数。
* **KPI 卡片高度不足** → 数值与说明文字重叠。
  所以卡片有最小内容高度，且行距按实际高度压缩。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "pick_columns",
    "estimate_card_height",
    "measure_kpi_grid",
    "measure_cards",
    "measure_block",
    "BLOCK_GAP_IN",
]

#: 块与块之间的间距（英寸）
BLOCK_GAP_IN = 0.18

_INCH = 914400


def _inches(pal, name: str) -> int:
    """惰性取主题里的英寸尺寸（避免模块级依赖 pptx）。"""
    return int(getattr(pal, name, 0) or 0)


def pick_columns(n: int, preferred: int, maximum: int = 4) -> int:
    """挑列数：优先不留空位，平手时取最接近主题偏好的列数。

    实测问题：3 张卡片按 2 列排 → 第二行只有 1 张，右半页空着。
    判据按优先级排序：
        ① 空槽数最少（别留半行）
        ② 最接近 preferred（尊重主题设定，例：4 张卡用 2×2 而不是挤成 1×4）
        ③ 列数更多（同样合理时更铺满）
    """
    if n <= 1:
        return 1
    best_cols, best_key = preferred, None
    for cols in range(2, min(maximum, n) + 1):
        rows = -(-n // cols)
        waste = rows * cols - n
        key = (waste, abs(cols - preferred), -cols)
        if best_key is None or key < best_key:
            best_cols, best_key = cols, key
    return best_cols


def estimate_card_height(item: Dict[str, Any], cw, pal,
                         pad: float = 0.24) -> float:
    """按内容估算卡片需要多高（英寸），而不是撑满可用空间。

    实测问题：卡片被拉成 2.1" 高、内容只占 0.6" → 大片空洞。
    这里按「标题一行 + 正文按字数折行」估算实际需要的高度。
    """
    title = str(item.get("title", "") or "")
    body = str(item.get("body", "") or "")

    card_title_size = float(getattr(pal, "card_title_size", 12))
    card_body_size = float(getattr(pal, "card_body_size", 10))

    title_h = card_title_size / 72 * 1.5 if title else 0.0

    body_h = 0.0
    if body:
        usable_in = cw / _INCH - 0.4
        per_line = max(int(usable_in / (card_body_size / 72 * 1.05)), 6)
        lines = max(1, -(-len(body) // per_line))
        body_h = lines * card_body_size / 72 * 1.45

    return title_h + (0.1 if (title_h and body_h) else 0.0) + body_h + pad * 2


def measure_kpi_grid(block: Dict[str, Any], pal, card_h_in: float = 1.35) -> int:
    """KPI 网格的自然高度。"""
    items = block.get("items") or []
    cols = max(1, int(block.get("columns") or getattr(pal, "kpi_columns", 3)))
    rows = max(1, -(-len(items) // cols))
    gap = int(0.22 * _INCH)
    return int(card_h_in * _INCH) * rows + gap * (rows - 1)


def measure_cards(block: Dict[str, Any], pal) -> int:
    """卡片网格的自然高度（按最宽的那张卡估算）。"""
    items = block.get("items") or []
    if not items:
        return 0
    gap = int(0.22 * _INCH)
    cols = pick_columns(len(items),
                        int(block.get("columns")
                            or getattr(pal, "cards_columns", 2)))
    rows = max(1, -(-len(items) // cols))
    content_w = int(getattr(pal, "content_w", 0) or 0)
    cw = int((content_w - gap * (cols - 1)) / cols) if content_w else _INCH * 4
    need = max((estimate_card_height(it, cw, pal) for it in items), default=1.0)
    return int(min(need, 2.2) * _INCH) * rows + gap * (rows - 1)


def measure_block(block: Dict[str, Any], pal, available: int) -> int:
    """估算一个块的**自然高度**（内容需要多高），不渲染。

    用于「内容不足时垂直居中」与「渲染前溢出预判」：
    图表是弹性的（吃掉剩余空间），其余块按内容定高。
    字体按行高 1.45 估算。
    """
    btype = block.get("type")
    gap = int(0.22 * _INCH)
    body_size = float(getattr(pal, "body_size", 12))
    content_w = int(getattr(pal, "content_w", 0) or 0)

    def line_height(pt: float) -> int:
        return int(_INCH * (pt / 72 * 1.45))

    if btype == "kpi_grid":
        return measure_kpi_grid(block, pal)

    if btype == "cards":
        return measure_cards(block, pal)

    if btype == "text":
        text = str(block.get("text") or "")
        usable = max(content_w / _INCH, 1.0)
        per_line = max(int(usable / (body_size / 72 * 1.05)), 10)
        lines = max(1, -(-len(text) // per_line))
        return line_height(body_size) * lines + int(0.1 * _INCH)

    if btype == "bullets":
        items = block.get("items") or []
        return line_height(body_size) * len(items) + int(0.1 * _INCH)

    if btype == "table":
        rows = 1 + len(block.get("rows") or [])
        return int(0.34 * _INCH) * rows

    # 其他（含 chart）按可用空间弹性处理
    return int(min(available, 2.0 * _INCH))
