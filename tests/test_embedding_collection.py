from memory import embedding as embedding_module
from memory.embedding import embedding_collection_name


def test_collection_name_is_model_and_dimension_versioned(monkeypatch):
    monkeypatch.setenv(
        "EMBED_MODEL_NAME",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    monkeypatch.delenv("EPISODIC_VECTOR_COLLECTION", raising=False)
    monkeypatch.setattr(embedding_module, "get_dimension", lambda default=384: 384)

    name = embedding_collection_name(
        "episodic_vectors",
        explicit_env="EPISODIC_VECTOR_COLLECTION",
    )

    assert name == "episodic_vectors_paraphrase_multilingual_minilm_l12_v2_384_v1"


def test_collection_name_allows_explicit_override(monkeypatch):
    monkeypatch.setenv("EPISODIC_VECTOR_COLLECTION", "custom_episodic_384")
    monkeypatch.setattr(embedding_module, "get_dimension", lambda default=384: 384)

    name = embedding_collection_name(
        "episodic_vectors",
        explicit_env="EPISODIC_VECTOR_COLLECTION",
    )

    assert name == "custom_episodic_384"
