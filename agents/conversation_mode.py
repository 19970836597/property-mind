# -*- coding: utf-8 -*-
"""对话模式选择与本会话内消息路由。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional


InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


class ConversationMode(str, Enum):
    """一次 CLI 会话固定的对话模式。"""

    QUICK = "quick"
    COLLABORATION = "collaboration"

    @property
    def display_name(self) -> str:
        if self is ConversationMode.QUICK:
            return "快速模式"
        return "协作模式"


@dataclass(frozen=True)
class ConversationSession:
    """当前 CLI 运行的会话元数据；模式创建后不可修改。"""

    mode: ConversationMode

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ConversationMode):
            raise TypeError("mode 必须是 ConversationMode 枚举值")

    @property
    def group_chat_enabled(self) -> bool:
        return self.mode is ConversationMode.COLLABORATION

    def dispatch(
        self,
        text: str,
        *,
        quick_handler: Callable[[str], None],
        group_handler: Callable[[str], None],
    ) -> None:
        """按不可变模式把普通消息交给唯一对应的处理器。"""
        if self.mode is ConversationMode.QUICK:
            quick_handler(text)
        else:
            group_handler(text)


def parse_conversation_mode(raw: str) -> ConversationMode:
    """解析模式输入；空值默认快速模式，非法值抛出 ValueError。"""
    value = str(raw or "").strip().lower()
    if value in ("", "1", "quick", "fast", "快速", "快速模式"):
        return ConversationMode.QUICK
    if value in (
        "2",
        "collab",
        "collaboration",
        "group",
        "群聊",
        "群聊模式",
        "协作",
        "协作模式",
    ):
        return ConversationMode.COLLABORATION
    raise ValueError(f"未知对话模式：{raw}")


def choose_conversation_mode(
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
) -> Optional[ConversationMode]:
    """在启动时选择对话模式；EOF/中断返回 None。"""
    output_fn("")
    output_fn("请选择对话模式（创建后本会话不可切换）：")
    output_fn("  1. 快速模式：主 Agent 自动调度子 Agent")
    output_fn("  2. 协作模式：进入群聊，由多成员协作回答")

    while True:
        try:
            raw = input_fn("选择 [1]: ")
        except (EOFError, KeyboardInterrupt):
            output_fn("\n未选择对话模式，已退出。")
            return None
        try:
            return parse_conversation_mode(raw)
        except ValueError:
            output_fn("无效选择，请输入 1（快速模式）或 2（协作模式）。")


def choose_collaboration_group(
    registry: Any,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
) -> Optional[Any]:
    """在协作模式中选择现有群、默认群或新建群；取消时返回 None。"""
    while True:
        names = list(registry.names())
        output_fn("")
        output_fn("请选择协作群聊：")
        for index, name in enumerate(names, start=1):
            chat = registry.groups.get(name)
            members = len(getattr(chat, "members", []) or [])
            active = "，活跃" if name == registry.active_name else ""
            output_fn(f"  {index}. {name}（{members} 位成员{active}）")
        new_index = len(names) + 1
        output_fn(f"  {new_index}. 新建群聊")
        output_fn("  0. 退出")

        try:
            raw = input_fn("选择 [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("\n未选择群聊，已退出。")
            return None

        if not raw:
            raw = "1"
        if raw == "0":
            return None

        try:
            choice = int(raw)
        except ValueError:
            output_fn("无效选择，请输入列表中的编号。")
            continue

        if 1 <= choice <= len(names):
            try:
                return registry.switch(names[choice - 1])
            except (ValueError, KeyError) as exc:
                output_fn(f"切换群聊失败：{exc}")
                continue

        if choice == new_index:
            try:
                name = input_fn("群名：").strip()
                if not name:
                    raise ValueError("群名不能为空。")
                members = input_fn(
                    "成员（例如 @催费 @法务，至少一个）："
                ).strip()
                if not members:
                    raise ValueError("新建群聊至少需要指定一个成员。")
                chat = registry.create(name, raw_mentions=members)
                output_fn(f"已进入协作群聊「{name}」。")
                return chat
            except (EOFError, KeyboardInterrupt):
                output_fn("\n未完成建群，已取消。")
                return None
            except Exception as exc:
                output_fn(f"建群失败：{exc}")
                continue

        output_fn("无效选择，请输入列表中的编号。")
