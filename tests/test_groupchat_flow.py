# -*- coding: utf-8 -*-
"""群聊端到端测试：manager 兜底 + 发布流程（stub LLM/成员）。

stub 复用 test_groupchat_memory 里的（pytest 把 tests/ 目录本身加进
sys.path，所以按顶层模块导入）。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_groupchat_memory import (  # noqa: E402
    ExplodingLLM,
    StubManagerAgent,
    StubMemberAgent,
    StubMemoryManager,
)
from agents.groupchat.group_chat import (  # noqa: E402
    GroupChatManager,
    GroupMember,
    SharedHistory,
)
from agents.groupchat.shared_memory import SharedMemory  # noqa: E402


def _manager_no_habit_rewrite():
    """习惯为空 + 单人 → 不调改写 LLM（ExplodingLLM 会炸，证明没调）。"""
    member = GroupMember(
        name="collection_agent", display="催费助手",
        agent=StubMemberAgent("催费助手", "欠费100万"),
        aliases=["催费助手", "催费"],
    )
    gc = GroupChatManager(
        manager_agent=StubManagerAgent(ExplodingLLM()),
        members=[member],
        alias_map={"collection_agent": ["催费助手", "催费"]},
        shared_history=SharedHistory(),
        shared_memory=SharedMemory(
            manager=StubMemoryManager(), session_id="s",
        ),
        session_id="s",
    )
    return gc


def test_single_speaker_no_habit_posts_raw():
    gc = _manager_no_habit_rewrite()
    final = gc.run("@催费 欠款多少")
    assert final == "欠费100万"
    assert [t.speaker for t in gc.history.turns] == ["user", "manager"]


def test_clean_query_passed_to_member():
    seen = {}

    class SpyAgent(StubMemberAgent):
        def run(self, query, **kwargs):
            seen["query"] = query
            return "ok"

    member = GroupMember(
        name="legal_agent", display="法务助手",
        agent=SpyAgent("法务助手", "ok"),
        aliases=["法务助手", "法务"],
    )
    gc = GroupChatManager(
        manager_agent=StubManagerAgent(ExplodingLLM()),
        members=[member],
        alias_map={"legal_agent": ["法务助手", "法务"]},
        shared_history=SharedHistory(),
        shared_memory=None,
        session_id="s",
    )
    gc.run("@法务助手 诉讼时效")
    # @ 段已去掉，不污染成员的意图识别
    assert seen["query"] == "诉讼时效"


def test_multi_speaker_publishes_single_message():
    members = [
        GroupMember(
            name="collection_agent", display="催费助手",
            agent=StubMemberAgent("催费助手", "欠费100万"),
            aliases=["催费"],
        ),
        GroupMember(
            name="legal_agent", display="法务助手",
            agent=StubMemberAgent("法务助手", "时效3年"),
            aliases=["法务"],
        ),
    ]
    gc = GroupChatManager(
        manager_agent=StubManagerAgent(ExplodingLLM()),
        members=members,
        alias_map={"collection_agent": ["催费"], "legal_agent": ["法务"]},
        shared_history=SharedHistory(),
        shared_memory=None,
        session_id="s",
    )
    final = gc.run("@催费 @法务 一起说说")
    # 改写 LLM 炸了 → 降级原文拼接，但仍是一条 manager 消息
    assert "欠费100万" in final and "时效3年" in final
    assert [t.speaker for t in gc.history.turns] == ["user", "manager"]
