# -*- coding: utf-8 -*-
"""Render the fixed 10-page property collection deck as editable SVG pages."""

from __future__ import annotations

from pathlib import Path
import re
from xml.sax.saxutils import escape

from property_metrics import BuildingMetric, ChannelMetric, PropertyMetrics, ReasonMetric


BG = "#F7F5F0"
SECONDARY = "#E9E4D8"
PRIMARY = "#17324D"
ACCENT = "#C08A2E"
TEAL = "#4F7C8A"
TEXT = "#1E2A33"
MUTED = "#5C6670"
DIVIDER = "#D6CFC2"
NEG = "#B23A2E"
POS = "#2E7D32"
WARN = "#B7791F"

BODY_FONT = "Microsoft YaHei, Arial, sans-serif"
TITLE_FONT = "SimHei, Arial, sans-serif"
MONO_FONT = "Consolas, monospace"

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
_DIGIT_RE = re.compile(r"[0-9]")


def x(text: object) -> str:
    return escape(str(text), {'"': "&quot;", "'": "&apos;"})


def money(value: float) -> str:
    return f"{value:,.2f}"


def pct(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}%"


def estimate_width(text: str, font_size: int) -> float:
    """Conservative width estimate using ppt-master's 24px calibration rates."""
    cjk = len(_CJK_RE.findall(text))
    digits = len(_DIGIT_RE.findall(text))
    other = max(0, len(text) - cjk - digits)
    width_at_24 = cjk / 3.9 * 100 + digits / 7.1 * 100 + other / 8.1 * 100
    return width_at_24 * font_size / 24


def fit_text(text: str, max_width: float, font_size: int) -> list[str]:
    """Split text at punctuation/arrow boundaries before it can auto-wrap."""
    if not text:
        return [""]
    limit = max_width * 0.9
    if estimate_width(text, font_size) <= limit:
        return [text]
    segments = [part for part in re.split(r"(?<=[，。；：、→/·])", text) if part]
    if not segments:
        segments = list(text)
    lines: list[str] = []
    current = ""
    for segment in segments:
        candidate = current + segment
        if not current or estimate_width(candidate, font_size) <= limit:
            current = candidate
            continue
        lines.append(current)
        current = segment
    if current:
        lines.append(current)
    # Split any single overlong segment by characters.
    fixed: list[str] = []
    for line in lines:
        if estimate_width(line, font_size) <= limit or len(line) <= 1:
            fixed.append(line)
            continue
        chunk = ""
        for char in line:
            if chunk and estimate_width(chunk + char, font_size) > limit:
                fixed.append(chunk)
                chunk = char
            else:
                chunk += char
        if chunk:
            fixed.append(chunk)
    return fixed or [text]


def multiline_text(
    x_pos: float,
    y_pos: float,
    text: str,
    font_size: int,
    fill: str,
    max_width: float | None = None,
    font_family: str | None = None,
    font_weight: str | None = None,
    leading: int | None = None,
    anchor: str | None = None,
    one_line: bool = False,
) -> str:
    """Render one text frame with explicit line breaks and a budget marker."""
    lines: list[str] = []
    for raw_line in text.split("\n"):
        lines.extend(fit_text(raw_line, max_width, font_size) if max_width else [raw_line])
    if one_line and len(lines) != 1:
        raise ValueError(f"文本超出单行预算：{text!r} -> {lines!r}")
    leading = leading or max(font_size + 8, int(font_size * 1.5))
    attrs = [f'x="{x_pos:g}"', f'y="{y_pos:g}"', f'font-size="{font_size}"', f'fill="{fill}"']
    if font_family:
        attrs.append(f'font-family="{font_family}"')
    if font_weight:
        attrs.append(f'font-weight="{font_weight}"')
    if anchor:
        attrs.append(f'text-anchor="{anchor}"')
    marker = f"<!-- text-budget: {max_width:g} -->\n" if max_width else ""
    if len(lines) == 1:
        return marker + f'<text {" ".join(attrs)}>{x(lines[0])}</text>'
    body = x(lines[0])
    for line in lines[1:]:
        body += f'<tspan x="{x_pos:g}" dy="{leading}">{x(line)}</tspan>'
    return marker + f'<text {" ".join(attrs)}>{body}</text>'


def num(value: float) -> str:
    return f"{int(round(value)):,}"


def short_period(value: str) -> str:
    parts = [p for p in str(value).replace("~", "-").split("-") if p]
    if len(parts) >= 5:
        return f"{parts[0]}-{parts[1]}~{parts[3]}-{parts[4]}"
    return value


def _root(role: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" '
        f'data-pptx-page-role="{role}" font-family="{BODY_FONT}" fill="{TEXT}">'
        f'<rect id="page-bg" data-pptx-role="background" x="0" y="0" width="1280" height="720" fill="{BG}"/>'
    )


def _header(kicker: str, title: str, title_size: int = 44) -> str:
    return (
        f'<g id="header" data-pptx-bounds="72 40 1136 100">'
        f'<line x1="72" y1="42" x2="1208" y2="42" stroke="{PRIMARY}" stroke-width="1"/>'
        f'<text x="72" y="74" font-size="18" fill="{ACCENT}" letter-spacing="2">{x(kicker)}</text>'
        + multiline_text(
            72,
            120,
            title,
            title_size,
            PRIMARY,
            max_width=1100,
            font_family=TITLE_FONT,
            font_weight="bold",
            one_line=True,
        )
        + f'</g>'
    )


