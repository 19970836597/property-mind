# -*- coding: utf-8 -*-
"""群聊（GroupChat）：@指定 + 自主抢答 + 记忆共享。

成员是现有子 agent 拆出来的内部 ``ReActAgent``（见 main.build_sub_agents），
manager 由主 agent 兼任（只做选人/整理/发布/记习惯，不亲自答业务题）。

单轮流程::

    用户消息（含可选 @）
      → parse_mentions / willingness 自荐（零新增 LLM 调用）
      → 命中成员依次 run()（串行，raw 输出暂存，不进群）
      → manager 按习惯改写整理（一次 LLM 调用，失败降级用原文）
      → 以一条 manager 消息发布进 SharedHistory
      → 轮末记习惯偏好（只记表达偏好）+ 结论双写（私有 + 共享）
"""

from .group_chat import (
    ChatTurn,
    GroupChatManager,
    GroupMember,
    attach_shared_provider,
    build_groupchat,
    describe_pool,
    detach_shared_provider,
)
from .mention import extract_mentions
from .registry import DEFAULT_GROUP_NAME, GroupRegistry
from .shared_memory import SharedMemory, build_shared_memory
from .speaker_selector import select_speakers, willingness

__all__ = [
    "ChatTurn",
    "DEFAULT_GROUP_NAME",
    "GroupChatManager",
    "GroupMember",
    "GroupRegistry",
    "SharedMemory",
    "attach_shared_provider",
    "build_groupchat",
    "build_shared_memory",
    "describe_pool",
    "detach_shared_provider",
    "extract_mentions",
    "select_speakers",
    "willingness",
]
