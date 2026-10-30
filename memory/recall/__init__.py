"""File-memory recall experiment (evidence-level hybrid retrieval).

从 ``memory.rag`` 独立出来：线上 RAG 只做文档摄入/索引/检索，
本目录只放“可复现的召回评测”相关代码，与线上检索解耦，
冻结 benchmark 通过前不合入生产链路。
"""

from .hybrid_recall import (
    EvidenceCandidate,
    HybridEvidenceRetriever,
    QueryTrace,
    RecallCase,
    RequiredFact,
    SelectedEvidence,
    choose_threshold,
    evaluate_at_threshold,
    hard_signature_terms,
    required_fact_matches,
    signature_terms,
    split_query,
)

__all__ = [
    "EvidenceCandidate",
    "HybridEvidenceRetriever",
    "QueryTrace",
    "RecallCase",
    "RequiredFact",
    "SelectedEvidence",
    "choose_threshold",
    "evaluate_at_threshold",
    "hard_signature_terms",
    "required_fact_matches",
    "signature_terms",
    "split_query",
]
