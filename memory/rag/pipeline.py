"""RAG Pipeline 模块 - 向后兼容的重导出入口

所有功能已拆分到独立模块中:
- document.py:  数据模型 (Document, DocumentChunk, DocumentProcessor)
- parser.py:    文档解析 (_convert_to_markdown, etc.)
- chunker.py:   文档分块 (load_and_chunk_texts, etc.)
- indexer.py:   向量索引 (index_chunks, embed_query, etc.)
- retriever.py: 检索查询 (search_vectors, create_rag_pipeline, etc.)

本文件保留所有原有导入以维持向后兼容。
"""

# 数据模型
from .document import Document, DocumentChunk, DocumentProcessor

# 解析器
from .parser import (
    _get_markitdown_instance,
    _is_markitdown_supported_format,
    _convert_to_markdown,
    _enhanced_pdf_processing,
    _post_process_pdf_text,
    _fallback_text_reader,
)

# 分块器
from .chunker import (
    _detect_lang,
    _is_cjk,
    _hard_split_text,
    _split_paragraphs_with_headings,
    _split_oversized_paragraph,
    _chunk_paragraphs,
    load_and_chunk_texts,
    build_graph_from_chunks,
)

# 索引器
from .indexer import (
    _preprocess_markdown_for_embedding,
    _create_default_vector_store,
    index_chunks,
    embed_query,
)

# 检索器
from .retriever import (
    search_vectors,
    _prompt_mqe,
    _prompt_hyde,
    search_vectors_expanded,
    _try_load_cross_encoder,
    rerank_with_cross_encoder,
    compute_graph_signals_from_pool,
    rank,
    merge_snippets,
    expand_neighbors_from_pool,
    merge_snippets_grouped,
    compress_ranked_items,
    tldr_summarize,
    create_rag_pipeline,
)