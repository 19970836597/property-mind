# -*- coding: utf-8 -*-
"""子 agent 封装：把一个装配好的 ReActAgent 包成主 agent 能力表里的单个 Tool。

架构::

    主 agent（只做意图识别/调度）
      └─ 能力表: [collection_agent] [legal_agent] [data_file] ... [skill_ppt_skill]
              │
              ├─ collection_agent → 内部 ReActAgent（自有 system_prompt + 专属工具）
              └─ legal_agent      → 内部 ReActAgent（自有 system_prompt + 专属工具）

设计要点：
    * 递归安全靠结构保证：子 agent 的 router 里**只放工具**，不放任何
      SubAgentTool → 子 agent 不可能再往下委托，不会无限递归；
    * 子 agent 实例跨请求保留会话历史（同轮追问有上下文），
      ``reset=true`` 时先 ``clear_history()``；
    * 主 agent 的工具结果**没有截断机制**，这里自己做输出长度保护，
      超长截断，防止子 agent 的大段输出撑爆主 agent 上下文。
"""

from typing import Any, Dict, List, Optional

from core.capability import CapabilityRouter
from skills.skill_register import load_skill_document
from tools.tools import Tool, ToolParameter

__all__ = ["SubAgentTool", "build_sub_agent"]

#: 输出保护：主 agent 的 tool 结果无截断，超长内容在此截断
MAX_OUTPUT_CHARS = 8000


class SubAgentTool(Tool):
    """把一个预装配的 ReActAgent 包装成主 agent 可调度的单个能力。

    主 agent 看到的是普通 tool（name/description/params），内部跑完整的
    ReAct 循环。description 会进意图识别目录（截断到 110 字），
    触发关键词要写在前面。
    """

    def __init__(
        self,
        agent: Any,
        name: str,
        description: str,
        tags: Optional[List[str]] = None,
        max_output_chars: int = MAX_OUTPUT_CHARS,
    ):
        super().__init__(name, description)
        self.agent = agent
        self.tags = list(tags or [])
        self.max_output_chars = max_output_chars

    def get_params(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="request",
                type="string",
                description=(
                    "要委托给该子助手的完整任务描述，用用户的原始措辞，"
                    "包含所有必要背景（欠费数据、问题细节等）。"
                ),
                required=True,
            ),
            ToolParameter(
                name="reset",
                type="boolean",
                description=(
                    "是否先清空该子助手的会话历史再执行（换了话题/用户时传 true；"
                    "同一话题追问保持默认 false 以保留上下文）。"
                ),
                required=False,
                default=False,
            ),
        ]

    def run(self, params: Dict[str, Any]) -> str:
        request = params.get("request") or params.get("input") or ""
        if not str(request).strip():
            return f"未提供任务内容（request 参数为空），无法调用 {self.name}。"

        if params.get("reset"):
            try:
                self.agent.clear_history()
            except Exception:
                pass

        try:
            answer = self.agent.run(str(request))
        except Exception as exc:
            # 子 agent 崩溃不能拖垮主 agent 的 ReAct 循环
            return f"子助手 '{self.name}' 执行失败: {exc}"

        answer = str(answer)
        if len(answer) > self.max_output_chars:
            answer = (
                answer[: self.max_output_chars]
                + f"\n\n[输出超长已截断，原文 {len(answer)} 字符。"
                  "如需完整结果请缩小任务范围后重试。]"
            )
        return answer


def build_sub_agent(
    llm: Any,
    skill_dir: str,
    tools: List[Any],
    name: str,
    description: str,
    tags: Optional[List[str]] = None,
    agent_name: Optional[str] = None,
    max_steps: int = 8,
    verbose: bool = False,
) -> SubAgentTool:
    """装配一个子 agent 并包装成 SubAgentTool。

    Args:
        llm: 与主 agent 共享的 LLM 实例（客户端无状态，可共享）。
        skill_dir: 技能包目录——SKILL.md + references/ 由
            ``load_skill_document`` 合并为子 agent 的 system_prompt。
        tools: 子 agent 专属工具列表（**只放工具**，不放 SubAgentTool，
            递归安全由这一条结构性保证）。
        name: 注册进主能力表的名字，形如 ``collection_agent``。
        description: 给意图识别看的能力描述，触发关键词写在前 110 字。
        tags: 意图路由的关键词标签。
        max_steps: 子 agent 内部 ReAct 最大步数。
    """
    # 局部导入避免循环依赖（agents 包 ↔ core/skills/tool）
    from agents.react_agent.react_agent import ReActAgent

    frontmatter, document = load_skill_document(skill_dir)
    # 技能文档（含参考文件）整体作为子 agent 的领域 system_prompt。
    # 必须非空：ReActAgent 不传 system_prompt 时会退化成**主 agent 的
    # 总调度人格**（见 react_agent.MAIN_SYSTEM_PROMPT），子 agent 顶着
    # 调度人格会去找它 router 里根本没有的 *_agent，症状很难查。
    # 宁可装配期直接报错。
    system_prompt = (document or "").strip()
    if not system_prompt:
        raise ValueError(
            f"子 agent '{name}' 的技能文档为空（{skill_dir}）："
            "无法确定其领域人格，请检查 SKILL.md 是否存在且有正文。"
        )

    router = CapabilityRouter(f"{name}_router")
    for tool in tools:
        router.add_tool(tool)

    agent = ReActAgent(
        name=agent_name or name,
        llm=llm,
        router=router,
        system_prompt=system_prompt,
        max_steps=max_steps,
        # 子 agent 内部不做意图路由：工具少且固定，直接全量暴露
        enable_intent_routing=False,
        verbose=verbose,
    )

    return SubAgentTool(agent, name=name, description=description, tags=tags)
