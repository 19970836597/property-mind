"""文档分块模块 - 将解析后的文本切分为可索引的块

本模块是分块逻辑的唯一归属：
- 函数式流水线：``load_and_chunk_texts``（文件 → markdown → 段落 → chunk）
- 对象式 API：``DocumentProcessor``（从 ``document.py`` 搬入，操作
  ``Document`` / ``DocumentChunk`` 数据模型）
"""

import os
import hashlib
from datetime import datetime
from typing import Any, List, Dict, Optional, Set

from .document import Document, DocumentChunk
from .parser import _convert_to_markdown


class DocumentProcessor:
    """对象式文档分块器（从 ``document.py`` 搬入，归属分块模块）。

    与 ``load_and_chunk_texts`` 的区别：本类操作 ``Document`` /
    ``DocumentChunk`` 数据模型，走简单的分隔符切分；后者是文件 →
    markdown → 段落感知的函数式流水线。两者并存，新代码按需选用。
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        separators: Optional[List[str]] = None
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or ["\n\n", "\n", "。", ".", " "]

    def process_document(self, document: Document) -> List[DocumentChunk]:
        """
        处理文档，分割成块

        Args:
            document: 输入文档

        Returns:
            文档块列表
        """
        chunks = self._split_text(document.content)

        document_chunks = []
        for i, chunk_content in enumerate(chunks):
            # 创建块的元数据
            chunk_metadata = document.metadata.copy()
            chunk_metadata.update({
                "doc_id": document.doc_id,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "processed_at": datetime.now().isoformat()
            })

            chunk = DocumentChunk(
                content=chunk_content,
                metadata=chunk_metadata,
                doc_id=document.doc_id,
                chunk_index=i
            )
            document_chunks.append(chunk)

        return document_chunks

    def process_documents(self, documents: List[Document]) -> List[DocumentChunk]:
        """
        批量处理文档

        Args:
            documents: 文档列表

        Returns:
            所有文档块列表
        """
        all_chunks = []
        for document in documents:
            chunks = self.process_document(document)
            all_chunks.extend(chunks)

        return all_chunks

    def _split_text(self, text: str) -> List[str]:
        """
        分割文本为块

        Args:
            text: 输入文本

        Returns:
            文本块列表
        """
        if len(text) <= self.chunk_size:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            # 确定块的结束位置
            end = start + self.chunk_size

            if end >= len(text):
                # 最后一块
                chunks.append(text[start:])
                break

            # 寻找合适的分割点
            split_point = self._find_split_point(text, start, end)

            if split_point == -1:
                # 没找到合适的分割点，强制分割
                split_point = end

            chunks.append(text[start:split_point])

            # 计算下一块的开始位置（考虑重叠）
            start = max(start + 1, split_point - self.chunk_overlap)

        return chunks

    def _find_split_point(self, text: str, start: int, end: int) -> int:
        """
        在指定范围内寻找最佳分割点

        Args:
            text: 文本
            start: 开始位置
            end: 结束位置

        Returns:
            分割点位置，-1表示未找到
        """
        # 从后往前寻找分隔符
        for separator in self.separators:
            # 在end附近寻找分隔符
            search_start = max(start, end - 100)  # 在最后100个字符中寻找

            for i in range(end - len(separator), search_start - 1, -1):
                if text[i:i + len(separator)] == separator:
                    return i + len(separator)

        return -1

    def merge_chunks(self, chunks: List[DocumentChunk], max_length: int = 2000) -> List[DocumentChunk]:
        """
        合并小的文档块

        Args:
            chunks: 文档块列表
            max_length: 合并后的最大长度

        Returns:
            合并后的文档块列表
        """
        if not chunks:
            return []

        merged_chunks = []
        current_chunk = chunks[0]

        for next_chunk in chunks[1:]:
            # 检查是否可以合并
            combined_length = len(current_chunk.content) + len(next_chunk.content)

            if (combined_length <= max_length and
                current_chunk.doc_id == next_chunk.doc_id):
                # 合并块
                current_chunk.content += "\n" + next_chunk.content
                current_chunk.metadata["total_chunks"] = current_chunk.metadata.get("total_chunks", 1) + 1
            else:
                # 不能合并，保存当前块
                merged_chunks.append(current_chunk)
                current_chunk = next_chunk

        # 添加最后一个块
        merged_chunks.append(current_chunk)

        return merged_chunks

    def filter_chunks(self, chunks: List[DocumentChunk], min_length: int = 50) -> List[DocumentChunk]:
        """
        过滤太短的文档块

        Args:
            chunks: 文档块列表
            min_length: 最小长度

        Returns:
            过滤后的文档块列表
        """
        return [chunk for chunk in chunks if len(chunk.content.strip()) >= min_length]

    def add_chunk_metadata(self, chunks: List[DocumentChunk], metadata: Dict[str, Any]) -> List[DocumentChunk]:
        """
        为文档块添加额外元数据

        Args:
            chunks: 文档块列表
            metadata: 要添加的元数据

        Returns:
            更新后的文档块列表
        """
        for chunk in chunks:
            chunk.metadata.update(metadata)

        return chunks


def _detect_lang(sample: str) -> str:
    try:
        from langdetect import detect
        return detect(sample[:1000]) if sample else "unknown"
    except Exception:
        return "unknown"


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF or
        0x3400 <= code <= 0x4DBF or
        0x20000 <= code <= 0x2A6DF or
        0x2A700 <= code <= 0x2B73F or
        0x2B740 <= code <= 0x2B81F or
        0x2B820 <= code <= 0x2CEAF or
        0xF900 <= code <= 0xFAFF
    )


def _hard_split_text(
    text: str,
    max_chars: int = 1000,
    overlap_chars: int = 200
) -> List[str]:
    """
    文本硬切分，根据中文标点符号和换行符切分
    """

    text = text.strip()

    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + max_chars, text_len)

        if end < text_len:
            candidates = [
                text.rfind("。", start, end),
                text.rfind("！", start, end),
                text.rfind("？", start, end),
                text.rfind(".", start, end),
                text.rfind("!", start, end),
                text.rfind("?", start, end),
                text.rfind("\n", start, end),
            ]

            boundary = max(candidates)

            if boundary > start + max_chars * 0.6:
                end = boundary + 1

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= text_len:
            break

        start = max(start + 1, end - overlap_chars)

    return chunks


def _split_paragraphs_with_headings(text: str) -> List[Dict]:
    lines = text.splitlines()
    heading_stack: List[str] = []
    paragraphs: List[Dict] = []
    buf: List[str] = []
    char_pos = 0

    def flush_buf(end_pos: int):
        if not buf:
            return
        content = "\n".join(buf).strip()
        if not content:
            return
        paragraphs.append({
            "content": content,
            "heading_path": " > ".join(heading_stack) if heading_stack else None,
            "start": max(0, end_pos - len(content)),
            "end": end_pos,
        })

    for ln in lines:
        raw = ln
        if raw.strip().startswith("#"):
            flush_buf(char_pos)
            level = len(raw) - len(raw.lstrip('#'))
            title = raw.lstrip('#').strip()
            if level <= 0:
                level = 1
            if level <= len(heading_stack):
                heading_stack = heading_stack[:level - 1]
            heading_stack.append(title)
            char_pos += len(raw) + 1
            continue

        if raw.strip() == "":
            flush_buf(char_pos)
            buf = []
        else:
            buf.append(raw)
        char_pos += len(raw) + 1

    flush_buf(char_pos)

    if not paragraphs:
        paragraphs = [{"content": text, "heading_path": None, "start": 0, "end": len(text)}]

    return paragraphs


def _split_oversized_paragraph(paragraph: Dict, chunk_tokens: int) -> List[Dict]:
    content = paragraph["content"]
    heading_path = paragraph.get("heading_path")
    start = paragraph.get("start", 0)

    import re
    sentence_endings = re.compile(r'(?<=[。！？\.!?])\s*|(?<=\n)\s*')
    splits = [s for s in sentence_endings.split(content) if s.strip()]

    if not splits:
        return [paragraph]

    sub_paragraphs: List[Dict] = []
    cur_text = ""
    cur_start = start

    for seg in splits:
        seg_tokens = len(_hard_split_text(seg))
        cur_tokens = len(_hard_split_text(cur_text)) if cur_text else 0

        if cur_tokens + seg_tokens > chunk_tokens and cur_text.strip():
            sub_paragraphs.append({
                "content": cur_text.strip(),
                "heading_path": heading_path,
                "start": cur_start,
                "end": cur_start + len(cur_text),
            })
            cur_start = cur_start + len(cur_text)
            cur_text = seg
        else:
            cur_text += seg

    if cur_text.strip():
        sub_paragraphs.append({
            "content": cur_text.strip(),
            "heading_path": heading_path,
            "start": cur_start,
            "end": cur_start + len(cur_text),
        })

    if not sub_paragraphs:
        return [paragraph]

    return sub_paragraphs


def _chunk_paragraphs(paragraphs: List[Dict], chunk_tokens: int, overlap_tokens: int) -> List[Dict]:
    chunks: List[Dict] = []
    cur: List[Dict] = []
    cur_tokens = 0
    i = 0

    while i < len(paragraphs):
        p = paragraphs[i]
        p_tokens = len(_hard_split_text(p["content"])) or 1

        if p_tokens > chunk_tokens:
            if cur:
                content = "\n\n".join(x["content"] for x in cur)
                s = cur[0]["start"]
                e = cur[-1]["end"]
                hp = next((x["heading_path"] for x in reversed(cur) if x.get("heading_path")), None)
                chunks.append({"content": content, "start": s, "end": e, "heading_path": hp})
                cur = []
                cur_tokens = 0

            sub_parts = _split_oversized_paragraph(p, chunk_tokens)
            for sp in sub_parts:
                sp_tokens = len(_hard_split_text(sp["content"])) or 1
                if cur_tokens + sp_tokens <= chunk_tokens or not cur:
                    cur.append(sp)
                    cur_tokens += sp_tokens
                else:
                    if cur:
                        content = "\n\n".join(x["content"] for x in cur)
                        s = cur[0]["start"]
                        e = cur[-1]["end"]
                        hp = next((x["heading_path"] for x in reversed(cur) if x.get("heading_path")), None)
                        chunks.append({"content": content, "start": s, "end": e, "heading_path": hp})
                    cur = [sp]
                    cur_tokens = sp_tokens
            i += 1
            continue

        if cur_tokens + p_tokens <= chunk_tokens or not cur:
            cur.append(p)
            cur_tokens += p_tokens
            i += 1
        else:
            content = "\n\n".join(x["content"] for x in cur)
            s = cur[0]["start"]
            e = cur[-1]["end"]
            hp = next((x["heading_path"] for x in reversed(cur) if x.get("heading_path")), None)
            chunks.append({
                "content": content,
                "start": s,
                "end": e,
                "heading_path": hp,
            })

            if overlap_tokens > 0 and cur:
                kept: List[Dict] = []
                kept_tokens = 0
                for x in reversed(cur):
                    t = len(_hard_split_text(x["content"])) or 1
                    if kept_tokens + t > overlap_tokens:
                        break
                    kept.append(x)
                    kept_tokens += t
                cur = list(reversed(kept))
                cur_tokens = kept_tokens
            else:
                cur = []
                cur_tokens = 0

    if cur:
        content = "\n\n".join(x["content"] for x in cur)
        s = cur[0]["start"]
        e = cur[-1]["end"]
        hp = next((x["heading_path"] for x in reversed(cur) if x.get("heading_path")), None)
        chunks.append({
            "content": content,
            "start": s,
            "end": e,
            "heading_path": hp,
        })

    return chunks


def load_and_chunk_texts(
    paths: List[str],
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    namespace: Optional[str] = None,
    source_label: str = "rag"
) -> List[Dict]:
    print(f"[RAG] Universal loader start: files={len(paths)} chunk_size={chunk_size} overlap={chunk_overlap} ns={namespace or 'default'}")
    chunks: List[Dict] = []
    seen_hashes: Set[str] = set()

    for path in paths:
        if not os.path.exists(path):
            print(f"[WARNING] File not found: {path}")
            continue

        print(f"[RAG] Processing: {path}")
        ext = (os.path.splitext(path)[1] or '').lower()

        markdown_text = _convert_to_markdown(path)
        if not markdown_text.strip():
            print(f"[WARNING] No content extracted from: {path}")
            continue

        lang = _detect_lang(markdown_text)
        doc_id = hashlib.md5(f"{path}|{len(markdown_text)}".encode('utf-8')).hexdigest()

        para = _split_paragraphs_with_headings(markdown_text)
        token_chunks = _chunk_paragraphs(para, chunk_tokens=max(1, chunk_size), overlap_tokens=max(0, chunk_overlap))

        for ch in token_chunks:
            content = ch["content"]

            safe_contents = _hard_split_text(
                content,
                max_chars=1000,
                overlap_chars=200
            )

            for sub_index, safe_content in enumerate(safe_contents):
                safe_content = safe_content.strip()

                if not safe_content:
                    continue

                content_hash = hashlib.md5(
                    safe_content.encode("utf-8")
                ).hexdigest()

                if content_hash in seen_hashes:
                    continue

                seen_hashes.add(content_hash)

                chunk_id = hashlib.md5(
                    f"{doc_id}|{ch.get('start', 0)}|"
                    f"{ch.get('end', 0)}|"
                    f"{sub_index}|{content_hash}".encode("utf-8")
                ).hexdigest()

                chunks.append({
                    "id": chunk_id,
                    "content": safe_content,
                    "metadata": {
                        "source_path": path,
                        "file_ext": ext,
                        "doc_id": doc_id,
                        "lang": lang,
                        "start": ch.get("start", 0),
                        "end": ch.get("end", 0),
                        "content_hash": content_hash,
                        "namespace": namespace or "default",
                        "source": source_label,
                        "external": True,
                        "heading_path": ch.get("heading_path"),
                        "format": "markdown",
                        "sub_chunk_index": sub_index,
                    },
                })

    return chunks


def build_graph_from_chunks(neo4j, chunks: List[Dict]) -> None:
    created_docs = set()
    for ch in chunks:
        mem_id = ch["id"]
        meta = ch.get("metadata", {})
        source_path = meta.get("source_path")
        doc_id = meta.get("doc_id")
        if doc_id and doc_id not in created_docs:
            created_docs.add(doc_id)
            try:
                neo4j.add_entity(
                    entity_id=doc_id,
                    name=os.path.basename(source_path or doc_id),
                    entity_type="Document",
                    properties={"source_path": source_path, "lang": meta.get("lang")}
                )
            except Exception:
                pass
        try:
            neo4j.add_entity(entity_id=mem_id, name=mem_id, entity_type="Memory", properties={
                "source_path": source_path,
                "doc_id": doc_id,
                "start": meta.get("start"),
                "end": meta.get("end"),
            })
        except Exception:
            pass
        if doc_id:
            try:
                neo4j.add_relationship(from_id=doc_id, to_id=mem_id, rel_type="HAS_CHUNK", properties={})
            except Exception:
                pass