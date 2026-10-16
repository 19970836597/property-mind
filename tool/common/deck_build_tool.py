# -*- coding: utf-8 -*-
"""通用演示文稿渲染引擎（能力名 ``deck_build``）

从 IR 渲染出演示文稿。**完全业务无关**：
它只认识 ``deck_ir`` 定义的 layout / block 与 ``deck_theme`` 定义的样式，
不认识"物业""动漫"或任何行业。

    数据 ──[技能+领域知识]──▶ Deck IR ──[本模块 + theme]──▶ .pptx

样式从哪来
----------
所有字体/颜色/尺寸/位置都来自 **theme**（见 ``deck_theme``），
工具本身**不持有任何魔法数字**：

    DeckBuildTool.run({"deck": IR, "theme": {...}, "output": "x.pptx"})

不传 theme 时用 ``deck_theme.DEFAULT_THEME``，保证零配置也能跑。

设计取舍
--------
* **宽容渲染**：未知 layout/block 不抛异常，画提示页并继续。
* **单页失败隔离**：每页包 try/except，失败页画错误页，产物仍完整。
* **语义色固定映射**：ok/warn/bad 的具体色值由 theme 给，映射关系在此固定。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import (
    XL_CHART_TYPE,
    XL_LABEL_POSITION,
    XL_LEGEND_POSITION,
)
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from ..tools import Tool, ToolParameter
from .deck_ir import BLOCK_TYPES, validate_deck
from .deck_theme import DEFAULT_THEME, build_theme, validate_theme

# ---------------------------------------------------------------------
# 版式测量已抽到 deck_layout（渲染之前先算尺寸，可只测量不渲染）
# 这里保留同名私有别名作为**向后兼容出口**。
# ---------------------------------------------------------------------
from .deck_layout import (
    BLOCK_GAP_IN as _BLOCK_GAP_IN,
    estimate_card_height as _estimate_card_height,
    measure_block as _measure_block,
    pick_columns as _pick_columns,
)

__all__ = ["DeckBuildTool", "render_deck", "ThemePalette"]

EMU_PER_INCH = 914400


def _hex_to_rgb(value: Any, fallback: str = "000000") -> RGBColor:
    """把主题里的 6 位十六进制字符串转成 RGBColor。容错：非法值用 fallback。"""
    text = str(value or fallback).lstrip("#").strip()
    if len(text) != 6:
        text = fallback
    try:
        return RGBColor.from_string(text.upper())
    except Exception:
        return RGBColor.from_string(fallback.upper())


class ThemePalette:
    """把 theme 字典包装成好用的访问器，并缓存 RGBColor。

    渲染函数只依赖这个对象，不再直接读模块级常量 ——
    这样换主题就是换一个对象，不用改任何页面代码。
    """

    def __init__(self, theme: Dict[str, Any]):
        self.theme = theme
        colors = theme.get("colors") or {}
        fonts = theme.get("fonts") or {}
        layout = theme.get("layout") or {}
        canvas = theme.get("canvas") or {}

        # ---- 颜色 ----
        self.title = _hex_to_rgb(colors.get("title"), "142A4C")
        self.accent = _hex_to_rgb(colors.get("accent"), "1E88E5")
        self.muted = _hex_to_rgb(colors.get("muted"), "6B7786")
        self.page_bg = _hex_to_rgb(colors.get("page_bg"), "FFFFFF")
        self.panel = _hex_to_rgb(colors.get("panel"), "F2F5F9")
        self.text = _hex_to_rgb(colors.get("text"), "333D4D")
        self.line = _hex_to_rgb(colors.get("line"), "E3EAF2")
        self.on_dark = _hex_to_rgb(colors.get("on_dark"), "FFFFFF")
        self.on_dark_muted = _hex_to_rgb(colors.get("on_dark_muted"), "8FC4F0")

        self.ok = _hex_to_rgb(colors.get("ok"), "2EA06A")
        self.warn = _hex_to_rgb(colors.get("warn"), "E8A31D")
        self.bad = _hex_to_rgb(colors.get("bad"), "D9453D")
        self.info = _hex_to_rgb(colors.get("info"), colors.get("accent") or "1E88E5")

        self.status_color = {
            "ok": self.ok, "good": self.ok, "pass": self.ok, "done": self.ok,
            "warn": self.warn, "warning": self.warn, "risk": self.warn,
            "bad": self.bad, "fail": self.bad, "error": self.bad,
            "info": self.info, "neutral": self.muted,
        }

        palette = theme.get("palette") or []
        self.palette = [_hex_to_rgb(c, "1E88E5") for c in palette] or [self.accent]
        # 兼容旧调用：PALETTE 名字仍可用
        self.PALETTE = self.palette

        # ---- 字体与字号 ----
        self.font_title = fonts.get("title") or "微软雅黑"
        self.font_body = fonts.get("body") or self.font_title
        self.title_size = float(fonts.get("title_size", 19))
        self.body_size = float(fonts.get("body_size", 12))
        self.kicker_size = float(fonts.get("kicker_size", 11))
        self.card_title_size = float(fonts.get("card_title_size", 12))
        self.card_body_size = float(fonts.get("card_body_size", 10))
        self.footnote_size = float(fonts.get("footnote_size", 8.5))
        self.cover_title_size = float(fonts.get("cover_title_size", 30))
        self.cover_subtitle_size = float(fonts.get("cover_subtitle_size", 15))
        self.cover_project_size = float(fonts.get("cover_project_size", 22))
        self.kpi_value_size = float(fonts.get("kpi_value_size", 21))
        self.kpi_label_size = float(fonts.get("kpi_label_size", 10.5))
        self.chart_size = float(fonts.get("chart_size", 10))
        self.line_spacing = float(fonts.get("line_spacing", 1.25))

        # ---- 画布与版式 ----
        self.sw = Inches(float(canvas.get("width_in", 13.333)))
        self.sh = Inches(float(canvas.get("height_in", 7.5)))
        self.margin = Inches(float(canvas.get("margin_in", 0.55)))
        self.content_w = self.sw - 2 * self.margin

        self.body_top = Inches(float(layout.get("body_top_in", 1.55)))
        self.so_what_top = Inches(float(layout.get("so_what_top_in", 6.24)))
        self.cover_band = Inches(float(layout.get("cover_band_in", 2.5)))
        self.kpi_columns = int(layout.get("kpi_columns", 3))
        self.cards_columns = int(layout.get("cards_columns", 2))
        self.show_page_number = bool(layout.get("page_number", True))
        # 由顶层推导的几何量
        self.header_rule_top = Inches(float(layout.get("body_top_in", 1.55)) - 0.13)
        self.footer_top = self.sh - Inches(0.44)

        # ---- 装饰样式（决定"长什么样"，不只是"什么颜色"）----
        decor = theme.get("decor") or {}
        self.card_shape = str(decor.get("card_shape", "rect")).lower()
        self.card_radius = float(decor.get("card_radius", 0.12))
        self.header_rule = bool(decor.get("header_rule", True))
        self.kicker_bar = bool(decor.get("kicker_bar", False))
        self.so_what_style = str(decor.get("so_what_style", "panel")).lower()
        self.cover_band_on = bool(decor.get("cover_band", True))
        # 图表装饰：柱间距（越小柱越粗）与是否要网格线
        self.chart_gap_width = int(decor.get("chart_gap_width", 100))
        self.chart_gridlines = bool(decor.get("chart_gridlines", True))

        # 深色主题：底色亮度偏低时铺满页面底色，并翻转标题/文字色
        self.page_bg_hex = str(colors.get("page_bg", "FFFFFF")).lstrip("#").upper()
        self.dark_bg = self._is_dark(colors.get("page_bg", "FFFFFF"))
        self.card_enum = (MSO_SHAPE.ROUNDED_RECTANGLE if self.card_shape == "rounded"
                          else MSO_SHAPE.RECTANGLE)

    @staticmethod
    def _is_dark(hex_color: Any) -> bool:
        """按相对亮度判断底色深浅（0.299R+0.587G+0.114B）。"""
        text = str(hex_color or "FFFFFF").lstrip("#")
        if len(text) != 6:
            return False
        try:
            r = int(text[0:2], 16) / 255
            g = int(text[2:4], 16) / 255
            b = int(text[4:6], 16) / 255
        except ValueError:
            return False
        return (0.299 * r + 0.587 * g + 0.114 * b) < 0.5

    def status(self, status: Optional[str]) -> RGBColor:
        return self.status_color.get(str(status or "").lower(), self.muted)

    def style_of(self, role: str) -> Tuple[RGBColor, float]:
        """按语义角色取 (颜色, 字号)。"""
        table = {
            "title": (self.title, self.title_size),
            "kicker": (self.accent, self.kicker_size),
            "body": (self.text, self.body_size),
            "muted": (self.muted, self.footnote_size),
            "card_title": (self.title, self.card_title_size),
            "card_body": (self.muted, self.card_body_size),
            "kpi_value": (self.accent, self.kpi_value_size),
            "kpi_label": (self.muted, self.kpi_label_size),
        }
        return table.get(role, (self.text, self.body_size))


# ================================================================
# 基础绘图原语（全部依赖 palette，无硬编码样式）
# ================================================================

def _add_slide(prs, pal: Optional[ThemePalette] = None):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    # 深色主题必须自己铺底色 —— 否则深色卡片浮在白页上会很怪
    if pal is not None and pal.dark_bg:
        _rect(slide, 0, 0, pal.sw, pal.sh, fill=pal.page_bg)
    return slide


def _txbox(slide, l, t, w, h, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(l, t, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = 0
    tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    tf.paragraphs[0].alignment = align
    return box, tf


def _write(tf, lines: Sequence[Tuple], pal: ThemePalette,
           font: Optional[str] = None, first: bool = True) -> None:
    """lines: [(text, size, bold, color[, align]), ...]"""
    font_name = font or pal.font_body
    for i, item in enumerate(lines):
        text, size, bold, color = item[0], item[1], item[2], item[3]
        align = item[4] if len(item) > 4 else None
        p = tf.paragraphs[0] if (i == 0 and first) else tf.add_paragraph()
        if align is not None:
            p.alignment = align
        p.line_spacing = pal.line_spacing
        run = p.add_run()
        run.text = str(text)
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
        run.font.name = font_name


def _strip_style_ref(shape) -> None:
    """删掉形状上的 ``<p:style>``，消除主题带来的阴影等副作用。

    为什么要这么做
    --------------
    ``add_shape`` 会自动挂一段 ``<p:style>``，其中
    ``<a:effectRef idx="2">`` 指向主题第 2 号效果样式 —— 默认模板里**带投影**。

    ``shape.shadow.inherit = False`` 会写一个空 ``<a:effectLst/>``；
    PowerPoint 认这个空元素（阴影消失），但 **LibreOffice 仍然按 effectRef
    画阴影**（实测：扁平化页面上的色带与卡片边缘都有灰色投影）。

    填充和线条我们都显式设过了，``<p:style>`` 纯属多余，直接删掉最稳 ——
    两个渲染器表现一致。
    """
    element = shape._element
    for style in element.findall(
        "{http://schemas.openxmlformats.org/presentationml/2006/main}style"
    ):
        element.remove(style)


def _rect(slide, l, t, w, h, fill=None, line=None,
          shape=MSO_SHAPE.RECTANGLE, radius: Optional[float] = None) -> Any:
    shp = slide.shapes.add_shape(shape, l, t, w, h)
    _strip_style_ref(shp)
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(1)
    # 圆角矩形可调圆角比例（0~0.5）
    if radius is not None and shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        try:
            shp.adjustments[0] = max(0.0, min(0.5, float(radius)))
        except Exception:
            pass
    shp.shadow.inherit = False
    return shp


def _card(slide, l, t, w, h, pal: ThemePalette, fill=None, line=None) -> Any:
    """按主题装饰画一个卡片（直角/圆角）。"""
    return _rect(slide, l, t, w, h,
                 fill=fill if fill is not None else pal.page_bg,
                 line=line if line is not None else pal.line,
                 shape=pal.card_enum, radius=pal.card_radius)


def _same_label(a: str, b: str) -> bool:
    """两段文字是否"同义"——用于去掉页眉里 kicker 与标题的重复。

    判据刻意保守：完全相同，或其中一个是另一个的前缀
    （「收缴率」 vs 「收缴率：应收 / 实收 / 未缴」属于重复，去掉 kicker；
    「人均效能」 vs 「效能：平均单价」不是前缀关系，两者都保留）。
    """
    x = str(a or "").strip()
    y = str(b or "").strip()
    if not x or not y:
        return False
    if x == y:
        return True
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    return long.startswith(short) and len(short) >= 2


def _header(slide, idx: Optional[int], title: str, kicker: str,
            pal: ThemePalette) -> None:
    """页眉：序号 + 结论型标题 + 阶段标签。

    kicker 与 title 同义时**只留 title** —— 实测踩过：收入页的 kicker
    是「收入」、标题也是「收入」，同一个词在页眉里印两遍，既占高度又显业余。
    """
    if kicker and _same_label(kicker, title):
        kicker = ""
    if kicker:
        text_left = pal.margin
        if pal.kicker_bar:
            _rect(slide, pal.margin, Inches(0.46), Pt(3.5), Inches(0.22),
                  fill=pal.accent)
            text_left = pal.margin + Inches(0.13)
        _, tf = _txbox(slide, text_left, Inches(0.42), Inches(9), Inches(0.3))
        _write(tf, [(kicker, pal.kicker_size, True, pal.accent)], pal,
               font=pal.font_title)
    _, tf = _txbox(slide, pal.margin, Inches(0.72), pal.content_w - Inches(1.1),
                   Inches(0.6))
    _write(tf, [(title, pal.title_size, True, pal.title)], pal,
           font=pal.font_title)
    if idx is not None and pal.show_page_number:
        _, tf = _txbox(slide, pal.sw - pal.margin - Inches(0.7), Inches(0.46),
                       Inches(0.7), Inches(0.4), align=PP_ALIGN.RIGHT)
        # 页码是辅助信息：用 muted 小字，不抢标题的视觉权重
        # （实测踩过：title_size+1 的淡灰大字号，看着像个没填完的占位符）
        _write(tf, [(f"{idx:02d}", pal.footnote_size + 1.5, False, pal.muted)],
               pal, font=pal.font_title)
    if pal.header_rule:
        _rect(slide, pal.margin, pal.header_rule_top, pal.content_w, Pt(1.2),
              fill=pal.line)


def _so_what(slide, text: str, pal: ThemePalette) -> None:
    """底部结论栏。三种形态由 decor.so_what_style 决定。"""
    if not text:
        return
    style = pal.so_what_style
    if style == "panel":
        _rect(slide, pal.margin, pal.so_what_top, pal.content_w, Inches(0.72),
              fill=pal.panel)
        _rect(slide, pal.margin, pal.so_what_top, Inches(0.07), Inches(0.72),
              fill=pal.accent)
        text_left = pal.margin + Inches(0.25)
    elif style == "rule":
        # 只留左侧色条 + 一条细横线：更克制，适合学术/商务极简
        _rect(slide, pal.margin, pal.so_what_top, Inches(0.05), Inches(0.72),
              fill=pal.accent)
        _rect(slide, pal.margin, pal.so_what_top, pal.content_w, Pt(0.8),
              fill=pal.line)
        text_left = pal.margin + Inches(0.18)
    else:  # plain
        text_left = pal.margin

    _, tf = _txbox(slide, text_left, pal.so_what_top + Inches(0.1),
                   pal.sw - text_left - pal.margin, Inches(0.52),
                   anchor=MSO_ANCHOR.MIDDLE)
    _write(tf, [("结论 | " + text, pal.body_size, True, pal.title)], pal)


def _footer(slide, note: str, pal: ThemePalette) -> None:
    if not note:
        return
    _, tf = _txbox(slide, pal.margin, pal.footer_top, pal.content_w, Inches(0.3))
    _write(tf, [(note, pal.footnote_size, False, pal.muted)], pal)


def _kpi_card(slide, l, t, w, h, label, value, delta, status, pal: ThemePalette):
    """KPI 卡片：标签 / 数值 / 说明三行。

    高度保护：三行内容 + 间距至少需要约 1.35"，低于这个高度会重叠
    （实测踩过：封面卡片被压到 1.15"，"108.0 万" 与 "预算 100.0 万" 叠在一起）。
    所以这里不依赖调用方给足高度，而是**按实际可用高度压缩行距**，
    并在拿到的高度不够时优先保证数值行完整。
    """
    color = pal.status(status)
    _card(slide, l, t, w, h, pal)
    _rect(slide, l, t, w, Pt(3.2), fill=color)

    h_in = h / 914400
    pad_x = Inches(0.16)
    inner_w = w - Inches(0.3)

    # 三行按比例分配：标签 ~0.30，数值 ~0.42，说明 ~0.26
    label_h = Inches(min(0.30, max(0.22, h_in * 0.24)))
    value_h = Inches(min(0.46, max(0.32, h_in * 0.34)))
    delta_h = Inches(min(0.28, max(0.20, h_in * 0.22)))

    label_top = t + Inches(0.16)
    value_top = label_top + label_h
    delta_top = min(value_top + value_h, t + h - delta_h - Inches(0.06))

    _, tf = _txbox(slide, l + pad_x, label_top, inner_w, label_h)
    _write(tf, [(label, pal.kpi_label_size, False, pal.muted)], pal)

    _, tf = _txbox(slide, l + pad_x, value_top, inner_w, value_h)
    _write(tf, [(value, pal.kpi_value_size, True, color)], pal,
           font=pal.font_title)

    if delta:
        _, tf = _txbox(slide, l + pad_x, delta_top, inner_w, delta_h)
        _write(tf, [(delta, pal.footnote_size, False, pal.muted)], pal)


def _set_chart_dark_fill(chart, pal: ThemePalette) -> None:
    """深色主题下给图表区铺深色底。

    python-pptx 没有暴露 chartSpace 的填充接口，只能写 XML。
    不铺的话：深色页面上会浮出一块白底图表，且我们设的浅色字看不见。
    """
    if not pal.dark_bg:
        return
    from pptx.oxml.ns import qn

    chart_space = chart._chartSpace
    # 已有 spPr 就复用，没有就插到 c:chart 之前（schema 顺序要求）
    sp_pr = chart_space.find(qn("c:spPr"))
    if sp_pr is None:
        sp_pr = chart_space.makeelement(qn("c:spPr"), {})
        chart_el = chart_space.find(qn("c:chart"))
        if chart_el is not None:
            chart_el.addprevious(sp_pr)
        else:
            chart_space.append(sp_pr)
    for child in list(sp_pr):
        sp_pr.remove(child)
    solid = sp_pr.makeelement(qn("a:solidFill"), {})
    color = sp_pr.makeelement(qn("a:srgbClr"), {"val": str(pal.page_bg_hex)})
    solid.append(color)
    sp_pr.append(solid)
    line = sp_pr.makeelement(qn("a:ln"), {})
    line.append(line.makeelement(qn("a:noFill"), {}))
    sp_pr.append(line)


def _style_chart(chart, pal: ThemePalette, legend: bool = True,
                 labels: bool = True, number_format: Optional[str] = None,
                 title: bool = False, axis_format: Optional[str] = None,
                 peak: float = 0.0) -> None:
    chart.font.size = Pt(pal.chart_size)
    chart.font.name = pal.font_body
    chart.font.color.rgb = pal.title

    # 关掉图表自带标题：幻灯片标题已经说明了这页在讲什么，
    # 图表再挂一个（默认还会用系列名）就是重复。
    try:
        chart.has_title = title
    except Exception:
        pass

    chart.has_legend = legend
    if legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(max(pal.chart_size - 1, 6))
        chart.legend.font.name = pal.font_body
        chart.legend.font.color.rgb = pal.muted
    try:
        plot = chart.plots[0]
        # 柱/条更粗一点：gap_width 60 在只有 2~3 个分类时会画出"牙签条"
        plot.gap_width = pal.chart_gap_width
        if labels:
            plot.has_data_labels = True
            plot.data_labels.font.size = Pt(max(pal.chart_size - 1.5, 6))
            plot.data_labels.font.name = pal.font_body
            plot.data_labels.font.color.rgb = pal.text
            if number_format:
                plot.data_labels.number_format = number_format
                plot.data_labels.number_format_is_linked = False
    except Exception:
        pass

    # 坐标轴刻度标签：`chart.font` 管不到它们，必须单独设。
    # 深色主题下尤其重要 —— 否则灰色刻度压在深底上几乎看不见。
    #
    # 注意：python-pptx 的 ``chart.category_axis`` 在**没有该轴时抛异常**
    # 而不是返回 None（饼图/环形图就没有坐标轴），所以必须 try 着取。
    for axis_name in ("category_axis", "value_axis"):
        try:
            axis = getattr(chart, axis_name)
        except Exception:
            continue
        if axis is None:
            continue
        try:
            tick = axis.tick_labels.font
            tick.size = Pt(max(pal.chart_size - 1, 6))
            tick.name = pal.font_body
            tick.color.rgb = pal.text
            axis.format.line.color.rgb = pal.line
        except Exception:
            continue

    _style_axes(chart, pal, axis_format=axis_format, labels=labels, peak=peak)
    _set_chart_dark_fill(chart, pal)


def _style_axes(chart, pal: ThemePalette, axis_format: Optional[str] = None,
                labels: bool = True, peak: float = 0.0) -> None:
    """网格线与数值轴刻度：决定图表"干净"还是"脏"。

    实测问题（物业收入页）
    ----------------------
    * **两个方向的网格线都开着**，而且用的是模板默认深灰 → 深色竖线把
      数据标签切成两半，整页像张草稿。专业版式的惯例是：只留**垂直于
      数值轴**的那组网格线，颜色压到和分隔线同级的浅灰。
    * 数值轴刻度是 ``100000 200000 …`` 的裸数字，而全篇口径是万元 →
      看的人要自己心算。刻度格式交给调用方（``axis_format``）声明。
    * 数据标签贴着柱子末端时会压到网格线上，``OUTSIDE_END`` 把它推到外侧。
    """
    # 数值轴：条形图在横轴，柱状图在纵轴
    try:
        value_axis = chart.value_axis
    except Exception:
        value_axis = None
    try:
        category_axis = chart.category_axis
    except Exception:
        category_axis = None

    if value_axis is not None:
        try:
            # 只留数值轴方向的网格线，并压成浅灰（默认深灰会盖住数据）
            value_axis.has_major_gridlines = pal.chart_gridlines
            if pal.chart_gridlines:
                gl = value_axis.major_gridlines.format.line
                gl.color.rgb = pal.line
                gl.width = Pt(0.75)
        except Exception:
            pass
        try:
            if axis_format:
                value_axis.tick_labels.number_format = axis_format
                value_axis.tick_labels.number_format_is_linked = False
        except Exception:
            pass
        try:
            value_axis.format.line.color.rgb = pal.line
        except Exception:
            pass
        try:
            axis_max = _nice_axis_max(peak)
            if axis_max is not None:
                value_axis.maximum_scale = axis_max
                value_axis.minimum_scale = 0
        except Exception:
            pass

    if category_axis is not None:
        try:
            # 分类轴网格线一律关掉：它垂直于分类，不传递任何信息
            category_axis.has_major_gridlines = False
        except Exception:
            pass

    # 数据标签放到柱/条外侧末端，避免压在网格线和柱体上
    if labels:
        try:
            chart.plots[0].data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
        except Exception:
            pass


def _color_series(chart, colors: Sequence[RGBColor]) -> None:
    for i, series in enumerate(chart.series):
        try:
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = colors[i % len(colors)]
        except Exception:
            pass


def _color_points(chart, colors: Sequence[RGBColor], n: int) -> None:
    try:
        plot = chart.plots[0]
        for i, point in enumerate(plot.series[0].points):
            if i >= n:
                break
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = colors[i % len(colors)]
    except Exception:
        pass


def _grid_table(slide, l, t, w, h, rows: Sequence[Sequence[str]],
                pal: ThemePalette, weights: Optional[Sequence[float]] = None,
                font: Optional[float] = None,
                head_font: Optional[float] = None) -> None:
    """用矩形+文本自绘表格（比原生表格更容易控制样式）。"""
    if not rows:
        return
    body_font = font if font is not None else max(pal.body_size - 2.5, 7)
    head_size = head_font if head_font is not None else max(body_font + 0.5, 8)

    weights = list(weights or [1] * len(rows[0]))
    total = sum(weights) or 1
    widths = [w * (x / total) for x in weights]
    row_h = h / max(len(rows), 1)

    for r, row in enumerate(rows):
        x = l
        is_head = (r == 0)
        for c, cell in enumerate(row):
            if c >= len(widths):
                break
            _rect(slide, x, t + row_h * r, widths[c], row_h,
                  fill=(pal.panel if is_head else pal.page_bg), line=pal.line)
            _, tf = _txbox(slide, x + Inches(0.08), t + row_h * r,
                           widths[c] - Inches(0.16), row_h,
                           anchor=MSO_ANCHOR.MIDDLE)
            _write(tf, [(cell, head_size if is_head else body_font, is_head,
                         pal.title if is_head else pal.text)], pal)
            x += widths[c]


# ================================================================
# 块渲染
# ================================================================

def _render_kpi_grid(slide, block: Dict, top, height, pal: ThemePalette) -> None:
    items = block.get("items") or []
    if not items:
        return
    cols = min(int(block.get("columns") or pal.kpi_columns), max(1, len(items)))
    rows = (len(items) + cols - 1) // cols
    gap = Inches(0.22)
    cw = int((pal.content_w - gap * (cols - 1)) / cols)
    ch = int((height - gap * (rows - 1)) / max(rows, 1))
    for i, item in enumerate(items[:cols * rows]):
        r, c = divmod(i, cols)
        _kpi_card(slide, pal.margin + c * (cw + gap), top + r * (ch + gap),
                  cw, ch,
                  str(item.get("label", "")), str(item.get("value", "")),
                  str(item.get("delta", "") or ""), item.get("status"), pal)


def _series_peak(series: Sequence[Dict]) -> float:
    """所有系列里的最大值（用于收紧数值轴上限）。"""
    peak = 0.0
    for s in series or []:
        for v in (s.get("values") or []):
            try:
                peak = max(peak, abs(float(v or 0)))
            except (TypeError, ValueError):
                continue
    return peak


def _nice_axis_max(peak: float) -> Optional[float]:
    """把轴顶收到"比最大值略高的整数刻度"。

    实测问题：最大值 48.3（万元）时 PowerPoint 自动把轴顶定在 60 ——
    右侧 20% 的画布是纯空白，最长的那根条只画到五分之四处。
    这里在最大值上方 5% 处找第一个"整齐"刻度：步长按量级取
    1/2/2.5/5 的倍数（48.3 → 50，而不是粗档位算出来的 60）。
    """
    if peak <= 0:
        return None
    import math

    target = peak * 1.02
    exp = math.floor(math.log10(target))
    base = 10.0 ** exp
    # 经典「nice number」梯子：轴顶只允许是 1/1.2/1.5/2/2.5/3/4/5/6/8/10
    # 乘以量级，取第一个 ≥ target 的档位（48.3 → 50，92.48 → 100）。
    # 余量只给 2%：梯子本身就会向上取整留出空间，余量给大了反而会跳一档
    # （5% 时 48.3 被推到 60，右侧又空出一片）。
    for mult in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        value = mult * base
        if value >= target - 1e-9:
            return round(value, 6)
    return round(10 * base, 6)


def _chart_height(block: Dict, pal: ThemePalette, available: int) -> int:
    """图表该占多高：按分类数算，而不是一律铺满正文区。

    实测问题（收入页只有 2 个收费项）
    --------------------------------
    条形图拿到 4.6" 的高度，PowerPoint 就把 2 根条平摊到整个绘图区 ——
    条很细、条间空隙极大，页面一片空。``gap_width`` 管不了这个：
    它只控制条间距占条宽的比例，绘图区高度一变，条宽跟着变。
    真正的解法是**限制绘图区高度**：每个分类给一条"合理行高"，
    分类少就让图表矮一些，剩下的空间留白（居中后上下对称，不难看）。

    每类行高取 0.62"（含条间距），再加上坐标轴与图例的固定开销。
    """
    n = len(block.get("categories") or [])
    if n <= 0:
        return available
    chart_type = str(block.get("chart") or "column").lower()
    # 饼图/环形图跟分类数无关，按正方形区域给高度
    if chart_type in ("pie", "doughnut"):
        return min(available, int(4.0 * EMU_PER_INCH))
    # 折线/面积图横轴是序列，铺满才看得出趋势
    if chart_type in ("line", "area"):
        return available
    overhead = 1.1 if len(block.get("series") or []) > 1 else 0.8
    # 分类很少时每类给更高的行高：2 根条按 0.62" 算只有 2.0" 高，
    # 和 4.6" 的正文区相比显得缩在角落；按 1.1"/类 算出 3.0"，撑得住版面。
    per_row = 1.1 if n <= 3 else (0.85 if n <= 5 else 0.62)
    need = n * per_row + overhead
    return max(int(2.4 * EMU_PER_INCH), int(need * EMU_PER_INCH))


def _render_chart(slide, block: Dict, top, height, pal: ThemePalette) -> None:
    categories = [str(c) for c in (block.get("categories") or [])]
    series = block.get("series") or []
    if not categories or not series:
        return

    chart_type = str(block.get("chart") or "column").lower()
    mapping = {
        "bar": XL_CHART_TYPE.BAR_CLUSTERED,
        "hbar": XL_CHART_TYPE.BAR_CLUSTERED,
        "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "col": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "line": XL_CHART_TYPE.LINE_MARKERS,
        "pie": XL_CHART_TYPE.PIE,
        "doughnut": XL_CHART_TYPE.DOUGHNUT,
        "area": XL_CHART_TYPE.AREA,
        "stacked": XL_CHART_TYPE.COLUMN_STACKED,
    }
    if len(series) == 1 and chart_type == "column" and block.get("prefer_bar"):
        chart_type = "bar"

    data = CategoryChartData()
    data.categories = categories
    for s in series:
        data.add_series(str(s.get("name", "")),
                        [float(v or 0) for v in s.get("values", [])])

    gframe = slide.shapes.add_chart(
        mapping.get(chart_type, XL_CHART_TYPE.COLUMN_CLUSTERED),
        pal.margin, top, pal.content_w, height, data,
    )
    chart = gframe.chart
    # 单系列不需要图例 —— 图例只会重复图表标题那一行，白占高度。
    # 多系列才需要图例区分。
    need_legend = len(series) > 1
    _style_chart(chart, pal, legend=need_legend,
                 labels=(chart_type not in ("pie", "doughnut")),
                 number_format=block.get("number_format"),
                 axis_format=block.get("axis_format"),
                 peak=_series_peak(series))

    # 逐点上色的适用条件：单系列 + 非折线/面积（那类点色没意义）。
    # 踩过的坑：原来只对 ``bar`` 判断 point_colors，``column``（柱状图）
    # 落到"按系列上色"分支 —— 单系列就只有一种颜色，
    # 于是「应收/实收/未缴」三根柱子全是同一个蓝，语义色白设了。
    point_colors = block.get("point_colors")
    can_color_points = (
        len(series) == 1
        and chart_type not in ("line", "area", "stacked", "pie", "doughnut")
    )

    if chart_type in ("pie", "doughnut"):
        _color_points(chart, pal.palette, len(categories))
    elif can_color_points and point_colors:
        _color_points(chart, [pal.status(s) for s in point_colors],
                      len(categories))
    elif can_color_points:
        # 单系列且没指定语义色 → 统一用强调色（比调色板纯色更稳）
        _color_series(chart, [pal.accent])
    else:
        _color_series(chart, pal.palette)


def _render_text(slide, block: Dict, top, height, pal: ThemePalette) -> int:
    style = str(block.get("style") or "body")
    text = str(block.get("text") or "")
    _, tf = _txbox(slide, pal.margin, top, pal.content_w, height)
    if style == "quote":
        _write(tf, [(text, pal.body_size, False, pal.muted)], pal)
    elif style == "lead":
        _write(tf, [(text, pal.body_size + 3, True, pal.title)], pal,
               font=pal.font_title)
    else:
        _write(tf, [(text, pal.body_size, False, pal.text)], pal)
    return height


def _render_bullets(slide, block: Dict, top, height, pal: ThemePalette) -> int:
    items = block.get("items") or []
    if not items:
        return 0
    _, tf = _txbox(slide, pal.margin, top, pal.content_w, height)
    _write(tf, [(f"· {item}", pal.body_size, False, pal.text) for item in items], pal)
    return height


def _render_table(slide, block: Dict, top, height, pal: ThemePalette) -> int:
    rows = [block.get("headers") or []] + list(block.get("rows") or [])
    _grid_table(slide, pal.margin, top, pal.content_w, height, rows, pal,
                block.get("weights"), block.get("font"))
    return height


def _render_cards(slide, block: Dict, top, height, pal: ThemePalette) -> int:
    items = block.get("items") or []
    if not items:
        return 0

    preferred = int(block.get("columns") or pal.cards_columns)
    cols = _pick_columns(len(items), preferred)
    rows = (len(items) + cols - 1) // cols
    gap = Inches(0.22)
    cw = int((pal.content_w - gap * (cols - 1)) / cols)

    # 高度取三者的折中：
    #   · 不低于内容需要（否则文字挤压）
    #   · 不高于可用空间
    #   · 允许放大到可用空间的 ~38%，让卡片撑起版面但**不被拉成空壳**
    #     （实测两头都踩过：0.45 时两行卡片下方空 2"；提到 0.62 后卡片
    #      变成 3" 高的空盒子、文字挤在顶部，比页面留白更难看。
    #      所以放大系数只用来"去掉局促感"，撑满版面是内容的事。）
    need = max((_estimate_card_height(it, cw, pal) for it in items), default=1.0)
    per_row_avail = (height - gap * (rows - 1)) / max(rows, 1) / 914400
    target = min(per_row_avail, max(need, (height / 914400) * 0.38 / rows))
    ch = int(min(per_row_avail, max(need, target)) * 914400)

    for i, item in enumerate(items[:cols * rows]):
        r, c = divmod(i, cols)
        l = pal.margin + c * (cw + gap)
        t = top + r * (ch + gap)
        color = pal.status(item.get("status"))
        _card(slide, l, t, cw, ch, pal)
        # 左侧色条按卡片高度自适应：太矮就细一点，避免比例失调
        bar_w = Pt(4) if ch >= Inches(1.3) else Pt(3)
        _rect(slide, l, t, bar_w, ch, fill=color)

        # 卡片内三块内容整体垂直居中（否则内容会挤在顶部）
        title = str(item.get("title", "") or "")
        body = str(item.get("body", "") or "")
        inner = (pal.card_title_size / 72 * 1.5 if title else 0) \
            + (0.1 if (title and body) else 0) \
            + (pal.card_body_size / 72 * 1.45 if body else 0)
        pad = max((ch / 914400 - inner) / 2, 0.14)
        pad = min(pad, 0.5)

        cursor = t + Inches(pad)
        if title:
            _, tf = _txbox(slide, l + Inches(0.2), cursor,
                           cw - Inches(0.36), Inches(0.34))
            _write(tf, [(title, pal.card_title_size, True, pal.title)], pal,
                   font=pal.font_title)
            cursor += Inches(pal.card_title_size / 72 * 1.5 + 0.1)
        if body:
            _, tf = _txbox(slide, l + Inches(0.2), cursor,
                           cw - Inches(0.36), max(ch - Inches(pad) * 2, Inches(0.3)))
            _write(tf, [(body, pal.card_body_size, False, pal.muted)], pal)
    return rows * (ch + gap) - gap


# ================================================================
# 页面渲染
# ================================================================

def _render_cover(slide, slide_ir: Dict, deck: Dict, pal: ThemePalette) -> None:
    """封面：标题块在色带里**垂直居中**，项目/期间贴色带下沿。

    实测问题：原来标题固定在 0.62" 起排，色带却有 2.5" 高 →
    下半段是死空间，而项目名孤悬在色带与卡片之间。
    现在按色带高度算居中位置，项目名压到色带底部（浅色字，落在色带上），
    标题块与项目行形成一个整体。
    """
    meta = deck.get("meta") or {}
    band_h = pal.cover_band
    if pal.cover_band_on:
        _rect(slide, 0, 0, pal.sw, band_h, fill=pal.title)

    title_text = str(meta.get("title") or slide_ir.get("title") or "")
    subtitle = str(meta.get("subtitle") or "")
    project = str(meta.get("project") or "")
    period = str(meta.get("period") or "")

    # 封面去重：项目名/标题/副标题三行经常互相复述（自动取数时 project
    # 来自文件名，title 又是 "<project> 经营分析"，于是同一串字印三遍）。
    # 规则：标题保留；与标题同义的项目名丢掉；副标题里重复标题的部分丢掉。
    if _same_label(project, title_text):
        project = ""
    if subtitle and _same_label(subtitle, title_text):
        subtitle = ""

    parts = [x for x in (project, period) if x]

    # 标题框高度按主题字号推导，避免大字号主题（如动漫 34pt）被裁切
    title_box_h = Inches(max(0.5, pal.cover_title_size / 72 * 1.5))
    sub_h = Inches(pal.cover_subtitle_size / 72 * 1.8) if subtitle else 0

    # 项目 / 期间行（放在色带内底部）
    proj_h = Inches(pal.cover_project_size / 72 * 1.6) if parts else 0

    inner_h = title_box_h + sub_h + proj_h
    if pal.cover_band_on:
        # 三块内容在色带内垂直居中，上下留白大致相等
        block_top = max(Inches(0.35), int((band_h - inner_h) / 2))
    else:
        block_top = Inches(0.62)

    _, tf = _txbox(slide, pal.margin, block_top, pal.content_w, title_box_h)
    _write(tf, [(title_text, pal.cover_title_size, True, pal.on_dark)], pal,
           font=pal.font_title)

    cursor = block_top + title_box_h
    if subtitle:
        _, tf = _txbox(slide, pal.margin, cursor, pal.content_w, sub_h)
        _write(tf, [(subtitle, pal.cover_subtitle_size, False, pal.on_dark_muted)],
               pal)
        cursor += sub_h

    if parts:
        _, tf = _txbox(slide, pal.margin, cursor, pal.content_w, proj_h)
        # 在色带上用浅色，不在色带上用深色
        proj_color = pal.on_dark if pal.cover_band_on else pal.title
        _write(tf, [("  ".join(parts), pal.cover_project_size, True, proj_color)],
               pal, font=pal.font_title)

    # 内容块：紧贴色带下方起排（不再在整片剩余空间里居中）。
    # 实测问题：色带 3.1" + 卡片在剩下的 4.4" 里居中 → 卡片上方空 1.2"、
    # 下方空 1.7"，三张卡孤零零浮在页面正中，像还没排完。
    # 封面的视觉重心本来就该在上半部：卡片贴着色带走，下方留白是刻意的。
    band_bottom = band_h if pal.cover_band_on else Inches(0)
    for block in slide_ir.get("blocks") or []:
        if block.get("type") == "kpi_grid":
            items = block.get("items") or []
            cols = max(1, int(block.get("columns") or pal.kpi_columns))
            rows = max(1, -(-len(items) // cols))
            gap = Inches(0.22)
            # 卡片高度按三行内容算（标签0.30 + 数值0.46 + 说明0.28 + 内边距），
            # 不撑满可用空间 —— 撑满会在卡片下半部留出一块空洞。
            card_h = Inches(1.35)
            grid_h = int(card_h * rows + gap * (rows - 1))
            _render_kpi_grid(slide, block, band_bottom + Inches(0.55),
                             grid_h, pal)
        elif block.get("type") == "text":
            _render_text(slide, block, band_bottom + Inches(0.45),
                         Inches(0.9), pal)

    _footer(slide, str(meta.get("footnote") or slide_ir.get("footnote") or ""), pal)


def _render_agenda(slide, deck: Dict, pal: ThemePalette) -> None:
    """路线图页：把后面几段的标题列成卡片。"""
    cards = []
    for s in (deck.get("slides") or []):
        if s.get("layout") in ("cover", "agenda", "appendix"):
            continue
        title = str(s.get("title") or "")
        if not title:
            continue
        cards.append({"title": s.get("kicker") or title[:12],
                      "body": title, "status": "info"})
    if cards:
        _render_cards(slide, {"type": "cards", "items": cards[:6],
                              "columns": pal.cards_columns},
                      pal.body_top, Inches(4.6), pal)


def _render_blocks(slide, slide_ir: Dict, deck: Dict, pal: ThemePalette) -> None:
    """按顺序排布本页所有内容块。

    注意命名：``slide`` 是 pptx 的 Slide 对象，``slide_ir`` 才是 IR 字典。
    这两个混用很容易写出 ``Slide object has no attribute 'get'``。

    垂直节奏（实测修过两轮）
    ------------------------
    块按**内容自然高度**定高，一律顶着 ``body_top`` 排。页面只有 3 张
    KPI 卡时，正文区下方会空出一段 —— 这是刻意的：

    * 试过"整组垂直居中" → 卡片飘到页面正中，和页眉断开一大段，
      看着像两个互不相干的东西；
    * 试过"把块撑高填满版面" → 卡片变成 3" 高的空壳，内容挤在顶部，
      卡片内部空出 2"，比页面空白更难看（``deck_layout`` 的注释里
      早写过这个坑）。

    结论：**空白留在内容之外，不要塞进内容里**。内容少的页面就该是
    "上半页讲完、下半页留白"，这和咨询报告的版式惯例一致。真正要修的是
    "一页只有三个数"这件事本身 —— 那是 spec 的内容问题，不是渲染问题。
    """
    blocks = slide_ir.get("blocks") or []
    top = pal.body_top
    bottom = pal.so_what_top - Inches(0.12) if slide_ir.get("so_what") \
        else pal.sh - Inches(0.6)
    if slide_ir.get("footnote"):
        bottom -= Inches(0.22)

    available = max(int(bottom - top), int(Inches(0.6)))

    for block in blocks:
        btype = block.get("type")
        if btype not in BLOCK_TYPES:
            _render_text(slide, {"text": f"[未支持的块类型: {btype}]",
                                 "style": "quote"}, top, Inches(0.5), pal)
            top += Inches(0.6)
            continue
        remaining = max(int(bottom - top), int(Inches(0.6)))
        if btype == "kpi_grid":
            height = _measure_block(block, pal, remaining)
            _render_kpi_grid(slide, block, top, height, pal)
            top += height + Inches(0.18)
        elif btype == "chart":
            height = remaining
            if block.get("height_inches"):
                height = min(remaining, Inches(float(block["height_inches"])))
            else:
                height = min(remaining, _chart_height(block, pal, remaining))
            # 图表矮于正文区时略微下移（约 1/4 空隙），让它和标题之间有呼吸，
            # 但不居中 —— 居中会在图表上下各留一条白，页面重心反而往下掉。
            chart_top = top + max(0, int((remaining - height) / 4))
            _render_chart(slide, block, chart_top, height, pal)
        elif btype == "text":
            top += _render_text(slide, block, top, remaining, pal)
        elif btype == "bullets":
            top += _render_bullets(slide, block, top, remaining, pal)
        elif btype == "table":
            top += _render_table(slide, block, top, remaining, pal)
        elif btype == "cards":
            used = _render_cards(slide, block, top, remaining, pal)
            top += used + Inches(0.18)


def _render_generic(slide, slide_ir: Dict, deck: Dict, idx: int,
                    pal: ThemePalette) -> None:
    _header(slide, idx, slide_ir.get("title", ""), slide_ir.get("kicker", ""), pal)
    _render_blocks(slide, slide_ir, deck, pal)
    _so_what(slide, slide_ir.get("so_what", ""), pal)
    _footer(slide, slide_ir.get("footnote", ""), pal)


# ================================================================
# 主入口
# ================================================================

def _save_with_retry(prs, output_path: str, attempts: int = 6) -> None:
    """保存 pptx，遇到文件占用时重试。

    真实场景：用户把上一次的产物留在 WPS / PowerPoint 里开着，
    再生成同名文件就会 PermissionError。重试几次通常能等到句柄释放；
    实在不行就报一个**能看懂**的错误，而不是抛裸的 Errno 13。
    """
    import time

    last_error: Optional[Exception] = None
    for i in range(attempts):
        try:
            prs.save(output_path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.2 * (i + 1))
    raise PermissionError(
        f"无法写入 {output_path}：文件正被其他程序占用"
        f"（通常是 WPS / PowerPoint 打开着该文件）。请关闭后重试。"
        f"原始错误: {last_error}"
    )


def render_deck(
    deck: Dict[str, Any],
    output_path: str,
    theme: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把 IR 渲染成 .pptx。

    Args:
        deck: ``deck_ir`` 定义的演示文稿 IR。
        output_path: 输出路径。
        theme: 主题（样式知识）。不传则用 ``DEFAULT_THEME``。

    Returns:
        {"path", "slides", "rendered", "errors", "size_kb", "theme"}
    """
    meta = deck.get("meta") or {}
    slides_ir = deck.get("slides") or []

    # 主题解析优先级：显式 theme > IR.meta.theme 预设 > 默认
    if theme is None:
        scene = meta.get("theme")
        theme = build_theme(scene if isinstance(scene, str) else None)
    elif not theme.get("colors"):
        theme = build_theme(theme=theme, scene=meta.get("theme")
                            if isinstance(meta.get("theme"), str) else None)
    pal = ThemePalette(theme)

    prs = Presentation()
    prs.slide_width, prs.slide_height = pal.sw, pal.sh

    rendered: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    page_no = 0

    for slide_ir in slides_ir:
        layout = str(slide_ir.get("layout") or "text")
        page_no += 1
        try:
            slide = _add_slide(prs, pal)
            if layout == "cover":
                _render_cover(slide, slide_ir, deck, pal)
            elif layout == "agenda":
                _header(slide, page_no, slide_ir.get("title", ""),
                        slide_ir.get("kicker", "汇报主线"), pal)
                _render_agenda(slide, deck, pal)
                _footer(slide, slide_ir.get("footnote", ""), pal)
            else:
                _render_generic(slide, slide_ir, deck, page_no, pal)
            rendered.append({"page": page_no, "layout": layout,
                             "title": slide_ir.get("title", "")})
        except Exception as exc:
            errors.append({"page": page_no, "layout": layout,
                           "error": f"{type(exc).__name__}: {exc}"})
            try:
                err_slide = _add_slide(prs, pal)
                _rect(err_slide, 0, 0, pal.sw, pal.sh, fill=pal.panel)
                _, tf = _txbox(err_slide, pal.margin, Inches(3.0), pal.content_w,
                               Inches(1.5))
                _write(tf, [
                    (f"第 {page_no} 页渲染失败", 22, True, pal.bad),
                    (f"layout={layout}  原因：{type(exc).__name__}: {exc}",
                     12, False, pal.title),
                ], pal, font=pal.font_title)
            except Exception:
                pass

    compact = {
        "v": 1,
        "pages": page_no,
        "secs": [f"{r['page']}:{r.get('layout') or ''}" for r in rendered],
    }
    try:
        prs.core_properties.comments = json.dumps(compact, ensure_ascii=False)
        title_bits = [str(x) for x in (meta.get("project"), meta.get("period"),
                                       meta.get("title")) if x]
        if title_bits:
            prs.core_properties.title = " ".join(title_bits)
    except Exception:
        pass

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    _save_with_retry(prs, output_path)

    return {
        "path": output_path,
        "slides": page_no,
        "rendered": rendered,
        "errors": errors,
        "size_kb": round(os.path.getsize(output_path) / 1024, 1),
        "theme": theme.get("name", "(未命名)"),
    }


