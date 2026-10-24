# -*- coding: utf-8 -*-
"""自荐打分测试：催费/法务 query 各自高分，无关 query 无人举手。

用极简 stub router（name/description/tags 三件套）验证打分逻辑，
不依赖真实 CapabilityRouter 与 LLM。
"""

from agents.groupchat.speaker_selector import (
    DEFAULT_THRESHOLD,
    select_speakers,
    willingness,
)


class StubCap:
    def __init__(self, name, description, tags):
        self.name = name
        self.description = description
        self.tags = tags


class StubRouter:
    def __init__(self, caps):
        self._caps = caps

    def __iter__(self):
        return iter(self._caps)


class StubMember:
    def __init__(self, name, caps):
        self.name = name
        self.router = StubRouter(caps)


def _members():
    collection = StubMember("collection_agent", [
        StubCap(
            "arrears_analysis",
            "欠费账龄分析、催缴优先级排序",
            ["催费", "催缴", "欠费", "账龄", "催收"],
        ),
    ])
    legal = StubMember("legal_agent", [
        StubCap(
            "legal_rag",
            "民法典物业费条款、诉讼时效、催收合规红线",
            ["法务", "法规", "律师", "诉讼", "合规"],
        ),
    ])
    return [collection, legal]


def test_collection_query_scores_collection_higher():
    members = _members()
    c_score, _ = willingness(members[0], "分析下欠款账龄和催缴优先级")
    l_score, _ = willingness(members[1], "分析下欠款账龄和催缴优先级")
    assert c_score >= DEFAULT_THRESHOLD
    assert c_score > l_score


def test_legal_query_scores_legal_higher():
    members = _members()
    c_score, _ = willingness(members[0], "物业费诉讼时效与起诉合规红线")
    l_score, _ = willingness(members[1], "物业费诉讼时效与起诉合规红线")
    assert l_score >= DEFAULT_THRESHOLD
    assert l_score > c_score


def test_irrelevant_query_no_speaker():
    members = _members()
    picked = select_speakers(members, "今天天气怎么样")
    assert picked == []


def test_empty_query_no_score():
    members = _members()
    score, reason = willingness(members[0], "  ")
    assert score == 0.0
    assert reason


def test_member_without_router_scores_zero():
    class NoRouter:
        name = "ghost"
        router = None

    score, _ = willingness(NoRouter(), "欠费催缴")
    assert score == 0.0