def _footer(source: str, page_no: str) -> str:
    return (
        f'<g id="footer" data-pptx-bounds="72 646 1136 60">'
        f'<line x1="72" y1="648" x2="1208" y2="648" stroke="{DIVIDER}" stroke-width="1"/>'
        f'<text x="72" y="680" font-size="15" fill="{MUTED}">{x(source)}</text>'
        f'<text x="1208" y="680" font-size="15" fill="{MUTED}" text-anchor="end">{x(page_no)}</text>'
        f'</g></svg>'
    )


def _bar_rows(
    items: list[tuple[str, float, float]],
    label_x: int,
    bar_x: int,
    y_start: int,
    gap: int,
    bar_height: int,
    scale: float,
    colors: list[str],
    value_fmt,
    label_size: int = 18,
    value_size: int = 18,
) -> str:
    parts: list[str] = []
    for i, (name, value, width) in enumerate(items):
        y = y_start + i * gap
        color = colors[i % len(colors)]
        parts.append(
            f'<text x="{label_x}" y="{y + 19}" font-size="{label_size}" fill="{TEXT}" '
            f'text-anchor="end">{x(name)}</text>'
            f'<rect x="{bar_x}" y="{y}" width="{max(0.0, width):.1f}" height="{bar_height}" fill="{color}"/>'
            f'<text x="{bar_x + max(0.0, width) - 12:.1f}" y="{y + 18}" '
            f'font-family="{MONO_FONT}" font-size="{value_size}" fill="{BG}" '
            f'text-anchor="end">{x(value_fmt(value))}</text>'
        )
    return "".join(parts)


def _donut(metrics: PropertyMetrics) -> str:
    total = max(1, metrics.full_count + metrics.zero_count + metrics.partial_count)
    circumference = 691.15
    full = circumference * metrics.full_count / total
    zero = circumference * metrics.zero_count / total
    partial = circumference * metrics.partial_count / total
    return (
        f'<circle cx="282" cy="380" r="110" fill="none" stroke="{SECONDARY}" stroke-width="60"/>'
        f'<circle cx="282" cy="380" r="110" fill="none" stroke="{PRIMARY}" stroke-width="60" '
        f'stroke-dasharray="{full:.1f} {circumference:.2f}" transform="rotate(-90 282 380)"/>'
        f'<circle cx="282" cy="380" r="110" fill="none" stroke="{NEG}" stroke-width="60" '
        f'stroke-dasharray="{zero:.1f} {circumference:.2f}" stroke-dashoffset="-{full:.1f}" '
        f'transform="rotate(-90 282 380)"/>'
        f'<circle cx="282" cy="380" r="110" fill="none" stroke="{TEAL}" stroke-width="60" '
        f'stroke-dasharray="{partial:.1f} {circumference:.2f}" stroke-dashoffset="-{full + zero:.1f}" '
        f'transform="rotate(-90 282 380)"/>'
    )


def _top_debtors(metrics: PropertyMetrics) -> str:
    rows = list(metrics.top_debtors)
    rows.extend([None] * (10 - len(rows)))
    out = [
        f'<rect x="72" y="170" width="1136" height="38" fill="{SECONDARY}"/>',
        f'<text x="88" y="196" font-size="18" font-weight="bold" fill="{PRIMARY}">房产</text>',
        f'<text x="228" y="196" font-size="18" font-weight="bold" fill="{PRIMARY}">业主</text>',
        f'<text x="480" y="196" font-size="18" font-weight="bold" fill="{PRIMARY}" text-anchor="end">欠费金额</text>',
        f'<text x="516" y="196" font-size="18" font-weight="bold" fill="{PRIMARY}">欠费原因</text>',
        f'<text x="936" y="196" font-size="18" font-weight="bold" fill="{PRIMARY}">欠费区间</text>',
        f'<line x1="72" y1="208" x2="1208" y2="208" stroke="{PRIMARY}" stroke-width="1"/>',
    ]
    for i, debtor in enumerate(rows):
        y0 = 208 + i * 38
        baseline = y0 + 26
        if i % 2 == 1:
            out.append(f'<rect x="72" y="{y0}" width="1136" height="38" fill="{TEXT}" fill-opacity="0.04"/>')
        out.append(f'<line x1="72" y1="{y0 + 38}" x2="1208" y2="{y0 + 38}" stroke="{DIVIDER}" stroke-width="1"/>')
        if debtor is None:
            continue
        amount_color = NEG if i == 0 else TEXT
        out.extend(
            [
                multiline_text(88, baseline, debtor.property, 18, TEXT, max_width=130, one_line=True),
                multiline_text(228, baseline, debtor.owner_display + "　", 15, TEXT, max_width=100, one_line=True),
                multiline_text(480, baseline, money(debtor.amount), 18, amount_color, max_width=150, font_family=MONO_FONT, anchor="end", one_line=True),
                multiline_text(516, baseline, debtor.reason, 18, TEXT, max_width=410, one_line=True),
                multiline_text(936, baseline, short_period(debtor.period), 15, MUTED, max_width=260, one_line=True),
            ]
        )
    return "".join(out)


