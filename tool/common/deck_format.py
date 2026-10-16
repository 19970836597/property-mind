# -*- coding: utf-8 -*-
"""数值与标签格式化 —— 展示层的小工具

为什么独立
----------
这些函数原先散在 ``deck_content_tool`` 里，但它们既被内容层用（KPI 卡片文案），
也被渲染层用（图表刻度）。放在任一侧都会造成反向依赖，所以独立出来。

规矩（都是踩过坑之后定的）
--------------------------
* **金额单位自适应**：``≥1万`` 用「万」，否则用「元」。
  用「万」显示 30 元会得到 ``0.00 万`` —— 等于没给信息。
* **比值不在这里格式化** —— 比值必须按**合计**算（见 table_derive 的 scope），
  逐行算比值再求和会得到 1,476,800% 这种荒唐值。
"""

from __future__ import annotations

from typing import Any

__all__ = ["wan", "fmt_wan", "fmt_money", "short_label"]


def wan(value: Any) -> float:
    """元 → 万元，保留 2 位。"""
    try:
        return round(float(value) / 10000.0, 2)
    except (TypeError, ValueError):
        return 0.0


def fmt_money(value: Any) -> str:
    """金额自适应单位，避免小数额被"万"抹成 0。

    踩过的坑：单条均值 30 元，用「万」显示成 ``0.00 万`` —— 等于没给信息。
    规则：≥1 万元用「万」，否则用「元」。
    """
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(amount) >= 10000:
        return f"{amount / 10000:,.2f} 万"
    if abs(amount) >= 1:
        return f"{amount:,.0f} 元"
    return f"{amount:,.2f} 元"


def fmt_wan(value: Any) -> str:
    """历史别名，与 :func:`fmt_money` 同义。"""
    return fmt_money(value)


def short_label(column: Any, limit: int = 12) -> str:
    """把列名压成适合卡片标签的短名。

    例：``账单金额(元)`` → ``账单``；``应收金额(元)`` → ``应收``。
    """
    name = str(column)
    for junk in ("(元)", "（元）", "(万元)", "金额", "合计"):
        name = name.replace(junk, "")
    name = name.strip(" （）()")
    return name[:limit] or str(column)[:limit]
