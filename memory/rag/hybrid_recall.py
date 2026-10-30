"""向后兼容重导出 - 已搬到 ``memory.recall.hybrid_recall``.

线上 RAG 与召回评测解耦：新代码请从 ``memory.recall`` 导入，
本模块仅为旧导入路径保留的转发。
"""

from memory.recall.hybrid_recall import (
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
