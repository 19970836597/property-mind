# -*- coding: utf-8 -*-
"""记忆共享：群共享库 + 私有/共享双路检索。

三层结构（见群聊计划定稿）：

* 成员私有库 —— 各成员自己的 ``MemoryRecallProvider`` 背后的 manager，
  存领域偏好与内部状态，他人不可见；
* 群共享库（``SharedMemory``）—— 同一 session 下唯一的 ``MemoryManager``
  （working + episodic，与现有默认一致；**不开 semantic**——它构造期会
  raise，前车之鉴见 provider.py），存群事实与结论，所有人可见；
* manager 习惯库 —— manager 私有（表达偏好）+ ``MEMORY.md`` 落盘，
  由 ``group_chat.py`` 维护，这里只提供群共享这一半。

成员检索 = 私有 ``retrieve()`` + 共享 ``recall()`` 两路合并
（``merged_recall``，私有优先、按正文去重）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

__all__ = ["SharedMemory", "SharedRecallProvider", "build_shared_memory"]


class SharedMemory:
    """群共享记忆库：包一层共享的 ``MemoryManager``，按 session 打标。

    读写失败全部收敛（记忆是锦上添花，不能崩掉一轮群聊）。
    """

    def __init__(
        self,
        manager: Optional[Any] = None,
        user_id: str = "default_user",
        session_id: str = "group-default",
        limit: int = 3,
        min_importance: float = 0.0,
        memory_types: Optional[Sequence[str]] = None,
        max_chars: int = 1200,
    ):
        self.user_id = user_id
        self.session_id = session_id
        self.limit = int(limit)
        self.min_importance = float(min_importance)
        #: 与现有默认一致：只开 working/episodic
        self.memory_types = list(memory_types or ["working", "episodic"])
        self.max_chars = int(max_chars)
        self._manager = manager
        self._disabled = False

    # ------------------------------------------------------------------
    # 构造（懒 + 兜底：与 MemoryRecallProvider 同款防御）
    # ------------------------------------------------------------------

    def _ensure_manager(self) -> Optional[Any]:
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
                enable_perceptual=False,
                enable_semantic=False,
            )
            return self._manager
        except Exception:
            self._disabled = True
            return None

    # ------------------------------------------------------------------
    # 写：结论双写时调这里（私有那份由各成员自己的 provider 写）
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        speaker: str = "",
        importance: float = 0.6,
        memory_type: str = "working",
        session_id: Optional[str] = None,
    ) -> bool:
        """写入一条群共享记忆，metadata 带 session/speaker，静默失败。"""
        manager = self._ensure_manager()
        if manager is None or not str(content or "").strip():
            return False
        try:
            manager.add_memory(
                content=str(content),
                memory_type=memory_type,
                importance=float(importance),
                metadata={
                    "session_id": session_id or self.session_id,
                    "speaker": speaker,
                    "participants": [speaker] if speaker else [],
                    "scope": "group-shared",
                },
                auto_classify=False,
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 读：按本轮输入召回群共享结论
    # ------------------------------------------------------------------

    def recall(
        self,
        query: str,
        limit: Optional[int] = None,
        memory_types: Optional[Sequence[str]] = None,
        min_importance: Optional[float] = None,
    ) -> List[Any]:
        manager = self._ensure_manager()
        if manager is None or not str(query or "").strip():
            return []
        try:
            items = list(
                manager.retrieve_memories(
                    query=str(query),
                    memory_types=list(memory_types or self.memory_types),
                    limit=int(limit or self.limit),
                    min_importance=(
                        self.min_importance
                        if min_importance is None
                        else float(min_importance)
                    ),
                )
                or []
            )
        except Exception:
            return []
        # 多群并存时 episodic 落同一个 sqlite 库：按 session_id 后过滤，
        # 否则 A 群的结论会在 B 群被召回（working 每群独立 manager，不串）。
        out: List[Any] = []
        for item in items:
            meta = getattr(item, "metadata", None) or {}
            sid = str(meta.get("session_id", "") or "")
            if sid and sid != self.session_id:
                continue
            out.append(item)
        return out

    # ------------------------------------------------------------------
    # 双路合并：私有优先、按正文去重
    # ------------------------------------------------------------------

    @staticmethod
    def merged_recall(
        private_items: Sequence[Any],
        shared_items: Sequence[Any],
        limit: int = 5,
    ) -> List[Any]:
        """合并私有与共享两路召回结果。"""
        merged: List[Any] = []
        seen = set()

        def _key(item: Any) -> str:
            return str(getattr(item, "content", "") or "").strip()

        for item in list(private_items or []) + list(shared_items or []):
            key = _key(item)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= max(1, int(limit)):
                break
        return merged


def build_shared_memory(
    user_id: str = "default_user",
    session_id: str = "group-default",
    manager: Optional[Any] = None,
    **kwargs: Any,
) -> SharedMemory:
    """构造群共享库（测试可注入 stub manager，避免碰 sqlite/embedding）。"""
    return SharedMemory(
        manager=manager, user_id=user_id, session_id=session_id, **kwargs
    )


class SharedRecallProvider:
    """群共享召回 provider：实现 ContextProvider 协议，直接挂进成员的
    ``ContextManager.providers``，组装 messages 时自动注入共享结论。

    一个实例可被多个成员复用（collect 只依赖 query，无状态）。
    """

    name = "群共享记忆"
    priority = 45

    def __init__(self, shared: SharedMemory):
        self.shared = shared

    def collect(self, query: str) -> str:
        if self.shared is None or not str(query or "").strip():
            return ""
        try:
            items = self.shared.recall(query)
        except Exception:
            return ""
        lines: List[str] = []
        for item in items or []:
            content = str(getattr(item, "content", "") or "").strip()
            if not content:
                continue
            meta: Dict[str, Any] = getattr(item, "metadata", None) or {}
            speaker = str(meta.get("speaker", "") or "").strip()
            lines.append(f"- [{speaker}] {content}" if speaker else f"- {content}")
        if not lines:
            return ""
        text = "\n".join(lines)
        if len(text) > self.shared.max_chars:
            text = text[: self.shared.max_chars] + "…"
        return text
