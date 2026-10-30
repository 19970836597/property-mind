"""RAG工具 - 检索增强生成

为HelloAgents框架提供简洁易用的RAG能力：

数据流程：
  用户数据 → [parser] 文档解析 → [chunker] 文本分块 → [indexer] 向量化存储 → [retriever] 智能检索 → LLM增强问答

核心特性：
- 多格式支持：PDF、Word、Excel、PPT、图片、音频、网页等
- 智能问答：自动检索相关内容，注入提示词，生成准确答案
- 命名空间隔离：支持多项目知识库隔离管理
- 高级搜索：支持多查询扩展(MQE)、假设文档嵌入(HyDE)

使用示例：
```python
rag = RAGTool()
rag.run({"action": "add_document", "file_path": "document.pdf"})
answer = rag.run({"action": "ask", "question": "什么是机器学习？"})
```
"""

from typing import Dict, Any, List, Optional
import os
import time

from core.paths import RAG_STORAGE_DIR
from ..tools import Tool, ToolParameter, tool_action
from memory.rag.pipeline import create_rag_pipeline
from core.llm import HelloAgentsLLM


class RAGTool(Tool):
    """RAG工具 - 检索增强生成工具

    封装了完整的RAG工作流程：
    1. 文档摄入：add_document / add_text / batch_add
    2. 知识检索：search / search_advanced
    3. 智能问答：ask（检索 + LLM生成）
    4. 管理操作：stats / clear / clear_all_namespaces

    内部依赖解耦的RAG模块：
    - memory.rag.parser   → 文档解析
    - memory.rag.chunker  → 文本分块
    - memory.rag.indexer  → 向量索引
    - memory.rag.retriever → 检索查询
    """

    def __init__(
        self,
        knowledge_base_path: str = str(RAG_STORAGE_DIR),
        qdrant_url: str = None,
        qdrant_api_key: str = None,
        collection_name: Optional[str] = None,
        rag_namespace: str = "default",
        expandable: bool = False,
        retriever_mode: Optional[str] = None,
    ):
        """初始化RAG工具

        Args:
            knowledge_base_path: 知识库本地文件存储路径（用于临时文件）
            qdrant_url:          Qdrant向量数据库地址（默认读取环境变量QDRANT_URL）
            qdrant_api_key:      Qdrant API密钥（默认读取环境变量QDRANT_API_KEY）
            collection_name:     Qdrant中的集合名称
            rag_namespace:       默认命名空间，用于多项目知识库隔离
            expandable:          是否支持展开模式（子工具模式）
        """
        super().__init__(
            name="rag",
            description="RAG工具 - 支持多格式文档检索增强生成，提供智能问答能力",
            expandable=expandable
        )

        self.knowledge_base_path = knowledge_base_path
        self.qdrant_url = qdrant_url or os.getenv("QDRANT_URL")
        self.qdrant_api_key = qdrant_api_key or os.getenv("QDRANT_API_KEY")
        self.retriever_mode = (
            retriever_mode or os.getenv("RAG_RETRIEVER_MODE", "hybrid")
        ).strip().lower()
        if self.retriever_mode not in {"legacy", "hybrid"}:
            raise ValueError(
                f"RAG_RETRIEVER_MODE must be legacy or hybrid, "
                f"got {self.retriever_mode!r}"
            )
        if collection_name is None:
            if self.retriever_mode == "hybrid":
                collection_name = os.getenv(
                    "RAG_HYBRID_COLLECTION",
                    "rag_knowledge_base_local_minilm_384_v1",
                )
            else:
                collection_name = "rag_knowledge_base"
        self.collection_name = collection_name
        self.rag_namespace = rag_namespace

        # 多命名空间管道缓存：{namespace: pipeline_dict}
        # 每个pipeline包含 store(向量库)、search(检索)、add_documents(索引) 等
        self._pipelines: Dict[str, Dict[str, Any]] = {}

        # 确保知识库本地目录存在
        os.makedirs(knowledge_base_path, exist_ok=True)

        # 初始化LLM和默认命名空间的RAG管道
        self._init_components()

    # ================================================================
    # 初始化
    # ================================================================

    def _init_components(self):
        """初始化RAG核心组件

        初始化流程：
        1. 创建默认命名空间的RAG管道（包含Qdrant向量库连接）
        2. 初始化LLM实例用于问答生成
        3. 设置初始化状态标记

        失败时会设置 self.initialized = False 和 self.init_error
        调用方在 run() 开始时会检查初始化状态
        """
        try:
            # 创建默认命名空间的RAG管道
            default_pipeline = create_rag_pipeline(
                qdrant_url=self.qdrant_url,
                qdrant_api_key=self.qdrant_api_key,
                collection_name=self.collection_name,
                rag_namespace=self.rag_namespace,
                retriever_mode=self.retriever_mode,
            )
            self._pipelines[self.rag_namespace] = default_pipeline

            # 初始化LLM，用于基于检索结果生成答案
            self.llm = HelloAgentsLLM()

            self.initialized = True
            print(f"✅ RAG工具初始化成功: namespace={self.rag_namespace}, collection={self.collection_name}")

        except Exception as e:
            self.initialized = False
            self.init_error = str(e)
            print(f"❌ RAG工具初始化失败: {e}")

    def _get_pipeline(self, namespace: Optional[str] = None) -> Dict[str, Any]:
        """获取指定命名空间的RAG管道（懒创建）

        如果目标命名空间的管道尚未初始化，则自动创建。
        每个命名空间拥有独立的Qdrant连接和向量库。

        Args:
            namespace: 命名空间名称，默认使用 self.rag_namespace

        Returns:
            RAG管道字典，包含 store、search、add_documents、get_stats 等
        """
        target_ns = namespace or self.rag_namespace

        # 命中缓存则直接返回
        if target_ns in self._pipelines:
            return self._pipelines[target_ns]

        # 缓存未命中：创建新命名空间的管道
        pipeline = create_rag_pipeline(
            qdrant_url=self.qdrant_url,
            qdrant_api_key=self.qdrant_api_key,
            collection_name=self.collection_name,
            rag_namespace=target_ns,
            retriever_mode=self.retriever_mode,
        )
        self._pipelines[target_ns] = pipeline
        return pipeline

    # ================================================================
    # 主入口 & 参数定义
    # ================================================================

    def run(self, parameters: Dict[str, Any]) -> str:
        """工具主入口 - 执行指定的action操作

        根据parameters中的action字段路由到对应的处理方法。

        Args:
            parameters: 工具参数字典，必须包含 action 字段
                支持的操作:
                - add_document: 添加文档文件
                - add_text:     添加纯文本
                - ask:          智能问答
                - search:       知识库搜索
                - stats:        获取统计信息
                - clear:        清空知识库

        Returns:
            执行结果字符串（Markdown格式）
        """
        # 参数校验
        if not self.validate_parameters(parameters):
            return "❌ 参数验证失败：缺少必需的参数"

        # 初始化状态检查
        if not self.initialized:
            return f"❌ RAG工具未正确初始化，请检查配置: {getattr(self, 'init_error', '未知错误')}"

        action = parameters.get("action")

        try:
            if action == "add_document":
                return self._add_document(
                    file_path=parameters.get("file_path"),
                    document_id=parameters.get("document_id"),
                    namespace=parameters.get("namespace", "default"),
                    chunk_size=parameters.get("chunk_size", 800),
                    chunk_overlap=parameters.get("chunk_overlap", 100)
                )
            elif action == "add_text":
                return self._add_text(
                    text=parameters.get("text"),
                    document_id=parameters.get("document_id"),
                    namespace=parameters.get("namespace", "default"),
                    chunk_size=parameters.get("chunk_size", 800),
                    chunk_overlap=parameters.get("chunk_overlap", 100)
                )
            elif action == "ask":
                question = parameters.get("question") or parameters.get("query")
                return self._ask(
                    question=question,
                    limit=parameters.get("limit", 5),
                    enable_advanced_search=parameters.get("enable_advanced_search", True),
                    include_citations=parameters.get("include_citations", True),
                    max_chars=parameters.get("max_chars", 1200),
                    namespace=parameters.get("namespace", "default")
                )
            elif action == "search":
                return self._search(
                    query=parameters.get("query") or parameters.get("question"),
                    limit=parameters.get("limit", 5),
                    min_score=parameters.get("min_score"),
                    enable_advanced_search=parameters.get("enable_advanced_search", True),
                    max_chars=parameters.get("max_chars", 1200),
                    include_citations=parameters.get("include_citations", True),
                    namespace=parameters.get("namespace", "default")
                )
            elif action == "stats":
                return self._get_stats(namespace=parameters.get("namespace", "default"))
            elif action == "clear":
                return self._clear_knowledge_base(
                    confirm=parameters.get("confirm", False),
                    namespace=parameters.get("namespace", "default")
                )
            else:
                return f"❌ 不支持的操作: {action}"
        except Exception as e:
            return f"❌ 执行操作 '{action}' 时发生错误: {str(e)}"

    def get_params(self) -> List[ToolParameter]:
        """获取工具参数定义 - Tool基类要求的接口

        Returns:
            工具参数列表，用于框架自动生成参数说明和校验
        """
        return [
            # ── 核心操作参数 ──
            ToolParameter(
                name="action",
                type="string",
                description="操作类型：add_document(添加文档), add_text(添加文本), ask(智能问答), search(搜索), stats(统计), clear(清空)",
                required=True
            ),
            # ── 内容输入参数 ──
            ToolParameter(
                name="file_path",
                type="string",
                description="文档文件路径（支持PDF、Word、Excel、PPT、图片、音频等多种格式）",
                required=False
            ),
            ToolParameter(
                name="text",
                type="string",
                description="要添加的文本内容",
                required=False
            ),
            ToolParameter(
                name="question",
                type="string",
                description="用户问题（用于智能问答）",
                required=False
            ),
            ToolParameter(
                name="query",
                type="string",
                description="搜索查询词（用于基础搜索）",
                required=False
            ),
            # ── 可选配置参数 ──
            ToolParameter(
                name="namespace",
                type="string",
                description="知识库命名空间（用于隔离不同项目，默认：default）",
                required=False,
                default="default"
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="返回结果数量（默认：5）",
                required=False,
                default=5
            ),
            ToolParameter(
                name="include_citations",
                type="boolean",
                description="是否包含引用来源（默认：true）",
                required=False,
                default=True
            )
        ]

    # ================================================================
    # 文档摄入操作
    # ================================================================

    @tool_action("rag_add_document", "添加文档到知识库（支持PDF、Word、Excel、PPT、图片、音频等多种格式）")
    def _add_document(
        self,
        file_path: str,
        document_id: str = None,
        namespace: str = "default",
        chunk_size: int = 300,
        chunk_overlap: int = 100
    ) -> str:
        """添加文档文件到知识库

        内部流程：
        1. 文件存在性校验
        2. 通过RAG管道完成：解析(parser) → 分块(chunker) → 向量化(indexer) → 存储(Qdrant)
        3. 返回处理统计信息

        Args:
            file_path:     文档文件路径（支持PDF/Word/Excel/PPT/图片/音频等格式）
            document_id:   自定义文档ID（可选，默认自动生成）
            namespace:     目标命名空间
            chunk_size:    分块大小（字符数）
            chunk_overlap: 分块重叠大小（字符数）

        Returns:
            格式化的处理结果
        """
        try:
            if not file_path or not os.path.exists(file_path):
                return f"❌ 文件不存在: {file_path}"

            pipeline = self._get_pipeline(namespace)
            t0 = time.time()

            # 调用管道完成 解析→分块→向量化→存储 全流程
            chunks_added = pipeline["add_documents"](
                file_paths=[file_path],
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )

            t1 = time.time()
            process_ms = int((t1 - t0) * 1000)

            if chunks_added == 0:
                return f"⚠️ 未能从文件解析内容: {os.path.basename(file_path)}"

            return (
                f"✅ 文档已添加到知识库: {os.path.basename(file_path)}\n"
                f"📊 分块数量: {chunks_added}\n"
                f"⏱️ 处理时间: {process_ms}ms\n"
                f"📝 命名空间: {pipeline.get('namespace', self.rag_namespace)}"
            )

        except Exception as e:
            return f"❌ 添加文档失败: {str(e)}"

    @tool_action("rag_add_text", "添加文本到知识库")
    def _add_text(
        self,
        text: str,
        document_id: str = None,
        namespace: str = "default",
        chunk_size: int = 800,
        chunk_overlap: int = 100
    ) -> str:
        """添加纯文本内容到知识库

        将文本写入临时Markdown文件，然后走与add_document相同的管道流程。
        处理完成后自动清理临时文件。

        Args:
            text:          要添加的文本内容
            document_id:   自定义文档ID（可选，默认基于文本hash生成）
            namespace:     目标命名空间
            chunk_size:    分块大小
            chunk_overlap: 分块重叠大小

        Returns:
            格式化的处理结果
        """
        try:
            if not text or not text.strip():
                return "❌ 文本内容不能为空"

            # 生成文档ID：基于文本hash确保相同文本产生相同ID
            document_id = document_id or f"text_{abs(hash(text)) % 100000}"
            tmp_path = os.path.join(self.knowledge_base_path, f"{document_id}.md")

            try:
                # 将文本写入临时Markdown文件
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    f.write(text)

                pipeline = self._get_pipeline(namespace)
                t0 = time.time()

                # 走标准管道流程
                chunks_added = pipeline["add_documents"](
                    file_paths=[tmp_path],
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap
                )

                t1 = time.time()
                process_ms = int((t1 - t0) * 1000)

                if chunks_added == 0:
                    return f"⚠️ 未能从文本生成有效分块"

                return (
                    f"✅ 文本已添加到知识库: {document_id}\n"
                    f"📊 分块数量: {chunks_added}\n"
                    f"⏱️ 处理时间: {process_ms}ms\n"
                    f"📝 命名空间: {pipeline.get('namespace', self.rag_namespace)}"
                )

            finally:
                # 无论成功与否都要清理临时文件
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

        except Exception as e:
            return f"❌ 添加文本失败: {str(e)}"

    # ================================================================
    # 知识检索操作
    # ================================================================

    @tool_action("rag_search", "搜索知识库中的相关内容")
    def _search(
        self,
        query: str,
        limit: int = 5,
        min_score: Optional[float] = None,
        enable_advanced_search: bool = True,
        max_chars: int = 1200,
        include_citations: bool = True,
        namespace: str = "default"
    ) -> str:
        """搜索知识库中的相关内容

        支持两种搜索模式：
        - 基础搜索：直接向量相似度检索
        - 高级搜索：启用MQE（多查询扩展）+ HyDE（假设文档嵌入）提升召回率

        Args:
            query:                  搜索查询词
            limit:                  返回结果数量上限
            min_score:              最低相似度阈值（低于此分数的结果被过滤）
            enable_advanced_search: 是否启用高级搜索（MQE + HyDE）
            max_chars:              结果内容最大字符数
            include_citations:      是否在结果中附带引用来源信息
            namespace:              目标命名空间

        Returns:
            格式化的搜索结果（Markdown格式）
        """
        try:
            if not query or not query.strip():
                return "❌ 搜索查询不能为空"

            pipeline = self._get_pipeline(namespace)

            # 根据配置选择搜索策略
            if self.retriever_mode == "hybrid":
                results = pipeline["search_hybrid"](
                    query=query,
                    top_k=limit,
                    score_threshold=min_score,
                )
            elif enable_advanced_search:
                results = pipeline["search_advanced"](
                    query=query,
                    top_k=limit,
                    enable_mqe=True,
                    enable_hyde=True,
                    score_threshold=min_score if min_score and min_score > 0 else None
                )
            else:
                results = pipeline["search"](
                    query=query,
                    top_k=limit,
                    score_threshold=min_score if min_score and min_score > 0 else 0.1
                )

            if not results:
                return f"🔍 未找到与 '{query}' 相关的内容"

            # 格式化搜索结果为可读文本
            search_result = self._format_search_results(
                results=results,
                include_citations=include_citations,
                max_chars=max_chars
            )

            return search_result

        except Exception as e:
            return f"❌ 搜索失败: {str(e)}"

    def _format_search_results(
        self,
        results: List[Dict],
        include_citations: bool = True,
        max_chars: int = 1200
    ) -> str:
        """格式化搜索结果为可读的Markdown文本

        Args:
            results:           搜索结果列表（来自Qdrant的hits）
            include_citations: 是否包含引用来源信息
            max_chars:         结果内容最大字符数

        Returns:
            格式化后的搜索结果字符串
        """
        search_result = ["搜索结果："]
        total_chars = 0

        for i, result in enumerate(results, 1):
            meta = result.get("metadata", {})
            score = result.get("score", 0.0)
            content = meta.get("content", "")[:200] + "..." if len(meta.get("content", "")) > 200 else meta.get("content", "")
            source = meta.get("source_path", "unknown")

            # 安全清理Unicode字符
            clean_content = self._safe_encode(content)
            clean_source = self._safe_encode(source)

            search_result.append(f"\n{i}.  来源: **{clean_source}** \n   相似度: {score:.3f}")
            search_result.append(f"   {clean_content}")

            # 附加上下文路径信息（如Markdown标题层级）
            if include_citations and meta.get("heading_path"):
                clean_heading = self._safe_encode(str(meta['heading_path']))
                search_result.append(f"   章节: {clean_heading}")

            total_chars += len(clean_content)
            if total_chars > max_chars:
                search_result.append("\n   ...(更多结果已截断)")
                break

        return "\n".join(search_result)

    # ================================================================
    # 智能问答操作
    # ================================================================

    @tool_action("rag_ask", "基于知识库进行智能问答")
    def _ask(
        self,
        question: str,
        limit: int = 5,
        enable_advanced_search: bool = True,
        include_citations: bool = True,
        max_chars: int = 1200,
        namespace: str = "default"
    ) -> str:
        """基于知识库的智能问答（RAG核心流程）

        完整流程：
        1. 向量检索：从知识库中查找与问题最相关的文档块
        2. 上下文构建：将检索结果整理为LLM可理解的上下文
        3. 提示词增强：构建包含系统指令和上下文的提示词
        4. LLM生成：调用大模型基于上下文生成答案
        5. 结果格式化：附加引用来源和性能信息

        Args:
            question:               用户问题
            limit:                  检索结果数量
            enable_advanced_search: 是否启用高级搜索
            include_citations:      是否包含引用来源
            max_chars:              上下文最大字符数（防止超token限制）
            namespace:              目标命名空间

        Returns:
            格式化的问答结果（Markdown格式）
        """
        try:
            if not question or not question.strip():
                return "❌ 请提供要询问的问题"

            user_question = question.strip()
            print(f"🔍 智能问答: {user_question}")

            # ── 步骤1: 检索相关内容 ──
            pipeline = self._get_pipeline(namespace)
            search_start = time.time()

            if self.retriever_mode == "hybrid":
                results = pipeline["search_hybrid"](
                    query=user_question,
                    top_k=limit,
                )
            elif enable_advanced_search:
                results = pipeline["search_advanced"](
                    query=user_question,
                    top_k=limit,
                    enable_mqe=True,
                    enable_hyde=True
                )
            else:
                results = pipeline["search"](
                    query=user_question,
                    top_k=limit
                )

            search_time = int((time.time() - search_start) * 1000)

            # 无结果时的友好提示
            if not results:
                return (
                    f"🤔 抱歉，我在知识库中没有找到与「{user_question}」相关的信息。\n\n"
                    f"💡 建议：\n"
                    f"• 尝试使用更简洁的关键词\n"
                    f"• 检查是否已添加相关文档\n"
                    f"• 使用 stats 操作查看知识库状态"
                )

            # ── 步骤2: 构建上下文 ──
            context_parts = []
            citations = []
            total_score = 0.0

            for i, result in enumerate(results):
                meta = result.get("metadata", {})
                content = meta.get("content", "").strip()
                source = meta.get("source_path", "unknown")
                score = result.get("score", 0.0)
                total_score += score

                if content:
                    # 清理内容格式后加入上下文
                    cleaned_content = self._clean_content_for_context(content)
                    context_parts.append(f"片段 {i + 1}：{cleaned_content}")

                    if include_citations:
                        citations.append({
                            "index": i + 1,
                            "source": os.path.basename(source),
                            "score": score
                        })

            # 合并上下文并智能截断
            context = "\n\n".join(context_parts)
            if len(context) > max_chars:
                context = self._smart_truncate_context(context, max_chars)

            # ── 步骤3: 构建增强提示词 ──
            system_prompt = self._build_system_prompt()
            user_prompt = self._build_user_prompt(user_question, context)

            enhanced_prompt = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # ── 步骤4: LLM生成答案 ──
            llm_start = time.time()
            answer = self.llm.invoke(enhanced_prompt)
            # 兼容 HelloAgentsLLM.invoke 返回 ChatCompletionMessage（为支持
            # tool calling 不再只返回 str）；旧版本/其他实现仍返回 str
            if hasattr(answer, "content"):
                answer = answer.content
            llm_time = int((time.time() - llm_start) * 1000)

            if not answer or not answer.strip():
                return "❌ LLM未能生成有效答案，请稍后重试"

            # ── 步骤5: 格式化最终回答 ──
            avg_score = total_score / len(results) if results else 0.0

            final_answer = self._format_final_answer(
                question=user_question,
                answer=answer.strip(),
                citations=citations if include_citations else None,
                search_time=search_time,
                llm_time=llm_time,
                avg_score=avg_score
            )

            return final_answer

        except Exception as e:
            return f"❌ 智能问答失败: {str(e)}\n💡 请检查知识库状态或稍后重试"

    # ================================================================
    # 上下文处理辅助方法
    # ================================================================

    def _clean_content_for_context(self, content: str) -> str:
        """清理检索内容用于构建LLM上下文

        处理步骤：
        1. 合并多余空白字符为单个空格
        2. 过长内容截断并添加省略标记

        Args:
            content: 原始检索片段内容

        Returns:
            清理后的紧凑文本
        """
        # 标准化空白字符：将连续空白符压缩为单个空格
        content = " ".join(content.split())
        # 防止单个片段过长占用过多token
        if len(content) > 300:
            content = content[:300] + "..."
        return content

    def _smart_truncate_context(self, context: str, max_chars: int) -> str:
        """智能截断上下文，尽量保持段落完整性

        优先在段落边界（双换行）处截断，避免在句子中间截断。

        Args:
            context:   完整的上下文字符串
            max_chars: 最大允许字符数

        Returns:
            截断后的上下文，末尾附带截断提示
        """
        if len(context) <= max_chars:
            return context

        # 在截断位置附近寻找最近的段落分隔符
        truncated = context[:max_chars]
        last_break = truncated.rfind("\n\n")

        if last_break > max_chars * 0.7:
            # 找到合理的段落断点
            return truncated[:last_break] + "\n\n[...更多内容被截断]"
        else:
            # 未找到合适断点，硬截断
            return truncated[:max_chars - 20] + "...[内容被截断]"

    # ================================================================
    # 提示词构建辅助方法
    # ================================================================

    def _build_system_prompt(self) -> str:
        """构建RAG问答的系统提示词

        定义LLM在RAG场景下的行为准则：
        - 严格基于提供的上下文回答，不编造信息
        - 如果上下文不足则坦诚说明
        - 使用清晰的结构化格式输出

        Returns:
            系统提示词字符串
        """
        return (
            "你是一个专业的知识助手，具备以下能力：\n"
            "1. 📖 精准理解：仔细理解用户问题的核心意图\n"
            "2. 🎯 可信回答：严格基于提供的上下文信息回答，不编造内容\n"
            "3. 🔍 信息整合：从多个片段中提取关键信息，形成完整答案\n"
            "4. 💡 清晰表达：用简洁明了的语言回答，适当使用结构化格式\n"
            "5. 🚫 诚实表达：如果上下文不足以回答问题，请坦诚说明\n\n"
            "回答格式要求：\n"
            "• 直接回答核心问题\n"
            "• 必要时使用要点或步骤\n"
            "• 引用关键原文时使用引号\n"
            "• 避免重复和冗余"
        )

    def _build_user_prompt(self, question: str, context: str) -> str:
        """构建包含问题和上下文的用户提示词

        Args:
            question: 用户的原始问题
            context:  从知识库检索到的相关上下文

        Returns:
            组装好的用户提示词字符串
        """
        return (
            f"请基于以下上下文信息回答问题：\n\n"
            f"【问题】{question}\n\n"
            f"【相关上下文】\n{context}\n\n"
            f"【要求】请提供准确、有帮助的回答。如果上下文信息不足，请说明需要什么额外信息。"
        )

    def _format_final_answer(
        self,
        question: str,
        answer: str,
        citations: Optional[List[Dict]] = None,
        search_time: int = 0,
        llm_time: int = 0,
        avg_score: float = 0.0
    ) -> str:
        """格式化最终的问答结果

        组装输出内容包括：
        - LLM生成的答案正文
        - 引用来源列表（带相似度分数和可视标记）
        - 性能指标（检索耗时、生成耗时、平均相似度）

        Args:
            question:    用户问题（保留用于日志）
            answer:      LLM生成的答案正文
            citations:   引用来源列表 [{index, source, score}, ...]
            search_time: 检索耗时（毫秒）
            llm_time:    LLM生成耗时（毫秒）
            avg_score:   检索结果平均相似度

        Returns:
            格式化的完整回答（Markdown格式）
        """
        result = [f"🤖 **智能问答结果**\n"]
        result.append(answer)

        # 添加引用来源
        if citations:
            result.append("\n\n📚 **参考来源**")
            for citation in citations:
                # 用颜色标记相似度等级
                if citation["score"] > 0.8:
                    score_emoji = "🟢"
                elif citation["score"] > 0.6:
                    score_emoji = "🟡"
                else:
                    score_emoji = "🔵"
                result.append(
                    f"{score_emoji} [{citation['index']}] {citation['source']} "
                    f"(相似度: {citation['score']:.3f})"
                )

        # 添加性能指标
        result.append(
            f"\n⚡ 检索: {search_time}ms | 生成: {llm_time}ms | 平均相似度: {avg_score:.3f}"
        )

        return "\n".join(result)###

    # ================================================================
    # 管理操作
    # ================================================================

    @tool_action("rag_stats", "获取知识库统计信息")
    def _get_stats(self, namespace: str = "default") -> str:
        """获取知识库的统计信息

        展示内容包括：
        - 命名空间和集合名称
        - 已索引的文档分块数量
        - 向量维度配置
        - 系统运行状态

        Args:
            namespace: 目标命名空间

        Returns:
            格式化的统计信息（Markdown格式）
        """
        try:
            pipeline = self._get_pipeline(namespace)
            stats = pipeline["get_stats"]()

            stats_info = [
                "📊 **RAG 知识库统计**",
                f"📝 命名空间: {pipeline.get('namespace', self.rag_namespace)}",
                f"📋 集合名称: {self.collection_name}",
                f"📂 存储根路径: {self.knowledge_base_path}"
            ]

            # 解析向量库统计信息
            if stats:
                store_type = stats.get("store_type", "unknown")
                total_vectors = (
                    stats.get("points_count") or
                    stats.get("vectors_count") or
                    stats.get("count") or 0
                )

                stats_info.extend([
                    f"📦 存储类型: {store_type}",
                    f"📊 文档分块数: {int(total_vectors)}",
                ])

                # 向量配置信息
                if "config" in stats:
                    config = stats["config"]
                    if isinstance(config, dict):
                        vector_size = config.get("vector_size", "unknown")
                        distance = config.get("distance", "unknown")
                        stats_info.extend([
                            f"🔢 向量维度: {vector_size}",
                            f"📎 距离度量: {distance}"
                        ])

            # 系统运行状态
            stats_info.extend([
                "",
                "🟢 **系统状态**",
                f"✅ RAG 管道: {'正常' if self.initialized else '异常'}",
                f"✅ LLM 连接: {'正常' if hasattr(self, 'llm') else '异常'}"
            ])

            stats_info.insert(
                2,
                f"检索模式: {pipeline.get('retriever_mode', self.retriever_mode)}",
            )
            return "\n".join(stats_info)

        except Exception as e:
            return f"❌ 获取统计信息失败: {str(e)}"

    @tool_action("rag_clear", "清空知识库（危险操作，请谨慎使用）")
    def _clear_knowledge_base(self, confirm: bool = False, namespace: str = "default") -> str:
        """清空指定命名空间的知识库

        危险操作：会删除向量库中的所有数据。
        需要显式设置 confirm=True 才会执行。

        清空后自动重新初始化该命名空间的管道。

        Args:
            confirm:   必须设置为True才执行，防止误操作
            namespace: 目标命名空间

        Returns:
            操作结果
        """
        try:
            if not confirm:
                return (
                    "⚠️ 危险操作：清空知识库将删除所有数据！\n"
                    "请使用 confirm=true 参数确认执行。"
                )

            pipeline = self._get_pipeline(namespace)
            store = pipeline.get("store")
            namespace_id = pipeline.get("namespace", self.rag_namespace)
            success = store.clear_collection() if store else False

            if success:
                # 清空后重新创建管道，恢复可用状态
                self._pipelines[namespace_id] = create_rag_pipeline(
                    qdrant_url=self.qdrant_url,
                    qdrant_api_key=self.qdrant_api_key,
                    collection_name=self.collection_name,
                    rag_namespace=namespace_id,
                    retriever_mode=self.retriever_mode,
                )
                return f"✅ 知识库已成功清空（命名空间：{namespace_id}）"
            else:
                return "❌ 清空知识库失败"

        except Exception as e:
            return f"❌ 清空知识库失败: {str(e)}"

    def clear_all_namespaces(self) -> str:
        """清空当前工具管理的所有命名空间数据

        遍历所有已初始化的命名空间管道，逐一清空向量库，
        然后重新初始化默认命名空间。

        Returns:
            操作结果
        """
        try:
            for ns, pipeline in self._pipelines.items():
                store = pipeline.get("store")
                if store:
                    store.clear_collection()
            self._pipelines.clear()
            # 重新初始化默认命名空间
            self._init_components()
            return "✅ 所有命名空间数据已清空并重新初始化"
        except Exception as e:
            return f"❌ 清空所有命名空间失败: {str(e)}"

    # ================================================================
    # 通用工具方法
    # ================================================================

    @staticmethod
    def _safe_encode(text: str) -> str:
        """安全的Unicode编码处理

        确保文本在Windows控制台（如GBK编码环境）下也能正常显示。

        Args:
            text: 可能包含特殊Unicode字符的文本

        Returns:
            安全处理后的文本
        """
        try:
            return str(text).encode('utf-8', errors='ignore').decode('utf-8')
        except Exception:
            return str(text)

    # ================================================================
    # Agent集成接口
    # ================================================================

    def get_relevant_context(
        self,
        query: str,
        limit: int = 3,
        max_chars: int = 1200,
        namespace: Optional[str] = None
    ) -> str:
        """为Agent查询获取相关上下文（精简接口）

        供Agent在需要时直接调用，返回合并后的上下文字符串，
        可用于注入到Agent的提示词中。

        Args:
            query:     搜索查询
            limit:     返回片段数量
            max_chars: 合并上下文最大长度
            namespace: 目标命名空间

        Returns:
            合并后的上下文字符串，无结果时返回空字符串
        """
        try:
            if not query:
                return ""

            pipeline = self._get_pipeline(namespace)
            results = pipeline["search"](
                query=query,
                top_k=limit
            )

            if not results:
                return ""

            # 提取并合并各片段内容
            context_parts = []
            for result in results:
                content = result.get("metadata", {}).get("content", "")
                if content:
                    context_parts.append(content)

            merged_context = "\n\n".join(context_parts)

            # 长度限制
            if len(merged_context) > max_chars:
                merged_context = merged_context[:max_chars] + "..."

            return merged_context

        except Exception as e:
            return f"获取上下文失败: {str(e)}"

    # ================================================================
    # 便捷调用接口
    # ================================================================

    def add_document(self, file_path: str, namespace: str = "default") -> str:
        """便捷方法：添加单个文档文件"""
        return self.run({
            "action": "add_document",
            "file_path": file_path,
            "namespace": namespace
        })

    def add_text(self, text: str, namespace: str = "default", document_id: str = None) -> str:
        """便捷方法：添加文本内容到知识库"""
        return self.run({
            "action": "add_text",
            "text": text,
            "namespace": namespace,
            "document_id": document_id
        })

    def ask(self, question: str, namespace: str = "default", **kwargs) -> str:
        """便捷方法：发起智能问答"""
        params = {
            "action": "ask",
            "question": question,
            "namespace": namespace
        }
        params.update(kwargs)
        return self.run(params)

    def search(self, query: str, namespace: str = "default", **kwargs) -> str:
        """便捷方法：搜索知识库"""
        params = {
            "action": "search",
            "query": query,
            "namespace": namespace
        }
        params.update(kwargs)
        return self.run(params)

    # ================================================================
    # 批量操作接口
    # ================================================================

    def batch_add_texts(
        self,
        texts: List[str],
        document_ids: Optional[List[str]] = None,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        namespace: Optional[str] = None
    ) -> str:
        """批量添加多个文本到知识库

        每个文本写入独立的临时文件后走标准管道流程，
        处理完成后清理所有临时文件。

        Args:
            texts:         文本内容列表
            document_ids:  对应的文档ID列表（可选，默认自动生成）
            chunk_size:    分块大小
            chunk_overlap: 分块重叠大小
            namespace:     目标命名空间

        Returns:
            批量处理统计结果
        """
        try:
            if not texts:
                return "❌ 文本列表不能为空"

            if document_ids and len(document_ids) != len(texts):
                return "❌ 文本数量和文档ID数量不匹配"

            pipeline = self._get_pipeline(namespace)
            t0 = time.time()

            total_chunks = 0
            successful_files = []

            for i, text in enumerate(texts):
                if not text or not text.strip():
                    continue

                doc_id = document_ids[i] if document_ids else f"batch_text_{i}"
                tmp_path = os.path.join(self.knowledge_base_path, f"{doc_id}.md")

                try:
                    with open(tmp_path, 'w', encoding='utf-8') as f:
                        f.write(text)

                    chunks_added = pipeline["add_documents"](
                        file_paths=[tmp_path],
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap
                    )

                    total_chunks += chunks_added
                    successful_files.append(doc_id)

                finally:
                    # 清理临时文件
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass

            t1 = time.time()
            process_ms = int((t1 - t0) * 1000)

            return (
                f"✅ 批量添加完成\n"
                f"📊 成功文件: {len(successful_files)}/{len(texts)}\n"
                f"📊 总分块数: {total_chunks}\n"
                f"⏱️ 处理时间: {process_ms}ms"
            )

        except Exception as e:
            return f"❌ 批量添加失败: {str(e)}"

    def add_documents_batch(self, file_paths: List[str], namespace: str = "default") -> str:
        """批量添加多个文档文件

        Args:
            file_paths: 文档文件路径列表
            namespace:  目标命名空间

        Returns:
            批量处理统计结果
        """
        if not file_paths:
            return "❌ 文件路径列表不能为空"

        results = []
        successful = 0
        failed = 0
        total_chunks = 0
        start_time = time.time()

        for i, file_path in enumerate(file_paths, 1):
            print(f"📄 处理文档 {i}/{len(file_paths)}: {os.path.basename(file_path)}")

            try:
                result = self.add_document(file_path, namespace)
                if "✅" in result:
                    successful += 1
                    # 从结果中提取分块数量
                    if "分块数量:" in result:
                        chunks = int(result.split("分块数量: ")[1].split("\n")[0])
                        total_chunks += chunks
                else:
                    failed += 1
                    results.append(f"❌ {os.path.basename(file_path)}: 处理失败")
            except Exception as e:
                failed += 1
                results.append(f"❌ {os.path.basename(file_path)}: {str(e)}")

        process_time = int((time.time() - start_time) * 1000)

        summary = [
            "📊 **批量处理完成**",
            f"✅ 成功: {successful}/{len(file_paths)} 个文档",
            f"📊 总分块数: {total_chunks}",
            f"⏱️ 总耗时: {process_time}ms",
            f"📝 命名空间: {namespace}"
        ]

        if failed > 0:
            summary.append(f"❌ 失败: {failed} 个文档")
            summary.append("\n**失败详情:**")
            summary.extend(results)

        return "\n".join(summary)

    def add_texts_batch(
        self,
        texts: List[str],
        namespace: str = "default",
        document_ids: Optional[List[str]] = None
    ) -> str:
        """批量添加多个文本（简化的批量接口）

        Args:
            texts:         文本内容列表
            namespace:     目标命名空间
            document_ids:  对应的文档ID列表（可选）

        Returns:
            批量处理统计结果
        """
        if not texts:
            return "❌ 文本列表不能为空"

        if document_ids and len(document_ids) != len(texts):
            return "❌ 文本数量和文档ID数量不匹配"

        results = []
        successful = 0
        failed = 0
        total_chunks = 0
        start_time = time.time()

        for i, text in enumerate(texts):
            doc_id = document_ids[i] if document_ids else f"batch_text_{i + 1}"
            print(f"📝 处理文本 {i + 1}/{len(texts)}: {doc_id}")

            try:
                result = self.add_text(text, namespace, doc_id)
                if "✅" in result:
                    successful += 1
                    if "分块数量:" in result:
                        chunks = int(result.split("分块数量: ")[1].split("\n")[0])
                        total_chunks += chunks
                else:
                    failed += 1
                    results.append(f"❌ {doc_id}: 处理失败")
            except Exception as e:
                failed += 1
                results.append(f"❌ {doc_id}: {str(e)}")

        process_time = int((time.time() - start_time) * 1000)

        summary = [
            "📊 **批量文本处理完成**",
            f"✅ 成功: {successful}/{len(texts)} 个文本",
            f"📊 总分块数: {total_chunks}",
            f"⏱️ 总耗时: {process_time}ms",
            f"📝 命名空间: {namespace}"
        ]

        if failed > 0:
            summary.append(f"❌ 失败: {failed} 个文本")
            summary.append("\n**失败详情:**")
            summary.extend(results)

        return "\n".join(summary)
