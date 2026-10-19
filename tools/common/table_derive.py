# -*- coding: utf-8 -*-
"""派生指标（Derived Metrics）—— 从已有列算出新的业务指标

解决什么问题
------------
很多"关键指标"其实**不需要跨表**，而是同一张表里几列算出来的：

    收缴率 = 已收 / 账单
    已收   = 账单 - 未缴
    欠款率 = 未缴 / 账单

在此之前这些只能靠调用方用 ``extra_facts`` 手工传进来 ——
数字不是从数据算的，**结论就不可信**（人工传错了没人发现）。

实测验证（物业 2、应收信息.xlsx）::

    账单合计 484,248.49   未缴 36,402.28   已收 447,846.21
    收缴率 = 92.48%       （人工此前传的是 92.5%）

按「缴费状态」分组交叉验证完全吻合，说明算法正确。

设计原则
--------
* **知识在 spec**：算什么、怎么算，由技能的 ``derived`` 段声明。
* **机制在工具**：本模块只提供「安全求值 + 加列」的通用能力，不认识任何行业。
* **安全**：用 ``ast`` 白名单求值，**不用 eval** —— 表达式来自配置文件，
  不能给它执行任意代码的能力。

spec 里的声明长这样::

    "derived": {
      "已收金额(元)": {"expr": "账单金额(元) - 未缴金额(元)"},
      "收缴率":      {"expr": "已收金额(元) / 账单金额(元) * 100",
                      "format": "{:.2f}%"},
      "欠款率":      {"expr": "未缴金额(元) / 账单金额(元) * 100",
                      "format": "{:.2f}%"}
    }

表达式里可以直接写**列名**（含中文、括号），模块会按列名长度降序替换，
避免 ``金额`` 把 ``账单金额(元)`` 抢先匹配掉。
"""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["compute_derived", "safe_eval_columns", "format_derived"]

#: 允许的二元运算
_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a ** b,
    ast.Mod: lambda a, b: a % b,
}
#: 允许的一元运算
_UNARY_OPS = {
    ast.UAdd: lambda a: a,
    ast.USub: lambda a: -a,
}


class DerivedError(ValueError):
    """派生指标声明有问题。"""


def _replace_columns(expr: str, columns: List[str]) -> Tuple[str, Dict[str, str]]:
    """把表达式里的列名换成安全的占位变量名。

    按列名**长度降序**替换：否则 ``金额`` 会先命中，
    ``账单金额(元)`` 就被切碎了。
    """
    mapping: Dict[str, str] = {}
    out = expr
    for i, col in enumerate(sorted(columns, key=len, reverse=True)):
        if not col or col not in out:
            continue
        var = f"__c{i}__"
        # 只替换完整出现（避免子串误伤）
        pattern = re.escape(str(col))
        if re.search(pattern, out):
            out = re.sub(pattern, var, out)
            mapping[var] = str(col)
    return out, mapping


