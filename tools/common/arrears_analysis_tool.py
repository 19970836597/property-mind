# -*- coding: utf-8 -*-
"""欠费账龄分析与催缴优先级排序工具。

给催费助手用的确定性数据工具：

    aging    → 账龄分档统计（30天内 / 31-90 / 91-180 / 180天以上）
    priority → 催缴优先级评分排序（账龄 + 金额 + 历史欠费）
    detail   → 行级欠费明细查询

数据来源默认在常用目录里模糊定位「欠款明细」报表（data/common/4、欠款明细报表.xlsx）。

已知报表形态（务必保持兼容）：
  * 首行是标题行「欠款明细报表」，真正的表头在第 2 行 → 读取时自动检测；
  * 无现成账龄列，账龄从「物业服务费欠款区间」推导，格式如
    ``2018-01-01~2018-12-31``，口径：账龄天数 = 参考日 − 区间结束日；
  * 换月份的新报表可能没有标题行 → 检测逻辑不写死 header=1。
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from ..tools import Tool, ToolParameter
from .commons import find_files

__all__ = ["ArrearsAnalysisTool"]

#: 账龄分档（天数上限，含）
AGING_BUCKETS: List[tuple] = [
    ("30天内", 30),
    ("31-90天", 90),
    ("91-180天", 180),
    ("180天以上", float("inf")),
]

#: 各档建议的催缴语气（与催费技能 notice_tones.md 的档位一致）
BUCKET_TONE = {
    "30天内": "温馨提醒",
    "31-90天": "温馨提醒",
    "91-180天": "正式催缴",
    "180天以上": "律师函预警（建议转法务）",
}


def _col(df, *keywords: str, exclude: tuple = ()) -> Optional[str]:
    """按关键词模糊定位列名（列名可能有空格/单位后缀）。

    exclude: 命中任一关键词的列名跳过（如业主列要避开「车位名称」）。
    """
    for name in df.columns:
        norm = str(name).replace(" ", "")
        if any(e in norm for e in exclude):
            continue
        if all(k in norm for k in keywords):
            return name
    return None


def _parse_end_date(interval: Any) -> Optional[date]:
    """解析欠款区间 ``start~end``，取区间结束日。"""
    if interval is None:
        return None
    text = str(interval).strip()
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*[~～]\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        y, m, d = int(match.group(4)), int(match.group(5)), int(match.group(6))
        try:
            return date(y, m, d)
        except ValueError:
            return None
    # 只有单日期的兜底
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def _parse_reference_date(value: Any) -> date:
    if value:
        text = str(value).strip()
        match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
        if match:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()


def _bucket_of(days: Optional[int]) -> str:
    if days is None:
        return "账龄未知"
    if days < 0:
        days = 0  # 区间未结束，视作仍在期内
    for label, upper in AGING_BUCKETS:
        if days <= upper:
            return label
    return "180天以上"


def _fmt_money(value) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "-"


class ArrearsAnalysisTool(Tool):
    """欠费账龄分析与催缴优先级排序。"""

    def __init__(self):
        super().__init__(
            name="arrears_analysis",
            description=(
                "物业欠费账龄分析与催缴优先级排序。读取欠款明细报表，按 30天内/31-90/91-180/180天以上"
                "分档统计金额与户数，评分排序催缴优先级，识别应转法务（律师函预警）的高账龄欠费。"
                "关键词：欠费、欠款、账龄、催缴、催费、催收、优先级。"
            ),
        )
        # capability_tags() 用 getattr 读取，Tool 基类无需改
        self.tags = ["催费", "催缴", "欠费", "账龄", "催收"]

    # ------------------------------------------------------------------
    # 参数定义
    # ------------------------------------------------------------------
    def get_params(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="action",
                type="string",
                description="操作类型：aging(账龄分档统计，默认) / priority(催缴优先级排序) / detail(欠费明细查询)",
                required=False,
                default="aging",
            ),
            ToolParameter(
                name="file",
                type="string",
                description="欠款明细报表的完整路径（优先）。不传则自动在常用目录按文件名搜索。",
                required=False,
            ),
            ToolParameter(
                name="file_name",
                type="string",
                description="文件名关键词，用于模糊匹配常用目录（默认：欠款明细）。",
                required=False,
                default="欠款明细",
            ),
            ToolParameter(
                name="reference_date",
                type="string",
                description="账龄参考日 YYYY-MM-DD（默认今天）。账龄天数 = 参考日 − 欠款区间结束日。",
                required=False,
            ),
            ToolParameter(
                name="top_n",
                type="integer",
                description="priority 时返回前 N 名（默认 20）。",
                required=False,
                default=20,
            ),
            ToolParameter(
                name="owner",
                type="string",
                description="detail 时按业主姓名过滤（可只写姓氏）。",
                required=False,
            ),
        ]

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def run(self, params: Dict[str, Any]) -> str:
        import pandas as pd  # 延迟导入，与 table_io 同风格

        path = self._resolve_file(params)
        if not path:
            return "❌ 未找到欠款明细报表：请把报表放到 data/common 下，或用 file 参数给出完整路径。"

        reference_date = _parse_reference_date(params.get("reference_date"))

        try:
            df = self._load_table(pd, path)
        except Exception as exc:  # noqa: BLE001
            return f"❌ 读取报表失败: {path}\n{exc}"

        if df is None or df.empty:
            return f"❌ 报表内容为空: {path}"

        # ---- 列定位（不写死列名，兼容列名微调）----
        col_amount = _col(df, "总欠款") or _col(df, "欠款")
        col_property = _col(df, "房产全称") or _col(df, "房产")
        col_owner = _col(df, "名称", exclude=("车位", "房产"))
        col_interval = (
            _col(df, "物业服务费", "区间")
            or _col(df, "服务费", "区间")
            or _col(df, "欠款区间")
        )
        col_reason = _col(df, "原因")

        if col_amount is None or col_property is None:
            return (
                "❌ 报表缺少必要列（总欠款金额 / 房产全称）。\n"
                f"实际列名: {list(df.columns)}"
            )

        # ---- 过滤表尾的合计行/空行（房产全称或金额为空的行不是欠费户）----
        df = df[
            df[col_property].notna()
            & (df[col_property].astype(str).str.strip() != "")
            & df[col_amount].notna()
        ]
        if df.empty:
            return f"❌ 报表过滤后没有有效的欠费记录: {path}"

        # ---- 账龄推导 ----
        rows: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            interval = row.get(col_interval) if col_interval else None
            end_date = _parse_end_date(interval)
            days = (reference_date - end_date).days if end_date else None
            raw_amount = row[col_amount]
            try:
                # NaN 判断必须显式做：NaN 为真值，`NaN or 0` 不成立
                amount = 0.0 if pd.isna(raw_amount) else float(raw_amount)
            except (TypeError, ValueError):
                amount = 0.0
            rows.append(
                {
                    "property": str(row[col_property]),
                    "owner": str(row.get(col_owner, "")) if col_owner else "",
                    "amount": amount,
                    "days": days,
                    "bucket": _bucket_of(days),
                    "interval": str(interval) if interval and str(interval) != "nan" else "",
                    "reason": str(row.get(col_reason, "")) if col_reason and str(row.get(col_reason, "")) != "nan" else "",
                }
            )

        action = str(params.get("action") or "aging").strip().lower()
        if action == "priority":
            return self._priority(rows, params, reference_date, path)
        if action == "detail":
            return self._detail(rows, params, reference_date, path)
        return self._aging(rows, reference_date, path)

    # ------------------------------------------------------------------
    # 文件定位与读取
    # ------------------------------------------------------------------
    def _resolve_file(self, params: Dict[str, Any]) -> Optional[str]:
        file = params.get("file")
        if file and os.path.exists(file):
            return file
        query = params.get("file_name") or "欠款明细"
        hits = find_files(query, limit=1)
        return hits[0].path if hits else None

    def _load_table(self, pd, path: str):
        """读表 + 标题行自动检测。

        「欠款明细报表」首行只有标题一个值 → 真实表头在第 2 行；
        其他月份的新报表可能首行就是表头 → 按非空单元格数判断，不写死。
        """
        raw = pd.read_excel(path, header=None, nrows=3)
        header_row = 1 if raw.iloc[0].notna().sum() <= 2 else 0
        return pd.read_excel(path, header=header_row)

    # ------------------------------------------------------------------
    # action: aging
    # ------------------------------------------------------------------
    def _aging(self, rows: List[Dict], reference_date: date, path: str) -> str:
        total_amount = sum(r["amount"] for r in rows)
        lines = [
            "📊 **欠费账龄分档统计**",
            f"数据来源: {os.path.basename(path)}（{len(rows)} 条欠费记录）",
            f"账龄参考日: {reference_date.isoformat()}（口径：账龄天数 = 参考日 − 欠款区间结束日）",
            "",
            "| 账龄档 | 户数 | 欠款金额(元) | 金额占比 | 建议催缴语气 |",
            "|---|---:|---:|---:|---|",
        ]

        stats: Dict[str, Dict[str, float]] = {}
        for row in rows:
            bucket = row["bucket"]
            stat = stats.setdefault(bucket, {"count": 0, "amount": 0.0})
            stat["count"] += 1
            stat["amount"] += row["amount"]

        # 固定档位顺序输出（没数据的档也显示 0，便于扫读）
        for label, _ in AGING_BUCKETS:
            stat = stats.get(label, {"count": 0, "amount": 0.0})
            share = stat["amount"] / total_amount if total_amount else 0.0
            lines.append(
                f"| {label} | {int(stat['count'])} | {_fmt_money(stat['amount'])} "
                f"| {share * 100:.1f}% | {BUCKET_TONE[label]} |"
            )

        if "账龄未知" in stats:
            stat = stats["账龄未知"]
            lines.append(f"| 账龄未知 | {int(stat['count'])} | {_fmt_money(stat['amount'])} | - | 待核实欠款区间 |")

        lines.append(
            f"| **合计** | **{len(rows)}** | **{_fmt_money(total_amount)}** | 100% | |"
        )

        # ---- 法务联动提示 ----
        high = stats.get("180天以上", {"count": 0, "amount": 0.0})
        if high["count"]:
            lines += [
                "",
                f"⚠️ **法务联动提示**：账龄 180 天以上的欠费 {int(high['count'])} 户 / ¥{_fmt_money(high['amount'])}，"
                "建议转入法务流程（律师函预警），可调用 docx_letter 生成律师函预警函，"
                "并参考法务技能的合规要求。",
            ]
        lines += [
            "",
            "说明：账龄按「欠款区间结束日」推算（区间是费用所属期间而非应缴日，实际拖欠可能更短）；"
            "如需按其他口径，可用 reference_date 参数调整参考日。",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # action: priority
    # ------------------------------------------------------------------
    def _priority(self, rows: List[Dict], params: Dict, reference_date: date, path: str) -> str:
        top_n = params.get("top_n") or 20
        try:
            top_n = max(1, int(top_n))
        except (TypeError, ValueError):
            top_n = 20

        # ---- 按房产聚合（同一房产可能有多期欠费）----
        groups: Dict[tuple, Dict[str, Any]] = {}
        for row in rows:
            key = (row["property"], row["owner"])
            group = groups.setdefault(
                key,
                {"property": row["property"], "owner": row["owner"], "amount": 0.0,
                 "count": 0, "max_days": None},
            )
            group["amount"] += row["amount"]
            group["count"] += 1
            if row["days"] is not None:
                if group["max_days"] is None or row["days"] > group["max_days"]:
                    group["max_days"] = row["days"]

        items = list(groups.values())
        if not items:
            return "❌ 没有可排序的欠费记录。"

        amounts = sorted(g["amount"] for g in items)
        counts = [g["count"] for g in items]
        max_count = max(counts) if counts else 1

        def _percentile(value: float) -> float:
            """金额在全体中的分位（0~1）。"""
            rank = sum(1 for a in amounts if a <= value)
            return rank / len(amounts)

        # ---- 评分：40×账龄档位 + 40×金额分位 + 20×历史欠费期数 ----
        for group in items:
            bucket = _bucket_of(group["max_days"])
            bucket_index = 0 if bucket == "账龄未知" else [l for l, _ in AGING_BUCKETS].index(bucket) + 1
            group["bucket"] = bucket
            group["score"] = round(
                40 * bucket_index / 4 + 40 * _percentile(group["amount"])
                + 20 * (group["count"] / max_count),
                1,
            )
            group["tone"] = "待核实" if bucket == "账龄未知" else BUCKET_TONE[bucket]

        items.sort(key=lambda g: g["score"], reverse=True)

        lines = [
            "🎯 **催缴优先级排序**（前 %d 名 / 共 %d 户）" % (min(top_n, len(items)), len(items)),
            f"数据来源: {os.path.basename(path)}，账龄参考日: {reference_date.isoformat()}",
            "评分 = 40×账龄档位 + 40×金额分位 + 20×历史欠费期数（满分 100）",
            "",
            "| 排名 | 业主 | 房产 | 总欠款(元) | 欠费期数 | 账龄档 | 评分 | 建议语气 |",
            "|---:|---|---|---:|---:|---|---:|---|",
        ]
        for rank, group in enumerate(items[:top_n], 1):
            days = group["max_days"]
            aging_text = f"{days}天" if days is not None and days >= 0 else "未知"
            lines.append(
                f"| {rank} | {group['owner'] or '-'} | {group['property']} "
                f"| {_fmt_money(group['amount'])} | {group['count']} "
                f"| {group['bucket']}({aging_text}) | {group['score']} | {group['tone']} |"
            )

        legal_count = sum(1 for g in items if g["bucket"] == "180天以上")
        if legal_count:
            lines += [
                "",
                f"⚠️ 其中 {legal_count} 户账龄 180 天以上，建议转法务流程（律师函预警），"
                "生成函件用 docx_letter 工具，语气参考催费技能 notice_tones.md。",
            ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # action: detail
    # ------------------------------------------------------------------
    def _detail(self, rows: List[Dict], params: Dict, reference_date: date, path: str) -> str:
        owner = str(params.get("owner") or "").strip()
        filtered = rows
        if owner:
            filtered = [r for r in rows if owner in r["owner"] or owner in r["property"]]

        limit = 40  # 防止上下文爆炸，与计划一致
        lines = [
            "📋 **欠费明细**" + (f"（过滤：{owner}）" if owner else ""),
            f"数据来源: {os.path.basename(path)}，账龄参考日: {reference_date.isoformat()}，"
            f"共 {len(filtered)} 条，显示前 {min(limit, len(filtered))} 条",
            "",
            "| 房产 | 业主 | 欠款区间 | 欠款(元) | 账龄 | 欠费原因 |",
            "|---|---|---|---:|---|---|",
        ]
        for row in filtered[:limit]:
            days = row["days"]
            aging_text = f"{days}天" if days is not None and days >= 0 else "未知"
            lines.append(
                f"| {row['property']} | {row['owner'] or '-'} | {row['interval'] or '-'} "
                f"| {_fmt_money(row['amount'])} | {aging_text} | {row['reason'] or '-'} |"
            )
        if len(filtered) > limit:
            lines.append(f"| ... | | | | | 其余 {len(filtered) - limit} 条已省略 |")
        if not filtered:
            return f"未找到与「{owner}」相关的欠费记录。"
        return "\n".join(lines)
