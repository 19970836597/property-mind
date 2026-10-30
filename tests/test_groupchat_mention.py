# -*- coding: utf-8 -*-
"""群聊 @ 解析测试：中英 alias、@多人、@错名、@all、clean 去 @。"""

from agents.groupchat.mention import extract_mentions

ALIAS_MAP = {
    "collection_agent": ["催费助手", "催费"],
    "legal_agent": ["法务助手", "法务"],
}


def test_mention_chinese_alias():
    mentioned, clean, unknown = extract_mentions(
        "@催费助手 分析下欠款账龄", ALIAS_MAP
    )
    assert mentioned == ["collection_agent"]
    assert clean == "分析下欠款账龄"
    assert unknown == []


def test_mention_english_name():
    mentioned, clean, unknown = extract_mentions(
        "@legal_agent 诉讼时效是多久", ALIAS_MAP
    )
    assert mentioned == ["legal_agent"]
    assert clean == "诉讼时效是多久"
    assert unknown == []


def test_mention_multiple_and_dedup():
    mentioned, clean, unknown = extract_mentions(
        "@催费 @法务助手 联合出个方案 @催费", ALIAS_MAP
    )
    assert mentioned == ["collection_agent", "legal_agent"]
    assert clean == "联合出个方案"
    assert unknown == []


def test_mention_all_expands_in_order():
    mentioned, clean, unknown = extract_mentions("@all 都说说", ALIAS_MAP)
    assert mentioned == ["collection_agent", "legal_agent"]
    assert clean == "都说说"
    assert unknown == []


def test_mention_unknown_goes_to_unknown():
    mentioned, clean, unknown = extract_mentions(
        "@不存在的 随便问问", ALIAS_MAP
    )
    assert mentioned == []
    assert clean == "随便问问"
    assert unknown == ["不存在的"]


def test_mention_no_space_between_alias_and_text():
    mentioned, clean, unknown = extract_mentions(
        "@催费欠款多少", ALIAS_MAP
    )
    assert mentioned == ["collection_agent"]
    assert clean == "欠款多少"
    assert unknown == []


def test_no_mention():
    mentioned, clean, unknown = extract_mentions("分析欠款账龄", ALIAS_MAP)
    assert mentioned == []
    assert clean == "分析欠款账龄"
    assert unknown == []
