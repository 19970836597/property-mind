"""检索模块 - 从向量知识库中搜索和检索相关文档块"""

import os
from typing import List, Dict, Optional, Any

from ..storage.qdrant import QdrantVectorStore
from .indexer import embed_query, _create_default_vector_store


def search_vectors(
    store=None,
    query: str = "",
    top_k: int = 8,
    rag_namespace: Optional[str] = None,
    only_rag_data: bool = True,
    score_threshold: Optional[float] = None
) -> List[Dict]:
    if not query:
        return []

    if store is None:
        store = _create_default_vector_store()

    qv = embed_query(query)

    where = {"memory_type": "rag_chunk"}
    if only_rag_data:
        where["is_rag_data"] = True
        where["data_source"] = "rag_pipeline"
    if rag_namespace:
        where["rag_namespace"] = rag_namespace

    try:
        return store.search_similar(
            query_vector=qv,
            limit=top_k,
            score_threshold=score_threshold,
            where=where
        )
    except Exception as e:
        print(f"[WARNING] RAG search failed: {e}")
        return []


def _prompt_mqe(query: str, n: int) -> List[str]:
    try:
        from core.llm import HelloAgentsLLM
        llm = HelloAgentsLLM()
        prompt = [
            {"role": "system", "content": "你是检索查询扩展助手。生成语义等价或互补的多样化查询。使用中文，简短，避免标点。"},
            {"role": "user", "content": f"原始查询：{query}\n请给出{n}个不同表述的查询，每行一个。"}
        ]
        text = llm.invoke(prompt)
        lines = [ln.strip("- \t") for ln in (text or "").splitlines()]
        outs = [ln for ln in lines if ln]
        return outs[:n] or [query]
    except Exception:
        return [query]


def _prompt_hyde(query: str) -> Optional[str]:
    try:
        from core.llm import HelloAgentsLLM
        llm = HelloAgentsLLM()
        prompt = [
            {"role": "system", "content": "根据用户问题，先写一段可能的答案性段落，用于向量检索的查询文档（不要分析过程）。"},
            {"role": "user", "content": f"问题：{query}\n请直接写一段中等长度、客观、包含关键术语的段落。"}
        ]
        return llm.invoke(prompt)
    except Exception:
        return None