def render_cover(m: PropertyMetrics) -> str:
    return (
        _root("cover")
        + f'<rect id="cover-field" data-pptx-role="decoration" x="0" y="0" width="690" height="720" fill="{PRIMARY}"/>'
        + f'<line id="cover-field-rule" data-pptx-role="decoration" x1="690" y1="0" x2="690" y2="720" stroke="{ACCENT}" stroke-width="2"/>'
        + f'<g id="cover-kpi" data-pptx-bounds="72 168 560 400">'
        f'<text x="96" y="236" font-size="18" fill="{ACCENT}" letter-spacing="2">{x(m.period)} · 经营分析</text>'
        f'<text x="96" y="400" font-family="{MONO_FONT}" font-size="96" font-weight="bold" fill="{BG}">{pct(m.comprehensive_rate)}</text>'
        f'<text x="96" y="452" font-family="{TITLE_FONT}" font-size="30" fill="{BG}">综合收缴率</text>'
        f'<line x1="96" y1="482" x2="360" y2="482" stroke="{ACCENT}" stroke-width="2"/>'
        f'<text x="96" y="526" font-size="18" fill="{SECONDARY}">当年实收 {money(m.current_paid)} 元 · 欠收 {money(m.current_unpaid)} 元</text>'
        f'</g>'
        + f'<g id="cover-title" data-pptx-bounds="760 154 440 310">'
        f'<text x="760" y="230" font-family="{TITLE_FONT}" font-size="44" font-weight="bold" fill="{PRIMARY}">收缴差距不在总量，'
        f'<tspan x="760" dy="60">而在少数户与两栋楼</tspan></text>'
        f'<line x1="760" y1="326" x2="980" y2="326" stroke="{ACCENT}" stroke-width="2"/>'
        f'<text x="760" y="374" font-size="24" fill="{TEAL}">{m.zero_count}户占{pct(m.zero_share,1)}</text>'
        f'</g>'
        + f'<g id="cover-meta" data-pptx-bounds="760 500 440 170">'
        f'<text x="760" y="530" font-family="{TITLE_FONT}" font-size="24" fill="{PRIMARY}">{x(m.project_name)}　</text>'
        f'<text x="760" y="600" font-size="24" fill="{TEXT}">{x(m.period)}物业收缴经营分析</text>'
        f'<text x="760" y="645" font-size="15" fill="{MUTED}">数据源：5张物业管理业务表</text>'
        f'</g></svg>'
    )


def render_summary(m: PropertyMetrics) -> str:
    return (
        _root("content")
        + _header("执行摘要", "三个数字定义当前收缴问题")
        + f'<g id="kpi-rate" data-pptx-bounds="72 180 320 360">'
        f'<use data-icon="tabler-outline/chart-bar" x="72" y="180" width="36" height="36" fill="{PRIMARY}" stroke-width="2"/>'
        f'<text x="72" y="258" font-size="24" fill="{MUTED}">综合收缴率</text>'
        f'<text x="72" y="360" font-family="{MONO_FONT}" font-size="72" font-weight="bold" fill="{PRIMARY}">{pct(m.comprehensive_rate)}</text>'
        f'<line x1="72" y1="392" x2="392" y2="392" stroke="{ACCENT}" stroke-width="2"/>'
        + multiline_text(72, 432, f"当年实收{money(m.current_paid)}元\n低于应收+往转入口径", 18, MUTED, max_width=300)
        + '</g>'
        + f'<g id="kpi-amount" data-pptx-bounds="412 180 456 360">'
        f'<use data-icon="tabler-outline/cash-banknote" x="412" y="180" width="36" height="36" fill="{NEG}" stroke-width="2"/>'
        f'<text x="412" y="258" font-size="24" fill="{MUTED}">欠收金额</text>'
        f'<text x="412" y="360" font-family="{MONO_FONT}" font-size="72" font-weight="bold" fill="{NEG}">{money(m.current_unpaid)}</text>'
        f'<text x="412" y="398" font-size="24" fill="{TEXT}">元</text>'
        + multiline_text(412, 438, f"仅按当年应收口径\n收缴率为{pct(m.current_rate)}", 18, MUTED, max_width=400)
        + '</g>'
        + f'<g id="kpi-cohort" data-pptx-bounds="888 180 320 360">'
        f'<use data-icon="tabler-outline/users" x="888" y="180" width="36" height="36" fill="{TEAL}" stroke-width="2"/>'
        f'<text x="888" y="258" font-size="24" fill="{MUTED}">零缴纳户数</text>'
        f'<text x="888" y="360" font-family="{MONO_FONT}" font-size="72" font-weight="bold" fill="{TEAL}">{m.zero_count}</text>'
        f'<text x="966" y="360" font-size="24" fill="{TEXT}">户</text>'
        f'<line x1="888" y1="392" x2="1208" y2="392" stroke="{ACCENT}" stroke-width="2"/>'
        + multiline_text(888, 432, f"贡献欠收{money(m.zero_unpaid)}元\n占全部欠收{pct(m.zero_share,1)}", 18, MUTED, max_width=280)
        + '</g>'
        + f'<g id="takeaway" data-pptx-bounds="72 570 1136 60">'
        f'<rect x="72" y="570" width="1136" height="60" fill="{SECONDARY}"/>'
        f'<rect x="72" y="570" width="6" height="60" fill="{ACCENT}"/>'
        f'<text x="104" y="608" font-size="24" fill="{PRIMARY}">先抓{m.zero_count}户与{x(m.low_group_compact)}，再做全量提醒</text></g>'
        + _footer("来源：5、房产收缴率明细表；4、欠款明细报表", "02")
    )


