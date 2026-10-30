from types import SimpleNamespace

from core.llm import HelloAgentsLLM


def _chunk(reasoning=None, content=None):
    delta = SimpleNamespace(
        reasoning_content=None,
        reasoning=reasoning,
        content=content,
        tool_calls=None,
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def test_stream_accepts_ollama_reasoning_field():
    class Completions:
        def create(self, **kwargs):
            return iter(
                [
                    _chunk(reasoning="思考中"),
                    _chunk(content="回答"),
                ]
            )

    llm = HelloAgentsLLM.__new__(HelloAgentsLLM)
    llm.provider = "ollama"
    llm.model = "qwen3.5:2b"
    llm.kwargs = {}
    llm.temperature = 0.0
    llm.max_tokens = None
    llm._client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )

    chunks = list(llm.stream(messages=[]))

    assert chunks == [
        {"type": "reasoning", "content": "思考中"},
        {"type": "text", "content": "回答"},
    ]
