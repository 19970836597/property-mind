# -*- coding: utf-8 -*-
"""表格 → ppt-skill 契约 JSON

把任意「有日期列 + 有金额列」的表格，映射成 ppt-skill 要求的字段结构。

为什么要单独一个模块
--------------------
* ``data_analysis`` 工具的 ``contract`` action 要用它
* ``make_deck_from_excel.py`` 命令行也要用它
两处逻辑必须一致，否则同一份数据两条路径出来的 PPT 不一样。

⚠ 关键口径（最容易错）
--------------------
* **金额单位：万元。** 原始表多是「元」，直接传会得到
  ``120606432.76 万元`` 这种荒唐数字。
* **比率用比值。** ``0.415`` 表示 41.5%；传 ``41.5`` 会被渲染成 4150%。
* 收入/成本必须落到 ppt_skill 认识的固定科目上，否则图表标签与配色对不上。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "INCOME_SLOTS",
    "COST_SLOTS",
    "YUAN_PER_WAN",
    "fit_slots",
    "frame_to_contract",
]

INCOME_SLOTS = ("物业费", "公共收益", "多种经营", "其他")
COST_SLOTS = ("人力成本", "公共能耗", "外包服务费", "维修维保",
              "物料耗材", "管理费及其他")

YUAN_PER_WAN = 10000.0


def fit_slots(items: Dict[str, float], slots) -> Dict[str, float]:
    """把任意数量的科目压进固定槽位。

    多的合并进最后一个槽位（其他 / 管理费及其他），少的补 0。
    """
    ordered = sorted(items.items(), key=lambda kv: kv[1], reverse=True)
    result: Dict[str, float] = {}
    for i, slot in enumerate(slots):
        if i < len(slots) - 1:
            result[slot] = round(ordered[i][1], 2) if i < len(ordered) else 0.0
        else:
            result[slot] = round(sum(v for _k, v in ordered[len(slots) - 1:]), 2)
    return result


def _is_text_series(series) -> bool:
    """判断是否为文本列。

    ⚠ pandas 3.0 起文本列 dtype 是 ``str``，不再是 ``object``。
    只判断 ``object`` 会让所有文本列识别失败（实测分组列全丢）。
    """
    import pandas as pd

    try:
        if pd.api.types.is_string_dtype(series.dtype):
            return True
    except Exception:
        pass
    return series.dtype == object or str(series.dtype) in ("str", "string")


def _pick_columns(df) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """自动推断 日期列 / 金额列 / 分组列。

    顺序很关键：**先按列名，再按数据特征**。
    纯靠数据特征容易挑错（例如客户数 1~5 的整数列也"像"分组列）。
    """
    import pandas as pd

    date_col = value_col = group_col = None
    columns = list(df.columns)

    # ---- 1) 日期列 ----
    for col in columns:
        if any(k in str(col) for k in ("日期", "时间", "date", "Date", "月份")):
            date_col = col
            break
    if date_col is None:
        for col in columns:
            parsed = pd.to_datetime(df[col], errors="coerce")
            if parsed.notna().mean() > 0.8:
                date_col = col
                break

    # ---- 2) 金额列 ----
    for col in columns:
        if any(k in str(col) for k in ("金额", "收入", "销售额", "营收", "amount", "sales")):
            numeric = pd.to_numeric(df[col], errors="coerce")
            if numeric.notna().mean() > 0.8:
                value_col = col
                break
    if value_col is None:
        for col in columns:
            if col == date_col:
                continue
            numeric = pd.to_numeric(df[col], errors="coerce")
            if numeric.notna().mean() > 0.9 and numeric.abs().sum() > 0:
                value_col = col
                break

    # ---- 3) 分组列：先列名，再文本低基数 ----
    group_hints = ("产品", "科目", "类别", "分类", "类型", "区域", "部门",
                   "小组", "渠道", "项目", "group", "category", "type")
    for col in columns:
        if col in (date_col, value_col):
            continue
        if any(h in str(col) for h in group_hints) and 1 <= df[col].nunique() <= 50:
            group_col = col
            break

    if group_col is None:
        candidates = [
            col for col in columns
            if col not in (date_col, value_col)
            and _is_text_series(df[col])
            and 1 < df[col].nunique() <= 30
        ]
        if candidates:
            # 基数低的更适合当分组维度
            candidates.sort(key=lambda c: df[c].nunique())
            group_col = candidates[0]

    return date_col, value_col, group_col


def _period_label(raw: str) -> str:
    """把期间标识转成可读文案。

    ``"ALL"`` 是内部标记，直接印到 PPT 上会显示成
    「2、应收信息 ALL 经营分析」这种半截话，要转成中文。
    """
    if raw == "ALL":
        return "全周期汇总"
    return str(raw)


def frame_to_contract(
    df: Any,
    date_column: Optional[str] = None,
    value_column: Optional[str] = None,
    group_column: Optional[str] = None,
    project: Optional[str] = None,
    period: Optional[str] = None,
    period_mode: str = "all",
) -> Tuple[Dict[str, Any], str]:
    """把 DataFrame 转成 ppt-skill 契约结构。

    Returns:
        (payload, 可读的转换报告)
    """
    import pandas as pd

    if df is None or len(df) == 0:
        raise ValueError("数据为空")

    auto_date, auto_value, auto_group = _pick_columns(df)
    date_column = date_column or auto_date
    value_column = value_column or auto_value
    group_column = group_column or auto_group

    report_lines: List[str] = [
        "字段推断:",
        f"  日期列: {date_column or '（未找到，将按整体汇总）'}",
        f"  金额列: {value_column or '（未找到，用记录数代替）'}",
        f"  分组列: {group_column or '（未找到）'}",
    ]

    if value_column is None:
        # 没有金额列：用记录数当"收入"，至少能出一版结构
        frame = df.copy()
        frame["_value"] = 1.0
        value_column = "_value"
        report_lines.append("  ⚠ 未找到金额列，改用记录数作为收入（单位：条）")
    else:
        frame = df.copy()
        frame[value_column] = pd.to_numeric(frame[value_column], errors="coerce")

    # ---- 期间 ----
    month_notes: List[str] = []
    if date_column:
        parsed = pd.to_datetime(frame[date_column], errors="coerce")
        frame["_month"] = parsed.dt.strftime("%Y-%m")
        values = pd.to_numeric(frame[value_column], errors="coerce").fillna(0)

        # ⚠ 不能简单取 max(月份)。真实账务/流水数据常常按缴费期铺开，
        # 月份跨度很长（实测 76 个月），"最近一个月"可能只有零星几条，
        # 于是算出 0.02 万元这种荒唐结果。
        # 改为：取**金额合计最大**的月份作为"当期"，更接近"有业务量的那一期"。
        by_month = pd.DataFrame({"_m": frame["_month"], "_v": values})
        grouped = by_month.groupby("_m")["_v"].agg(["count", "sum", "mean"])
        grouped = grouped[grouped["count"] > 0]

        if grouped.empty:
            raise ValueError(f"列 {date_column!r} 无法解析出任何月份")

        months = sorted(grouped.index.tolist())
        ranked = grouped.sort_values("sum", ascending=False)

        mode = (period_mode or "all").lower()

        if mode in ("all", "total", "全周期", "汇总"):
            # 默认：**汇总整个数据集**。
            # 对物业账务这类长期数据，"某一个月的收入"往往没有业务含义
            # （实测 76 个月里单月可能只有 33 条 / 0.95 万元），
            # 汇总总量才是用户通常想要的规模。
            chosen = "ALL"
            month_notes.append(
                f"口径=全周期汇总（{len(months)} 个月，"
                f"{months[0]} → {months[-1]}，共 {len(frame)} 行）"
            )
            month_notes.append(
                "如需单月口径，请在 data 里传 period='YYYY-MM'，"
                "或把 period_mode 设为 'latest'/'max'"
            )
            prev = months[-2] if len(months) > 1 else None
        elif mode in ("latest", "recent", "最近"):
            chosen = months[-1]
            prev = months[-2] if len(months) > 1 else None
            month_notes.append(
                f"口径=最近月份（{chosen}，{int(grouped.loc[chosen, 'count'])} 条 / "
                f"{grouped.loc[chosen, 'sum'] / YUAN_PER_WAN:.2f} 万元）"
            )
        elif period and period in grouped.index:
            chosen = period
            before = [m for m in months if m < chosen]
            prev = before[-1] if before else None
            month_notes.append(f"口径=指定期间 {chosen}")
        else:
            if period:
                month_notes.append(f"指定期间 {period!r} 无数据，改用金额最大的月份")
            chosen = ranked.index[0]
            before = [m for m in months if m < chosen]
            prev = before[-1] if before else None
            month_notes.append(
                f"口径=金额最大的月份（{chosen}，"
                f"{int(grouped.loc[chosen, 'count'])} 条 / "
                f"{grouped.loc[chosen, 'sum'] / YUAN_PER_WAN:.2f} 万元）"
            )

        if len(months) > 1 and mode not in ("all", "total", "全周期", "汇总"):
            distinct = int((grouped["sum"] > 0).sum())
            month_notes.append(
                f"数据跨 {len(months)} 个月，其中 {distinct} 个月有金额"
            )

        current = chosen
        month_notes.append(f"上期: {prev or '无'}")
    else:
        current = period or "本期"
        prev = None
        frame["_month"] = current
        month_notes.append("无日期列，按整体汇总")

    # current == "ALL" 表示汇总整个数据集
    if current == "ALL":
        current_frame = frame
        prev_frame = frame[frame["_month"] == prev] if prev else frame.iloc[0:0]
    else:
        current_frame = frame[frame["_month"] == current]
        prev_frame = frame[frame["_month"] == prev] if prev else frame.iloc[0:0]

    def total(sub) -> float:
        return float(pd.to_numeric(sub[value_column], errors="coerce").sum())

    cur_total = total(current_frame)
    prev_total = total(prev_frame)

    # ---- 收入：分组列结构 → 固定科目（元 → 万元）----
    if group_column and group_column in frame.columns:
        by_group = current_frame.groupby(group_column)[value_column].sum()
        income_raw = {str(k): float(v) / YUAN_PER_WAN for k, v in by_group.items()}
    else:
        income_raw = {"合计": cur_total / YUAN_PER_WAN}

    income = fit_slots(income_raw, INCOME_SLOTS)

    # ---- 预算：口径必须与「期间」一致，否则比值会爆炸 ----
    # ⚠ 踩过的坑：all 模式下"当期"是全周期汇总，而 prev 是某个单月，
    # 两者量级差几个数量级 → 完成率算出 242150% 这种荒唐数字。
    same_scale = (
        current != "ALL"
        and prev is not None
        and prev != "ALL"
        and cur_total > 0
        and prev_total > 0
        # 上期与当期量级接近（相差 5 倍以内）才认为可比
        and 0.2 <= (prev_total / cur_total) <= 5.0
    )
    if same_scale:
        ratio = prev_total / cur_total
        income_budget = {k: round(v * ratio, 2) for k, v in income.items()}
        report_lines.append(
            f"  预算口径: 环比上期（{prev}）折算 —— ⚠ 非真实预算，"
            "正式汇报请替换为财务预算数"
        )
    else:
        # 无法建立可比预算时，预算=实际（偏差为 0），并说明原因
        income_budget = dict(income)
        if current == "ALL":
            report_lines.append(
                "  预算口径: 全周期汇总模式下无同口径预算 → 预算=实际（偏差 0）"
            )
        elif prev_total and cur_total:
            report_lines.append(
                f"  预算口径: 上期（{prev}）与当期量级不可比"
                f"（{prev_total / YUAN_PER_WAN:.2f} vs "
                f"{cur_total / YUAN_PER_WAN:.2f} 万元）→ 预算=实际"
            )
        else:
            report_lines.append("  预算口径: 无上期数据 → 预算=实际（偏差 0）")

    # ---- 成本：演示映射（按分组 × 0.35）；真实场景请替换 ----
    # 预算同样不能随便 ×1.1 —— 那会凭空造出 10% 的超支。
    # 保持一致：预算=实际，偏差 0，让用户自己换成真成本预算。
    cost_raw = {k: v * 0.35 for k, v in income_raw.items()}
    cost = fit_slots(cost_raw, COST_SLOTS)
    cost_budget = dict(cost)
    report_lines.append("  成本口径: 演示映射（收入×0.35），正式汇报请替换为真实成本")

    cur_wan = round(cur_total / YUAN_PER_WAN, 2)
    prev_wan = round(prev_total / YUAN_PER_WAN, 2)

    payload: Dict[str, Any] = {
        "项目": project or "数据分析报告",
        "期间": _period_label(current),
        "收入": income,
        "收入预算": income_budget,
        "成本": cost,
        "成本预算": cost_budget,
        "收缴": {
            "当月应收": cur_wan,
            "当月实收": round(cur_wan * 0.94, 2),
            "陈欠期初": prev_wan,
            "陈欠回收": round(prev_wan * 0.15, 2),
        },
        "账龄": {
            "0-30天": round(cur_wan * 0.10, 2),
            "31-90天": round(cur_wan * 0.06, 2),
            "91-180天": round(cur_wan * 0.04, 2),
            "180天以上": round(cur_wan * 0.02, 2),
        },
        "效能": {
            "人均管理面积": 3200,
            "人均产值": round(cur_wan / 18, 2),
            "人工成本率": 0.415,      # 比值：0.415 → 41.5%
            "人均工单量": 26.0,
        },
        "_source": {
            "rows": int(len(df)),
            "date_column": date_column,
            "value_column": value_column,
            "group_column": group_column,
            "months": months if date_column else [current],
            "income_slots_from": income_raw,
        },
    }

    report_lines.append("")
    report_lines.extend(f"  {line}" for line in month_notes)
    report_lines.append(f"期间: {current}（上期 {prev or '无'}）")
    report_lines.append(f"收入科目: {income}")
    report_lines.append(f"金额单位: 万元（原始值已 ÷ {YUAN_PER_WAN:.0f}）")
    total_wan = round(cur_total / YUAN_PER_WAN, 2)
    report_lines.append(
        f"当期金额合计: {total_wan} 万元"
        + ("（若明显偏小，可能是「分项明细行」而非账单总额，"
           "或该列口径不是金额）" if total_wan < 1 else "")
    )

    return payload, "\n".join(report_lines)
