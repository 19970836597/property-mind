# -*- coding: utf-8 -*-
"""建群/多群测试：建群解析、切换隔离、@跨群提示、解散规则（stub，不调 LLM）。"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from test_groupchat_memory import (  # noqa: E402
    ExplodingLLM,
    StubManagerAgent,
    StubMemberAgent,
    StubMemoryManager,
)
from agents.groupchat.group_chat import (  # noqa: E402
    DEFAULT_ALIASES,
    SharedHistory,
    attach_shared_provider,
    detach_shared_provider,
)
from agents.groupchat.registry import DEFAULT_GROUP_NAME, GroupRegistry  # noqa: E402
from agents.groupchat.shared_memory import (  # noqa: E402
    SharedMemory,
    SharedRecallProvider,
)


class StubTool:
    """SubAgentTool 最小形状：name/tags/.agent。"""

    def __init__(self, name, display, reply="ok"):
        self.name = name
        self.tags = []
        self.agent = StubMemberAgent(display, reply)


def _registry(replies=None):
    replies = replies or {
        "collection_agent": ("催费助手", "欠费100万"),
        "legal_agent": ("法务助手", "时效3年"),
    }
    tools = [
        StubTool(name, display, reply)
        for name, (display, reply) in replies.items()
    ]
    return GroupRegistry(
        StubManagerAgent(ExplodingLLM()), tools, user_id="u-test",
    )


def _provider_sids(member):
    return [
        getattr(p.shared, "session_id", None)
        for p in getattr(member.agent.context, "providers", [])
        if getattr(p, "name", "") == SharedRecallProvider.name
    ]


def test_default_group_has_all_members():
    reg = _registry()
    assert reg.active_name == DEFAULT_GROUP_NAME
    assert {m.name for m in reg.active.members} == {
        "collection_agent", "legal_agent",
    }


def test_create_and_auto_switch():
    reg = _registry()
    chat = reg.create("催收群", raw_mentions="@催费 @法务")
    assert reg.active_name == "催收群"
    assert chat.group_name == "催收群"
    assert chat.session_id == "group-催收群"
    assert {m.name for m in chat.members} == {
        "collection_agent", "legal_agent",
    }


def test_create_partial_members():
    reg = _registry()
    reg.create("催费群", raw_mentions="@催费")
    assert {m.name for m in reg.active.members} == {"collection_agent"}


def test_create_no_member_fails_fast():
    reg = _registry()
    with pytest.raises(ValueError):
        reg.create("空群", raw_mentions="大家好")


def test_create_unknown_member_fails_fast():
    reg = _registry()
    with pytest.raises(ValueError, match="没有找到"):
        reg.create("错群", raw_mentions="@不存在的")


def test_create_duplicate_and_bad_name():
    reg = _registry()
    with pytest.raises(ValueError, match="已存在"):
        reg.create(DEFAULT_GROUP_NAME, raw_mentions="@催费")
    with pytest.raises(ValueError, match="不合法"):
        reg.create("a", raw_mentions="@催费")


def test_switch_moves_shared_provider():
    reg = _registry()
    reg.create("催费群", raw_mentions="@催费")
    # 活跃群=催费群：催费成员挂的是催费群的 provider
    member = next(
        m for m in reg.active.members if m.name == "collection_agent"
    )
    assert _provider_sids(member) == ["group-催费群"]

    reg.switch(DEFAULT_GROUP_NAME)
    # 切回默认群：provider 已换成默认群的，无残留
    assert _provider_sids(member) == [reg.groups[DEFAULT_GROUP_NAME].session_id]


def test_mention_outside_group_is_unknown():
    reg = _registry()
    reg.create("催费群", raw_mentions="@催费")
    final = reg.active.run("@法务 诉讼时效")
    # 法务不在本群 → unknown 提示 + 群内无人举手兜底
    assert "没有找到 @法务" in final


def test_disband_default_refused_and_active_falls_back():
    reg = _registry()
    with pytest.raises(ValueError, match="不可解散"):
        reg.disband(DEFAULT_GROUP_NAME)
    reg.create("临时群", raw_mentions="@催费")
    msg = reg.disband("临时群")
    assert "已解散" in msg
    assert reg.active_name == DEFAULT_GROUP_NAME


def test_add_remove_members():
    reg = _registry()
    reg.create("小群", raw_mentions="@催费")
    reg.add_members("@法务")
    assert {m.name for m in reg.active.members} == {
        "collection_agent", "legal_agent",
    }
    with pytest.raises(ValueError, match="已在群里"):
        reg.add_members("@催费")
    reg.remove_member("@法务")
    assert {m.name for m in reg.active.members} == {"collection_agent"}
    with pytest.raises(ValueError, match="至少保留"):
        reg.remove_member("@催费")


def test_attach_detach_provider_helpers():
    reg = _registry()
    chat = reg.groups[DEFAULT_GROUP_NAME]
    member = chat.members[0]
    detach_shared_provider(chat)
    assert _provider_sids(member) == []
    attach_shared_provider(chat)
    assert _provider_sids(member) == [chat.session_id]
