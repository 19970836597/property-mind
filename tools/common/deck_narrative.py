# -*- coding: utf-8 -*-
"""叙事模板引擎（Narrative Engine）—— 把「事实」变成「判断」

解决什么问题
------------
纯数据驱动的结论句只能陈述事实：

    ❌ "物业服务费 合计 482,731.40（占比 99.7%），是该项最大来源。"

这是把数字念了一遍，没有分析。专业结论需要**领域知识**：

    ✅ "产品A 占收入 99.7%，结构单一、抗风险能力弱；
        下月应把第二增长曲线作为专项来抓。"

「单一收入结构是风险」不是数据里有的，是行业经验。

怎么做到
--------
把领域知识写成**声明式模板**（放在技能的 spec 里，不是代码里）：

    {
      "when": "top_share > 0.9",
      "so_what": "{top_name}占 {top_share_pct}%，结构单一、抗风险能力弱，"
                 "下月应把 {gap_name} 作为专项来抓。"
    }

引擎负责两件事：
    1. 按 ``when`` 条件从模板列表里挑出适用的那条（第一个命中的优先）
    2. 用真实数字填充 ``{占位符}``

条件语言
--------
刻意做得极简（够用即可，避免变成一个半成品表达式引擎）：

    top_share > 0.9          top_share >= 0.9       top_share < 0.5
    gap > 0                  total > 10000
    has(gap_name)            没有 gap_name
    top_share > 0.9 and gap > 0
    top_share > 0.9 or gap > 0

占位符
------
``{top_name}`` ``{top_share_pct}`` ``{total}`` …
另外提供内置格式化后缀：

    {total:万}      除以 10000 后保留 2 位
    {share:1%}      乘 100 保留 1 位再加 %
    {count:,}       千分位

未知占位符保留原样（``{xxx}``），便于一眼看出模板写错了 —— 而不是静默填空。
"""

from __future__ import annotations

import operator
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "evaluate_condition",
    "render_template",
    "pick_narrative",
    "apply_narrative",
    "extract_facts",
]


# ===================================================================
# 1) 事实提取（纯统计，不含任何行业知识）
# ===================================================================

def extract_facts(
    total: float = 0.0,
    count: int = 0,
    groups: Optional[Sequence[Dict[str, Any]]] = None,
    prev_total: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """从「分组聚合结果」抽出可用于模板的通用事实。

    Args:
        total: 当期合计。
        count: 记录数。
        groups: ``[{"name": ..., "value": ...}, ...]``，需已按 value 降序。
        prev_total: 上期合计（用于增长率）。
        extra: 额外事实（业务方自己算的），会合并进来。

    Returns:
        事实字典。占位符直接用键名引用。
    """
    groups = list(groups or [])
    facts: Dict[str, Any] = {
        "total": round(float(total), 2),
        "count": int(count),
    }

    if groups:
        top = groups[0]
        bottom = groups[-1]
        top_value = float(top["value"])
        facts.update({
            "top_name": str(top["name"]),
            "top_value": round(top_value, 2),
            "top_share": round(top_value / total, 4) if total else 0.0,
            "top_share_pct": round(top_value / total * 100, 1) if total else 0.0,
            "group_count": len(groups),
            "bottom_name": str(bottom["name"]),
            "bottom_value": round(float(bottom["value"]), 2),
            "bottom_share": round(float(bottom["value"]) / total, 4) if total else 0.0,
            "bottom_share_pct": round(float(bottom["value"]) / total * 100, 1)
            if total else 0.0,
            "has_groups": True,
        })
        shares = [float(g["value"]) / total for g in groups] if total else []
        if shares:
            facts["max_share"] = round(max(shares), 4)
            facts["min_share"] = round(min(shares), 4)
            facts["avg_share"] = round(sum(shares) / len(shares), 4)
    else:
        facts.update({
            "top_name": "", "top_value": 0.0, "top_share": 0.0,
            "top_share_pct": 0.0, "group_count": 0,
            "bottom_name": "", "bottom_value": 0.0,
            "bottom_share": 0.0, "bottom_share_pct": 0.0,
            "has_groups": False,
        })

    # 缺口：金额最大的负向项（若调用方用 extra 传了 diff 结构）
    if prev_total is not None and prev_total > 0:
        facts["prev_total"] = round(float(prev_total), 2)
        facts["growth"] = round((total - prev_total) / prev_total, 4)
        facts["growth_pct"] = round((total - prev_total) / prev_total * 100, 1)
    else:
        facts["prev_total"] = None
        facts["growth"] = None
        facts["growth_pct"] = None

    if extra:
        facts.update(extra)

    return facts


# ===================================================================
# 2) 条件求值
# ===================================================================

_COMPARATORS = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "==": operator.eq,
    "!=": operator.ne,
}

