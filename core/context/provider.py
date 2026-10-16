# -*- coding: utf-8 -*-
"""上下文来源（Context Provider）—— 记忆怎么**自动**进入上下文。

这是"记忆不是工具，而是本身就有的"这句话的落点
--------------------------------------------------
原来 ``tool/builtin/memory_tool.py`` 把记忆做成模型调用的工具：16 个参数、
9 个 action。两个问题：

1. **语义错位**。记忆不是"模型想起来要用"的能力，而是"框架保证一直在"的
   底座。模型不该需要先调 ``memory(action="search")`` 才知道用户的偏好。
2. **小模型根本用不对**。本项目主模型 qwen3.5:2b 给工具传参会随机臆造，
   从 16 个参数里选对 action + 配套参数几乎不可能。

所以改成 Provider：框架在组装 messages 时**主动**去各个来源收集上下文，
模型的能力表里看不到任何 memory 工具。

两种加载时机（对标 Claude Code 的 CLAUDE.md + 检索）
----------------------------------------------------
* ``MemoryFileProvider``   —— **启动即注入**。纯文件、零依赖，永远在。
* ``MemoryRecallProvider`` —— **按需检索**。每轮按用户输入召回 top-k。

优先级（``priority`` 越小越不可丢）决定超预算时谁先被牺牲。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

__all__ = [
    "ContextProvider",
    "MemoryFileProvider",
    "MemoryRecallProvider",
    "EnvironmentProvider",
]

logger = logging.getLogger(__name__)


@runtime_checkable
class ContextProvider(Protocol):
    """上下文来源协议。

    照 ``core/capability.py`` 的 Capability 协议写法：结构化子类型，
    任何有这三样东西的对象都能当 provider 用，不必继承。
    """

    #: 层名，会出现在预算账本与 describe() 里
    name: str
    #: 优先级，越小越重要、越不会被压缩掉
    priority: int

    def collect(self, query: str) -> str:
        """返回要注入的文本；没有内容就返回空串。"""
        ...


class MemoryFileProvider:
    """常驻记忆：启动时读记忆文件，全程注入。

    对标 Claude Code 的 CLAUDE.md / MEMORY.md：**不是检索**，而是
    "这些事情我一直记得"。纯文件读取，不碰 embedding / 向量库，
    所以永远不会因为依赖不可用而失效。

    多个路径按**从通用到具体**的顺序给（用户级 → 项目级），
    后面的更贴近当前项目，所以放在后面、离用户输入更近。
    """

    name = "常驻记忆"
    priority = 20

    def __init__(
        self,
        paths: Optional[List[str]] = None,
        max_chars: int = 4000,
    ):
        self.paths = list(paths or [])
        self.max_chars = int(max_chars)
        #: 启动即读 —— 这就是"常驻"的含义
        self._cached = self._load()

    def _load(self) -> str:
        chunks: List[str] = []
        for path in self.paths:
            try:
                if not path or not os.path.isfile(path):
                    continue
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read().strip()
                if text:
                    chunks.append(text)
            except Exception as exc:
                # 记忆文件读不出来不该拖垮 agent，降级为"没有这条记忆"
                logger.warning("读取记忆文件失败 %s: %s", path, exc)
        merged = "\n\n".join(chunks)
        if len(merged) > self.max_chars:
            merged = merged[: self.max_chars] + "\n…（记忆文件过长，已截断）"
        return merged

    def reload(self) -> None:
        """重新读盘（记忆文件被外部改动后调用）。"""
        self._cached = self._load()

    def collect(self, query: str) -> str:  # noqa: ARG002 - 常驻记忆与 query 无关
        return self._cached


class MemoryRecallProvider:
    """召回记忆：按本轮输入去记忆库检索 top-k。

    ⚠️ 这里的防御是本模块最关键的部分
    ----------------------------------
    ``MemoryManager`` 启用 semantic 记忆时，构造过程会走
    ``SemanticMemory._init_embedding_model()``，而该方法在失败时
    **直接 raise**（memory/types/semanticmemory.py）。也就是说：
    在 ``__init__`` 里建 MemoryManager，会让 **agent 启动就崩** ——
    只因为一个"有更好、没有也行"的辅助功能不可用。

    所以这里坚持两条：

    1. **懒构造**：第一次真正要检索时才建 MemoryManager，不在 __init__。
    2. **整体兜底**：构造和检索都包在 try/except 里；失败就永久关闭本
       provider（``self._disabled``），agent 照常运行。

    好消息：``MemoryManager.retrieve_memories`` 自身已对每种记忆类型做了
    try/except + log warning，**检索阶段**本来就是优雅降级的；真正危险的
    只有构造阶段。
    """

    name = "召回记忆"
    priority = 40

    def __init__(
        self,
        manager: Optional[Any] = None,
        user_id: str = "default_user",
        limit: int = 3,
        min_importance: float = 0.3,
        memory_types: Optional[List[str]] = None,
        max_chars: int = 1200,
    ):
        self._manager = manager
        self.user_id = user_id
        self.limit = int(limit)
        self.min_importance = float(min_importance)
        #: 默认**不**启用 semantic：它是唯一会在构造期 raise 的类型，
        #: 而 working/episodic 不需要 embedding 就能用。
        self.memory_types = list(memory_types or ["working", "episodic"])
        self.max_chars = int(max_chars)
        self._disabled = False

    def _ensure_manager(self) -> Optional[Any]:
        """懒构造 MemoryManager；任何失败都永久降级。"""
        if self._disabled:
            return None
        if self._manager is not None:
            return self._manager
        try:
            from memory.base import MemoryConfig
            from memory.memory_manger import MemoryManager

            self._manager = MemoryManager(
                config=MemoryConfig(),
                user_id=self.user_id,
                enable_working="working" in self.memory_types,
                enable_episodic="episodic" in self.memory_types,
                enable_semantic="semantic" in self.memory_types,
                enable_perceptual="perceptual" in self.memory_types,
            )
            return self._manager
        except Exception as exc:
            # embedding / 向量库 / 依赖缺失都走这里：关掉自己，不影响主流程
            logger.warning("记忆库不可用，已关闭记忆召回: %s", exc)
            self._disabled = True
            return None

    def collect(self, query: str) -> str:
        if not str(query or "").strip():
            return ""
        manager = self._ensure_manager()
        if manager is None:
            return ""
        try:
            results = manager.retrieve_memories(
                query=query,
                memory_types=self.memory_types,
                limit=self.limit,
                min_importance=self.min_importance,
            )
        except Exception as exc:
            logger.warning("记忆召回失败（已忽略）: %s", exc)
            return ""

        lines: List[str] = []
        for item in results or []:
            content = str(getattr(item, "content", "") or "").strip()
            if content:
                lines.append(f"- {content}")
        if not lines:
            return ""
        text = "\n".join(lines)
        if len(text) > self.max_chars:
            text = text[: self.max_chars] + "…"
        return text

    def remember(self, content: str, importance: float = 0.6,
                 memory_type: str = "working") -> bool:
        """写入一条记忆（供框架在对话结束后自动调用，**不是**给模型调的工具）。"""
        manager = self._ensure_manager()
        if manager is None or not str(content or "").strip():
            return False
        try:
            manager.add_memory(
                content=content,
                memory_type=memory_type,
                importance=importance,
                auto_classify=False,
            )
            return True
        except Exception as exc:
            logger.warning("记忆写入失败（已忽略）: %s", exc)
            return False


class EnvironmentProvider:
    """环境信息：当前时间等每轮都该刷新的事实。

    为什么需要：模型没有时钟。不注入的话它只能靠训练数据猜"今天"，
    在"本月收缴率""下月第一动作"这类汇报场景里会直接说错月份。
    """

    name = "环境"
    priority = 30

    def __init__(self, extra: Optional[Dict[str, str]] = None):
        self.extra = dict(extra or {})

    def collect(self, query: str) -> str:  # noqa: ARG002
        from datetime import datetime

        parts = [f"当前日期：{datetime.now().strftime('%Y-%m-%d')}"]
        parts.extend(f"{k}：{v}" for k, v in self.extra.items() if v)
        return "\n".join(parts)