def render_base(m: PropertyMetrics) -> str:
    bars = []
    for i, name in enumerate(["1栋", "2栋", "3栋", "4栋", "5栋", "6栋"]):
        value = m.building_counts.get(name, 0)
        bars.append((name, value, value / 60 * 560))
    bars.append(("其他", m.other_count, m.other_count / 60 * 560))
    rows = _bar_rows(
        bars, 530, 550, 226, 45, 20, 0, [PRIMARY] * 6 + [TEAL],
        lambda v: f"{int(v)}", value_size=18,
    )
    return (
        _root("content")
        + _header(
            "项目基本盘",
            f"{m.property_count}户、{m.total_area/10000:.2f}万m²：成熟社区的收缴基本盘",
        )
        + f'<g id="base-total" data-pptx-bounds="72 180 320 380">'
        f'<use data-icon="tabler-outline/building-community" x="72" y="180" width="40" height="40" fill="{PRIMARY}" stroke-width="2"/>'
        f'<text x="72" y="300" font-family="{MONO_FONT}" font-size="96" font-weight="bold" fill="{PRIMARY}">{m.property_count}</text>'
        f'<text x="270" y="300" font-size="30" fill="{PRIMARY}">户</text>'
        f'<line x1="72" y1="330" x2="360" y2="330" stroke="{ACCENT}" stroke-width="2"/>'
        f'<text x="72" y="374" font-family="{MONO_FONT}" font-size="32" fill="{TEXT}">{m.total_area:,.2f} m²</text>'
        f'<text x="72" y="408" font-size="24" fill="{MUTED}">收费面积</text>'
        + multiline_text(72, 460, f"{m.occupied_count}户入住　\n{m.residential_count}套住宅　\n{m.virtual_count}套虚拟房　", 18, TEXT, max_width=300)
        + '</g>'
        + f'<g id="households-by-building" data-pptx-bounds="420 160 788 440">'
        f'<text x="420" y="200" font-size="24" fill="{MUTED}">楼栋户数</text>'
        f'<g id="households-by-building-chartArea">'
        f'<line x1="550" y1="220" x2="550" y2="540" stroke="{DIVIDER}" stroke-width="1"/>{rows}</g></g>'
        + f'<g id="base-note" data-pptx-bounds="72 602 1136 34">'
        f'<text x="72" y="628" font-size="24" fill="{PRIMARY}">3栋/6栋各{m.building_counts.get("3栋",0)}户，是收缴工作量的主要承载</text></g>'
        + _footer("来源：1、房产客户信息", "03")
    )


def render_collection(m: PropertyMetrics) -> str:
    annual = "".join(
        f'<text x="640" y="{288 + i*58}" font-size="24" fill="{TEXT}">{year}</text>'
        f'<text x="1208" y="{288 + i*58}" font-family="{MONO_FONT}" font-size="32" '
        f'fill="{NEG if year == "2025" else PRIMARY}" text-anchor="end">{pct(rate)}</text>'
        f'<line x1="640" y1="{306 + i*58}" x2="1208" y2="{306 + i*58}" stroke="{DIVIDER}" stroke-width="1"/>'
        for i, (year, rate) in enumerate(m.annual_rates)
    )
    fill = m.comprehensive_rate / 100 * 440
    return (
        _root("content")
        + _header("收缴差距", f"收缴率连续下行，当前综合口径{pct(m.comprehensive_rate)}")
        + f'<g id="collection-score" data-pptx-bounds="72 180 520 400">'
        f'<use data-icon="tabler-outline/chart-bar" x="72" y="180" width="36" height="36" fill="{PRIMARY}" stroke-width="2"/>'
        f'<text x="72" y="246" font-size="24" fill="{MUTED}">{x(m.period)}综合收缴率</text>'
        f'<text x="72" y="350" font-family="{MONO_FONT}" font-size="96" font-weight="bold" fill="{PRIMARY}">{pct(m.comprehensive_rate)}</text>'
        f'<line x1="72" y1="378" x2="592" y2="378" stroke="{DIVIDER}" stroke-width="1"/>'
        f'<g id="collection-progress"><g id="collection-progress-chartArea">'
        f'<rect x="100" y="392" width="440" height="20" fill="{SECONDARY}"/>'
        f'<rect x="100" y="392" width="{fill:.1f}" height="20" fill="{PRIMARY}"/>'
        f'<line x1="{100+fill:.1f}" y1="386" x2="{100+fill:.1f}" y2="418" stroke="{ACCENT}" stroke-width="2"/>'
        f'</g></g>'
        f'<text x="100" y="462" font-size="18" fill="{MUTED}">0%</text>'
        f'<text x="540" y="462" font-size="18" fill="{MUTED}" text-anchor="end">100%</text>'
        f'<text x="72" y="512" font-size="18" fill="{MUTED}">当年应收{money(m.current_due)}元 · 往年转入{money(m.prior_due)}元'
        f'<tspan x="72" dy="30">当年实收{money(m.current_paid)}元 · 欠收{money(m.current_unpaid)}元</tspan></text></g>'
        + f'<g id="annual-rates" data-pptx-bounds="640 180 568 400">'
        f'<text x="640" y="220" font-size="24" fill="{MUTED}">年度收缴率</text>'
        f'<line x1="640" y1="238" x2="1208" y2="238" stroke="{DIVIDER}" stroke-width="1"/>{annual}'
        f'<text x="640" y="570" font-size="18" fill="{MUTED}">2026年当前值：{pct(m.comprehensive_rate)}</text></g>'
        + f'<g id="collection-note" data-pptx-bounds="72 600 1136 30">'
        f'<text x="72" y="622" font-size="15" fill="{MUTED}">口径：综合口径含往年转入；仅按当年应收为{pct(m.current_rate)}</text></g>'
        + _footer("来源：2、应收信息；5、房产收缴率明细表", "04")
    )


