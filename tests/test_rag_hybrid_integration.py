from memory.rag import retriever as retriever_module
from memory.rag.retriever import search_vectors_hybrid
from tools.builtin import rag_tool as rag_tool_module
from tools.builtin.rag_tool import RAGTool


class FakeStore:
    def __init__(self, payloads, dense_hits):
        self.payloads = payloads
        self.dense_hits = dense_hits

    def scroll_payloads(self, where=None, max_points=10000):
        return list(self.payloads)

    def search_similar(self, **kwargs):
        return list(self.dense_hits)


def _payload(memory_id, content):
    return {
        "memory_id": memory_id,
        "memory_type": "rag_chunk",
        "is_rag_data": True,
        "data_source": "rag_pipeline",
        "rag_namespace": "default",
        "content": content,
        "content_hash": memory_id,
    }


def test_hybrid_search_rejects_wrong_month_even_if_dense_score_is_higher(monkeypatch):
    wrong = _payload("wrong", "PM-001 在 2025-07 的收缴率为 80%")
    correct = _payload("correct", "PM-001 在 2026-07 的收缴率为 92%")
    store = FakeStore(
        payloads=[wrong, correct],
        dense_hits=[
            {"id": "wrong", "score": 0.95, "metadata": wrong},
            {"id": "correct", "score": 0.40, "metadata": correct},
        ],
    )
    monkeypatch.setattr(retriever_module, "embed_query", lambda query: [0.0])

    results = search_vectors_hybrid(
        store=store,
        query="PM-001 在 2026-07 的收缴率是多少？",
        top_k=3,
        rag_namespace="default",
        score_threshold=0.3,
    )

    assert [item["metadata"]["memory_id"] for item in results] == ["correct"]


def test_hybrid_search_refuses_unknown_identifier(monkeypatch):
    payload = _payload("known", "PM-001 在 2026-07 的收缴率为 92%")
    store = FakeStore(
        payloads=[payload],
        dense_hits=[{"id": "known", "score": 0.9, "metadata": payload}],
    )
    monkeypatch.setattr(retriever_module, "embed_query", lambda query: [0.0])

    results = search_vectors_hybrid(
        store=store,
        query="ZZ-999 在 2026-07 的收缴率是多少？",
        top_k=3,
        rag_namespace="default",
        score_threshold=0.3,
    )

    assert results == []


def test_rag_tool_defaults_to_versioned_hybrid_collection(monkeypatch):
    captured = {}

    def fake_pipeline(**kwargs):
        captured.update(kwargs)
        return {
            "store": object(),
            "namespace": kwargs["rag_namespace"],
            "retriever_mode": kwargs["retriever_mode"],
            "get_stats": lambda: {},
        }

    monkeypatch.delenv("RAG_RETRIEVER_MODE", raising=False)
    monkeypatch.delenv("RAG_HYBRID_COLLECTION", raising=False)
    monkeypatch.setattr(rag_tool_module, "create_rag_pipeline", fake_pipeline)
    monkeypatch.setattr(rag_tool_module, "HelloAgentsLLM", lambda: object())

    tool = RAGTool(collection_name=None)

    assert tool.retriever_mode == "hybrid"
    assert tool.collection_name == "rag_knowledge_base_local_minilm_384_v1"
    assert captured["retriever_mode"] == "hybrid"
    assert captured["collection_name"] == tool.collection_name


def test_rag_tool_allows_legacy_rollback(monkeypatch):
    captured = {}

    def fake_pipeline(**kwargs):
        captured.update(kwargs)
        return {
            "store": object(),
            "namespace": kwargs["rag_namespace"],
            "retriever_mode": kwargs["retriever_mode"],
            "get_stats": lambda: {},
        }

    monkeypatch.setattr(rag_tool_module, "create_rag_pipeline", fake_pipeline)
    monkeypatch.setattr(rag_tool_module, "HelloAgentsLLM", lambda: object())

    tool = RAGTool(retriever_mode="legacy")

    assert tool.collection_name == "rag_knowledge_base"
    assert captured["retriever_mode"] == "legacy"


def test_rag_ask_uses_hybrid_search():
    called = {}

    def search_hybrid(query, top_k, score_threshold=None):
        called["query"] = query
        return [
            {
                "id": "fact",
                "score": 0.9,
                "metadata": {
                    "content": "收缴率口径为当月实收除以当月应收。",
                    "source_path": "policy.md",
                },
            }
        ]

    class FakeLLM:
        def invoke(self, messages):
            return "按实收除以应收计算。"

    tool = RAGTool.__new__(RAGTool)
    tool.retriever_mode = "hybrid"
    tool.rag_namespace = "default"
    tool.initialized = True
    tool.llm = FakeLLM()
    tool._pipelines = {
        "default": {
            "namespace": "default",
            "retriever_mode": "hybrid",
            "search_hybrid": search_hybrid,
        }
    }

    answer = tool._ask(question="收缴率怎么算？", limit=3)

    assert called["query"] == "收缴率怎么算？"
    assert "按实收除以应收计算" in answer
