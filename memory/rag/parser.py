"""文档解析模块 - 负责将各种格式的文档转换为文本"""

import os
import re
from typing import Optional


def _get_markitdown_instance():
    try:
        from markitdown import MarkItDown
        return MarkItDown()
    except ImportError:
        print("[WARNING] MarkItDown not available. Install with: pip install markitdown")
        return None


def _is_markitdown_supported_format(path: str) -> bool:
    ext = (os.path.splitext(path)[1] or '').lower()
    supported_formats = {
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        '.txt', '.md', '.csv', '.json', '.xml', '.html', '.htm',
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp',
        '.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg',
        '.zip', '.tar', '.gz', '.rar',
        '.py', '.js', '.ts', '.java', '.cpp', '.c', '.h', '.css', '.scss',
        '.log', '.conf', '.ini', '.cfg', '.yaml', '.yml', '.toml'
    }
    return ext in supported_formats


def _convert_to_markdown(path: str) -> str:
    if not os.path.exists(path):
        return ""

    ext = (os.path.splitext(path)[1] or '').lower()
    if ext == '.pdf':
        return _enhanced_pdf_processing(path)

    md_instance = _get_markitdown_instance()
    if md_instance is None:
        return _fallback_text_reader(path)

    try:
        result = md_instance.convert(path)
        text = getattr(result, "text_content", None)
        if isinstance(text, str) and text.strip():
            return text
        return ""
    except Exception as e:
        print(f"[WARNING] MarkItDown failed for {path}: {e}")
        return _fallback_text_reader(path)


def _enhanced_pdf_processing(path: str) -> str:
    print(f"[RAG] Using enhanced PDF processing for: {path}")

    md_instance = _get_markitdown_instance()
    if md_instance is None:
        return _fallback_text_reader(path)

    try:
        result = md_instance.convert(path)
        raw_text = getattr(result, "text_content", None)
        if not raw_text or not raw_text.strip():
            return ""

        cleaned_text = _post_process_pdf_text(raw_text)
        print(f"[RAG] PDF post-processing completed: {len(raw_text)} -> {len(cleaned_text)} chars")
        return cleaned_text

    except Exception as e:
        print(f"[WARNING] Enhanced PDF processing failed for {path}: {e}")
        return _fallback_text_reader(path)


def _post_process_pdf_text(text: str) -> str:
    lines = text.splitlines()
    cleaned_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if len(line) <= 2 and not line.isdigit():
            continue

        if re.match(r'^\d+$', line):
            continue

        if line.lower() in ['物业', '小区', '楼栋', '业主', '报修',"物业费"]:
            continue

        cleaned_lines.append(line)

    merged_lines = []
    i = 0

    while i < len(cleaned_lines):
        current_line = cleaned_lines[i]

        if len(current_line) < 60 and i + 1 < len(cleaned_lines):
            next_line = cleaned_lines[i + 1]

            if (not current_line.endswith('：') and
                not current_line.endswith(':') and
                not current_line.startswith('#') and
                not next_line.startswith('#') and
                len(next_line) < 120):

                merged_line = current_line + " " + next_line
                merged_lines.append(merged_line)
                i += 2
                continue

        merged_lines.append(current_line)
        i += 1

    paragraphs = []
    current_paragraph = []

    for line in merged_lines:
        if (line.startswith('#') or
            line.endswith('：') or
            line.endswith(':') or
            len(line) > 150 or
            not current_paragraph):

            if current_paragraph:
                paragraphs.append(' '.join(current_paragraph))
                current_paragraph = []

            paragraphs.append(line)
        else:
            current_paragraph.append(line)

    if current_paragraph:
        paragraphs.append(' '.join(current_paragraph))

    return '\n\n'.join(paragraphs)


def _fallback_text_reader(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception:
        try:
            with open(path, 'r', encoding='latin-1', errors='ignore') as f:
                return f.read()
        except Exception:
            return ""


def parse_document(path: str) -> Optional[str]:
    """解析文档的公共入口，返回纯文本内容"""
    text = _convert_to_markdown(path)
    if not text or not text.strip():
        return None
    return text


def batch_parse_documents(paths: list) -> dict:
    """批量解析文档，返回 {path: text} 字典"""
    result = {}
    for path in paths:
        text = parse_document(path)
        if text:
            result[path] = text
    return result