def render_buildings(m: PropertyMetrics) -> str:
    items = []
    for b in m.building_metrics:
        color = TEAL if b.name == m.highest_building.name else (NEG if b.name in m.low_group_label else PRIMARY)
        items.append((b.name, b.rate, b.rate * 6.4))
    bars = []
    for i, (name, rate, width) in enumerate(items):
        y = 238 + i * 52
        color = TEAL if name == m.highest_building.name else (NEG if name in m.low_group_label else PRIMARY)
        bars.append(
            f'<text x="240" y="{y+19}" font-size="18" fill="{color if name in m.low_group_label else TEXT}" text-anchor="end">{x(name)}</text>'
            f'<rect x="260" y="{y}" width="{width:.1f}" height="24" fill="{color}"/>'
            f'<text x="{260+width-12:.1f}" y="{y+18}" font-family="{MONO_FONT}" font-size="18" '
            f'fill="{BG}" text-anchor="end">{pct(rate)}</text>'
        )
    unpaid = "".join(
        f'<text x="1208" y="{257+i*52}" font-family="{MONO_FONT}" font-size="24" '
        f'fill="{NEG if b.name in m.low_group_label else TEXT}" text-anchor="end">{money(b.unpaid)}</text>'
        for i, b in enumerate(m.building_metrics)
    )
    return (
        _root("content")
        + _header(
            "楼栋对比",
            f"{m.highest_building.name}{pct(m.highest_building.rate,1)}，{x(m.low_group_compact)}承压",
            42,
        )
        + f'<g id="building-collection" data-pptx-bounds="72 160 880 440">'
        f'<text x="72" y="200" font-size="24" fill="{MUTED}">楼栋收缴率</text>'
        f'<g id="building-collection-chartArea">'
        f'<line x1="260" y1="220" x2="260" y2="540" stroke="{DIVIDER}" stroke-width="1"/>{bars}</g></g>'
        + f'<g id="building-unpaid" data-pptx-bounds="972 160 236 440">'
        f'<text x="972" y="200" font-size="24" fill="{MUTED}">欠收金额</text>'
        f'<line x1="972" y1="218" x2="1208" y2="218" stroke="{DIVIDER}" stroke-width="1"/>{unpaid}'
        f'<line x1="972" y1="540" x2="1208" y2="540" stroke="{DIVIDER}" stroke-width="1"/>'
        f'<text x="972" y="574" font-size="18" fill="{MUTED}">{x(m.low_group_compact)}合计</text>'
        f'<text x="1208" y="574" font-family="{MONO_FONT}" font-size="24" fill="{NEG}" text-anchor="end">{money(m.building_56_unpaid)}</text></g>'
        + f'<g id="building-takeaway" data-pptx-bounds="72 602 1136 34">'
        f'<text x="72" y="628" font-size="24" fill="{PRIMARY}">{x(m.low_group_compact)}合计欠收{money(m.building_56_unpaid)}元，占全项目欠收{pct(m.building_56_share,1)}</text></g>'
        + _footer("来源：5、房产收缴率明细表", "05")
    )


def render_households(m: PropertyMetrics) -> str:
    total = m.full_count + m.zero_count + m.partial_count
    return (
        _root("content")
        + _header(
            "户数结构",
            f"{m.full_count}户当年全额缴纳，{m.zero_count}户零缴纳，欠收高度集中",
            42,
        )
        + f'<g id="household-status" data-pptx-bounds="72 170 420 420"><g id="household-status-chartArea">{_donut(m)}</g>'
        f'<text x="282" y="390" font-family="{MONO_FONT}" font-size="32" font-weight="bold" fill="{PRIMARY}" text-anchor="middle">{total}户</text></g>'
        + f'<g id="household-readout" data-pptx-bounds="540 170 668 420">'
        f'<text x="540" y="218" font-size="24" fill="{MUTED}">当年收缴状态</text>'
        f'<line x1="540" y1="236" x2="1208" y2="236" stroke="{DIVIDER}" stroke-width="1"/>'
        f'<text x="540" y="306" font-family="{TITLE_FONT}" font-size="72" font-weight="bold" fill="{PRIMARY}">{m.full_count}</text>'
        f'<text x="700" y="300" font-size="24" fill="{TEXT}">户全额缴纳</text>'
        f'<text x="700" y="330" font-size="18" fill="{MUTED}">当年应收已覆盖</text>'
        f'<line x1="540" y1="352" x2="1208" y2="352" stroke="{DIVIDER}" stroke-width="1"/>'
        f'<text x="540" y="420" font-family="{TITLE_FONT}" font-size="72" font-weight="bold" fill="{NEG}">{m.zero_count}</text>'
        f'<text x="700" y="414" font-size="24" fill="{TEXT}">户零缴纳</text>'
        f'<text x="700" y="444" font-size="18" fill="{MUTED}">贡献{money(m.zero_unpaid)}元，占欠收{pct(m.zero_share,1)}</text>'
        f'<line x1="540" y1="466" x2="1208" y2="466" stroke="{DIVIDER}" stroke-width="1"/>'
        f'<text x="540" y="534" font-family="{TITLE_FONT}" font-size="72" font-weight="bold" fill="{TEAL}">{m.partial_count}</text>'
        f'<text x="700" y="528" font-size="24" fill="{TEXT}">户部分缴纳</text>'
        f'<text x="700" y="558" font-size="18" fill="{MUTED}">剩余欠收分散在部分缴纳与往转</text></g>'
        + f'<g id="household-note" data-pptx-bounds="72 600 1136 30">'
        f'<text x="72" y="622" font-size="15" fill="{MUTED}">口径：户数按当年收缴率状态；金额按综合口径</text></g>'
        + _footer("来源：5、房产收缴率明细表", "06")
    )