# ================================================================
# Tool 封装
# ================================================================

class DeckBuildTool(Tool):
    """通用演示文稿渲染工具（``deck_build``）。"""

    def __init__(self, expandable: bool = False):
        super().__init__(
            name="deck_build",
            description=(
                "把演示文稿 IR（JSON）按指定主题渲染成 .pptx。业务无关："
                "支持 cover/agenda/kpi/chart/table/text/cards/appendix 布局，"
                "kpi_grid/chart/text/bullets/table/cards 内容块。"
                "样式全部来自 theme（字体/语义色/调色板/画布/版式），"
                "不传则用默认主题。IR 见 deck_ir，主题见 deck_theme。"
            ),
            expandable=expandable,
        )

    def get_params(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="deck",
                type="object",
                description=(
                    "演示文稿 IR：{meta:{title,project,period},"
                    " slides:[{layout,title,kicker,blocks,so_what,footnote}]}"
                ),
                required=True,
            ),
            ToolParameter(
                name="output",
                type="string",
                description="输出 .pptx 路径",
                required=True,
            ),
            ToolParameter(
                name="theme",
                type="object",
                description=(
                    "主题（样式知识）：{name, canvas, fonts, colors, palette, layout}。"
                    "colors 用 6 位十六进制字符串。不传则用默认主题。"
                ),
                required=False,
                default=None,
            ),
            ToolParameter(
                name="scene",
                type="string",
                description="主题场景预设名（如 property / anime）；theme 存在时忽略",
                required=False,
                default=None,
            ),
            ToolParameter(
                name="strict",
                type="boolean",
                description="为真时 IR 校验有错就拒绝渲染（默认宽容渲染）",
                required=False,
                default=False,
            ),
        ]

    def run(self, params: Dict[str, Any]) -> str:
        deck = params.get("deck")
        if isinstance(deck, str):
            try:
                deck = json.loads(deck)
            except json.JSONDecodeError as exc:
                return f"deck 不是合法 JSON: {exc}"
        if not isinstance(deck, dict):
            return "deck 必须是对象（含 meta 与 slides）"

        output = str(params.get("output") or "")
        if not output:
            return "缺少 output 参数"

        raw_theme = params.get("theme")
        if isinstance(raw_theme, str):
            try:
                raw_theme = json.loads(raw_theme)
            except json.JSONDecodeError as exc:
                return f"theme 不是合法 JSON: {exc}"
        if raw_theme is not None and not isinstance(raw_theme, dict):
            return "theme 必须是对象"

        scene = params.get("scene")
        theme_problems: List[str] = []
        if raw_theme:
            theme: Optional[Dict[str, Any]] = build_theme(
                str(scene) if scene else None, theme=raw_theme)
            theme_problems = validate_theme(theme)
        elif scene:
            theme = build_theme(str(scene))
        else:
            # 两个参数都没给 → 传 None，交给 render_deck 用
            # IR.meta.theme 兜底，最后才退回默认主题。
            # （早先这里无条件解析出主题，导致 meta.theme 兜底永远不生效）
            theme = None

        problems = validate_deck(deck)
        if problems and params.get("strict"):
            return "IR 校验失败（strict 模式）:\n" + "\n".join(
                f"  - {p}" for p in problems)

        try:
            result = render_deck(deck, output, theme=theme)
        except Exception as exc:
            return f"渲染失败: {type(exc).__name__}: {exc}"

        lines = [
            f"[deck_build] 已生成 {result['slides']} 页 → {result['path']}",
            f"  主题: {result['theme']}",
            f"  大小: {result['size_kb']} KB",
        ]
        if result["errors"]:
            lines.append(f"  ⚠ {len(result['errors'])} 页渲染失败:")
            for err in result["errors"]:
                lines.append(f"    - 第 {err['page']} 页 ({err['layout']}): {err['error']}")
        if theme_problems:
            lines.append(f"  ⚠ 主题校验 {len(theme_problems)} 条:")
            for p in theme_problems[:8]:
                lines.append(f"    - {p}")
        if problems:
            lines.append(f"  IR 校验提示（{len(problems)} 条，已宽容渲染）:")
            for p in problems[:8]:
                lines.append(f"    - {p}")
        return "\n".join(lines)
