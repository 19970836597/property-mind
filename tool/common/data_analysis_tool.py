# -*- coding: utf-8 -*-
"""数据文件读取与分析工具（能力名 ``data_analysis``）

职责
----
把「一个表格文件」变成「可用的数据事实」：

    profile   读出 形状/列名/类型/空值/数值概览  ← 先看这个
    preview   前 N 行原始预览
    sheets    列出 Excel 的所有工作表
    stats     按某列分组做聚合（求和/均值/计数…）
    timeseries 按时间列做月度/日度汇总
    contract  把表格转成 ppt-skill 的契约 JSON（可直接喂给技能）

与 ``data_file`` 的分工
-----------------------
    data_file  负责**找到**文件（常用目录里的模糊匹配）
    data_analysis 负责**读懂**文件

设计取舍
--------
引擎优先 pandas，缺失时自动退化到标准库（csv/openpyxl），
避免因为环境差异整个工具不可用。
"""

from __future__ import annotations

import csv
import io
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from ..tools import Tool, ToolParameter
from .commons import find_files, resolve_common_dirs
from .table_io import HAS_PANDAS, pd, read_table

__all__ = ["DataAnalysisTool"]

MAX_PREVIEW_ROWS = 20
MAX_GROUPS = 30


def _resolve_target(params: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """决定要读哪个文件。

    优先 ``file``（完整路径），其次 ``file_name``（名字，去常用目录里找）。

    Returns:
        (路径 或 None, 说明文本)
    """
    explicit = params.get("file") or params.get("path")
    if explicit:
        path = os.path.abspath(str(explicit))
        if os.path.isfile(path):
            return path, f"使用指定文件: {path}"
        return None, f"指定文件不存在: {path}"

    name = str(params.get("file_name") or params.get("name") or "").strip()
    if not name:
        return None, "未提供 file 或 file_name。"

    hits = find_files(query=name, dirs=resolve_common_dirs(), limit=1)
    if not hits:
        return None, (
            f"常用文件目录里找不到 {name!r}。"
            "请把文件放进 data/common，或用 file 传完整路径。"
        )
    return hits[0].path, f"由 {name!r} 匹配到: {hits[0].name}（{hits[0].path}）"


def _read_table(path: str) -> Tuple[Optional[Any], str]:
    """兼容旧调用：转调共享的 table_io.read_table。"""
    return read_table(path)


def _describe_frame(df: Any) -> str:
    """给出数据画像。"""
    lines = [
        f"形状: {df.shape[0]} 行 × {df.shape[1]} 列",
        "",
        "列信息:",
    ]
    for col in df.columns:
        series = df[col]
        non_null = int(series.notna().sum())
        dtype = str(series.dtype)
        extra = ""
        if str(dtype).startswith(("int", "float")):
            try:
                extra = (f"  最小={series.min():.4g} 最大={series.max():.4g} "
                         f"均值={series.mean():.4g}")
            except Exception:
                extra = ""
        sample = series.dropna().head(2).tolist()
        sample_text = ", ".join(str(s)[:18] for s in sample)
        lines.append(
            f"  - {col}  [{dtype}]  非空 {non_null}/{len(series)}"
            f"  样例: {sample_text}{extra}"
        )

    numeric = df.select_dtypes(include="number")
    if not numeric.empty:
        lines.append("")
        lines.append(f"数值列汇总（{len(numeric.columns)} 列）:")
        try:
            desc = numeric.describe().transpose()
            for name, row in desc.iterrows():
                lines.append(
                    f"  - {name}: 均值 {row['mean']:.4g} · 合计 "
                    f"{numeric[name].sum():.4g} · 最大 {row['max']:.4g}"
                )
        except Exception:
            pass
    return "\n".join(lines)


def _describe_rows(rows: List[Any]) -> str:
    """退化路径下的画像。"""
    if not rows:
        return "文件为空。"
    header = [str(c) for c in rows[0]]
    body = rows[1:]
    lines = [f"形状: {len(body)} 行 × {len(header)} 列", "", "列信息（标准库模式，无类型推断）:"]
    for i, col in enumerate(header):
        samples = []
        numeric_hits = 0
        for row in body[:50]:
            if i < len(row) and row[i] is not None:
                samples.append(str(row[i])[:18])
                try:
                    float(row[i])
                    numeric_hits += 1
                except (TypeError, ValueError):
                    pass
        kind = "数值" if samples and numeric_hits >= len(samples) * 0.8 else "文本"
        lines.append(f"  - {col}  [{kind}]  样例: {', '.join(samples[:2])}")
    return "\n".join(lines)


class DataAnalysisTool(Tool):
    """数据文件读取与分析工具（``data_analysis``）。"""

    def __init__(self, expandable: bool = False):
        super().__init__(
            name="data_analysis",
            description=(
                "读取并分析数据文件（xlsx/csv/tsv/json）：查看形状与列画像、"
                "预览数据、按列分组聚合、按时间汇总，以及把表格转成 "
                "ppt-skill 可用的契约 JSON。用于「先看清数据再决定怎么用」。"
            ),
            expandable=expandable,
        )

    def get_params(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="file",
                type="string",
                description="数据文件完整路径（优先使用）",
                required=False,
                default=None,
            ),
            ToolParameter(
                name="file_name",
                type="string",
                description="文件名或关键词；不传 file 时会去常用目录里匹配",
                required=False,
                default=None,
            ),
            ToolParameter(
                name="action",
                type="string",
                description=(
                    "profile=形状与列画像（默认）; preview=前 N 行; "
                    "sheets=列出工作表; stats=按列分组聚合; "
                    "timeseries=按时间列按月汇总; contract=转 ppt-skill 契约 JSON"
                ),
                required=False,
                default="profile",
            ),
            ToolParameter(
                name="group_by",
                type="string",
                description="分组列名（action=stats 时使用）",
                required=False,
                default=None,
            ),
            ToolParameter(
                name="value_column",
                type="string",
                description="聚合的数值列名（action=stats/timeseries 时使用）",
                required=False,
                default=None,
            ),
            ToolParameter(
                name="agg",
                type="string",
                description="聚合方式：sum/mean/count/max/min（默认 sum）",
                required=False,
                default="sum",
            ),
            ToolParameter(
                name="date_column",
                type="string",
                description="时间列名（action=timeseries 时使用）",
                required=False,
                default=None,
            ),
            ToolParameter(
                name="rows",
                type="integer",
                description="preview 返回行数，默认 10",
                required=False,
                default=10,
            ),
            ToolParameter(
                name="sheet",
                type="string",
                description="Excel 工作表名（默认第一个）",
                required=False,
                default=None,
            ),
        ]

    def run(self, params: Dict[str, Any]) -> str:
        path, note = _resolve_target(params)
        if path is None:
            return note

        action = str(params.get("action") or "profile").strip().lower()

        if action in ("sheets", "sheet"):
            return self._sheets(path)

        try:
            table, engine = _read_table(path)
        except Exception as exc:
            return f"读取失败: {type(exc).__name__}: {exc}\n{note}"

        if table is None:
            return f"无法解析该文件为表格: {engine}\n{note}"

        head = f"[数据读取] {os.path.basename(path)}\n  引擎: {engine}\n  {note}\n"

        if action == "preview":
            return head + "\n" + self._preview(table, params)
        if action == "stats":
            return head + "\n" + self._stats(table, params)
        if action in ("timeseries", "trend"):
            return head + "\n" + self._timeseries(table, params)
        if action == "contract":
            return head + "\n" + self._contract(table, params)

        return head + "\n" + self._profile(table)

    # ---------------- 各 action ----------------

    def _profile(self, table: Any) -> str:
        if HAS_PANDAS and isinstance(table, pd.DataFrame):
            return _describe_frame(table)
        return _describe_rows(table)

    def _preview(self, table: Any, params: Dict[str, Any]) -> str:
        try:
            rows = int(params.get("rows") or 10)
        except (TypeError, ValueError):
            rows = 10
        rows = max(1, min(rows, MAX_PREVIEW_ROWS))

        if HAS_PANDAS and isinstance(table, pd.DataFrame):
            head = table.head(rows)
            lines = [f"前 {len(head)} 行:"]
            lines.append(" | ".join(str(c) for c in head.columns))
            lines.append("-" * 60)
            for _idx, row in head.iterrows():
                lines.append(" | ".join(str(v)[:20] for v in row.tolist()))
            return "\n".join(lines)

        head_rows = table[:rows + 1]
        lines = [f"前 {max(0, len(head_rows) - 1)} 行（含表头）:"]
        for row in head_rows:
            lines.append(" | ".join(str(v)[:20] for v in row))
        return "\n".join(lines)

    def _sheets(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".xlsx", ".xlsm"):
            return f"{os.path.basename(path)} 不是 Excel，无工作表概念。"
        try:
            import openpyxl
        except Exception:
            return "读取工作表需要 openpyxl。"
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        lines = [f"[工作表] {os.path.basename(path)}"]
        for ws in wb.worksheets:
            lines.append(f"  - {ws.title}  ({ws.max_row} 行 × {ws.max_column} 列)")
        return "\n".join(lines)

    def _stats(self, table: Any, params: Dict[str, Any]) -> str:
        if not (HAS_PANDAS and isinstance(table, pd.DataFrame)):
            return "按列聚合需要 pandas（当前环境不可用）。可先用 preview 看结构。"

        group_by = params.get("group_by")
        value_column = params.get("value_column")
        agg = str(params.get("agg") or "sum").lower()

        if not group_by or group_by not in table.columns:
            return (f"group_by 无效: {group_by!r}\n"
                    f"可用列: {list(table.columns)}")

        if agg == "count" or not value_column:
            grouped = table.groupby(group_by).size()
            label = "记录数"
        else:
            if value_column not in table.columns:
                return (f"value_column 无效: {value_column!r}\n"
                        f"可用列: {list(table.columns)}")
            if agg not in ("sum", "mean", "max", "min"):
                agg = "sum"
            series = table[value_column]
            if agg != "sum":
                try:
                    import numpy as _np  # noqa: F401
                    series = pd.to_numeric(series, errors="coerce")
                except Exception:
                    pass
            grouped = table.groupby(group_by)[value_column].agg(agg)
            label = f"{value_column} 的 {agg}"

        grouped = grouped.sort_values(ascending=False).head(MAX_GROUPS)
        lines = [f"[分组聚合] 按 {group_by} 统计 {label}"
                 f"（展示前 {len(grouped)} 组）"]
        total = float(grouped.sum()) if agg in ("sum", "count") and len(grouped) else 0.0
        for key, value in grouped.items():
            suffix = ""
            if total:
                suffix = f"   占比 {value / total * 100:.1f}%"
            lines.append(f"  - {key}: {value:,.2f}{suffix}" if isinstance(value, (int, float))
                         else f"  - {key}: {value}")
        if agg in ("sum", "count"):
            lines.append(f"  合计: {total:,.2f}")
        return "\n".join(lines)

    def _timeseries(self, table: Any, params: Dict[str, Any]) -> str:
        if not (HAS_PANDAS and isinstance(table, pd.DataFrame)):
            return "时间汇总需要 pandas（当前环境不可用）。"

        date_column = params.get("date_column")
        value_column = params.get("value_column")
        if not date_column or date_column not in table.columns:
            return (f"date_column 无效: {date_column!r}\n"
                    f"可用列: {list(table.columns)}")

        parsed = pd.to_datetime(table[date_column], errors="coerce")
        ok = int(parsed.notna().sum())
        if ok == 0:
            return f"列 {date_column!r} 无法解析为日期。"

        frame = table.copy()
        frame["_period"] = parsed.dt.strftime("%Y-%m")

        if value_column and value_column in frame.columns:
            frame[value_column] = pd.to_numeric(frame[value_column], errors="coerce")
            grouped = frame.groupby("_period")[value_column].agg(["sum", "count"])
            lines = [f"[时间汇总] 按 {date_column} → 月，聚合 {value_column}",
                     f"（可解析 {ok}/{len(frame)} 行）"]
            for period, row in grouped.iterrows():
                lines.append(
                    f"  - {period}: 合计 {row['sum']:,.2f}  记录数 {int(row['count'])}"
                )
        else:
            grouped = frame.groupby("_period").size()
            lines = [f"[时间汇总] 按 {date_column} → 月 计数",
                     f"（可解析 {ok}/{len(frame)} 行）"]
            for period, count in grouped.items():
                lines.append(f"  - {period}: {int(count)} 条")

        lines.append("")
        lines.append("下一步: 可用 contract action 转成 ppt-skill 契约 JSON。")
        return "\n".join(lines)

    def _contract(self, table: Any, params: Dict[str, Any]) -> str:
        """把表格转成 ppt-skill 契约 JSON（写到临时文件并返回路径）。"""
        if not (HAS_PANDAS and isinstance(table, pd.DataFrame)):
            return "contract 需要 pandas（当前环境无法保证类型推断）。"

        from .deck_contract import frame_to_contract

        try:
            payload, report = frame_to_contract(
                table,
                date_column=params.get("date_column"),
                value_column=params.get("value_column"),
                group_column=params.get("group_by"),
                project=params.get("project"),
                period=params.get("period"),
            )
        except Exception as exc:
            return f"契约转换失败: {type(exc).__name__}: {exc}"

        out_dir = os.environ.get("SKILL_OUTPUT_DIR") or os.getcwd()
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "contract_data.json")
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

        lines = [
            "[契约转换] 已生成 ppt-skill 可用的 data 结构",
            f"  文件: {out_path}",
            "",
            report,
            "",
            "下一步: 把该 JSON 作为 data 传给 ppt_skill 生成演示文稿。",
        ]
        return "\n".join(lines)