def render_reasons(m: PropertyMetrics) -> str:
    reasons = list(m.arrears_reasons[:5])
    rest = m.arrears_reasons[5:]
    if rest:
        reasons.append(ReasonMetric("其他", sum(r.amount for r in rest), sum(r.share for r in rest)))
    max_amount = max((r.amount for r in reasons), default=1.0)
    bars = []
    for i, reason in enumerate(reasons):
        y = 230 + i * 50
        width = reason.amount / max_amount * 680 if max_amount else 0
        color = PRIMARY if i == 0 else (NEG if i == 1 else (TEAL if i < 4 else MUTED))
        bars.append(
            f'<text x="300" y="{y+19}" font-size="18" fill="{TEXT}" text-anchor="end">{x(reason.name if len(reason.name) <= 6 else reason.name[:6])}</text>'
            f'<rect x="320" y="{y}" width="{width:.1f}" height="20" fill="{color}"/>'
            f'<text x="{320+width+10:.1f}" y="{y+18}" font-family="{MONO_FONT}" font-size="18" '
            f'fill="{color}">{money(reason.amount)}</text>'
        )
    top_two = m.top_reason.share + m.second_reason.share
    return (
        _root("content")
        + _header(
            "欠费结构",
            f"欠费主因：质量遗留与恶意/困难{pct(top_two,1)}",
            42,
        )
        + f'<g id="arrears-reasons" data-pptx-bounds="72 170 1136 360"><g id="arrears-reasons-chartArea">'
        f'<line x1="320" y1="220" x2="320" y2="520" stroke="{DIVIDER}" stroke-width="1"/>{bars}</g></g>'
        + f'<g id="reasons-actions" data-pptx-bounds="72 550 1136 70">'
        f'<line x1="72" y1="556" x2="1208" y2="556" stroke="{ACCENT}" stroke-width="2"/>'
        f'<text x="72" y="600" font-size="24" fill="{PRIMARY}">质量遗留 → 先销项，再催缴</text>'
        f'<text x="640" y="600" font-size="24" fill="{NEG}">恶意/困难 → 分类处置，依法追缴</text></g>'
        + _footer("来源：4、欠款明细报表", "07")
    )


def render_top_debtors(m: PropertyMetrics) -> str:
    return (
        _root("content")
        + _header("重点欠费户", f"前10户欠费{m.top10_total/10000:.2f}万元，占全部欠费{pct(m.top10_share,1)}")
        + f'<g id="top-debtors" data-pptx-bounds="72 170 1136 418">{_top_debtors(m)}</g>'
        + _footer("来源：4、欠款明细报表", "08")
    )


def render_channels(m: PropertyMetrics) -> str:
    channels = [c for c in m.channels if c.name != "其他"][:4]
    colors = [PRIMARY, ACCENT, TEAL, MUTED]
    segments = []
    pos = 100.0
    for i, c in enumerate(channels):
        width = c.share / 100 * 1080
        segments.append(f'<rect x="{pos:.1f}" y="270" width="{width:.1f}" height="60" fill="{colors[i]}"/>')
        pos += width
    readout = []
    positions = [72, 350, 628, 906]
    for i, c in enumerate(channels):
        px = positions[i]
        readout.append(
            f'<text x="{px}" y="420" font-size="24" fill="{TEXT}">{x(c.name)}</text>'
            f'<text x="{px}" y="452" font-family="{MONO_FONT}" font-size="24" fill="{colors[i]}">{money(c.amount)}</text>'
            f'<text x="{px}" y="480" font-size="18" fill="{MUTED}">{pct(c.share,1)}</text>'
        )
    cash = next((c for c in m.channels if c.name == "现金"), ChannelMetric("现金"))
    top = m.top_channel
    return (
        _root("content")
        + _header("收款渠道", f"2025年{pct(top.share,1)}的收款来自{top.name}，现金仍占{pct(cash.share,1)}")
        + f'<g id="payment-channels" data-pptx-bounds="72 220 1136 160"><g id="payment-channels-chartArea">{"".join(segments)}</g></g>'
        + f'<g id="channel-readout" data-pptx-bounds="72 390 1136 100">{"".join(readout)}</g>'
        + f'<g id="channel-actions" data-pptx-bounds="72 510 1136 126">'
        f'<line x1="72" y1="516" x2="1208" y2="516" stroke="{ACCENT}" stroke-width="2"/>'
        + multiline_text(72, 556, "微信主渠道 → 线上对账优先", 18, PRIMARY, max_width=1100, one_line=True)
        + multiline_text(72, 588, "现金渠道 → 当日日结", 18, TEXT, max_width=1100, one_line=True)
        + multiline_text(72, 620, "退款/预收 → 月度复核", 18, TEXT, max_width=1100, one_line=True)
        + '</g>'
        + _footer(f"来源：3、缴费数据；2025年收款合计{money(m.channel_total)}元", "09")
    )


