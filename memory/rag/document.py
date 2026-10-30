"""文档数据模型 - 只放 Document / DocumentChunk 与轻量构造 helpers.

分块逻辑已搬到 ``chunker.DocumentProcessor``；旧的
``from memory.rag.document import DocumentProcessor`` 仍可用（延迟兼容导出），
新代码请从 ``memory.rag.chunker`` 导入。
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import hashlib

@dataclass
class Document:
    """文档类"""
    content: str
    metadata: Dict[str, Any]
    doc_id: Optional[str] = None

    def __post_init__(self):
        if self.doc_id is None:
            # 基于内容生成ID
            self.doc_id = hashlib.md5(self.content.encode()).hexdigest()

@dataclass
class DocumentChunk:
    """文档块类"""
    content: str
    metadata: Dict[str, Any]
    chunk_id: Optional[str] = None
    doc_id: Optional[str] = None
    chunk_index: int = 0

    def __post_init__(self):
        if self.chunk_id is None:
            # 基于文档ID和块索引生成ID
            chunk_content = f"{self.doc_id}_{self.chunk_index}_{self.content[:50]}"
            self.chunk_id = hashlib.md5(chunk_content.encode()).hexdigest()

def load_text_file(file_path: str, encoding: str = "utf-8") -> Document:
    """
    加载文本文件为文档

    Args:
        file_path: 文件路径
        encoding: 文件编码

    Returns:
        文档对象
    """
    with open(file_path, 'r', encoding=encoding) as f:
        content = f.read()

    metadata = {
        "source": file_path,
        "type": "text_file",
        "loaded_at": datetime.now().isoformat()
    }

    return Document(content=content, metadata=metadata)

def create_document(content: str, **metadata) -> Document:
    """
    创建文档的便捷函数

    Args:
        content: 文档内容
        **metadata: 元数据

    Returns:
        文档对象
    """
    return Document(content=content, metadata=metadata)


def __getattr__(name: str):
    """延迟兼容导出：分块逻辑已搬到 chunker，旧导入路径仍可用。"""
    if name == "DocumentProcessor":
        from .chunker import DocumentProcessor
        return DocumentProcessor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
