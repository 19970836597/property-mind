from scripts.dev.experiment_rag_recall import build_cases
from memory.recall.hybrid_recall import (
    EvidenceCandidate,
    HybridEvidenceRetriever,
    QueryTrace,
    RecallCase,
    RequiredFact,
    SelectedEvidence,
    evaluate_at_threshold,
    required_fact_matches,
    signature_terms,
    split_query,
)


def test_signature_terms_extracts_hard_identifiers():
    terms = signature_terms("PM-071小区在2026-07的收缴率是85.30%？")
    assert terms == ("PM-071", "2026-07", "85.30%", "收缴率")


def test_multi_query_is_split_without_an_llm():
    assert split_query("甲项目经是谁；乙项目收缴率多少？") == [
        "甲项目经是谁",
        "乙项目收缴率多少？",
    ]


def test_fact_matching_requires_code_month_and_metric():
    fact = RequiredFact("PM-071", "2026-07", "收缴率")
    assert required_fact_matches(
        "金桂苑8期（PM-071）2026-07 收缴率为 92.10%", fact
    )
    assert not required_fact_matches(
        "金桂苑8期（PM-071）2026-08 收缴率为 92.10%", fact
    )


def test_manager_fact_can_match_any_ledger_month():
    fact = RequiredFact("PM-071", "", "项目经理")
    assert required_fact_matches(
        "金桂苑8期（PM-071）2026-09 项目经理为张岚", fact
    )


class StubRetriever:
    def __init__(self, selected_by_case):
        self.selected_by_case = selected_by_case

    def select(self, trace, threshold):
        return self.selected_by_case.get(trace.case.case_id, [])


def _evidence(content):
    return SelectedEvidence(
        key=content,
        content=content,
        metadata={},
        score=1.0,
    )


def test_evaluation_covers_recall_precision_and_abstention():
    answerable = RecallCase(
        case_id="a",
        query="问题",
        split="test",
        category="direct",
        required_facts=(RequiredFact("PM-001", "2026-07", "收缴率"),),
    )
    unanswerable = RecallCase(
        case_id="b",
        query="无答案",
        split="test",
        category="unanswerable",
    )
    retriever = StubRetriever(
        {
            "a": [_evidence("PM-001 2026-07 收缴率 90%")],
            "b": [],
        }
    )
    result = evaluate_at_threshold(
        retriever,
        [
            QueryTrace(case=answerable, tier=3),
            QueryTrace(case=unanswerable, tier=3),
        ],
        threshold=0.5,
    )
    assert result["evidence_recall"] == 1.0
    assert result["case_full_recall"] == 1.0
    assert result["evidence_precision"] == 1.0
    assert result["false_answer_rate"] == 0.0


def test_benchmark_has_expected_size_and_split():
    cases = build_cases()
    assert len(cases) == 500
    assert sum(case.answerable for case in cases) == 400
    assert sum(case.split == "dev" for case in cases) == 350
    assert sum(case.split == "test" for case in cases) == 150


def test_selection_prefers_exact_identifier_candidate():
    retriever = HybridEvidenceRetriever.__new__(HybridEvidenceRetriever)
    retriever.max_evidence = 5
    exact = EvidenceCandidate(
        key="exact",
        content="PM-071 2026-07 收缴率 90%",
        metadata={"content_hash": "exact"},
        fit_score=0.8,
    )
    generic = EvidenceCandidate(
        key="generic",
        content="其他项目 2026-08 收缴率 80%",
        metadata={"content_hash": "generic"},
        fit_score=0.7,
    )
    case = RecallCase(
        case_id="case",
        query="PM-071 2026-07 收缴率",
        split="test",
        category="direct",
        required_facts=(RequiredFact("PM-071", "2026-07", "收缴率"),),
    )
    trace = QueryTrace(
        case=case,
        tier=3,
        segment_candidates=[[exact, generic]],
    )
    selected = retriever.select(trace, threshold=0.5)
    assert [item.key for item in selected] == ["exact"]
