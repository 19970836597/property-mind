# -*- coding: utf-8 -*-
"""表格读取（pandas 优先，标准库退化）

被两处复用：
  * ``data_analysis_tool``  —— Agent 主动分析数据
  * ``skills.loader``       —— 用户没给数据时，自动去常用目录抽数据

放成独立模块是为了让两条路径的行为一致：同一份文件，
手动分析 与 自动抽数 必须得到相同的表。
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, List, Optional, Tuple

__all__ = ["HAS_PANDAS", "pd", "read_table", "SUPPORTED_TABLE_EXT"]

try:  # pragma: no cover - 取决于环境
    import pandas as pd

    HAS_PANDAS = True
except Exception:  # pragma: no cover
    pd = None
    HAS_PANDAS = False

try:
    import openpyxl
except Exception:  # pragma: no cover
    openpyxl = None

SUPPORTED_TABLE_EXT = (".xlsx", ".xlsm", ".csv", ".tsv", ".json")


def read_table(path: str) -> Tuple[Optional[Any], str]:
    """读取表格。

    Returns:
        (DataFrame 或 行列表, 引擎说明)。失败时第一项为 None。
    """
    ext = os.path.splitext(path)[1].lower()

    if ext == ".json":
        with open(path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, list) and value:
                    payload = value
                    break
        if not isinstance(payload, list):
            return None, "JSON 不是数组，无法当表格处理"
        if HAS_PANDAS:
            return pd.DataFrame(payload), "json（pandas）"
        return payload, "json"

    if HAS_PANDAS:
        try:
            if ext in (".csv", ".tsv"):
                sep = "\t" if ext == ".tsv" else ","
                try:
                    return pd.read_csv(path, sep=sep), f"{ext[1:]}（pandas）"
                except UnicodeDecodeError:
                    return (pd.read_csv(path, sep=sep, encoding="gbk"),
                            f"{ext[1:]}/gbk（pandas）")
            if ext in (".xlsx", ".xlsm"):
                return pd.read_excel(path, sheet_name=0), "excel（pandas）"
        except Exception as exc:
            return None, f"pandas 读取失败: {type(exc).__name__}: {exc}"

    # ---- 退化路径 ----
    if ext in (".csv", ".tsv"):
        sep = "\t" if ext == ".tsv" else ","
        for encoding in ("utf-8-sig", "gbk"):
            try:
                with open(path, "r", encoding=encoding, newline="") as handle:
                    rows: List[Any] = list(csv.reader(handle, delimiter=sep))
                return rows, f"{ext[1:]}/{encoding}（标准库）"
            except UnicodeDecodeError:
                continue
        return None, "CSV 编码无法识别"

    if ext in (".xlsx", ".xlsm"):
        if openpyxl is None:
            return None, "读取 Excel 需要 pandas 或 openpyxl，均不可用"
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.worksheets[0]
        return list(ws.iter_rows(values_only=True)), "excel（openpyxl）"

    return None, f"不支持的扩展名: {ext}"
