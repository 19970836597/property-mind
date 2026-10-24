# -*- coding: utf-8 -*-
"""自主抢答：无 @ 时各成员按“这条消息跟我有没有关系”自荐。

**零新增 LLM 调用**——复用 ``core/capability.py`` 里 ``match()`` 的词面逻辑
（tag 整体命中 + 原串命中 + 字符 bigram 门槛），只是把“返回 list”改成
“返回 (分数, 原因)”，让 manager 能排序、能打日志、能调阈值。

如果以后想升级成 LLM 自荐打分，只需把 ``willingness()`` 换掉，
``select_speakers()`` 的签名不用变。
"""

from __future__ import annotations

from typing import Any, List, Sequence, Tuple

from core.capability import capability_tags

#: 举手门槛：与 match() 的 need = max(2, len*0.5) 对齐，
#: tag 整体命中（+2）即达线，纯 bigram 命中要过双窗口门槛。
DEFAULT_THRESHOLD = 2.0


def _char_bigrams(text: str) -> set:
    """字符级 2-gram（与 match() 同款，中英通吃，零分词依赖）。"""
    text = (text or "").strip().lower()
    if len(text) < 2:
        return {text} if text else set()
    grams = {text[i:i + 2] for i in range(len(text) - 1)}
    return {g for g in grams if g.strip()}


def _score_capability(capability: Any, lowered: str, grams: set) -> Tuple[float, str]:
    """单个能力的词面得分（match() 的打分逻辑，打包成分数+原因）。"""
    name = str(getattr(capability, "name", ""))
    desc = str(getattr(capability, "description", "") or "")
    tags = capability_tags(capability)
    haystack = " ".join([name, desc, " ".join(tags)]).lower()

    score = 0.0
    reasons: List[str] = []

    hit_tags = [t for t in tags if t and t.lower() in lowered]
    if hit_tags:
        score += 2.0
        reasons.append(f"命中tag:{'/'.join(hit_tags)}")

    if lowered and lowered in haystack:
        score += 5.0
        reasons.append("原串命中")

    matched = sum(1 for g in grams if g in haystack)
    need = max(2, int(len(grams) * 0.5)) if grams else 2
    if grams and matched >= need:
        score += float(matched)
        reasons.append(f"词面命中{matched}处")

    return score, "；".join(reasons)


def _member_router(member: Any) -> Any:
    """取成员的 router（GroupMember.router 或 member.agent.router）。"""
    router = getattr(member, "router", None)
    if router is not None:
        return router
    agent = getattr(member, "agent", None)
    return getattr(agent, "router", None)


def willingness(member: Any, query: str) -> Tuple[float, str]:
    """成员对本轮 query 的自荐分 = 其 router 里最高分能力的得分。

    Returns:
        (分数, 原因)。router 为空 / query 为空时返回 (0.0, "无能力/无输入")。
    """
    if not str(query or "").strip():
        return 0.0, "输入为空"
    router = _member_router(member)
    if router is None:
        return 0.0, "成员没有 router"
    try:
        capabilities = list(router)
    except TypeError:
        return 0.0, "router 不可遍历"

    lowered = str(query).strip().lower()
    grams = _char_bigrams(lowered)
    best, reason = 0.0, "无匹配"
    for cap in capabilities:
        score, why = _score_capability(cap, lowered, grams)
        if score > best:
            best, reason = score, (
                f"{getattr(cap, 'name', '?')}（{why}）" if why else str(getattr(cap, "name", "?"))
            )
    return best, reason


def select_speakers(
    members: Sequence[Any],
    query: str,
    threshold: float = DEFAULT_THRESHOLD,
) -> List[Tuple[Any, float, str]]:
    """按自荐分筛选发言人（分数从高到低）。

    没人达线返回空列表——调用方此时让 manager 兜底直答，
    不硬点名（硬点名在小模型上等于幻觉）。
    """
    scored: List[Tuple[Any, float, str]] = []
    for member in members or []:
        score, reason = willingness(member, query)
        if score >= threshold:
            scored.append((member, score, reason))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored
