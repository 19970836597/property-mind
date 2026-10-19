# -*- coding: utf-8 -*-
"""物业法务知识库检索工具（legal 命名空间 RAG）。

继承 RAGTool，做两件小事：

* 强制 ``legal`` 命名空间——构造时默认管道就是 legal，run 时兜底
  ``params.setdefault("namespace", "legal")``，小模型忘传也不会查错库；
* 描述限定法务场景关键词（民法典物业费条款、诉讼时效、催收合规、
  催缴函/律师函模板、判例），避免意图路由与通用 RAG 混淆。

Qdrant 不可用时按 RAGTool 的既有行为优雅降级（initialized=False，
返回错误提示文本），法务技能仍可基于内置 legal_knowledge.md 回答。
"""

from typing import Any, Dict, List

from .rag_tool import RAGTool
from ..tools import ToolParameter

__all__ = ["LegalRAGTool"]

LEGAL_NAMESPACE = "legal"


class LegalRAGTool(RAGTool):
    """物业法务知识库检索（legal 命名空间隔离）。"""

    def __init__(self, **kwargs):
        # 强制 legal 命名空间；其余参数（qdrant_url 等）照旧走 RAGTool
        kwargs.setdefault("rag_namespace", LEGAL_NAMESPACE)
        super().__init__(**kwargs)

        self.name = "legal_rag"
        self.description = (
            "物业法务知识库检索（legal 命名空间）：民法典物业费相关条款、诉讼时效、"
            "催收合规红线、催缴函/律师函模板、物业费纠纷判例。"
            "关键词：法律、法规、律师、诉讼、起诉、时效、合规、函件模板、判例。"
        )
        self.tags = ["法务", "法规", "律师", "诉讼", "合规"]

    def run(self, parameters: Dict[str, Any]) -> str:
        # 模型忘传 namespace 时兜底为 legal，绝不落到 default 库
        parameters.setdefault("namespace", LEGAL_NAMESPACE)
        if parameters.get("namespace") in (None, "", "default"):
            parameters["namespace"] = LEGAL_NAMESPACE
        # 高级检索（MQE/HyDE）依赖 LLM 生成扩展查询，本地小模型易产出
        # 非法输入触发 embedding 400；默认走基础向量检索，模型显式要求时才开
        parameters.setdefault("enable_advanced_search", False)
        return super().run(parameters)

    def get_params(self) -> List[ToolParameter]:
        params = super().get_params()
        # namespace 参数说明改为 legal 且不再必填可传错
        for p in params:
            if p.name == "namespace":
                p.description = "知识库命名空间（固定为 legal，无需传参）"
                p.default = LEGAL_NAMESPACE
        return params
