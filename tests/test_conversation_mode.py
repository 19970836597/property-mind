# -*- coding: utf-8 -*-
"""对话模式选择、不可变会话与消息路由测试。"""

from dataclasses import FrozenInstanceError

import pytest

from agents.conversation_mode import (
    ConversationMode,
    ConversationSession,
    choose_collaboration_group,
    choose_conversation_mode,
    parse_conversation_mode,
)


class FakeChat:
    def __init__(self, name, member_count=2):
        self.group_name = name
        self.members = [object()] * member_count


class FakeRegistry:
    def __init__(self, create_error=None):
        self.groups = {"默认群": FakeChat("默认群")}
        self.active_name = "默认群"
        self.create_error = create_error
        self.create_calls = []
        self.switch_calls = []

    def names(self):
        return list(self.groups)

    def switch(self, name):
        self.switch_calls.append(name)
        self.active_name = name
        return self.groups[name]

    def create(self, name, raw_mentions=""):
        self.create_calls.append((name, raw_mentions))
        if self.create_error is not None:
            raise self.create_error
        chat = FakeChat(name, member_count=1)
        self.groups[name] = chat
        self.active_name = name
        return chat


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ConversationMode.QUICK),
        ("1", ConversationMode.QUICK),
        ("快速模式", ConversationMode.QUICK),
        ("quick", ConversationMode.QUICK),
        ("2", ConversationMode.COLLABORATION),
        ("协作模式", ConversationMode.COLLABORATION),
        ("群聊", ConversationMode.COLLABORATION),
        ("COLLAB", ConversationMode.COLLABORATION),
    ],
)
def test_parse_conversation_mode(raw, expected):
    assert parse_conversation_mode(raw) is expected


def test_parse_conversation_mode_rejects_unknown_value():
    with pytest.raises(ValueError, match="未知对话模式"):
        parse_conversation_mode("自动")


def test_choose_mode_defaults_to_quick():
    mode = choose_conversation_mode(
        input_fn=lambda _prompt: "",
        output_fn=lambda _text: None,
    )
    assert mode is ConversationMode.QUICK


def test_choose_mode_retries_invalid_input():
    values = iter(["9", "协作模式"])
    mode = choose_conversation_mode(
        input_fn=lambda _prompt: next(values),
        output_fn=lambda _text: None,
    )
    assert mode is ConversationMode.COLLABORATION


def test_choose_mode_exits_on_eof():
    def fail(_prompt):
        raise EOFError

    assert choose_conversation_mode(input_fn=fail, output_fn=lambda _: None) is None


def test_conversation_session_is_immutable_and_routes_by_mode():
    quick = ConversationSession(ConversationMode.QUICK)
    collab = ConversationSession(ConversationMode.COLLABORATION)
    calls = []

    quick.dispatch(
        "@催费 你好",
        quick_handler=lambda text: calls.append(("quick", text)),
        group_handler=lambda text: calls.append(("group", text)),
    )
    collab.dispatch(
        "分析欠款",
        quick_handler=lambda text: calls.append(("quick", text)),
        group_handler=lambda text: calls.append(("group", text)),
    )

    assert calls == [
        ("quick", "@催费 你好"),
        ("group", "分析欠款"),
    ]
    assert not quick.group_chat_enabled
    assert collab.group_chat_enabled
    with pytest.raises(FrozenInstanceError):
        quick.mode = ConversationMode.COLLABORATION


def test_choose_group_selects_existing_group():
    registry = FakeRegistry()
    registry.groups["催收群"] = FakeChat("催收群", member_count=1)

    chat = choose_collaboration_group(
        registry,
        input_fn=lambda _prompt: "2",
        output_fn=lambda _text: None,
    )

    assert chat is registry.groups["催收群"]
    assert registry.active_name == "催收群"
    assert registry.switch_calls == ["催收群"]


def test_choose_group_creates_group_with_required_members():
    registry = FakeRegistry()
    values = iter(["2", "催收群", "@催费 @法务"])

    chat = choose_collaboration_group(
        registry,
        input_fn=lambda _prompt: next(values),
        output_fn=lambda _text: None,
    )

    assert chat is registry.groups["催收群"]
    assert registry.create_calls == [("催收群", "@催费 @法务")]


def test_choose_group_returns_to_menu_after_create_failure():
    registry = FakeRegistry(create_error=ValueError("群已存在"))
    values = iter(["2", "催收群", "@催费", "1"])

    chat = choose_collaboration_group(
        registry,
        input_fn=lambda _prompt: next(values),
        output_fn=lambda _text: None,
    )

    assert chat is registry.groups["默认群"]
    assert registry.create_calls == [("催收群", "@催费")]
    assert registry.switch_calls == ["默认群"]


def test_choose_group_rejects_empty_members():
    registry = FakeRegistry()
    values = iter(["2", "催收群", "", "1"])

    chat = choose_collaboration_group(
        registry,
        input_fn=lambda _prompt: next(values),
        output_fn=lambda _text: None,
    )

    assert chat is registry.groups["默认群"]
    assert registry.create_calls == []


def test_choose_group_can_cancel():
    registry = FakeRegistry()
    assert choose_collaboration_group(
        registry,
        input_fn=lambda _prompt: "0",
        output_fn=lambda _text: None,
    ) is None