#: 标识符：允许中文/其他 Unicode 词字符，也允许括号（列名常带 "(元)"）。
#: 踩过的坑：原先写死 ``[A-Za-z_]``，于是 spec 里 ``指标名 < 95`` 这种
#: 中文指标名永远解析不了，条件静默失配、结论句掉到默认条款 ——
#: 而"没报错"让人以为是数据问题。
_IDENT = r"[^\s<>=!,]+"
_IDENT_NO_PAREN = r"[^\s<>=!(),]+"

#: 支持 "has(x)" / "empty(x)" 两种断言
_FUNC_RE = re.compile(rf"^(has|empty|exists)\s*\(\s*({_IDENT_NO_PAREN})\s*\)$")
_CMP_RE = re.compile(rf"^({_IDENT})\s*(>=|<=|==|!=|>|<)\s*(.+)$")
_NOT_RE = re.compile(r"^(?:not|!)\s+(.+)$")
_NAME_RE = re.compile(rf"^({_IDENT})$")


def _coerce(text: str, facts: Dict[str, Any]) -> Any:
    """把条件右侧的字面量转成合适的类型。"""
    raw = text.strip()
    if not raw:
        return ""
    if raw.startswith(("'", '"')) and raw.endswith(("'", '"')) and len(raw) >= 2:
        return raw[1:-1]
    low = raw.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "none"):
        return None
    # 事实名引用
    if raw in facts:
        return facts[raw]
    try:
        return float(raw) if ("." in raw or "e" in low) else int(raw)
    except ValueError:
        return raw


def _eval_atom(text: str, facts: Dict[str, Any]) -> bool:
    atom = text.strip()
    if not atom:
        return False

    negate = False
    while True:
        m = _NOT_RE.match(atom)
        if not m:
            break
        negate = not negate
        atom = m.group(1).strip()

    result = _eval_atom_positive(atom, facts)
    return (not result) if negate else result


def _eval_atom_positive(atom: str, facts: Dict[str, Any]) -> bool:
    func = _FUNC_RE.match(atom)
    if func:
        kind, name = func.group(1), func.group(2)
        value = facts.get(name)
        if kind in ("has", "exists"):
            return bool(value) and value != 0
        return not value or value == 0

    plain = _NAME_RE.match(atom)
    if plain:
        return bool(facts.get(plain.group(1)))

    cmp = _CMP_RE.match(atom)
    if cmp:
        name, op, rhs_text = cmp.group(1), cmp.group(2), cmp.group(3)
        lhs = facts.get(name)
        rhs = _coerce(rhs_text, facts)
        if lhs is None:
            return False
        try:
            return bool(_COMPARATORS[op](lhs, rhs))
        except TypeError:
            # 类型不可比（例如 str vs float）→ 视为不命中，而不是崩
            return False

    # 无法解析 → 不命中（引擎要宽容，不能因为一条规则写错就整体失败）
    return False


def evaluate_condition(condition: str, facts: Dict[str, Any]) -> bool:
    """求值一条条件表达式。

    支持 ``and`` / ``or``（``and`` 优先级更高），以及 ``not`` / ``!``。
    空字符串视为 True（表示"默认条款"）。
    """
    if condition is None:
        return True
    text = str(condition).strip()
    if not text:
        return True

    for or_part in re.split(r"\s+or\s+", text, flags=re.IGNORECASE):
        and_parts = re.split(r"\s+and\s+", or_part, flags=re.IGNORECASE)
        if all(_eval_atom(part, facts) for part in and_parts):
            return True
    return False