def render_actions(m: PropertyMetrics) -> str:
    return (
        _root("ending")
        + _header("行动", "下一步：按楼栋、按原因、按户分层清欠")
        + f'<g id="action-buildings" data-pptx-bounds="72 170 260 390">'
        f'<use data-icon="tabler-outline/building-community" x="72" y="180" width="36" height="36" fill="{PRIMARY}" stroke-width="2"/>'
        f'<text x="72" y="280" font-family="{TITLE_FONT}" font-size="72" font-weight="bold" fill="{ACCENT}">01</text>'
        f'<text x="72" y="320" font-size="24" font-weight="bold" fill="{PRIMARY}">楼栋</text>'
        + multiline_text(72, 360, f"{m.low_group_compact}逐户核对\n欠费、退款、预收抵扣\n建立周度清欠看板", 18, TEXT, max_width=240)
        + f'<text x="72" y="540" font-size="15" fill="{MUTED}">项目经理 / 客服</text></g>'
        + f'<g id="action-households" data-pptx-bounds="352 170 260 390">'
        f'<use data-icon="tabler-outline/users" x="352" y="180" width="36" height="36" fill="{NEG}" stroke-width="2"/>'
        f'<text x="352" y="280" font-family="{TITLE_FONT}" font-size="72" font-weight="bold" fill="{ACCENT}">02</text>'
        f'<text x="352" y="320" font-size="24" font-weight="bold" fill="{PRIMARY}">重点户</text>'
        + multiline_text(352, 360, f"前10户/{m.zero_count}户清零\n清单化；质量先销项\n恶意/困难分类处置\n失联户补联系方式", 18, TEXT, max_width=240)
        + f'<text x="352" y="540" font-size="15" fill="{MUTED}">项目经理 / 法务 / 客服</text></g>'
        + f'<g id="action-quality" data-pptx-bounds="632 170 260 390">'
        f'<use data-icon="tabler-outline/alert-triangle" x="632" y="180" width="36" height="36" fill="{WARN}" stroke-width="2"/>'
        f'<text x="632" y="280" font-family="{TITLE_FONT}" font-size="72" font-weight="bold" fill="{ACCENT}">03</text>'
        f'<text x="632" y="320" font-size="24" font-weight="bold" fill="{PRIMARY}">质量遗留</text>'
        + multiline_text(632, 360, "地产/工程销项\n问题清单\n修复与催缴同步\n避免带病催收", 18, TEXT, max_width=240)
        + f'<text x="632" y="540" font-size="15" fill="{MUTED}">工程 / 地产接口人</text></g>'
        + f'<g id="action-cash" data-pptx-bounds="912 170 296 390">'
        f'<use data-icon="tabler-outline/cash-banknote" x="912" y="180" width="36" height="36" fill="{TEAL}" stroke-width="2"/>'
        f'<text x="912" y="280" font-family="{TITLE_FONT}" font-size="72" font-weight="bold" fill="{ACCENT}">04</text>'
        f'<text x="912" y="320" font-size="24" font-weight="bold" fill="{PRIMARY}">收款运营</text>'
        + multiline_text(912, 360, "微信主渠道保持\n现金当日对账\n退款/预收抵扣\n月度复核", 18, TEXT, max_width=270)
        + f'<text x="912" y="540" font-size="15" fill="{MUTED}">财务 / 收费员</text></g>'
        + f'<g id="closing" data-pptx-bounds="72 570 1136 64">'
        f'<line x1="72" y1="576" x2="1208" y2="576" stroke="{ACCENT}" stroke-width="2"/>'
        f'<text x="72" y="620" font-size="28" fill="{PRIMARY}">先抓结构，再抓总量：{m.zero_count}户与{x(m.low_group_compact)}是下一阶段的唯一优先级。</text></g>'
        + _footer("", "10")
    )


PAGE_RENDERERS = [
    ("01_cover.svg", render_cover, "cover"),
    ("02_summary.svg", render_summary, "content"),
    ("03_base.svg", render_base, "content"),
    ("04_collection.svg", render_collection, "content"),
    ("05_buildings.svg", render_buildings, "content"),
    ("06_households.svg", render_households, "content"),
    ("07_reasons.svg", render_reasons, "content"),
    ("08_top_debtors.svg", render_top_debtors, "content"),
    ("09_channels.svg", render_channels, "content"),
    ("10_actions.svg", render_actions, "ending"),
]


