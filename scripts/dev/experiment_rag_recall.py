# -*- coding: utf-8 -*-
"""Reproducible file-memory recall benchmark.

The benchmark is isolated from production collections. It generates a
property-domain corpus, indexes it with the configured local embedding model,
and evaluates dense + character-lexical retrieval at evidence level.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env", override=True)

from memory.embedding import HashEmbedding, get_text_embedder  # noqa: E402
from memory.recall.hybrid_recall import (  # noqa: E402
    HybridEvidenceRetriever,
    RecallCase,
    RequiredFact,
    choose_threshold,
    evaluate_at_threshold,
)
from memory.storage.qdrant import QdrantVectorStore  # noqa: E402


COLLECTION_NAME = "file_memory_recall_eval_v1"
PROJECTS = (
    "金桂苑",
    "云栖里",
    "书香府",
    "锦澜庭",
    "星河湾",
    "春晓园",
    "澄月府",
    "青禾里",
    "观澜苑",
    "映湖庭",
)
MANAGERS = ("张岚", "陈序", "周宁", "许嘉", "何川", "林悦", "宋哲", "叶澄")
MONTHS = ("2026-07", "2026-08", "2026-09")
UNRELATED_TOPICS = (
    "烘焙温度控制",
    "山地自行车保养",
    "深海生物观察",
    "城市摄影构图",
    "咖啡豆储存",
    "图书馆分类方法",
    "天文望远镜校准",
    "园艺土壤改良",
    "长跑训练恢复",
    "陶瓷烧制工艺",
    "海洋气象记录",
    "古建筑测绘",
    "鱼类养殖水质",
    "手账排版设计",
    "公共交通线路规划",
    "森林防火巡检",
)


@dataclass
class CorpusDoc:
    doc_id: str
    path: Path
    split: str
    tier_rank: int
    content: str
    kind: str


def project_name(index: int) -> str:
    return f"{PROJECTS[index % len(PROJECTS)]}{index // len(PROJECTS) + 1}期"


def project_code(index: int) -> str:
    return f"PM-{index + 1:03d}"


def manager(index: int) -> str:
    return MANAGERS[index % len(MANAGERS)]


def rate(index: int, month_index: int) -> float:
    return round(81.0 + (index * 7 + month_index * 3) % 1800 / 100.0, 2)


def arrears(index: int, month_index: int) -> int:
    return 12000 + index * 173 + month_index * 907


def actual(index: int, month_index: int) -> int:
    return 88000 + index * 311 + month_index * 1207


def split_for_project(index: int) -> str:
    return "dev" if index < 70 else "test"


def fact(index: int, month_index: int, metric: str) -> RequiredFact:
    return RequiredFact(
        project_code=project_code(index),
        month=MONTHS[month_index],
        metric=metric,
    )


def gold_document(index: int) -> str:
    name = project_name(index)
    code = project_code(index)
    lines = [f"# {name}（{code}）物业运营月报", ""]
    for month_index, month in enumerate(MONTHS):
        current_rate = rate(index, month_index)
        lines.extend(
            [
                f"## {month}",
                "",
                (
                    f"{name}（{code}）{month} 收缴率为 {current_rate:.2f}%，"
                    f"欠费金额 {arrears(index, month_index)} 元，"
                    f"实收金额 {actual(index, month_index)} 元，"
                    f"项目经理 {manager(index)}，"
                    f"投诉数量 {3 + index % 8 + month_index} 件，"
                    f"工单数量 {21 + index % 13 + month_index * 2} 件。"
                ),
                "",
            ]
        )
    return "\n".join(lines)


def near_document(index: int) -> str:
    name = project_name(index)
    code = project_code(index)
    lines = [f"# {name}（{code}）历史归档", ""]
    for month_index, month in enumerate(("2025-07", "2025-08", "2025-09")):
        wrong_rate = rate(index, month_index) - 7.3
        lines.extend(
            [
                f"## {month}",
                "",
                (
                    f"{name}（{code}）{month} 收缴率为 {wrong_rate:.2f}%，"
                    f"欠费金额 {arrears(index, month_index) + 6100} 元，"
                    f"实收金额 {actual(index, month_index) - 3200} 元，"
                    f"项目经理 {manager(index + 1)}。"
                ),
                "",
            ]
        )
    return "\n".join(lines)


def stale_document(index: int) -> str:
    name = project_name(index)
    code = project_code(index)
    return (
        f"# {name}（{code}，已作废）\n\n"
        f"历史草稿：{name}（{code}）2024-12 收缴率为 42.00%，"
        f"欠费金额 990000 元，项目经理 待定。本记录已作废，不得用于当前统计。"
    )


def unrelated_document(index: int) -> str:
    topic = UNRELATED_TOPICS[index % len(UNRELATED_TOPICS)]
    return (
        f"# {topic}观察记录 {index + 1:04d}\n\n"
        f"这是一份与物业管理无关的{topic}资料。记录编号 R-{index + 1:04d}，"
        f"用于记录流程、器材保养、现场观察和操作注意事项。"
        f"第 {index + 1} 组样本不包含小区运营、收缴率、欠费、项目经理或工单等业务事实。"
    )


def write_corpus(corpus_dir: Path) -> List[CorpusDoc]:
    if corpus_dir.exists():
        shutil.rmtree(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    docs: List[CorpusDoc] = []

    def add_doc(name: str, split: str, rank: int, content: str, kind: str) -> None:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        doc_id = f"{kind}-{digest}"
        path = corpus_dir / kind / f"{doc_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        docs.append(
            CorpusDoc(
                doc_id=doc_id,
                path=path,
                split=split,
                tier_rank=rank,
                content=content,
                kind=kind,
            )
        )

    for index in range(100):
        add_doc(
            f"gold-{index}",
            split_for_project(index),
            0,
            gold_document(index),
            "gold",
        )
        add_doc(
            f"near-{index}",
            split_for_project(index),
            2,
            near_document(index),
            "near",
        )
        if index < 50:
            add_doc(
                f"stale-{index}",
                split_for_project(index),
                2,
                stale_document(index),
                "stale",
            )

    for index in range(600):
        add_doc(
            f"unrelated-{index}",
            "shared",
            1 if index < 300 else 3,
            unrelated_document(index),
            "unrelated",
        )
    return docs


def build_cases() -> List[RecallCase]:
    cases: List[RecallCase] = []
    dev_indexes = list(range(70))
    test_indexes = list(range(70, 100))
    for split, indexes in (("dev", dev_indexes), ("test", test_indexes)):
        for position, index in enumerate(indexes):
            name = project_name(index)
            code = project_code(index)

            cases.append(
                RecallCase(
                    case_id=f"{split}-direct-{index}",
                    query=f"{name}（{code}）在{MONTHS[0]}的收缴率是多少？",
                    split=split,
                    category="direct",
                    required_facts=(fact(index, 0, "收缴率"),),
                )
            )
            cases.append(
                RecallCase(
                    case_id=f"{split}-paraphrase-{index}",
                    query=(
                        f"请问{code}这个小区在{MONTHS[1]}的欠费规模"
                        f"和实收情况如何？"
                    ),
                    split=split,
                    category="paraphrase",
                    required_facts=(fact(index, 1, "欠费金额"),),
                )
            )
            cases.append(
                RecallCase(
                    case_id=f"{split}-value-{index}",
                    query=(
                        f"哪个项目在{MONTHS[2]}的收缴率是"
                        f"{rate(index, 2):.2f}%？"
                    ),
                    split=split,
                    category="exact_value",
                    required_facts=(fact(index, 2, "收缴率"),),
                )
            )

            next_index = indexes[(position + 1) % len(indexes)]
            cases.append(
                RecallCase(
                    case_id=f"{split}-multi-{index}",
                    query=(
                        f"{name}（{code}）的项目经理是谁；"
                        f"{project_name(next_index)}（{project_code(next_index)}）"
                        f"在{MONTHS[2]}的收缴率是多少？"
                    ),
                    split=split,
                    category="multi_evidence",
                    required_facts=(
                        RequiredFact(
                            project_code=code,
                            month="",
                            metric="项目经理",
                        ),
                        fact(next_index, 2, "收缴率"),
                    ),
                )
            )
            cases.append(
                RecallCase(
                    case_id=f"{split}-noanswer-{index}",
                    query=(
                        f"ZZ-{900 + index}小区在{MONTHS[2]}的收缴率是多少？"
                    ),
                    split=split,
                    category="unanswerable",
                )
            )
    return cases


def chunks_for_document(doc: CorpusDoc) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    heading = ""
    source = doc.content
    cursor = 0
    for line in source.splitlines(keepends=True):
        stripped = line.strip()
        start = cursor
        cursor += len(line)
        if stripped.startswith("#"):
            heading = stripped
            continue
        if not stripped:
            continue
        content = f"{heading}\n{stripped}" if heading else stripped
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        chunk_id = hashlib.sha256(
            f"{doc.path}|{start}|{content_hash}".encode("utf-8")
        ).hexdigest()
        chunks.append(
            {
                "id": chunk_id,
                "content": content,
                "metadata": {
                    "doc_id": doc.doc_id,
                    "source_path": str(doc.path),
                    "start": start,
                    "end": start + len(stripped),
                    "content_hash": content_hash,
                    "eval_rank": doc.tier_rank,
                    "kind": doc.kind,
                },
            }
        )
    return chunks


def build_tier_chunks(docs: Sequence[CorpusDoc]) -> Dict[int, List[Dict[str, Any]]]:
    by_tier: Dict[int, List[Dict[str, Any]]] = {level: [] for level in range(4)}
    for doc in docs:
        for chunk in chunks_for_document(doc):
            for level in range(doc.tier_rank, 4):
                metadata = dict(chunk["metadata"])
                metadata["memory_id"] = f"L{level}:{chunk['id']}"
                metadata["eval_level"] = level
                by_tier[level].append(
                    {
                        "id": f"L{level}:{chunk['id']}",
                        "content": chunk["content"],
                        "metadata": metadata,
                    }
                )
    return by_tier


def index_tiers(
    store: QdrantVectorStore,
    chunks_by_tier: Dict[int, List[Dict[str, Any]]],
    embedder: Any,
) -> Dict[str, int]:
    store.clear_collection()
    vector_cache: Dict[str, List[float]] = {}
    stats: Dict[str, int] = {}
    for tier in sorted(chunks_by_tier):
        chunks = chunks_by_tier[tier]
        texts = [chunk["content"] for chunk in chunks]
        vectors: List[List[float]] = []
        batch_size = 64
        for offset in range(0, len(texts), batch_size):
            batch = texts[offset : offset + batch_size]
            encoded = embedder.encode(batch)
            for text, vector in zip(batch, encoded):
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if digest not in vector_cache:
                    if hasattr(vector, "tolist"):
                        vector = vector.tolist()
                    vector_cache[digest] = [float(value) for value in vector]
                vectors.append(vector_cache[digest])

        for batch_start in range(0, len(chunks), 128):
            batch_chunks = chunks[batch_start : batch_start + 128]
            batch_vectors = vectors[batch_start : batch_start + 128]
            metadata = []
            ids = []
            for chunk in batch_chunks:
                payload = {
                    "memory_id": chunk["id"],
                    "memory_type": "rag_chunk",
                    "user_id": "rag_eval_user",
                    "data_source": "rag_recall_benchmark",
                    "content": chunk["content"],
                    "eval_only": True,
                    **chunk["metadata"],
                }
                metadata.append(payload)
                ids.append(chunk["id"])
            if not store.add_vectors(
                vectors=batch_vectors,
                metadata=metadata,
                ids=ids,
            ):
                raise RuntimeError(f"Qdrant indexing failed at tier {tier}")
        stats[str(tier)] = len(chunks)
        print(f"[index] tier={tier} chunks={len(chunks)}")
    return stats


def parse_tiers(value: str) -> List[int]:
    tiers = sorted({int(part) for part in value.split(",") if part.strip()})
    if not tiers or any(tier not in (0, 1, 2, 3) for tier in tiers):
        raise argparse.ArgumentTypeError("tiers must be a subset of 0,1,2,3")
    return tiers


def write_report(
    output_dir: Path,
    config: Dict[str, Any],
    dev_calibration: Dict[str, Any],
    test_results: Dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": config,
        "development_calibration": dev_calibration,
        "test_results": test_results,
    }
    (output_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# File-memory recall experiment",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- Collection: `{config['collection']}`",
        f"- Embedding: `{config['embedding_model']}` ({config['embedding_dimension']}d)",
        f"- Calibrated threshold: `{config['threshold']}`",
        "",
        "## Blind test",
        "",
        "| Tier | Required facts | Recall | Evidence precision | Full-case recall | No-answer false rate |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for tier, result in sorted(test_results.items(), key=lambda item: int(item[0])):
        lines.append(
            f"| {tier} | {result['required_facts']} | "
            f"{result['evidence_recall']:.2%} | "
            f"{result['evidence_precision']:.2%} | "
            f"{result['case_full_recall']:.2%} | "
            f"{result['false_answer_rate']:.2%} |"
        )
    lines.extend(
        [
            "",
            "The acceptance target is evaluated at the highest requested tier:",
            "all answerable blind cases must be fully recalled, evidence precision",
            "must be at least 99%, and no-answer false rate must be at most 1%.",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for tier, result in test_results.items():
        (output_dir / f"failures-tier-{tier}.json").write_text(
            json.dumps(result.get("failures", []), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiers", type=parse_tiers, default=[0, 1, 2, 3])
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    embedder = get_text_embedder()
    if isinstance(embedder, HashEmbedding):
        print("ERROR: configured embedding model fell back to HashEmbedding")
        return 2

    dimension = int(getattr(embedder, "dimension", 0))
    if dimension != 384:
        print(f"ERROR: expected the cached local model dimension 384, got {dimension}")
        return 2

    run_dir = Path(
        args.output_dir
        or PROJECT_ROOT / "outputs" / "memory_recall"
        / datetime.now().strftime("run-%Y%m%d-%H%M%S")
    )
    corpus_dir = run_dir / "corpus"
    docs = write_corpus(corpus_dir)
    cases = build_cases()
    chunks_by_tier = build_tier_chunks(docs)

    print(
        f"[benchmark] docs={len(docs)} cases={len(cases)} "
        f"answerable={sum(case.answerable for case in cases)}"
    )
    for tier in args.tiers:
        print(f"[benchmark] tier={tier} chunks={len(chunks_by_tier[tier])}")

    store = QdrantVectorStore(
        url=os.getenv("QDRANT_URL"),
        api_key=os.getenv("QDRANT_API_KEY") or None,
        collection_name=COLLECTION_NAME,
        vector_size=dimension,
        distance="cosine",
    )
    started = time.time()
    all_tier_chunks = {
        tier: chunks_by_tier[tier]
        for tier in args.tiers
    }
    index_tiers(store, all_tier_chunks, embedder)

    retriever = HybridEvidenceRetriever(
        store=store,
        chunks_by_tier=all_tier_chunks,
        embedder=embedder,
        candidate_k=30,
        max_evidence=5,
    )
    traces_by_tier: Dict[int, List[Any]] = {}
    for tier in args.tiers:
        print(f"[retrieve] building traces for tier={tier}")
        traces_by_tier[tier] = [
            retriever.search_trace(case, tier) for case in cases
        ]

    highest_tier = max(args.tiers)
    dev_traces = [
        trace
        for tier in args.tiers
        for trace in traces_by_tier[tier]
        if trace.case.split == "dev"
    ]
    threshold, dev_result = choose_threshold(retriever, dev_traces)
    print(
        f"[calibrate] tier={highest_tier} threshold={threshold:.2f} "
        f"recall={dev_result['evidence_recall']:.2%} "
        f"precision={dev_result['evidence_precision']:.2%} "
        f"false_answer={dev_result['false_answer_rate']:.2%}"
    )

    test_results: Dict[str, Any] = {}
    for tier in args.tiers:
        test_traces = [
            trace
            for trace in traces_by_tier[tier]
            if trace.case.split == "test"
        ]
        result = evaluate_at_threshold(retriever, test_traces, threshold)
        test_results[str(tier)] = result
        print(
            f"[test] tier={tier} recall={result['evidence_recall']:.2%} "
            f"precision={result['evidence_precision']:.2%} "
            f"full={result['case_full_recall']:.2%} "
            f"false_answer={result['false_answer_rate']:.2%}"
        )

    elapsed = time.time() - started
    config = {
        "collection": COLLECTION_NAME,
        "embedding_backend": type(embedder).__name__,
        "embedding_model": getattr(embedder, "model_name", None),
        "embedding_dimension": dimension,
        "cases": len(cases),
        "answerable": sum(case.answerable for case in cases),
        "tiers": args.tiers,
        "threshold": threshold,
        "candidate_k": 30,
        "max_evidence": 5,
        "elapsed_seconds": round(elapsed, 3),
    }
    write_report(
        run_dir,
        config=config,
        dev_calibration=dev_result,
        test_results=test_results,
    )
    print(f"[report] {run_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
