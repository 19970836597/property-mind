# -*- coding: utf-8 -*-
"""超预算时的压缩策略 —— **先裁谁**。

压缩顺序（从先到后牺牲）
------------------------
1. **旧的工具返回结果**  最占 token（整张表 dump 动辄几千字），
   且时效最短 —— 上一轮的查询结果对本轮通常已无用。
2. **旧的对话轮次**      保留最近 N 轮原文，更早的折成摘要。
3. 召回记忆              可以重新检索，丢了不心疼。
4. 常驻记忆 / 人格       **绝不动**。丢了 agent 就失忆、失格。

这个顺序不是随便排的：它按"重建成本 ÷ token 成本"排序。工具结果重建成本
最低（再查一次就有）而 token 成本最高，所以最先走。

约束：消息序列必须保持合法
--------------------------
OpenAI 格式要求 ``role="tool"`` 的消息必须紧跟在带 ``tool_calls`` 的
assistant 消息之后。所以压缩工具结果时只能**改写 content**，不能删除整条
消息 —— 删了会把配对关系打断，请求直接 400。这是实现上最容易踩的坑。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .budget import estimate_messages_tokens, estimate_tokens

__all__ = ["Compactor", "TOOL_RESULT_KEEP_CHARS"]

logger = logging.getLogger(__name__)

#: 裁剪后的工具结果保留多少字符。够看出"查到了什么"，不够复述整张表。
TOOL_RESULT_KEEP_CHARS = 600

#: 摘要提示词：把多轮对话压成要点。刻意要求"保留结论与数字"——
#: 汇报场景里丢了数字的摘要等于没有。
_SUMMARY_PROMPT = """\
把下面的对话历史压缩成简洁要点，供后续对话参考。

要求：
1. 保留**结论、数字、已确定的决策**（这些丢了就等于没压缩对）。
2. 保留用户明确提出的要求与偏好。
3. 丢掉寒暄、过程性描述、已被后续推翻的内容。
4. 直接输出要点列表，不要加前言。