def render_deck(metrics: PropertyMetrics, out_dir: str | Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, renderer, _role in PAGE_RENDERERS:
        path = out_dir / filename
        path.write_text(renderer(metrics), encoding="utf-8")
        written.append(path)
    return written


def render_notes(m: PropertyMetrics) -> str:
    return f"""# 01_封面

这份材料先给结论：{m.project_name}{m.period}综合收缴率为{pct(m.comprehensive_rate)}，欠收{money(m.current_unpaid)}元。差距并不平均分布，而是集中在{m.zero_count}户零缴纳家庭和{x(m.low_group_compact)}两个楼栋。综合收缴率按当年实收除以当年应收加往年转入计算，后面会同时给出仅按当年应收计算的口径。

---

# 02_执行摘要

三个数字可以概括当前问题：{pct(m.comprehensive_rate)}的综合收缴率、{money(m.current_unpaid)}元欠收、{m.zero_count}户零缴纳。零缴纳户贡献了{money(m.zero_unpaid)}元欠收，占全部欠收的{pct(m.zero_share,1)}。如果只看当年应收，收缴率是{pct(m.current_rate)}，但结构性问题没有改变。下一阶段的动作应先锁定这{m.zero_count}户和{x(m.low_group_compact)}，再做全量提醒。

---

# 03_项目基本盘

{m.project_name}共有{m.property_count}套房产，收费面积{m.total_area:,.2f}平方米，其中{m.occupied_count}套已入住，{m.residential_count}套为多层住宅，另有{m.virtual_count}套虚拟房产。楼栋户数并不均衡，3栋和6栋各有{m.building_counts.get("3栋",0)}户，1栋{m.building_counts.get("1栋",0)}户，2栋、4栋、5栋各{m.building_counts.get("2栋",0)}户。3栋和6栋因此承担了更大的收缴工作量。

---

# 04_当前收缴

年度收缴率从2021年的{pct(m.annual_rates[0][1] if m.annual_rates else 0)}降到2025年的{pct(m.annual_rates[-1][1] if m.annual_rates else 0)}，当前综合口径为{pct(m.comprehensive_rate)}。今年当年应收{money(m.current_due)}元，往年转入{money(m.prior_due)}元，当年实收{money(m.current_paid)}元，欠收{money(m.current_unpaid)}元。仅按当年应收计算，收缴率是{pct(m.current_rate)}。这组数字说明，当前差距需要从楼栋和住户结构里找原因。

---

# 05_楼栋对比

楼栋之间的差距很清晰。{m.highest_building.name}收缴率{pct(m.highest_building.rate)}，{x(m.low_group_compact)}处于最低区间。{x(m.low_group_compact)}合计欠收{money(m.building_56_unpaid)}元，占全项目欠收的{pct(m.building_56_share,1)}。因此，资源不应平均铺开，{x(m.low_group_compact)}应作为第一优先级，逐户核对欠费、退款和预收抵扣。

---

# 06_户数结构

从当年收缴状态看，{m.full_count}户已全额缴纳，{m.zero_count}户零缴纳，{m.partial_count}户部分缴纳。{m.zero_count}户零缴纳贡献了{money(m.zero_unpaid)}元欠收，占全部欠收的{pct(m.zero_share,1)}。这说明全量提醒的边际效果有限，真正需要的是把这{m.zero_count}户清单化、责任到人。

---

# 07_欠费原因

全部欠费为{money(m.arrears_total)}元，涉及47户。房屋质量问题或地产遗留设备问题占{money(m.top_reason.amount)}元，比例{pct(m.top_reason.share,1)}；无理由、恶意拒交或经济困难占{money(m.second_reason.amount)}元，比例{pct(m.second_reason.share,1)}。两类合计{pct(m.top_reason.share+m.second_reason.share,1)}，但处理方式完全不同。质量遗留要先销项、再催缴，恶意或经济困难则要分类处置、依法追缴。

---

# 08_重点欠费户

前10户合计欠费{money(m.top10_total)}元，占全部欠费的{pct(m.top10_share,1)}。其中6栋有{m.top10_building_count}户，质量遗留原因有{m.top10_quality_count}户。这份名单适合直接转为责任清单，按户明确跟进人、原因和处理节点。

---

# 09_收款渠道

2025年收款合计{money(m.channel_total)}元，其中{m.top_channel.name}{money(m.top_channel.amount)}元，占{pct(m.top_channel.share,1)}；现金{next((c.amount for c in m.channels if c.name == "现金"),0):,.2f}元，占{pct(next((c.share for c in m.channels if c.name == "现金"),0),1)}。线上收款已经是主渠道，但现金仍是重要渠道。收款运营要同时做好线上对账和现金日结，并对退款和预收抵扣做月度复核。

---

# 10_行动

下一步按四条线推进：第一，{x(m.low_group_compact)}逐户核对欠费、退款和预收抵扣，建立周度清欠看板；第二，把前10户和{m.zero_count}户零缴纳清单化，质量遗留先销项，恶意或困难分类处置，失联户补联系方式；第三，与地产和工程建立质量问题销项清单，修复与催缴同步；第四，保持微信主渠道，现金当日对账，退款和预收抵扣月度复核。先抓结构，再抓总量，{m.zero_count}户与{x(m.low_group_compact)}就是下一阶段的唯一优先级。
"""