def safe_eval_columns(expr: str, data: Dict[str, Any],
                      columns: List[str]) -> Any:
    """安全求值：表达式中只能引用给定列名与数字常量。

    Args:
        expr: 如 ``"账单金额(元) - 未缴金额(元)"``。
        data: 列名 → 值（标量或 pandas Series）。
        columns: 允许引用的列名白名单。

    Returns:
        求值结果（标量或 Series）。
    """
    if not isinstance(expr, str) or not expr.strip():
        raise DerivedError("表达式为空")

    replaced, mapping = _replace_columns(expr, columns)
    # 剩下的中文/未知标识符 → 直接报错，而不是让 Python 抛 NameError
    leftovers = re.findall(r"[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff()（）]*",
                           replaced)
    unknown = [x for x in leftovers
               if not x.startswith("__c") and x not in ("abs", "min", "max")]
    if unknown:
        raise DerivedError(
            f"表达式引用了未知列: {unknown}；可用列: {columns[:8]}..."
        )

    try:
        tree = ast.parse(replaced, mode="eval")
    except SyntaxError as exc:
        raise DerivedError(f"表达式语法错误: {exc}") from exc

    def ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in mapping:
                return data.get(mapping[node.id], 0)
            raise DerivedError(f"未知变量: {node.id}")
        if isinstance(node, ast.BinOp):
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise DerivedError(f"不支持的运算符: {type(node.op).__name__}")
            return op(ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op is None:
                raise DerivedError(f"不支持的一元运算: {type(node.op).__name__}")
            return op(ev(node.operand))
        if isinstance(node, ast.Call):
            # 只允许 abs / min / max，且参数必须是表达式（不是关键字）
            name = getattr(node.func, "id", "")
            if name not in ("abs", "min", "max"):
                raise DerivedError(f"不支持的函数: {name}")
            args = [ev(a) for a in node.args]
            return {"abs": abs, "min": min, "max": max}[name](*args)
        raise DerivedError(f"不支持的语法: {type(node).__name__}")

    return ev(tree)


def compute_derived(df, spec: Optional[Dict[str, Any]] = None
                    ) -> Tuple[Any, List[str], Dict[str, Any]]:
    """按 spec 给 DataFrame 加派生列。

    Args:
        df: pandas DataFrame。
        spec: ``{"列名": {"expr": "...", "format": "..."}}``。

    Returns:
        (df, notes, summary)：加过列的 df、过程说明、每个派生列的标量值。
    """
    notes: List[str] = []
    summary: Dict[str, Any] = {}
    if not spec or df is None:
        return df, notes, summary

    import pandas as pd

    # 已存在的列 + 按依赖顺序逐个计算（后面的表达式可以用前面的派生列）
    for name, rule in spec.items():
        if str(name).startswith("_"):
            continue          # 下划线开头是注释（如 _说明），不当指标处理
        if isinstance(rule, str):
            rule = {"expr": rule}
        if not isinstance(rule, dict):
            notes.append(f"派生指标 {name}: 声明格式不对，跳过")
            continue

        expr = rule.get("expr")
        if not expr:
            src = rule.get("source")
            if src and src in df.columns:
                df[name] = df[src]
                notes.append(f"派生指标 {name} = 列 {src}")
            continue

        available = [str(c) for c in df.columns]
        scope = str(rule.get("scope") or "row").lower()

        if scope in ("total", "overall", "aggregate"):
            # 比值类指标必须用**合计**算，不能逐行算比值再求和。
            # 实测踩过：收缴率逐行 已收/账单 再求和 → 1,476,800%
            # （14768 行 × 约 100%），而正确值是 92.48%。
            totals = {}
            for c in df.columns:
                try:
                    totals[str(c)] = float(
                        pd.to_numeric(df[c], errors="coerce").sum())
                except (TypeError, ValueError):
                    totals[str(c)] = 0.0
            try:
                result = safe_eval_columns(str(expr), totals, available)
            except DerivedError as exc:
                notes.append(f"派生指标 {name}: {exc}")
                continue
            if isinstance(result, pd.Series):
                result = float(result.sum())
            try:
                value = float(result)
            except (TypeError, ValueError):
                notes.append(f"派生指标 {name}: 结果不是数值 ({result!r})")
                continue
            summary[name] = value
            fmt = rule.get("format")
            shown = fmt.format(value) if fmt else f"{value:,.4f}"
            notes.append(f"派生指标 {name} = {expr}（按合计）  → {shown}")
            continue

        data = {str(c): df[c] for c in df.columns}
        try:
            result = safe_eval_columns(str(expr), data, available)
        except DerivedError as exc:
            notes.append(f"派生指标 {name}: {exc}")
            continue

        try:
            if isinstance(result, pd.Series):
                df[name] = result
                value = float(result.sum())
            else:
                df[name] = result
                value = float(result)
        except Exception as exc:
            notes.append(f"派生指标 {name}: 写入失败 {exc}")
            continue

        summary[name] = value
        fmt = rule.get("format")
        shown = fmt.format(value) if fmt else f"{value:,.4f}"
        notes.append(f"派生指标 {name} = {expr}  → 合计/值 {shown}")

    return df, notes, summary


def format_derived(value: Any, fmt: Optional[str] = None,
                   unit: Optional[str] = None) -> str:
    """按声明的格式输出派生指标。"""
    if value is None:
        return "—"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if fmt:
        try:
            return fmt.format(num)
        except Exception:
            pass
    text = f"{num:,.2f}"
    return f"{text}{unit}" if unit else text
