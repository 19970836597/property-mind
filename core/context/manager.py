# -*- coding: utf-8 -*-
"""上下文组装 —— 替代原来那三行平铺拼接。

原来长这样（agents/react_agent/react_agent.py）::

    def _build_messages(self, input_text):
        messages = [{"role": "system", "content": self.system_prompt}]
        for msg in self._history:
            messages.append({...})
        messages.append({"role": "user", "content": input_text})
        return messages

三个问题：没有记忆、没有预算、只增不减。长会话必然超窗，表现为 LLM 调用
直接报错；而且报错前没有任何迹象可查。

现在的分层（越靠前越不可丢）
----------------------------
    1 人格      system_prompt
    2 常驻记忆  MEMORY.md（启动即注入）
    3 环境      当前日期等
    4 召回记忆  按本轮输入检索
    5 历史对话  _history
    6 本轮输入  user

压缩时从后往前牺牲，细节见 ``compactor.py``。

设计取舍：为什么 provider 的产出合并进 system 而不是单独一条消息
----------------------------------------------------------------
合并进 system 的好处是消息序列保持简单的 system→user→assistant 交替，
不会打断 role 配对（ollama 对多条 system 消息的兼容性也参差不齐）。
代价是 system 段变长，但它本来就是最不该被压缩的部分，放一起正合适。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from .budget import ContextBudget, estimate_messages_tokens, estimate_tokens
from .compactor import Compactor
from .provider import ContextProvider

__all__ = ["ContextManager"]

logger = logging.getLogger(__name__)

#: 各 provider 产出在 system 段里的小标题，让模型（和人）看得清来源
_SECTION_TITLES = {
    "常驻记忆": "以下是需要长期记住的信息",
    "召回记忆": "以下是与当前问题相关的历史记忆",
    "环境": "当前环境信息",
}


class ContextManager:
    """把人格、记忆、历史、本轮输入组装成 messages，并守住预算。

    Example::

        ctx = ContextManager(
            system_prompt=MAIN_SYSTEM_PROMPT,
            providers=[MemoryFileProvider(["MEMORY.md"])],
            llm=llm,
        )
        messages = ctx.build_messages("项目经理月度汇报", history)
        print(ctx.describe())      # 看各层占了多少 token
    """

    def __init__(
        self,
        system_prompt: str = "",
        providers: Optional[Sequence[ContextProvider]] = None,
        budget: Optional[ContextBudget] = None,
        llm: Optional[Any] = None,
        compactor: Optional[Compactor] = None,
        keep_recent_turns: int = 4,
        verbose: bool = False,
    ):
        self.system_prompt = system_prompt or ""
        # 按 priority 排序：小的在前，也就是越不可丢的越靠前
        self.providers: List[ContextProvider] = sorted(
            list(providers or []),
            key=lambda p: getattr(p, "priority", 100),
        )
        self.budget = budget or ContextBudget()
        self.compactor = compactor or Compactor(
            llm=llm, keep_recent_turns=keep_recent_turns
        )
        self.verbose = bool(verbose)
        #: 最近一次压缩做了什么，供 describe() 显示
        self.last_actions: List[str] = []

    # ----------------------------------------------------------------
    # 组装
    # ----------------------------------------------------------------

    def build_messages(
        self,
        input_text: str,
        history: Optional[Sequence[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """组装本轮的 messages。

        Args:
            input_text: 本轮用户输入。
            history: 历史消息，``core.history.message.Message`` 或已是 dict。
        """
        self.budget.reset()
        self.last_actions = []

        system_content = self._build_system(input_text)
        messages: List[Dict[str, Any]] = []
        if system_content:
            messages.append({"role": "system", "content": system_content})

        hist = self._normalize_history(history)
        self.budget.record("历史对话", estimate_messages_tokens(hist))
        messages.extend(hist)

        messages.append({"role": "user", "content": input_text})
        self.budget.record("本轮输入", estimate_tokens(input_text))

        return self._enforce_budget(messages)

    def _build_system(self, query: str) -> str:
        """人格 + 各 provider 的产出，合成一段 system。"""
        parts: List[str] = []
        if self.system_prompt:
            parts.append(self.system_prompt)
            self.budget.record("人格", estimate_tokens(self.system_prompt))

        for provider in self.providers:
            name = getattr(provider, "name", provider.__class__.__name__)
            try:
                text = provider.collect(query) or ""
            except Exception as exc:
                # provider 坏掉不该让对话失败，这是框架机制该有的韧性
                logger.warning("上下文来源 %s 失败（已忽略）: %s", name, exc)
                continue
            text = str(text).strip()
            if not text:
                continue
            title = _SECTION_TITLES.get(name, name)
            block = f"## {title}\n{text}"
            parts.append(block)
            self.budget.record(name, estimate_tokens(block))

        return "\n\n".join(parts)

    @staticmethod
    def _normalize_history(history: Optional[Sequence[Any]]) -> List[Dict[str, Any]]:
        """把 Message 对象或 dict 统一成 OpenAI 格式 dict。"""
        out: List[Dict[str, Any]] = []
        for msg in history or []:
            if isinstance(msg, dict):
                out.append(dict(msg))
                continue
            # core.history.message.Message
            to_dict = getattr(msg, "to_dict", None)
            if callable(to_dict):
                try:
                    out.append(to_dict())
                    continue
                except Exception:
                    pass
            role = getattr(msg, "role", None)
            role = getattr(role, "value", role)
            out.append({
                "role": str(role or "user"),
                "content": str(getattr(msg, "content", "") or ""),
            })
        return out

    # ----------------------------------------------------------------
    # 预算
    # ----------------------------------------------------------------

    def _enforce_budget(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        total = estimate_messages_tokens(messages)
        if total <= self.budget.usable:
            return messages

        compacted, actions = self.compactor.compact(messages, self.budget.usable)
        self.last_actions = actions
        if actions and self.verbose:
            print(f"[上下文] 超预算，已压缩: {'；'.join(actions)}")
        logger.info("上下文压缩: %s", "；".join(actions) if actions else "无动作")
        return compacted

    def compact_in_place(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """给 ReAct 循环用：循环内工具结果不断累积，每步过一次预算。

        与 ``build_messages`` 的区别是它不重新组装，只在现有序列上压缩。
        """
        if estimate_messages_tokens(messages) <= self.budget.usable:
            return messages
        compacted, actions = self.compactor.compact(messages, self.budget.usable)
        if actions:
            self.last_actions = actions
            if self.verbose:
                print(f"[上下文] 循环内压缩: {'；'.join(actions)}")
        return compacted

    # ----------------------------------------------------------------
    # 检视（对标 /context）
    # ----------------------------------------------------------------

    def describe(self) -> str:
        """打印分层 token 占用 —— 排查"为什么又超窗了"的第一手工具。"""
        lines = [self.budget.describe()]
        if self.providers:
            names = "、".join(
                getattr(p, "name", p.__class__.__name__) for p in self.providers
            )
            lines.append(f"上下文来源: {names}")
        if self.last_actions:
            lines.append(f"最近压缩动作: {'；'.join(self.last_actions)}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"ContextManager(providers={len(self.providers)}, "
            f"budget={self.budget.usable})"
        )
