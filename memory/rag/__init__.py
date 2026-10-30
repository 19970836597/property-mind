"""RAG 模块 - 检索增强生成

模块结构:
- document:   数据模型 (Document, DocumentChunk)
- parser:     文档解析 (parse_document, batch_parse_documents)
- chunker:    文档分块 (DocumentProcessor, load_and_chunk_texts, build_graph_from_chunks)
- indexer:    向量索引 (index_chunks, embed_query)
- retriever:  检索查询 (search_vectors, search_vectors_expanded, create_rag_pipeline)
- pipeline:   向后兼容的重导出入口
"""

from .document import Document, DocumentChunk

from .parser import parse_document, batch_parse_documents

from .chunker import DocumentProcessor, load_and_chunk_texts, build_graph_from_chunks

from .indexer import index_chunks, embed_query

from .retriever import (
    search_vectors,
    search_vectors_hybrid,
    search_vectors_expanded,
    rerank_with_cross_encoder,
    compute_graph_signals_from_pool,
    rank,
    merge_snippets,
    merge_snippets_grouped,
    expand_neighbors_from_pool,
    compress_ranked_items,
    tldr_summarize,
    create_rag_pipeline,
)

__all__ = [
    # document
    "Document",
    "DocumentChunk",
    # parser
    "parse_document",
    "batch_parse_documents",
    # chunker
    "DocumentProcessor",
    "load_and_chunk_texts",
    "build_graph_from_chunks",
    # indexer
    "index_chunks",
    "embed_query",
    # retriever
    "search_vectors",
    "search_vectors_hybrid",
    "search_vectors_expanded",
    "rerank_with_cross_encoder",
    "compute_graph_signals_from_pool",
    "rank",
    "merge_snippets",
    "merge_snippets_grouped",
    "expand_neighbors_from_pool",
    "compress_ranked_items",
    "tldr_summarize",
    "create_rag_pipeline",
]