# ===================================================================
# 3) 模板渲染
# ===================================================================

#: 占位符：``{名字}`` 或 ``{名字:格式}``。
#: 名字允许中文与括号（如 ``{账单金额(元)}`` ``{指标名:.2f}``）——
#: 踩过的坑：原先名字限制 ``[A-Za-z_]``，中文占位符永远替换不掉，
#: 生成的 PPT 里直接出现 ``{指标名:.2f}`` 这种花括号。
_PLACEHOLDER_RE = re.compile(r"\{([^{}:]+?)(?::([^{}]*))?\}")


def _format_value(value: Any, spec: str) -> str:
    """按 ``{name:spec}`` 里的 spec 格式化。"""
    spec = (spec or "").strip()
    if not spec:
        return str(value)

    # 百分比：{share:1%}
    if spec.endswith("%"):
        digits = spec[:-1].strip()
        try:
            num = float(value)
        except (TypeError, ValueError):
            return str(value)
        nd = int(digits) if digits.isdigit() else 1
        return f"{num * 100:.{nd}f}%"

    # 万元：{total:万}
    if spec in ("万", "wan", "w"):
        try:
            return f"{float(value) / 10000:.2f}"
        except (TypeError, ValueError):
            return str(value)

    # 千分位：{count:,}
    if spec == ",":
        try:
            return f"{float(value):,.0f}"
        except (TypeError, ValueError):
            return str(value)

    # 小数位：{value:.2f} 或 {value:2}
    digits_match = re.fullmatch(r"\.?(\d+)(?:f)?", spec)
    if digits_match:
        try:
            return f"{float(value):.{int(digits_match.group(1))}f}"
        except (TypeError, ValueError):
            return str(value)

    return str(value)


def render_template(template: str, facts: Dict[str, Any]) -> str:
    """用事实填充模板中的 ``{占位符}``。

    未知占位符**保留原样**（如 ``{xxx}``）—— 便于一眼看出模板写错，
    而不是静默留空让人以为没问题。
    """
    if not template:
        return ""

    def replace(match: re.Match) -> str:
        name, spec = match.group(1), match.group(2)
        if name not in facts:
            return match.group(0)
        value = facts[name]
        if value is None:
            return "—"
        return _format_value(value, spec or "")

    return _PLACEHOLDER_RE.sub(replace, str(template))


# ===================================================================
# 4) 叙事选择
# ===================================================================

def pick_narrative(
    rules: Sequence[Dict[str, Any]],
    facts: Dict[str, Any],
    key: str = "so_what",
) -> str:
    """从规则列表里挑出第一个命中的模板并渲染。

    Args:
        rules: ``[{"when": "...", "so_what": "..."}, ...]``，按优先级排列。
        facts: 事实字典。
        key: 模板字段名（默认 ``so_what``，也可用 ``title`` 等）。

    Returns:
        渲染后的文本；没有命中则返回空串。
    """
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        if evaluate_condition(rule.get("when", ""), facts):
            return render_template(str(rule.get(key) or ""), facts)
    return ""


def apply_narrative(
    spec_sections: Sequence[Dict[str, Any]],
    facts_by_section: Dict[str, Dict[str, Any]],
    key: str = "so_what",
) -> Dict[str, Dict[str, str]]:
    """对 spec 里的多个段落批量套用叙事规则。

    Returns:
        ``{section_key: {key: 渲染结果}}``
    """
    out: Dict[str, Dict[str, str]] = {}
    for section in spec_sections or []:
        if not isinstance(section, dict):
            continue
        section_key = section.get("key")
        if not section_key:
            continue

        facts = facts_by_section.get(section_key) or {}

        # so_what 与 title 各自独立处理 ——
        # 早先这里 `if not rules: continue` 会把「只有 title_narrative
        # 没有 narrative」的段落整个跳过，标题模板永远不生效。
        rules = section.get("narrative")
        if rules:
            text = pick_narrative(rules, facts, key=key)
            if text:
                out.setdefault(section_key, {})[key] = text

        title_rules = section.get("title_narrative")
        if title_rules:
            title = pick_narrative(title_rules, facts, key="title")
            if title:
                out.setdefault(section_key, {})["title"] = title

    return out
