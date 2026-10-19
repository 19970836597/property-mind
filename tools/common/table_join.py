# -*- coding: utf-8 -*-
"""跨表关联（Table Join）—— 把多张表的维度拼到主表上

什么时候真的需要它
------------------
先量再决定。实测物业数据：

    收缴率      → **不需要跨表**。表2 里同时有「账单金额」和「未缴金额」，
                  单表就能算（已收 = 账单 - 未缴），交叉验证吻合。

    户均面积    → **需要跨表**。面积在「1、房产客户信息」里，
                  主表（2、应收信息）只有房产编号。

所以本模块只解决"维度在主表里没有"这一类问题：

    主表(应收)  ──join on 房产编号──▶  维表(房产信息)  ──▶  加回面积等列

知识在哪
--------
"用哪张表、拿哪几列、按什么键关联"是**业务知识**，声明在技能的 spec 里：

    "datasets": {
      "properties": {
        "file": "1、房产客户信息.xlsx",
        "key_column": "房产编码",
        "pick": {"实测收费面积（m²）": "实测收费面积"}
      }
    },
    "join": {
      "dataset": "properties",
      "left_on": "房产全称",
      "how": "left"
    }

本模块只提供通用机制，不认识"房产""面积"这些概念。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["load_dataset", "join_dataset", "apply_joins"]


def _find_file(name: str, dirs: List[str]) -> Optional[str]:
    """在给定目录里找文件（精确名优先，其次子串匹配）。"""
    if os.path.isabs(name) and os.path.isfile(name):
        return name
    for d in dirs:
        cand = os.path.join(d, name)
        if os.path.isfile(cand):
            return cand
    # 模糊：去掉序号前缀再找
    import re

    stem = re.sub(r"^[0-9０-９]+[、.．\s]*", "", name)
    stem_no_ext = os.path.splitext(stem)[0]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.lower().endswith((".xlsx", ".xls", ".csv")):
                continue
            if stem_no_ext and stem_no_ext in f:
                return os.path.join(d, f)
    return None


def load_dataset(name: str, rule: Dict[str, Any], dirs: List[str]
                 ) -> Tuple[Any, Optional[str], List[str]]:
    """按声明加载一张表，并按 ``pick`` 只保留需要的列。

    Returns:
        (DataFrame 或 None, 文件路径, 说明列表)
    """
    notes: List[str] = []
    if not isinstance(rule, dict):
        return None, None, [f"数据集 {name}: 声明格式不对"]

    from .table_io import HAS_PANDAS, read_table

    if not HAS_PANDAS:
        return None, None, [f"数据集 {name}: 环境缺 pandas"]

    path = _find_file(str(rule.get("file") or ""), dirs)
    if path is None:
        return None, None, [f"数据集 {name}: 找不到文件 {rule.get('file')!r}"]

    # 报表格式的表：列名不在第一行 → 跳过前 N 行再读
    header_row = int(rule.get("header_row") or 0)
    if header_row:
        try:
            import pandas as pd

            raw = pd.read_excel(path, header=header_row)
            df = raw
        except Exception as exc:
            return None, path, [f"数据集 {name}: 按 header_row={header_row} 读取失败 {exc}"]
    else:
        df, engine = read_table(path)
        if df is None:
            return None, path, [f"数据集 {name}: 无法解析（{engine}）"]

    notes.append(f"数据集 {name}: {os.path.basename(path)}（{len(df)} 行）")

    # 丢掉"总计/合计"这类汇总行（报表导出常见）
    if rule.get("drop_total_rows"):
        key_column = str(rule.get("key_column") or "")
        if key_column and key_column in df.columns:
            before = len(df)
            mask = df[key_column].astype(str).str.contains(
                "总计|合计|小计|total", case=False, na=False)
            df = df[~mask]
            if before != len(df):
                notes.append(f"  丢弃汇总行 {before - len(df)} 行")

    pick = rule.get("pick")
    if isinstance(pick, dict) and pick:
        keep = [str(rule.get("key_column") or "")] + list(pick.keys())
        keep = [c for c in keep if c in df.columns]
        missing = [c for c in pick if c not in df.columns]
        if missing:
            notes.append(f"  ⚠ 维表缺少列: {missing}")
        df = df[keep].copy()
        df = df.rename(columns={k: v for k, v in pick.items()})
        notes.append(f"  保留列: {list(df.columns)}")

    return df, path, notes


def join_dataset(df, dataset, rule: Dict[str, Any]) -> Tuple[Any, List[str]]:
    """把维表按声明的键关联到主表。

    Returns:
        (新 DataFrame, 说明列表)
    """
    notes: List[str] = []
    if df is None or dataset is None or not isinstance(rule, dict):
        return df, notes

    left_on = str(rule.get("left_on") or "").strip()
    right_on = str(rule.get("right_on") or rule.get("key_column") or "").strip()
    how = str(rule.get("how") or "left").lower()

    if left_on not in df.columns:
        return df, [f"关联失败: 主表没有列 {left_on!r}"]
    if right_on not in dataset.columns:
        return df, [f"关联失败: 维表没有列 {right_on!r}"]

    import pandas as pd

    left = df.copy()
    right = dataset.copy()
    left["_join_key"] = left[left_on].astype(str).str.strip()
    right["_join_key"] = right[right_on].astype(str).str.strip()

    # 维表里按列的字典序排序后去重：同一键出现多次时结果稳定可复现
    extra_cols = [c for c in right.columns
                  if c not in ("_join_key", right_on)]
    right = right.sort_values(by=["_join_key"] + extra_cols,
                              kind="stable").drop_duplicates("_join_key")

    before = len(left)
    merged = left.merge(right[["_join_key"] + extra_cols], on="_join_key",
                        how=how)
    merged = merged.drop(columns=["_join_key"])

    matched = int(merged[extra_cols[0]].notna().sum()) if extra_cols else 0
    notes.append(
        f"关联 {left_on} → {right_on}（{how}）: "
        f"{matched}/{before} 行匹配上，新增列 {extra_cols}"
    )
    if matched == 0:
        notes.append("  ⚠ 一行都没匹配上 —— 检查键的格式是否一致（分隔符/前后空格）")
    return merged, notes


def apply_joins(df, spec: Dict[str, Any], dirs: List[str]
                ) -> Tuple[Any, List[str]]:
    """按 spec 的 ``datasets`` + ``join`` 完成全部关联。

    支持 ``join`` 是单个 dict 或 dict 列表（多表依次关联）。
    """
    notes: List[str] = []
    datasets_spec = spec.get("datasets") or {}
    join_spec = spec.get("join")
    if not datasets_spec or not join_spec:
        return df, notes

    joins = join_spec if isinstance(join_spec, list) else [join_spec]
    loaded: Dict[str, Any] = {}

    for rule in joins:
        if not isinstance(rule, dict):
            continue
        name = str(rule.get("dataset") or "")
        if not name:
            continue
        if name not in loaded:
            data_rule = datasets_spec.get(name) or {}
            if "_说明" in str(data_rule):
                data_rule = {k: v for k, v in data_rule.items()
                             if not str(k).startswith("_")}
            table, path, load_notes = load_dataset(name, data_rule, dirs)
            notes.extend(load_notes)
            loaded[name] = table
        merged_rule = dict(rule)
        merged_rule.setdefault("key_column",
                               (datasets_spec.get(name) or {}).get("key_column"))
        df, join_notes = join_dataset(df, loaded.get(name), merged_rule)
        notes.extend(join_notes)

    return df, notes
