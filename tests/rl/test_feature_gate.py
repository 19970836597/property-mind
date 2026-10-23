from __future__ import annotations

from core.capability import CapabilityRouter
from main import _register_optional_collection_rl


def test_rl_tool_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("COLLECTION_RL_ENABLED", raising=False)
    router = CapabilityRouter()
    _register_optional_collection_rl(router)
    assert router.names() == []


def test_rl_tool_is_registered_only_when_enabled(monkeypatch):
    monkeypatch.setenv("COLLECTION_RL_ENABLED", "true")
    monkeypatch.delenv("COLLECTION_RL_POLICY", raising=False)
    router = CapabilityRouter()
    _register_optional_collection_rl(router)
    assert "collection_rl_policy" in router.names()
    tool = router.resolve("collection_rl_policy")
    assert "不发送短信" in tool.description