def search_vectors_expanded(
    store=None,
    query: str = "",
    top_k: int = 8,
    rag_namespace: Optional[str] = None,
    only_rag_data: bool = True,
    score_threshold: Optional[float] = None,
    enable_mqe: bool = False,
    mqe_expansions: int = 2,
    enable_hyde: bool = False,
    candidate_pool_multiplier: int = 4,
) -> List[Dict]:
    if not query:
        return []

    if store is None:
        store = _create_default_vector_store()

    expansions: List[str] = [query]

    if enable_mqe and mqe_expansions > 0:
        expansions.extend(_prompt_mqe(query, mqe_expansions))
    if enable_hyde:
        hyde_text = _prompt_hyde(query)
        if hyde_text:
            expansions.append(hyde_text)

    uniq: List[str] = []
    for e in expansions:
        if e and e not in uniq:
            uniq.append(e)
    expansions = uniq[:max(1, len(uniq))]

    pool = max(top_k * candidate_pool_multiplier, 20)
    per = max(1, pool // max(1, len(expansions)))

    where = {"memory_type": "rag_chunk"}
    if only_rag_data:
        where["is_rag_data"] = True
        where["data_source"] = "rag_pipeline"
    if rag_namespace:
        where["rag_namespace"] = rag_namespace

    agg: Dict[str, Dict] = {}
    for q in expansions:
        qv = embed_query(q)
        hits = store.search_similar(query_vector=qv, limit=per, score_threshold=score_threshold, where=where)
        for h in hits:
            mid = h.get("metadata", {}).get("memory_id", h.get("id"))
            s = float(h.get("score", 0.0))
            if mid not in agg or s > float(agg[mid].get("score", 0.0)):
                agg[mid] = h

    merged = list(agg.values())
    merged.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
    return merged[:top_k]


def _try_load_cross_encoder(model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
    try:
        from sentence_transformers import CrossEncoder
        return CrossEncoder(model_name)
    except Exception:
        return None


def rerank_with_cross_encoder(query: str, items: List[Dict], model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2", top_k: int = 10) -> List[Dict]:
    ce = _try_load_cross_encoder(model_name)
    if ce is None or not items:
        return items[:top_k]
    pairs = [[query, it.get("content", "")] for it in items]
    try:
        scores = ce.predict(pairs)
        for it, s in zip(items, scores):
            it["rerank_score"] = float(s)
        items.sort(key=lambda x: x.get("rerank_score", x.get("score", 0.0)), reverse=True)
        return items[:top_k]
    except Exception:
        return items[:top_k]


def compute_graph_signals_from_pool(vector_hits: List[Dict], same_doc_weight: float = 1.0, proximity_weight: float = 1.0, proximity_window_chars: int = 1600) -> Dict[str, float]:
    by_doc: Dict[str, List[Dict]] = {}
    for h in vector_hits:
        meta = h.get("metadata", {})
        did = meta.get("doc_id")
        if not did:
            did = meta.get("memory_id") or h.get("id")
        by_doc.setdefault(did, []).append(h)

    doc_counts = {d: len(arr) for d, arr in by_doc.items()}
    max_count = max(doc_counts.values()) if doc_counts else 1

    graph_signal: Dict[str, float] = {}
    for did, arr in by_doc.items():
        arr.sort(key=lambda x: x.get("metadata", {}).get("start", 0))
        density = doc_counts.get(did, 1) / max_count
        for i, h in enumerate(arr):
            mid = h.get("metadata", {}).get("memory_id", h.get("id"))
            pos_i = h.get("metadata", {}).get("start", 0)
            prox_acc = 0.0

            j = i - 1
            while j >= 0:
                pos_j = arr[j].get("metadata", {}).get("start", 0)
                dist = abs(pos_i - pos_j)
                if dist > proximity_window_chars:
                    break
                prox_acc += max(0.0, 1.0 - (dist / max(1.0, float(proximity_window_chars))))
                j -= 1

            j = i + 1
            while j < len(arr):
                pos_j = arr[j].get("metadata", {}).get("start", 0)
                dist = abs(pos_i - pos_j)
                if dist > proximity_window_chars:
                    break
                prox_acc += max(0.0, 1.0 - (dist / max(1.0, float(proximity_window_chars))))
                j += 1

            score = same_doc_weight * density + proximity_weight * prox_acc
            graph_signal[mid] = graph_signal.get(mid, 0.0) + score

    if graph_signal:
        max_v = max(graph_signal.values())
        if max_v > 0:
            for k in list(graph_signal.keys()):
                graph_signal[k] = graph_signal[k] / max_v
    return graph_signal


def rank(vector_hits: List[Dict], graph_signals: Optional[Dict[str, float]] = None, w_vector: float = 0.7, w_graph: float = 0.3) -> List[Dict]:
    items: List[Dict] = []
    graph_signals = graph_signals or {}
    for h in vector_hits:
        mid = h.get("metadata", {}).get("memory_id", h.get("id"))
        g = float(graph_signals.get(mid, 0.0))
        v = float(h.get("score", 0.0))
        score = w_vector * v + w_graph * g
        items.append({
            "memory_id": mid,
            "score": score,
            "vector_score": v,
            "graph_score": g,
            "content": h.get("metadata", {}).get("content", ""),
            "metadata": h.get("metadata", {}),
        })
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def merge_snippets(ranked_items: List[Dict], max_chars: int = 1200) -> str:
    out: List[str] = []
    total = 0
    for it in ranked_items:
        text = it.get("content", "").strip()
        if not text:
            continue
        if total + len(text) > max_chars:
            remain = max_chars - total
            if remain <= 0:
                break
            out.append(text[:remain])
            total += remain
            break
        out.append(text)
        total += len(text)
    return "\n\n".join(out)


def expand_neighbors_from_pool(selected: List[Dict], pool: List[Dict], neighbors: int = 1, max_additions: int = 5) -> List[Dict]:
    if not selected or not pool or neighbors <= 0:
        return selected

    by_doc: Dict[str, List[Dict]] = {}
    for it in pool:
        meta = it.get("metadata", {})
        did = meta.get("doc_id")
        if not did:
            continue
        by_doc.setdefault(did, []).append(it)
    for did, arr in by_doc.items():
        arr.sort(key=lambda x: (x.get("metadata", {}).get("start", 0)))

    selected_ids = set(it.get("memory_id") for it in selected)
    additions: List[Dict] = []
    for it in selected:
        meta = it.get("metadata", {})
        did = meta.get("doc_id")
        if not did or did not in by_doc:
            continue
        arr = by_doc[did]
        try:
            idx = next(i for i, x in enumerate(arr) if x.get("memory_id") == it.get("memory_id"))
        except StopIteration:
            continue
        for offset in range(1, neighbors + 1):
            for j in (idx - offset, idx + offset):
                if 0 <= j < len(arr):
                    cand = arr[j]
                    mid = cand.get("memory_id")
                    if mid not in selected_ids:
                        additions.append(cand)
                        selected_ids.add(mid)
                        if len(additions) >= max_additions:
                            break
            if len(additions) >= max_additions:
                break
        if len(additions) >= max_additions:
            break

    extended = list(selected) + additions
    extended.sort(key=lambda x: (x.get("rerank_score", x.get("score", 0.0))), reverse=True)
    return extended


def merge_snippets_grouped(ranked_items: List[Dict], max_chars: int = 1200, include_citations: bool = True) -> str:
    by_doc: Dict[str, List[Dict]] = {}
    doc_score: Dict[str, float] = {}
    for it in ranked_items:
        meta = it.get("metadata", {})
        did = meta.get("doc_id") or meta.get("source_path") or "unknown"
        by_doc.setdefault(did, []).append(it)
        doc_score[did] = doc_score.get(did, 0.0) + float(it.get("score", 0.0))

    ordered_docs = sorted(by_doc.keys(), key=lambda d: doc_score.get(d, 0.0), reverse=True)
    for d in ordered_docs:
        by_doc[d].sort(key=lambda x: (x.get("metadata", {}).get("start", 0)))

    out: List[str] = []
    citations: List[Dict] = []
    total = 0
    cite_index = 1
    for did in ordered_docs:
        parts = by_doc[did]
        for it in parts:
            text = (it.get("content", "") or "").strip()
            if not text:
                continue
            suffix = ""
            if include_citations:
                suffix = f" [{cite_index}]"
            need = len(text) + (len(suffix) if suffix else 0)
            if total + need > max_chars:
                remain = max_chars - total
                if remain <= 0:
                    break
                clipped = text[:max(0, remain - len(suffix))]
                if clipped:
                    out.append(clipped + suffix)
                    total += len(clipped) + len(suffix)
                    if include_citations:
                        m = it.get("metadata", {})
                        citations.append({
                            "index": cite_index,
                            "source_path": m.get("source_path"),
                            "doc_id": m.get("doc_id"),
                            "start": m.get("start"),
                            "end": m.get("end"),
                            "heading_path": m.get("heading_path"),
                        })
                        cite_index += 1
                break
            out.append(text + suffix)
            total += need
            if include_citations:
                m = it.get("metadata", {})
                citations.append({
                    "index": cite_index,
                    "source_path": m.get("source_path"),
                    "doc_id": m.get("doc_id"),
                    "start": m.get("start"),
                    "end": m.get("end"),
                    "heading_path": m.get("heading_path"),
                })
                cite_index += 1
        if total >= max_chars:
            break

    merged = "\n\n".join(out)
    if include_citations and citations:
        lines: List[str] = [merged, "", "References:"]
        for c in citations:
            loc = ""
            if c.get("start") is not None and c.get("end") is not None:
                loc = f" ({c['start']}-{c['end']})"
            hp = f" – {c['heading_path']}" if c.get("heading_path") else ""
            sp = c.get("source_path") or c.get("doc_id") or "source"
            lines.append(f"[{c['index']}] {sp}{loc}{hp}")
        return "\n".join(lines)
    return merged


def compress_ranked_items(ranked_items: List[Dict], enable_compression: bool = True, max_per_doc: int = 2, join_gap: int = 200) -> List[Dict]:
    if not enable_compression:
        return ranked_items

    by_doc_count: Dict[str, int] = {}
    last_by_doc: Dict[str, Dict] = {}
    new_items: List[Dict] = []
    for it in ranked_items:
        meta = it.get("metadata", {})
        did = meta.get("doc_id") or meta.get("source_path") or "unknown"
        start = int(meta.get("start") or 0)
        end = int(meta.get("end") or (start + len(it.get("content", "") or "")))

        if did not in last_by_doc:
            last_by_doc[did] = it
            by_doc_count[did] = 1
            new_items.append(it)
            continue

        last = last_by_doc[did]
        lmeta = last.get("metadata", {})
        lstart = int(lmeta.get("start") or 0)
        lend = int(lmeta.get("end") or (lstart + len(last.get("content", "") or "")))

        if start - lend <= join_gap and start >= lstart:
            merged_text = (last.get("content", "") or "").strip()
            add_text = (it.get("content", "") or "").strip()
            if add_text:
                if merged_text:
                    merged_text = merged_text + "\n\n" + add_text
                else:
                    merged_text = add_text
                last["content"] = merged_text
                lmeta["end"] = max(lend, end)
                try:
                    last["score"] = max(float(last.get("score", 0.0)), float(it.get("score", 0.0)))
                except Exception:
                    pass
            last_by_doc[did] = last
        else:
            cnt = by_doc_count.get(did, 0)
            if cnt >= max_per_doc:
                continue
            new_items.append(it)
            last_by_doc[did] = it
            by_doc_count[did] = cnt + 1

    return new_items


def tldr_summarize(text: str, bullets: int = 3) -> Optional[str]:
    try:
        if not text or len(text.strip()) == 0:
            return None
        from core.llm import HelloAgentsLLM
        llm = HelloAgentsLLM()
        prompt = [
            {"role": "system", "content": "请将以下内容概括为简洁的要点列表（最多3-5条），用中文，避免重复，突出关键信息。"},
            {"role": "user", "content": f"请用 {max(1, min(5, int(bullets)))} 条要点总结：\n\n{text}"},
        ]
        out = llm.invoke(prompt)
        return out
    except Exception:
        return None


def create_rag_pipeline(
    qdrant_url: Optional[str] = None,
    qdrant_api_key: Optional[str] = None,
    collection_name: str = "hello_agents_rag_vectors",
    rag_namespace: str = "default"
) -> Dict[str, Any]:
    from ..embedding import get_dimension
    dimension = get_dimension(384)

    store = QdrantVectorStore(
        url=qdrant_url,
        api_key=qdrant_api_key,
        collection_name=collection_name,
        vector_size=dimension,
        distance="cosine"
    )

    def add_documents(file_paths: List[str], chunk_size: int = 800, chunk_overlap: int = 100):
        from .chunker import load_and_chunk_texts
        from .indexer import index_chunks
        chunks = load_and_chunk_texts(
            paths=file_paths,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            namespace=rag_namespace,
            source_label="rag"
        )
        index_chunks(
            store=store,
            chunks=chunks,
            rag_namespace=rag_namespace
        )
        return len(chunks)

    def search(query: str, top_k: int = 8, score_threshold: Optional[float] = None):
        return search_vectors(
            store=store,
            query=query,
            top_k=top_k,
            rag_namespace=rag_namespace,
            score_threshold=score_threshold
        )

    def search_advanced(
        query: str,
        top_k: int = 8,
        enable_mqe: bool = False,
        enable_hyde: bool = False,
        score_threshold: Optional[float] = None
    ):
        return search_vectors_expanded(
            store=store,
            query=query,
            top_k=top_k,
            rag_namespace=rag_namespace,
            enable_mqe=enable_mqe,
            enable_hyde=enable_hyde,
            score_threshold=score_threshold
        )

    def get_stats():
        return store.get_collection_stats()

    return {
        "store": store,
        "namespace": rag_namespace,
        "add_documents": add_documents,
        "search": search,
        "search_advanced": search_advanced,
        "get_stats": get_stats
    }