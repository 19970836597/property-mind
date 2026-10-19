# -*- coding: utf-8 -*-
"""段落内容生成（Section Builders）—— 「第 N 页该长成什么样」

为什么独立
----------
``_build_slide`` 曾是一个 299 行的巨型 if/elif 链：解包上下文、解析数据来源、
诚实降级、画指标图、再按 key/layout 分十几种段落 —— 全挤在一个函数里。
读它需要同时记住 7 个局部变量和 14 个分支，改一处极易误伤另一处。

现在拆成：
    · ``resolve_section_data``  段落自己声明的数据来源
    · ``build_metrics_chart``   指标对比图（含单值 → KPI 卡片的降级）
    · 若干 ``build_xxx``        每种段落一个函数，输入输出都是明牌
    · ``deck_content_tool._build_slide`` 只剩调度

依赖方向
--------
本模块**不** import ``deck_content_tool``（避免循环依赖）。
通用能力从 ``table_profile`` / ``deck_format`` 取；
``aggregate`` / ``resolve_metric`` / ``single_metric_card`` 因为属于
"内容组装"，也放在这里，由 ``deck_content_tool`` 反向引用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .deck_format import fmt_money as _fmt_money
from .deck_format import fmt_wan as _fmt_wan
from .deck_format import short_label as _short_label
from .table_profile import primary_value_column as _primary_value_column
from .table_io import pd

__all__ = [
    "MAX_GROUPS",
    "aggregate",
    "resolve_metric",
    "single_metric_card",
    "resolve_section_data",
    "build_metrics_chart",
    "build_cover",
    "build_summary",
    "build_grouped_chart",
    "build_cards",
    "build_kpi",
    "build_table",
    "build_appendix",
    "build_text",
]

#: 每页最多展示多少组
MAX_GROUPS = 8


def aggregate(df, group_column: Optional[str], value_column: Optional[str]
              ) -> List[Dict[str, Any]]:
    """按分组列做聚合，返回降序列表。"""
    if not (group_column and value_column) or df is None or pd is None:
        return []
    try:
        if group_column not in df.columns or value_column not in df.columns:
            return []
        sub = df.copy()
        sub[value_column] = pd.to_numeric(sub[value_column], errors="coerce")
        keys = sub[group_column].astype(str).str.strip()
        agg = sub.assign(_k=keys).groupby("_k")[value_column].sum()
        agg = agg.sort_values(ascending=False).head(MAX_GROUPS)
        return [{"name": str(k), "value": float(v)} for k, v in agg.items()]
    except Exception:
        return []


def resolve_metric(name: str, derived: Dict[str, Any],
                   metrics: Dict[str, Any], df) -> Optional[float]:
    """按名字取一个标量指标。

    查找顺序（先算出来的、再统计出来的、最后才是整列求和）：
        ① 派生指标（spec 的 derived 段算出来的）
        ② metrics 里的合计（自动识别的主指标列）
        ③ 数据里存在的列 → 整列求和
    """
    if not name:
        return None
    if name in (derived or {}):
        try:
            return float(derived[name])
        except (TypeError, ValueError):
            return None
    if name in (metrics or {}) and isinstance(metrics[name], dict):
        try:
            return float(metrics[name].get("sum"))
        except (TypeError, ValueError):
            return None
    if df is not None and name in getattr(df, "columns", []):
        try:
            return float(pd.to_numeric(df[name], errors="coerce").sum())
        except Exception:
            return None
    return None


def single_metric_card(chart_cfg: Dict[str, Any], entries, label: str,
                       value: float, status: str) -> Dict[str, Any]:
    """单个指标 → KPI 卡片（而不是一根孤零零的柱子）。

    支持目标值对比：声明 ``target`` 后会自动算出差距、判定状态，
    并写进卡片说明里。单值没有参照系就没有意义，所以这一步很重要。
    """
    entry = {}
    for candidate in entries or []:
        if isinstance(candidate, dict) and str(candidate.get("label") or "") == label:
            entry = candidate
            break

    fmt = entry.get("format") or chart_cfg.get("format") or "{:,.2f}"
    unit = str(entry.get("unit") or "")
    try:
        shown = fmt.format(float(value))
    except Exception:
        shown = f"{value:,.2f}"
    if unit:
        shown = f"{shown} {unit}"

    resolved_status = status or "info"
    delta_parts: List[str] = []

    target = entry.get("target")
    if target is None:
        target = chart_cfg.get("target")
    if target is not None:
        try:
            target = float(target)
        except (TypeError, ValueError):
            target = None
    if target is not None:
        gap = float(value) - target
        target_text = fmt.format(target)
        relation = "高于" if gap > 0 else ("低于" if gap < 0 else "持平")
        if gap == 0:
            delta_parts.append(f"目标 {target_text}（持平）")
        else:
            pct = f"{abs(gap) / abs(target) * 100:.1f}%" if target else "—"
            delta_parts.append(f"目标 {target_text}（{relation} {pct}）")
        if resolved_status == "info":
            # 目标方向：默认"越高越好"，可用 higher_is_better: false 反转
            higher_better = entry.get("higher_is_better",
                                      chart_cfg.get("higher_is_better", True))
            good = gap >= 0 if higher_better else gap <= 0
            near = abs(gap) <= abs(target) * 0.05 if target else False
            resolved_status = "ok" if good else ("warn" if near else "bad")

    note = entry.get("note") or chart_cfg.get("note")
    if note:
        delta_parts.append(str(note))

    return {
        "type": "kpi_grid",
        "columns": 1,
        "items": [{
            "label": label,
            "value": shown,
            "delta": " · ".join(delta_parts),
            "status": resolved_status,
        }],
    }


# =====================================================================
# 数据来源
# =====================================================================

def resolve_section_data(item: Dict[str, Any], df,
                         value_column: Optional[str],
                         group_column: Optional[str],
                         grouped: List[Dict[str, Any]]
                         ) -> Tuple[Optional[str], Optional[str],
                                    List[Dict[str, Any]], List[str]]:
    """按段落声明的 ``data`` 重新取数。

    关键：不同段落需要**不同的列**。用一套全局分组打天下
    会导致"收入项的科目被当成成本科目"这种语义错误。

    Returns:
        (value_column, group_column, grouped, data_missing)
    """
    section_data = item.get("data") if isinstance(item.get("data"), dict) else None
    missing: List[str] = []
    if not section_data:
        return value_column, group_column, grouped, missing

    sec_value = section_data.get("value_column")
    sec_group = section_data.get("group_column")
    for role, col in (("数值列", sec_value), ("分组列", sec_group)):
        if col and col not in df.columns:
            missing.append(f"{role}「{col}」")
    if missing:
        return value_column, group_column, grouped, missing

    value_column = sec_value or value_column
    group_column = sec_group or group_column
    return value_column, group_column, aggregate(df, group_column, value_column), missing


# =====================================================================
# 指标对比图（从派生标量画，而不是按列分组）
# =====================================================================

def build_metrics_chart(chart_cfg: Dict[str, Any], derived: Dict[str, Any],
                        metrics: Dict[str, Any], df
                        ) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
    """把 spec 声明的 ``metrics_chart`` 变成 blocks。

    为什么需要：原来图表只能"按某列分组聚合"。于是「收缴率」页只能画
    "未缴金额 by 收费项" —— 标题说收缴率、图画收费项构成，语义不符。
    应该画的是「应收 / 实收 / 未缴」三个**标量**的对比。

    Returns:
        (blocks, title 或 None, 是否已生成图)
    """
    if not (isinstance(chart_cfg, dict) and chart_cfg.get("items")):
        return [], None, False

    labels: List[str] = []
    values: List[float] = []
    colors: List[str] = []
    for entry in chart_cfg["items"]:
        if not isinstance(entry, dict):
            continue
        metric = str(entry.get("metric") or "")
        value = resolve_metric(metric, derived, metrics, df)
        if value is None:
            continue
        try:
            scale = float(entry.get("scale") or 1) or 1.0
        except (TypeError, ValueError):
            scale = 1.0
        labels.append(str(entry.get("label") or metric))
        values.append(round(float(value) / scale, 4))
        colors.append(str(entry.get("status") or ""))

    if not values:
        return [], None, False

    title = str(chart_cfg["title"]) if chart_cfg.get("title") else None

    # 只有一个指标时**不要画图** —— 一根柱子横跨整页视觉很弱，
    # 而且"单值 vs 坐标轴"没有可比性。改成 KPI 卡片：
    # 数值 + 目标对比，信息密度反而更高。
    if len(values) == 1:
        return [single_metric_card(chart_cfg, chart_cfg["items"],
                                   labels[0], values[0], colors[0])], title, True

    block: Dict[str, Any] = {
        "type": "chart",
        "chart": str(chart_cfg.get("chart") or "column"),
        "categories": labels,
        "series": [{"name": str(chart_cfg.get("series_name") or "数值"),
                    "values": values}],
    }
    if chart_cfg.get("number_format"):
        block["number_format"] = chart_cfg["number_format"]
    if chart_cfg.get("axis_format"):
        block["axis_format"] = chart_cfg["axis_format"]
    if any(colors):
        block["point_colors"] = [c or "info" for c in colors]
    if chart_cfg.get("height_inches"):
        block["height_inches"] = chart_cfg["height_inches"]
    return [block], title, True


# =====================================================================
# 各段落
# =====================================================================

def build_cover(metrics: Dict[str, Any], grouped: List[Dict[str, Any]],
                total: float, profile_name: str
                ) -> Tuple[List[Dict[str, Any]], str]:
    """封面：只放**有意义的**几个数，不罗列列名。

    踩过的坑：原来用 metrics 的前 3 个键，结果封面只有一张
    "记录数 15,876 / 数据规模" —— 因为金额列被错误过滤掉了。
    """
    items = [{"label": "数据规模", "value": f"{metrics['rows']:,} 条",
              "delta": "本次分析样本", "status": "info"}]

    primary = _primary_value_column(metrics)
    if primary:
        items.append({
            "label": _short_label(primary),
            "value": _fmt_wan(metrics[primary]["sum"]),
            "delta": "全部记录合计",
            "status": "info",
        })
    if grouped and total:
        top = grouped[0]
        share = top["value"] / total * 100
        items.append({
            "label": "最大构成项",
            "value": f"{share:.1f}%",
            "delta": str(top["name"])[:16],
            "status": "warn" if share > 90 else "ok",
        })
    # 刻意**不**凑第四张卡：原来把"第二大金额列"当「另一口径」放上来，
    # 封面于是出现「应收 48.43 万 / 另一口径」这种无信息量的卡片 ——
    # 看的人根本不知道那是什么口径。封面只放说得清的三个数。
    blocks = [{
        "type": "kpi_grid",
        "columns": min(3, max(1, len(items))),
        "items": items[:3],
    }]
    return blocks, profile_name


def build_summary(metrics: Dict[str, Any], grouped: List[Dict[str, Any]],
                  total: float) -> Tuple[List[Dict[str, Any]], str]:
    """一页纸结论：三张卡。"""
    items = [{"label": "数据规模", "value": f"{metrics['rows']:,} 条",
              "delta": "", "status": "info"}]
    primary = _primary_value_column(metrics)
    if primary:
        items.append({
            "label": _short_label(primary),
            "value": _fmt_wan(metrics[primary]["sum"]),
            "delta": f"单条均值 {_fmt_wan(metrics[primary]['mean'])}",
            "status": "info",
        })
    if grouped and total:
        top = grouped[0]
        share = top["value"] / total * 100
        items.append({
            "label": "最大构成项",
            "value": f"{share:.1f}%",
            "delta": str(top["name"])[:16],
            "status": "warn" if share > 90 else "ok",
        })

    so_what = ""
    if grouped:
        top = grouped[0]
        share = (top["value"] / total * 100) if total else 0
        so_what = (f"{top['name']} 占比 {share:.1f}%，是最大构成项，"
                   "下钻时优先看它的驱动因素。")
    blocks = [{"type": "kpi_grid",
               "columns": min(3, max(1, len(items))),
               "items": items[:3]}]
    return blocks, so_what


def scale_amounts(values: List[float]) -> Tuple[List[float], str, str, float]:
    """按量级选单位，返回 (换算后的值, 单位, 数字格式, 除数)。

    为什么必须换算
    --------------
    原来一律按「元」出图：收入页最大值 482,652.80 → 坐标轴刻度是
    ``100000 200000 … 600000`` 的裸数字，看的人得自己心算，而且轴顶
    （600000）比最大值高出 24%，右侧白掉一大片。
    汇报纪律本来就要求「全篇统一万元」（见 SKILL.md 阶段 1.1），
    这里按最大值量级自动升到万元/亿元，图表与结论句口径就一致了。
    """
    peak = max((abs(float(v or 0)) for v in values), default=0.0)
    if peak >= 1e8:
        div, unit, fmt = 1e8, "亿元", "#,##0.00"
    elif peak >= 1e4:
        div, unit, fmt = 1e4, "万元", "#,##0.0"
    else:
        return [round(float(v or 0), 2) for v in values], "元", "#,##0", 1.0
    return [round(float(v or 0) / div, 2) for v in values], unit, fmt, div


def build_grouped_chart(grouped: List[Dict[str, Any]],
                        value_column: Optional[str], total: float,
                        metrics_chart_used: bool
                        ) -> Tuple[List[Dict[str, Any]], str]:
    """按分组列的图表。

    已经用 metrics_chart 画了指标对比图 → 不再叠加分组图，
    否则一页两张图，且第二张（按列分组）与本页语义无关。
    """
    if metrics_chart_used:
        return [], ""

    if not (grouped and value_column):
        return [{
            "type": "text",
            "text": "未识别到可用于图表的「分组列 + 数值列」，"
                    "请用 group_column / value_column 指定。",
            "style": "quote",
        }], ""

    shown, unit, fmt, _div = scale_amounts([g["value"] for g in grouped])
    blocks = [{
        "type": "chart",
        "chart": "bar" if len(grouped) <= 6 else "column",
        "categories": [g["name"] for g in grouped],
        "series": [{"name": f"{value_column}（{unit}）", "values": shown}],
        "unit": unit,
        "number_format": fmt,
        "axis_format": fmt,
    }]
    top = grouped[0]
    share = (top["value"] / total * 100) if total else 0
    so_what = (f"{top['name']} 合计 {top['value']:,.2f}"
               f"（占比 {share:.1f}%），是该项最大来源。")
    return blocks, so_what


def build_cards(grouped: List[Dict[str, Any]], total: float
                ) -> Tuple[List[Dict[str, Any]], str]:
    """行动/归因卡片。"""
    cards = []
    for g in grouped[:4]:
        share = (g["value"] / total * 100) if total else 0
        cards.append({
            "title": g["name"],
            "body": f"{g['value']:,.2f}（占比 {share:.1f}%）"
                    " · 需补责任人与时限",
            "status": "warn" if share > 20 else "info",
        })
    if not cards:
        cards = [{"title": "待补充", "body": "缺少分组数据", "status": "warn"}]
    return ([{"type": "cards", "columns": 2, "items": cards}],
            "每项都需落到责任部门与时限，否则无法形成任务。")


def build_kpi(metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
    """KPI 页：罗列数值列的合计。"""
    items = [{"label": c[:14], "value": _fmt_wan(metrics[c]["sum"]),
              "delta": f"记录 {metrics[c]['non_null']:,} 条",
              "status": "info"}
             for c in list(metrics.keys())[1:7]]
    if items:
        return [{"type": "kpi_grid", "columns": 3, "items": items}]
    return [{"type": "text", "text": "未识别到数值列。", "style": "quote"}]


def build_table(grouped: List[Dict[str, Any]], value_column: Optional[str],
                total: float) -> List[Dict[str, Any]]:
    """明细表：分组 / 数值 / 占比。"""
    headers = ["分组", f"{value_column or '数值'}", "占比"]
    rows = []
    for g in grouped:
        share = (g["value"] / total * 100) if total else 0
        rows.append([g["name"], f"{g['value']:,.2f}", f"{share:.1f}%"])
    if rows:
        return [{"type": "table", "headers": headers, "rows": rows,
                 "weights": [2, 1, 1]}]
    return [{"type": "text", "text": "无明细可列。", "style": "quote"}]


def build_appendix(df) -> List[Dict[str, Any]]:
    """附录：列出数据列，便于溯源。"""
    cols = [str(c) for c in list(df.columns)[:10]]
    return [{"type": "bullets", "items": [f"数据列：{c}" for c in cols]}]


def build_text(metrics: Dict[str, Any], period: str) -> List[Dict[str, Any]]:
    """纯文字页：把可用信息列出来。"""
    return [{
        "type": "bullets",
        "items": [
            f"记录数 {metrics['rows']:,}",
            f"数值列：{', '.join(list(metrics.keys())[1:7]) or '无'}",
            f"期间：{period or '未识别'}",
        ],
    }]
