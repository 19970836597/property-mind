"""Evidence-level hybrid retrieval used by the file-memory recall experiment.

This module deliberately keeps the evaluation-oriented retrieval pipeline
separate from ``RAGTool`` until the frozen benchmark passes.

.. note::
    已从 ``memory.rag.hybrid_recall`` 搬到 ``memory.recall`` 独立目录；
    旧路径仅保留转发，新代码请从 ``memory.recall`` 导入。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


_IDENTIFIER_RE = re.compile(r"[A-Z]{2,}-\d+", re.IGNORECASE)
_DATE_RE = re.compile(r"20\d{2}-\d{2}")
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?%")
_METRIC_TERMS = (
    "收缴率",
    "欠费金额",
    "实收金额",
    "项目经理",
    "投诉数量",
    "工单数量",
)
_QUERY_SPLIT_RE = re.compile(r"\s*(?:；|;|并且|以及|\n)\s*")


@dataclass(frozen=True)
class RequiredFact:
    project_code: str
    month: str
    metric: str


@dataclass(frozen=True)
class RecallCase:
    case_id: str
    query: str
    split: str
    category: str
    required_facts: Tuple[RequiredFact, ...] = ()

    @property
    def answerable(self) -> bool:
        return bool(self.required_facts)


@dataclass
class EvidenceCandidate:
    key: str
    content: str
    metadata: Dict[str, Any]
    dense_score: float = 0.0
    lexical_score: float = 0.0
    signature_score: float = 0.0
    fusion_score: float = 0.0
    fit_score: float = 0.0


@dataclass
class QueryTrace:
    case: RecallCase
    tier: int
    segment_candidates: List[List[EvidenceCandidate]] = field(default_factory=list)


@dataclass
class SelectedEvidence:
    key: str
    content: str
    metadata: Dict[str, Any]
    score: float


def split_query(query: str) -> List[str]:
    """Split explicitly multi-part queries without asking an LLM at retrieval time."""
    parts = [part.strip(" \t，,") for part in _QUERY_SPLIT_RE.split(query or "")]
    return [part for part in parts if part] or [str(query or "").strip()]


def signature_terms(text: str) -> Tuple[str, ...]:
    """Return exact identifiers whose presence strongly discriminates a fact."""
    terms: List[str] = []
    terms.extend(match.group(0) for match in _IDENTIFIER_RE.finditer(text or ""))
    terms.extend(match.group(0) for match in _DATE_RE.finditer(text or ""))
    terms.extend(match.group(0) for match in _PERCENT_RE.finditer(text or ""))
    terms.extend(term for term in _METRIC_TERMS if term in (text or ""))
    return tuple(dict.fromkeys(terms))


def hard_signature_terms(text: str) -> Tuple[str, ...]:
    """Return query terms that must all occur in a candidate evidence chunk."""
    terms: List[str] = []
    terms.extend(match.group(0) for match in _IDENTIFIER_RE.finditer(text or ""))
    terms.extend(match.group(0) for match in _DATE_RE.finditer(text or ""))
    terms.extend(match.group(0) for match in _PERCENT_RE.finditer(text or ""))
    return tuple(dict.fromkeys(terms))


def required_fact_matches(content: str, fact: RequiredFact) -> bool:
    content_lower = (content or "").lower()
    return (
        fact.project_code.lower() in content_lower
        and (not fact.month or fact.month in (content or ""))
        and fact.metric in (content or "")
    )


def _percentile_rank_score(rank: int, weight: float, rrf_k: int = 60) -> float:
    return weight / (rrf_k + max(1, rank))


class HybridEvidenceRetriever:
    """Dense + character n-gram retrieval with deterministic evidence selection."""

    def __init__(
        self,
        store: Any,
        chunks_by_tier: Dict[int, Sequence[Dict[str, Any]]],
        embedder: Any,
        *,
        candidate_k: int = 50,
        max_evidence: int = 5,
        dense_weight: float = 0.5,
        lexical_weight: float = 0.32,
        signature_weight: float = 0.18,
        exact_identifier_bonus: float = 0.05,
    ):
        self.store = store
        self.chunks_by_tier = {
            int(tier): list(chunks) for tier, chunks in chunks_by_tier.items()
        }
        self.embedder = embedder
        self.candidate_k = max(1, int(candidate_k))
        self.max_evidence = max(1, int(max_evidence))
        self.dense_weight = float(dense_weight)
        self.lexical_weight = float(lexical_weight)
        self.signature_weight = float(signature_weight)
        self.exact_identifier_bonus = float(exact_identifier_bonus)
        self._lexical: Dict[int, Tuple[TfidfVectorizer, Any]] = {}
        for tier, chunks in self.chunks_by_tier.items():
            texts = [str(chunk.get("content", "")) for chunk in chunks]
            if not texts:
                continue
            vectorizer = TfidfVectorizer(
                analyzer="char",
                ngram_range=(2, 3),
                lowercase=True,
                sublinear_tf=True,
            )
            matrix = vectorizer.fit_transform(texts)
            self._lexical[tier] = (vectorizer, matrix)

    def _query_vector(self, query: str) -> List[float]:
        vector = self.embedder.encode(query)
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        return [float(value) for value in vector]

    def _dense_candidates(self, query: str, tier: int) -> List[Dict[str, Any]]:
        return self.store.search_similar(
            query_vector=self._query_vector(query),
            limit=self.candidate_k,
            where={
                "memory_type": "rag_chunk",
                "eval_only": True,
                "eval_level": int(tier),
            },
        )

    def _lexical_candidates(self, query: str, tier: int) -> List[Tuple[int, float]]:
        payload = self._lexical.get(tier)
        if not payload or not self.chunks_by_tier.get(tier):
            return []
        vectorizer, matrix = payload
        query_matrix = vectorizer.transform([query])
        scores = cosine_similarity(query_matrix, matrix).ravel()
        indexes = scores.argsort()[::-1][: self.candidate_k]
        return [(int(index), float(scores[index])) for index in indexes]

    def search_trace(self, case: RecallCase, tier: int) -> QueryTrace:
        chunks = self.chunks_by_tier[tier]
        trace = QueryTrace(case=case, tier=int(tier))

        for segment in split_query(case.query):
            candidates: Dict[str, EvidenceCandidate] = {}
            dense_hits = self._dense_candidates(segment, tier)
            dense_ranks: Dict[str, int] = {}
            for rank, hit in enumerate(dense_hits, 1):
                metadata = dict(hit.get("metadata") or {})
                key = str(metadata.get("memory_id") or hit.get("id"))
                dense_ranks[key] = rank
                candidates[key] = EvidenceCandidate(
                    key=key,
                    content=str(metadata.get("content", "")),
                    metadata=metadata,
                    dense_score=max(0.0, float(hit.get("score") or 0.0)),
                )

            lexical_ranks: Dict[str, int] = {}
            for rank, (index, score) in enumerate(
                self._lexical_candidates(segment, tier), 1
            ):
                chunk = chunks[index]
                metadata = dict(chunk.get("metadata") or {})
                key = str(metadata.get("memory_id") or chunk.get("id"))
                lexical_ranks[key] = rank
                candidate = candidates.get(key)
                if candidate is None:
                    candidate = EvidenceCandidate(
                        key=key,
                        content=str(chunk.get("content", "")),
                        metadata=metadata,
                    )
                    candidates[key] = candidate
                candidate.lexical_score = max(0.0, float(score))

            terms = signature_terms(segment)
            required_terms = hard_signature_terms(segment)
            for candidate in candidates.values():
                exact_hits = sum(
                    1 for term in terms if term.lower() in candidate.content.lower()
                )
                candidate.signature_score = (
                    exact_hits / len(terms) if terms else 0.0
                )
                dense_component = (
                    _percentile_rank_score(dense_ranks[candidate.key], 1.0)
                    if candidate.key in dense_ranks
                    else 0.0
                )
                lexical_component = (
                    _percentile_rank_score(lexical_ranks[candidate.key], 1.0)
                    if candidate.key in lexical_ranks
                    else 0.0
                )
                candidate.fusion_score = dense_component + lexical_component
                candidate.fit_score = (
                    self.dense_weight * candidate.dense_score
                    + self.lexical_weight * candidate.lexical_score
                    + self.signature_weight * candidate.signature_score
                )
                if required_terms and not all(
                    term.lower() in candidate.content.lower()
                    for term in required_terms
                ):
                    candidate.fit_score = 0.0
                elif required_terms:
                    candidate.fit_score += self.exact_identifier_bonus

            ranked = sorted(
                candidates.values(),
                key=lambda item: (
                    item.fit_score,
                    item.fusion_score,
                    item.dense_score,
                ),
                reverse=True,
            )
            trace.segment_candidates.append(ranked)

        return trace

    def select(
        self,
        trace: QueryTrace,
        threshold: float,
    ) -> List[SelectedEvidence]:
        selected: List[SelectedEvidence] = []
        seen: set[str] = set()
        for ranked in trace.segment_candidates:
            if not ranked:
                continue
            candidate = ranked[0]
            if candidate.fit_score < threshold:
                continue
            dedup_key = str(
                candidate.metadata.get("content_hash")
                or candidate.metadata.get("doc_id")
                or candidate.key
            )
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            selected.append(
                SelectedEvidence(
                    key=candidate.key,
                    content=candidate.content,
                    metadata=dict(candidate.metadata),
                    score=float(candidate.fit_score),
                )
            )
            if len(selected) >= self.max_evidence:
                break
        return selected


def evaluate_at_threshold(
    retriever: HybridEvidenceRetriever,
    traces: Iterable[QueryTrace],
    threshold: float,
) -> Dict[str, Any]:
    answerable = 0
    fully_recalled = 0
    required_total = 0
    required_found = 0
    returned_total = 0
    returned_correct = 0
    unanswerable = 0
    false_answer = 0
    failures: List[Dict[str, Any]] = []

    for trace in traces:
        selected = retriever.select(trace, threshold)
        case = trace.case
        returned_total += len(selected)

        if case.answerable:
            answerable += 1
            matched_facts = 0
            for fact in case.required_facts:
                required_total += 1
                if any(required_fact_matches(item.content, fact) for item in selected):
                    required_found += 1
                    matched_facts += 1
            correct_evidence = sum(
                1
                for item in selected
                if any(
                    required_fact_matches(item.content, fact)
                    for fact in case.required_facts
                )
            )
            returned_correct += correct_evidence
            if matched_facts == len(case.required_facts):
                fully_recalled += 1
            elif len(failures) < 100:
                failures.append(
                    {
                        "case_id": case.case_id,
                        "tier": trace.tier,
                        "query": case.query,
                        "required": [
                            {
                                "project_code": fact.project_code,
                                "month": fact.month,
                                "metric": fact.metric,
                            }
                            for fact in case.required_facts
                        ],
                        "selected": [
                            {
                                "score": round(item.score, 6),
                                "source_path": item.metadata.get("source_path"),
                                "content": item.content[:240],
                            }
                            for item in selected
                        ],
                    }
                )
        else:
            unanswerable += 1
            if selected:
                false_answer += 1

    recall = required_found / required_total if required_total else 0.0
    full_case_recall = fully_recalled / answerable if answerable else 0.0
    precision = returned_correct / returned_total if returned_total else 1.0
    false_answer_rate = false_answer / unanswerable if unanswerable else 0.0
    return {
        "threshold": round(float(threshold), 6),
        "answerable": answerable,
        "fully_recalled": fully_recalled,
        "required_facts": required_total,
        "required_found": required_found,
        "evidence_recall": round(recall, 6),
        "case_full_recall": round(full_case_recall, 6),
        "returned_evidence": returned_total,
        "correct_returned_evidence": returned_correct,
        "evidence_precision": round(precision, 6),
        "unanswerable": unanswerable,
        "false_answers": false_answer,
        "false_answer_rate": round(false_answer_rate, 6),
        "failures": failures,
    }


def choose_threshold(
    retriever: HybridEvidenceRetriever,
    traces: Sequence[QueryTrace],
    grid: Optional[Sequence[float]] = None,
) -> Tuple[float, Dict[str, Any]]:
    """Choose on development data: full recall first, then precision and threshold."""
    thresholds = list(grid or [round(x / 100, 2) for x in range(20, 91)])
    scored = [
        evaluate_at_threshold(retriever, traces, threshold)
        for threshold in thresholds
    ]
    eligible = [
        result
        for result in scored
        if result["case_full_recall"] >= 1.0
        and result["evidence_precision"] >= 0.99
        and result["false_answer_rate"] <= 0.01
    ]
    if eligible:
        chosen = max(
            eligible,
            key=lambda result: (
                result["evidence_precision"],
                result["threshold"],
            ),
        )
    else:
        chosen = max(
            scored,
            key=lambda result: (
                result["case_full_recall"],
                result["evidence_precision"],
                -result["false_answer_rate"],
            ),
        )
    return float(chosen["threshold"]), chosen
