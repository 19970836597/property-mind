"""索引模块 - 将文档块向量化并写入向量知识库"""

import os
import re
import time
from typing import List, Dict, Optional, Any

from ..embedding import get_text_embedder, get_dimension
from ..storage.qdrant import QdrantVectorStore


def _preprocess_markdown_for_embedding(text: str) -> str:
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'```[^\n]*\n([\s\S]*?)```', r'\1', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)

    result = text.strip()
    if not result:
        return " "
    return result


def _create_default_vector_store(dimension: int = None) -> QdrantVectorStore:
    if dimension is None:
        dimension = get_dimension(384)

    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")

    from ..storage.qdrant import QdrantConnectionManager
    return QdrantConnectionManager.get_instance(
        url=qdrant_url,
        api_key=qdrant_api_key,
        collection_name="hello_agents_rag_vectors",
        vector_size=dimension,
        distance="cosine"
    )


def embed_query(query: str) -> List[float]:
    embedder = get_text_embedder()
    dimension = get_dimension(384)
    try:
        vec = embedder.encode(query)

        if hasattr(vec, "tolist"):
            vec = vec.tolist()

        if isinstance(vec, list) and vec and isinstance(vec[0], (list, tuple)):
            vec = vec[0]

        result = [float(x) for x in vec]

        if len(result) != dimension:
            print(f"[WARNING] Query向量维度异常: 期望{dimension}, 实际{len(result)}")
            if len(result) < dimension:
                result.extend([0.0] * (dimension - len(result)))
            else:
                result = result[:dimension]

        return result
    except Exception as e:
        print(f"[WARNING] Query embedding failed: {e}")
        return [0.0] * dimension


def index_chunks(
    store=None,
    chunks: List[Dict] = None,
    cache_db: Optional[str] = None,
    batch_size: int = 64,
    rag_namespace: str = "default"
) -> None:
    if not chunks:
        print("[RAG] No chunks to index")
        return

    embedder = get_text_embedder()
    dimension = get_dimension(384)

    if store is None:
        store = _create_default_vector_store(dimension)
        print(f"[RAG] Created default Qdrant store with dimension {dimension}")

    processed_texts = []
    valid_chunks = []
    for c in chunks:
        raw_content = c["content"]
        processed_content = _preprocess_markdown_for_embedding(raw_content)
        if not processed_content or not processed_content.strip():
            print(f"[WARNING] Chunk 预处理后为空，跳过: id={c.get('id', '?')}")
            continue
        processed_texts.append(processed_content)
        valid_chunks.append(c)
    chunks = valid_chunks

    if not processed_texts:
        print("[RAG] All chunks empty after preprocessing, nothing to index")
        return

    batch_size = min(max(int(batch_size), 1), 10)
    print(
        f"[RAG] Chunk statistics: "
        f"count={len(processed_texts)}, "
        f"max_chars={max(len(x) for x in processed_texts)}, "
        f"avg_chars={sum(len(x) for x in processed_texts) / len(processed_texts):.1f}"
    )
    for idx, text in enumerate(processed_texts):
        if len(text) > 6000:
            print(
                f"[WARNING] Oversized chunk: "
                f"index={idx}, "
                f"chars={len(text)}, "
                f"preview={text[:100]!r}"
            )

    vecs: List[List[float]] = []
    for i in range(0, len(processed_texts), batch_size):
        part = processed_texts[i:i + batch_size]
        try:
            part_vecs = embedder.encode(part)

            if not isinstance(part_vecs, list):
                if hasattr(part_vecs, "tolist"):
                    part_vecs = [part_vecs.tolist()]
                else:
                    part_vecs = [list(part_vecs)]
            else:
                if part_vecs and not isinstance(part_vecs[0], (list, tuple)) and hasattr(part_vecs[0], "__len__"):
                    normalized_vecs = []
                    for v in part_vecs:
                        if hasattr(v, "tolist"):
                            normalized_vecs.append(v.tolist())
                        else:
                            normalized_vecs.append(list(v))
                    part_vecs = normalized_vecs
                elif part_vecs and not isinstance(part_vecs[0], (list, tuple)):
                    if hasattr(part_vecs, "tolist"):
                        part_vecs = [part_vecs.tolist()]
                    else:
                        part_vecs = [list(part_vecs)]

            for v in part_vecs:
                try:
                    if hasattr(v, "tolist"):
                        v = v.tolist()
                    v_norm = [float(x) for x in v]
                    if len(v_norm) != dimension:
                        print(f"[WARNING] 向量维度异常: 期望{dimension}, 实际{len(v_norm)}")
                        if len(v_norm) < dimension:
                            v_norm.extend([0.0] * (dimension - len(v_norm)))
                        else:
                            v_norm = v_norm[:dimension]
                    vecs.append(v_norm)
                except Exception as e:
                    raise RuntimeError(f"向量转换失败: {e}") from e

        except Exception as e:
            print(f"[WARNING] Batch {i} encoding failed: {e}")
            print(f"[RAG] Retrying batch {i} with smaller chunks...")

            success = False
            for j in range(0, len(part), 8):
                small_part = part[j:j + 8]
                try:
                    time.sleep(2)

                    small_vecs = embedder.encode(small_part)
                    if isinstance(small_vecs, list) and small_vecs and not isinstance(small_vecs[0], list):
                        small_vecs = [small_vecs]

                    for v in small_vecs:
                        if hasattr(v, "tolist"):
                            v = v.tolist()
                        try:
                            v_norm = [float(x) for x in v]
                            if len(v_norm) != dimension:
                                print(f"[WARNING] 向量维度异常: 期望{dimension}, 实际{len(v_norm)}")
                                if len(v_norm) < dimension:
                                    v_norm.extend([0.0] * (dimension - len(v_norm)))
                                else:
                                    v_norm = v_norm[:dimension]
                            vecs.append(v_norm)
                            success = True
                        except Exception as e2:
                            raise RuntimeError(f"小批次向量转换失败: {e2}") from e2
                except Exception as e2:
                    print(f"[WARNING] 小批次 {j // 8} 仍然失败: {e2}")

            if not success:
                raise RuntimeError(f"Embedding 批次 {i} 完全失败，已停止写入，避免产生零向量") from e

        print(f"[RAG] Embedding progress: {min(i + batch_size, len(processed_texts))}/{len(processed_texts)}")

    if len(vecs) != len(processed_texts):
        raise RuntimeError(f"Embedding 向量数量不匹配: 期望 {len(processed_texts)}，实际 {len(vecs)}")

    metas: List[Dict] = []
    ids: List[str] = []
    for ch in chunks:
        meta = {
            "memory_id": ch["id"],
            "user_id": "rag_user",
            "memory_type": "rag_chunk",
            "content": ch["content"],
            "data_source": "rag_pipeline",
            "rag_namespace": rag_namespace,
            "is_rag_data": True,
        }
        meta.update(ch.get("metadata", {}))
        metas.append(meta)
        ids.append(ch["id"])

    print(f"[RAG] Qdrant upsert start: n={len(vecs)}")
    success = store.add_vectors(vectors=vecs, metadata=metas, ids=ids)
    if success:
        print(f"[RAG] Qdrant upsert done: {len(vecs)} vectors indexed")
    else:
        print(f"[RAG] Qdrant upsert failed")
        raise RuntimeError("Failed to index vectors to Qdrant")