对话历史：
{history}
"""


class Compactor:
    """按固定顺序压缩 messages，直到落回预算内。

    ``llm`` 可选：给了就能把旧对话折成摘要；没给（或摘要失败）就退化为
    直接丢弃旧轮次。**摘要永远不能成为必需品** —— 它要调一次 LLM，又慢
    又可能失败，不能让主流程卡在它身上。
    """

    def __init__(
        self,
        llm: Optional[Any] = None,#压缩大模型
        keep_recent_turns: int = 4,
        tool_result_keep_chars: int = TOOL_RESULT_KEEP_CHARS,
    ):
        self.llm = llm
        self.keep_recent_turns = max(1, int(keep_recent_turns))
        self.tool_result_keep_chars = int(tool_result_keep_chars)

    # ----------------------------------------------------------------
    # 对外入口
    # ----------------------------------------------------------------

    def summarize(
        self, messages: List[Dict[str, Any]]
    ) -> Optional[str]:
        """把整段对话折成一条摘要（``/compact`` 命令用）。

        与 ``compact()`` 的区别：那是"超预算时被动裁"，这是"用户主动把
        全部历史收拢成一段"。没有 llm 或摘要失败返回 None（调用方保持
        原历史不变并告知用户）。
        """
        body = [m for m in messages or [] if (m or {}).get("role") != "system"]
        if not body:
            return None
        return self._summarize(body) or None

    def compact(
        self,
        messages: List[Dict[str, Any]],
        usable: int,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """把 messages 压到 usable 以内。

        Returns:
            (压缩后的 messages, 做了哪些动作的说明列表)
        """
        actions: List[str] = []
        work = list(messages or [])

        if estimate_messages_tokens(work) <= usable:
            return work, actions

        # ---- 第 1 顺位：裁旧的工具结果 ----
        work, trimmed = self._trim_tool_results(work)
        if trimmed:
            actions.append(f"裁剪 {trimmed} 条工具返回结果")
            if estimate_messages_tokens(work) <= usable:
                return work, actions

        # ---- 第 2 顺位：旧对话折摘要 ----
        work, note = self._fold_history(work)
        if note:
            actions.append(note)
            if estimate_messages_tokens(work) <= usable:
                return work, actions

        # ---- 兜底：仍然超预算就硬截最早的非系统消息 ----
        work, dropped = self._drop_oldest(work, usable)
        if dropped:
            actions.append(f"丢弃最早 {dropped} 条消息")

        return work, actions

    # ----------------------------------------------------------------
    # 各顺位的具体动作
    # ----------------------------------------------------------------

    def _trim_tool_results(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], int]:
        """截断工具结果的 content。

        **只改写、不删除** —— role="tool" 必须与前面的 tool_calls 配对，
        删掉会让请求直接报 400。最后一条工具结果保持原样：它往往正是
        模型当前要依据的观察。
        """
        tool_indexes = [
            i for i, m in enumerate(messages)
            if (m or {}).get("role") == "tool"
        ]
        if len(tool_indexes) <= 1:
            return messages, 0

        out = [dict(m) for m in messages]
        count = 0
        # 留最后一条不动
        for i in tool_indexes[:-1]:
            content = str(out[i].get("content") or "")
            if len(content) <= self.tool_result_keep_chars:
                continue
            omitted = len(content) - self.tool_result_keep_chars
            out[i]["content"] = (
                content[: self.tool_result_keep_chars]
                + f"\n…[已省略 {omitted:,} 字，如需完整结果请重新查询]"
            )
            count += 1
        return out, count

    def _fold_history(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], str]:
        """保留最近若干轮，更早的折成一条摘要。

        切点选在 **user 消息**上不是随意的：tool_calls/tool 配对永远发生在
        两次 user 输入之间，所以按 user 切能保证配对不会被劈成两半
        （改这里的切点逻辑时务必保持这个性质）。
        """
        # system 永远留在最前。注意这会把原本夹在中间的 system 消息
        # （比如上一次压缩留下的摘要）提到最前面 —— 这是故意的：
        # 摘要属于背景信息，放在人格后面、历史前面最合适。
        head = [m for m in messages if (m or {}).get("role") == "system"]
        body = [m for m in messages if (m or {}).get("role") != "system"]

        # 以 user 消息为轮次边界，找出"最近 N 轮"的起点
        user_positions = [
            i for i, m in enumerate(body) if (m or {}).get("role") == "user"
        ]
        if len(user_positions) <= self.keep_recent_turns:
            return messages, ""

        cut = user_positions[-self.keep_recent_turns]
        old, recent = body[:cut], body[cut:]
        if not old:
            return messages, ""

        summary = self._summarize(old)  #生成摘要 优化点
        if summary:
            folded = [{
                "role": "system",
                "content": f"[早前对话摘要]\n{summary}",
            }]
            note = f"{len(old)} 条旧消息折为摘要"
        else:
            folded = []
            note = f"丢弃 {len(old)} 条旧消息（摘要不可用）"

        return head + folded + recent, note

    def _summarize(self, old: List[Dict[str, Any]]) -> str:
        """调 LLM 生成摘要；没有 llm 或失败都返回空串（调用方会退化为丢弃）。"""
        if self.llm is None:
            return ""
        transcript = "\n".join(
            f"{(m or {}).get('role')}: {str((m or {}).get('content') or '')[:500]}"
            for m in old
        )
        if not transcript.strip():
            return ""
        try:
            response = self.llm.invoke(
                messages=[{
                    "role": "user",
                    "content": _SUMMARY_PROMPT.format(history=transcript),
                }],
            )
            text = str(getattr(response, "content", "") or "").strip()
            if not text:
                # 思考型小模型可能把正文放在 reasoning_content
                text = str(
                    getattr(response, "reasoning_content", "") or ""
                ).strip()
            return text
        except Exception as exc:
            logger.warning("历史摘要生成失败，退化为直接丢弃: %s", exc)
            return ""

    def _drop_oldest(
        self, messages: List[Dict[str, Any]], usable: int
    ) -> Tuple[List[Dict[str, Any]], int]:
        """最后兜底：从最早的非 system 消息开始丢，直到进预算。

        **成组丢弃**：带 ``tool_calls`` 的 assistant 消息必须和它后面配对的
        ``role="tool"`` 消息一起丢。只丢前者会留下孤立的 tool 消息，请求
        直接 400 —— 正是本模块开头警告的那个坑，这里是它的第二个入口。

        保证至少留下最后一条消息 —— 否则就没有"当前问题"了。
        """
        out = list(messages)
        dropped = 0
        while estimate_messages_tokens(out) > usable:
            victim = next(
                (i for i, m in enumerate(out)
                 if (m or {}).get("role") != "system"),
                None,
            )
            if victim is None:
                break
            # 只剩最后一条非 system 消息时停手，再丢就没有对话了
            non_system = [
                i for i, m in enumerate(out)
                if (m or {}).get("role") != "system"
            ]
            if len(non_system) <= 1:
                break

            # 连带后面紧跟的 tool 结果一起丢，保持 tool_calls 配对完整
            group_end = victim + 1
            if (out[victim] or {}).get("tool_calls"):
                while group_end < len(out) and \
                        (out[group_end] or {}).get("role") == "tool":
                    group_end += 1

            del out[victim:group_end]
            dropped += group_end - victim
        return out, dropped

    @staticmethod
    def estimate(messages: List[Dict[str, Any]]) -> int:
        """便捷转发，省得调用方再 import budget。"""
        return estimate_messages_tokens(messages)
