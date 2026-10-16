# -*- coding: utf-8 -*-
"""法务知识库入库脚本：把 data/legal/ 下的文档灌进 Qdrant 的 legal 命名空间。

用法：
    python scripts/ingest_legal_kb.py                  # 增量入库
    python scripts/ingest_legal_kb.py --rebuild        # 先清空 legal 命名空间再全量重灌
    python scripts/ingest_legal_kb.py --dir D:\\docs   # 指定其他目录

分块策略：
    * .md 按二级标题（##）拆成小节，每节作为独立文档 add_text 入库——
      规避 chunker 的 token 计量问题（整篇 md 只会被切成 1 块，检索粒度太粗）；
    * 其他格式（pdf/docx）整文件 add_document 走标准管道。

前置条件：
    * Qdrant 运行中（默认 localhost:6333，或 .env 的 QDRANT_URL/QDRANT_API_KEY）
      ``docker run -d -p 6333:6333 qdrant/qdrant``
    * embedding 已配置（.env 的 DASHSCOPE_API_KEY + EMBED_MODEL_TYPE=dashscope）
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# 保证从项目根运行时也能导入项目内模块
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 必须在导入 RAG/embedding 模块前加载 .env：
# EMBED_API_KEY / QDRANT_URL 等只在 .env 里，核心模块不会自动加载
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from tool.builtin.legal_rag_tool import LegalRAGTool  # noqa: E402

SUPPORTED = (".md", ".txt", ".pdf", ".docx")


def split_markdown_sections(text: str) -> list:
    """按二级标题（## ）把 markdown 拆成 (标题, 内容) 小节。

    没有二级标题时整篇作为一节；一级标题下的前言并入后一节。
    """
    lines = text.splitlines()
    sections: list = []
    current_title = ""
    current_lines: list = []

    for line in lines:
        if line.startswith("## "):
            if current_lines and any(l.strip() for l in current_lines):
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = line[3:].strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines and any(l.strip() for l in current_lines):
        sections.append((current_title, "\n".join(current_lines).strip()))

    return sections or [("", text)]


def ingest_markdown(tool: LegalRAGTool, path: str, namespace: str) -> tuple:
    """md 文件按小节 add_text 入库，返回 (成功节数, 总节数)。"""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    sections = split_markdown_sections(text)
    base = os.path.splitext(os.path.basename(path))[0]
    ok = 0
    for idx, (title, content) in enumerate(sections, 1):
        doc_id = f"{base}::{title or f'节{idx}'}"
        # RAGTool 会把 document_id 写成临时 .md 文件名，
        # Windows 非法字符（:: " < > | 等）会导致 [Errno 22]
        doc_id = re.sub(r'[\\/:*?"<>|\s]+', "_", doc_id)
        # 每节带上文档名上下文，检索命中时不依赖 chunk 元数据也能定位来源
        payload = f"（来源：{base}）\n\n{content}"
        result = tool.run({
            "action": "add_text",
            "text": payload,
            "document_id": doc_id,
            "namespace": namespace,
        })
        print(f"   [{idx}/{len(sections)}] {doc_id}: {'✅' if '✅' in result else '❌ ' + result[:120]}")
        ok += 1 if "✅" in result else 0
    return ok, len(sections)


def main() -> int:
    # Windows GBK 控制台兜底
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description="法务知识库入库（legal 命名空间）")
    parser.add_argument("--dir", default=os.path.join(PROJECT_ROOT, "data", "legal"),
                        help="语料目录（默认 data/legal）")
    parser.add_argument("--ns", default="legal", help="命名空间（默认 legal）")
    parser.add_argument("--rebuild", action="store_true",
                        help="先清空命名空间再全量重灌")
    args = parser.parse_args()

    corpus_dir = os.path.abspath(args.dir)
    if not os.path.isdir(corpus_dir):
        print(f"❌ 语料目录不存在: {corpus_dir}")
        return 1

    files = sorted(
        os.path.join(corpus_dir, name)
        for name in os.listdir(corpus_dir)
        if name.lower().endswith(SUPPORTED) and not name.startswith("~")
    )
    if not files:
        print(f"❌ 目录中没有可入库的文档: {corpus_dir}")
        return 1

    print(f"📚 法务语料: {len(files)} 个文件 ← {corpus_dir}")
    for path in files:
        print(f"   • {os.path.basename(path)}")

    tool = LegalRAGTool()
    if not getattr(tool, "initialized", False):
        print(f"❌ RAG 初始化失败（Qdrant/embedding 不可用）: {getattr(tool, 'init_error', '未知')}")
        print("   提示: docker run -d -p 6333:6333 qdrant/qdrant，并检查 .env 的 embedding 配置")
        return 1

    if args.rebuild:
        print("\n🗑️ 清空 legal 命名空间 (--rebuild) ...")
        print(tool.run({"action": "clear", "confirm": True, "namespace": args.ns}))

    print("\n📥 开始入库 ...")
    ok_files = 0
    for path in files:
        name = os.path.basename(path)
        print(f"\n── {name}")
        if name.lower().endswith((".md", ".txt")):
            ok, total = ingest_markdown(tool, path, args.ns)
            if ok == total:
                ok_files += 1
            print(f"   小节入库: {ok}/{total}")
        else:
            result = tool.run({"action": "add_document", "file_path": path, "namespace": args.ns})
            print(result)
            if "✅" in result:
                ok_files += 1

    print(f"\n{'=' * 40}")
    print(f"入库完成: {ok_files}/{len(files)} 个文件成功")

    if ok_files == len(files):
        print("\n💡 验证: python -c \"from tool.builtin.legal_rag_tool import LegalRAGTool; "
              "print(LegalRAGTool().run({'action':'ask','question':'物业费诉讼时效是多久？'}))\"")
    return 0 if ok_files == len(files) else 2


if __name__ == "__main__":
    raise SystemExit(main())
