# -*- coding: utf-8 -*-
"""记忆双层测试：私有隔离 + 共享互通 + session 隔离 + 改写降级。

全部用 stub（内存 manager / 固定回答 agent / 必炸 LLM），
不碰 sqlite/embedding，不调真实模型。
"""

from datetime import datetime

from agents.groupchat.group_chat import (
    GroupChatManager,
    GroupMember,
    SharedHistory,
)
from agents.groupchat.shared_memory import SharedMemory


class StubItem:
    def __init__(self, content, metadata=None):
        self.content = content
        self.metadata = metadata or {}


class StubMemoryManager:
    """内存版 MemoryManager：只实现 add/retrieve 的最小形状。"""

    def __init__(self):
        self.items = []

    def add_memory(self, content, memory_type="working", importance=0.5,
                   metadata=None, auto_classify=False):
        self.items.append({
            "content": content,
            "importance": importance,
            "metadata": metadata or {},
        })
        return f"id-{len(self.items)}"

    def retrieve_memories(self, query, memory_types=None, limit=10,
                          min_importance=0.0):
        scored = []
        for entry in self.items:
            if entry["importance"] < min_importance:
                continue
            hit = sum(
                1 for ch in query
                if ch.strip() and ch in entry["content"]
            )
            if hit:
                scored.append((hit, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            StubItem(e["content"], e["metadata"])
            for _, e in scored[:limit]
        ]


class StubProvider:
    """成员私有召回 provider（name 必须叫“召回记忆”，manager 才认）。"""

    name = "召回记忆"

    def __init__(self):
        self.items = []

    def remember(self, content, importance=0.6, memory_type="working"):
        self.items.append(content)
        return True

    def collect(self, query):
        return ""


class StubContext:
    def __init__(self):
        self.providers = [StubProvider()]


class StubMemberAgent:
    """固定回答的成员 agent：run 原样返回预设回答。"""

    def __init__(self, name, reply, caps=()):
        self.name = name
        self._reply = reply
        self.router = list(caps)
        self.context = StubContext()
        self._history = []

    def run(self, query, **kwargs):
        return self._reply


class ExplodingLLM:
    """必炸的 LLM：验证改写/习惯沉淀走降级分支。"""

    def invoke(self, messages, **kwargs):
        raise RuntimeError("llm down")


class StubManagerAgent:
    def __init__(self, llm, reply="manager兜底回答"):
        self.llm = llm
        self._reply = reply
        self.context = StubContext()

    def run(self, query, **kwargs):
        return self._reply


def _make_groupchat(llm, replies):
    members = [
        GroupMember(
            name=name, display=display,
            agent=StubMemberAgent(display, reply),
            aliases=[display],
        )
        for (name, display, reply) in replies
    ]
    alias_map = {m.name: list(m.aliases) for m in members}
    shared_a = SharedMemory(
        manager=StubMemoryManager(), session_id="group-test",
    )
    return GroupChatManager(
        manager_agent=StubManagerAgent(llm),
        members=members,
        alias_map=alias_map,
        shared_history=SharedHistory(),
        shared_memory=shared_a,
        session_id="group-test",
    )


def test_private_memory_isolated_between_members():
    gc = _make_groupchat(ExplodingLLM(), [
        ("collection_agent", "催费助手", "欠费100万"),
        ("legal_agent", "法务助手", "时效3年"),
    ])
    gc.run("@催费助手 欠款多少")
    private_a = gc.members[0].agent.context.providers[0].items
    private_b = gc.members[1].agent.context.providers[0].items
    assert private_a and not private_b  # 只有发言成员写了私有


def test_shared_memory_visible_to_all():
    gc = _make_groupchat(ExplodingLLM(), [
        ("collection_agent", "催费助手", "欠费100万"),
        ("legal_agent", "法务助手", "时效3年"),
    ])
    gc.run("@催费助手 欠款多少")
    hits = gc.shared.recall("欠款多少")
    assert any("欠费100万" in h.content for h in hits)
    assert hits[0].metadata.get("speaker") == "催费助手"


def test_only_published_version_in_history():
    gc = _make_groupchat(ExplodingLLM(), [
        ("collection_agent", "催费助手", "RAW-欠费100万"),
    ])
    final = gc.run("@催费助手 欠款多少")
    speakers = [t.speaker for t in gc.history.turns]
    assert speakers == ["user", "manager"]  # raw 不进群
    assert "RAW-欠费100万" in final  # 降级：LLM 炸了直接用原文


def test_no_speaker_falls_back_to_manager():
    gc = _make_groupchat(ExplodingLLM(), [
        ("collection_agent", "催费助手", "欠费100万"),
    ])
    answer = gc.run("今天天气怎么样")
    assert answer == "manager兜底回答"
    assert gc.last_speakers == []


def test_unknown_mention_hint_appended():
    gc = _make_groupchat(ExplodingLLM(), [
        ("collection_agent", "催费助手", "欠费100万"),
    ])
    answer = gc.run("@不存在的 欠款多少")
    assert "没有找到 @不存在的" in answer


def test_merged_recall_private_first_dedup():
    a = StubItem("同一条结论", {"speaker": "A"})
    b = StubItem("同一条结论", {"speaker": "B"})
    c = StubItem("另一条", {"speaker": "B"})
    merged = SharedMemory.merged_recall([a], [b, c], limit=5)
    assert [m.content for m in merged] == ["同一条结论", "另一条"]
    assert merged[0].metadata["speaker"] == "A"  # 私有优先


def test_session_isolation_in_metadata():
    mgr_a = StubMemoryManager()
    mgr_b = StubMemoryManager()
    shared_a = SharedMemory(manager=mgr_a, session_id="s-A")
    shared_b = SharedMemory(manager=mgr_b, session_id="s-B")
    shared_a.remember("A的结论", speaker="催费助手")
    shared_b.remember("B的结论", speaker="法务助手")
    assert mgr_a.items[0]["metadata"]["session_id"] == "s-A"
    assert mgr_b.items[0]["metadata"]["session_id"] == "s-B"
    assert "B的结论" not in [i["content"] for i in mgr_a.items]
