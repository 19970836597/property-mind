# -*- coding: utf-8 -*-
"""入口：装配能力表并启动主 agent。

主 agent 本体（人格 / 分派原则）定义在
``agents/react_agent/react_agent.py``（MAIN_SYSTEM_PROMPT）；
本文件只做装配：建子 agent、注册技能与工具、起 CLI。

架构::

    主 agent（意图识别 + 委托）
      ├─ collection_agent  催费子agent（账龄分析 + 催缴函）
      ├─ legal_agent       法务子agent（法务知识库检索 + 律师函）
      ├─ skill_ppt_skill   月度经营汇报 PPT 技能
      ├─ data_file / data_analysis / calculator  通用数据问答
      └─ skill_optimalstop37_skill  37%法则决策技能

催费 / 法务子 agent 各有独立 system_prompt（技能包文档）+ 专属工具集，
内部跑自己的 ReAct 循环（agents/sub_agent_tool.py）。

运行方式：
    python main.py                          # 交互式 CLI
    python main.py --task "项目经理月度汇报"  # 一次性任务
"""

import argparse
import os
import re
import sys

from dotenv import load_dotenv

from core.llm import LLM
from core.capability import CapabilityRouter
from core.paths import MEMORY_FILE, PROJECT_ROOT, PROPERTY_SKILLS_DIR, PPT_SKILL_DIR as PPT_SKILL_PATH
from tools.tools import Tool, ToolParameter
from tools.common.calculatortool import CalculatorTool
from tools.common.data_file_tool import DataFileTool
from tools.common.data_analysis_tool import DataAnalysisTool
from tools.common.arrears_analysis_tool import ArrearsAnalysisTool
from tools.common.docx_letter_tool import DocxLetterTool
from tools.builtin.legal_rag_tool import LegalRAGTool
from skills.loader import Skill, normalize_skill_id
from agents.sub_agent_tool import build_sub_agent
from agents.react_agent.react_agent import ReActAgent
from core.history.message import Message, MessageRole

# 核心模块只读 os.environ，.env 必须在这里显式加载
load_dotenv(PROJECT_ROOT / ".env")


#: 本部署的 PPT 主数据源（data/common 下的表 2；表1/表4 由 deck_spec 自动 join）
PPT_MAIN_DATA = "应收信息"

#: PPT 技能目录（项目内唯一的 PPT 生成技能）。
PPT_SKILL_DIR = str(PPT_SKILL_PATH)

#: 封面项目名。不传时 loader 会退化成用文件名当项目名（封面印出
#: 「2、应收信息 经营分析」这种东西），所以必须在装配层给定。
PPT_PROJECT_NAME = "物业管理项目"

#: 从 request 里认领项目名：「XX小区/XX花园/XX project」等常见写法
_PROJECT_HINT = re.compile(
    r"([一-龥A-Za-z0-9]{2,12}(?:小区|花园|家园|公寓|广场|大厦|社区|项目|园|苑))"
)

#: request 里显式提到其他数据文件时才允许换数据源（xlsx/xls/csv 文件名）
_FILE_HINT = re.compile(r"[^\s，。,]*\.(?:xlsx|xls|csv)", re.IGNORECASE)


_PERIOD_HINT = re.compile(
    r"(\d{4}\s*年\s*\d{1,2}\s*[-—~至]\s*\d{1,2}\s*月)"
)


class PropertyPptSkill(Tool):
    """物业 PPT 技能网关：确定性固定主数据源。

    为什么需要它：小模型（qwen3.5:2b）每次调用 skill_ppt_skill 都会换一种
    错法——臆造 file_name（匹配到表5宽表）、传空参数、或把 data 参数填成
    全 0 模板（技能于是用假数据而不自动取数）。与其靠提示词约束，不如在
    装配层强制：

    * 丢弃模型传的 data / file_name（本项目数据一律来自 data/common 文件）；
    * request 提到其他数据文件 → 按用户指定的来；
    * 否则固定 file_name=「应收信息」（find_files 确定性匹配表2）；
    * 补上 project（否则封面会印出用文件名凑的「2、应收信息 经营分析」）。
    """

    def __init__(self, skill: Skill):
        self.skill = skill
        super().__init__(
            name=skill.name,
            description=skill.description,
        )
        self.tags = list(getattr(skill, "tags", []) or [])

    def get_params(self):
        return [
            ToolParameter(
                name="request",
                type="string",
                description="要做物业经营分析汇报的完整需求（项目名、期间、汇报时长等）。",
                required=True,
            )
        ]

    def run(self, params):
        request = str(params.get("request") or "").strip()
        if not request:
            return "未提供汇报需求（request 为空）。"

        clean = {"request": request, "data": {}}
        # 用户在请求里显式点名了数据文件 → 尊重用户；否则固定主数据源
        # 项目名：request 里点了小区/项目名就用它，否则用部署默认名，
        # 总之不能留空让 loader 拿文件名顶上
        hint = _PROJECT_HINT.search(request)
        project = hint.group(1) if hint else PPT_PROJECT_NAME
        # 去掉「生成/制作/用/一份」等请求前缀，避免把动词带进封面项目名。
        project = re.sub(
            r"^(?:帮我|请|生成|制作|做|用|为|关于|一份|一个)+",
            "",
            project,
        ).strip()
        clean["data"]["project"] = project or PPT_PROJECT_NAME
        period_match = _PERIOD_HINT.search(request)
        if period_match:
            clean["data"]["period"] = (
                period_match.group(1)
                .replace("—", "-")
                .replace("~", "-")
                .replace("至", "-")
            )

        return self.skill.run(clean)


def build_sub_agents(llm) -> list:
    """装配催费 / 法务两个子 agent（各自技能包文档作 system_prompt）。

    递归安全由结构保证：子 router 里只放业务工具，不含任何 SubAgentTool。
    """
    collection = build_sub_agent(
        llm,
        skill_dir=str(PROPERTY_SKILLS_DIR / "collection_assistant"),
        tools=[
            ArrearsAnalysisTool(),   # 账龄分档 + 催缴优先级
            DocxLetterTool(),        # 温馨提醒函 / 催缴函 / 律师函预警 (.docx)
        ],
        name="collection_agent",
        agent_name="催费助手",
        description=(
            "物业欠费催缴助手：欠费账龄分析、催缴优先级排序、生成催缴通知与催缴函。"
            "关键词：欠费、欠款、催缴、催收、账龄、催费、物业费收缴、发函、催缴函。"
        ),
        tags=["催费", "催缴", "欠费", "欠款", "账龄", "催收"],
    )

    legal = build_sub_agent(
        llm,
        skill_dir=str(PROPERTY_SKILLS_DIR / "legal_assistant"),
        tools=[
            LegalRAGTool(),          # legal 命名空间法务知识库（Qdrant 不可用时降级）
            DocxLetterTool(),        # 律师函 / 催缴函（正式函件）
        ],
        name="legal_agent",
        agent_name="法务助手",
        description=(
            "物业法务助手：民法典物业费条款解读、诉讼时效、催收合规红线、律师函起草、"
            "物业费纠纷案例。关键词：法律、法规、律师、诉讼、起诉、时效、合规、判例。"
        ),
        tags=["法务", "法规", "律师", "诉讼", "合规"],
    )

    return [collection, legal]


def _load_ppt_skill_v2(llm) -> Skill:
    """Load the script-driven ppt-master property deck skill."""
    skill = Skill(
        PPT_SKILL_DIR,
        normalize_skill_id("ppt_skill"),
        llm=llm,
        auto_resolve_data=False,
    )
    if skill.effective_mode() != "script" or not skill.script:
        raise RuntimeError(
            f"PPT 技能加载失败: {PPT_SKILL_DIR} "
            f"模式={skill.effective_mode()} script={skill.script}"
        )
    return skill


def build_router(llm) -> CapabilityRouter:
    """主 agent 能力表：只放调度入口 + 通用数据工具，业务细节下沉到子 agent。

    注意：催费/法务技能包不再注册为技能——它们的文档被子 agent 加载为
    system_prompt（见 build_sub_agent），主 agent 只看到两个子 agent 入口。
    """
    router = CapabilityRouter()

    # 两个子 agent（预装配好的 ReActAgent 包装成 Tool）
    for sub_agent in build_sub_agents(llm):
        router.add_tool(sub_agent)

    # 通用数据问答（不涉及催费/法务专业流程时主 agent 直接处理）
    router.add_tool(DataFileTool())
    router.add_tool(DataAnalysisTool())
    router.add_tool(CalculatorTool())

    # 技能：显式构造注册（不再全目录扫描，避免催费/法务技能重复暴露）
    # PPT 技能套网关固定主数据源（见 PropertyPptSkill），37% 法则原样注册
    router.add_tool(PropertyPptSkill(_load_ppt_skill_v2(llm)))
    router.add_skill(Skill(
        str(PROPERTY_SKILLS_DIR / "optimalstop37"),
        normalize_skill_id("Optimalstop37-skill"),  # → skill_optimalstop37_skill
        llm=llm,
    ))

    _register_optional_collection_rl(router)
    return router


def _register_optional_collection_rl(router: CapabilityRouter) -> None:
    """Register the RL collection tool only when explicitly enabled.

    The tool is read-only and never sends SMS, calls, or letters.
    """

    if os.getenv("COLLECTION_RL_ENABLED", "false").lower() == "true":
        from rl.tool import CollectionPolicyTool

        router.add_tool(CollectionPolicyTool(os.getenv("COLLECTION_RL_POLICY")))


def _run_once(agent: ReActAgent, text: str) -> None:
    """执行单个任务并打印回答 + 意图/能力路由信息。

    回答本体在流式模式下已经边生成边打到终端了，这里不再重复打印，
    只收尾换行 + 打印路由信息（回答内容仍会正常返回给记忆/历史）。
    """
    answer = agent.run(text)

    if not agent.stream_output:
        print("\n最终回答:\n", answer)
    else:
        print()

    if agent.last_intent:
        print(f"识别到的意图: {agent.last_intent}")
        print(f"选中的能力:   {agent.last_selected or '（全部能力）'}")


def main() -> None:
    # Windows GBK 控制台兜底：中文输出（pandas 表格等）在 GBK 终端会崩
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description="物业智能助手（催费 / 法务 / 汇报）")
    parser.add_argument("--task", help="一次性任务，执行完退出（如 --task \"项目经理月度汇报\"）")
    parser.add_argument("--max-steps", type=int, default=8,
                        help="主 agent ReAct 最大步数（默认 8）")
    args = parser.parse_args()

    llm = LLM()
    router = build_router(llm)

    print()
    print(router.describe())

    agent = ReActAgent(
        name="助手",
        llm=llm,
        router=router,
        # system_prompt 不传：主 agent 的人格定义在 react_agent.py
        # （MAIN_SYSTEM_PROMPT），这里只负责装配能力表
        enable_intent_routing=True,   # 主 agent 只做意图识别 + 调度
        enable_planning=False,
        max_steps=args.max_steps,
        auto_remember=True,          # 轮末自动沉淀记忆（子 agent 不开）
        stream_output=True,          # 最终回复流式打到终端
    )

    # ---- 一次性任务：兼容旧行为 python main.py --task "项目经理月度汇报" ----
    if args.task:
        _run_once(agent, args.task)
        return

    # ---- 交互式 CLI：催费 / 法务 / 汇报由意图识别自动分派 ----
    print("\n物业智能助手已就绪（主 agent 调度 + 子 agent 执行）。可输入：")
    print("  • 催费：\"分析欠款账龄\" / \"给欠费最多的业主生成催缴函\"")
    print("  • 法务：\"物业费催收有哪些法律红线？\" / \"催缴函怎么写才合规？\"")
    print("  • 汇报：\"项目经理月度汇报\"")
    print("  • 命令：/context 看上下文预算  /compact 压缩历史  /clear 清空会话"
          "  /memory 看记忆状态  /help 帮助")
    print("  • 记忆：\"#内容\" 直接写一条长期记忆（落盘 MEMORY.md，重启仍在）")
    print("  • 输入 exit / quit / 退出 结束会话")

    while True:
        try:
            user_input = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input or user_input.lower() in ("exit", "quit", "退出"):
            break

        if user_input.startswith("/"):
            _run_command(agent, user_input)
            continue
        if user_input.startswith("#"):
            _quick_remember(agent, user_input[1:].strip())
            continue

        _run_once(agent, user_input)


def _run_command(agent: ReActAgent, line: str) -> None:
    """斜杠命令：上下文/记忆的检视与手动干预入口。"""
    cmd = line.split()[0].lower()
    ctx = agent.context

    if cmd == "/context":
        print("\n" + ctx.describe())
    elif cmd == "/compact":
        if not agent._history:
            print("（没有可压缩的历史）")
            return
        summary = ctx.compactor.summarize(
            [m.to_dict() for m in agent._history]
        )
        if not summary:
            print("（压缩需要 LLM 可用，本次未执行）")
            return
        agent.clear_history()
        agent.add_message(Message(
            role=MessageRole.USER,
            content=(
                "（此前对话已被压缩为以下摘要，请基于它继续对话）\n" + summary
            ),
        ))
        print("已把全部历史压缩为一条摘要。")
    elif cmd == "/clear":
        agent.clear_history()
        print("已清空会话历史。")
    elif cmd == "/memory":
        print("\n" + _memory_status(agent))
    elif cmd == "/help":
        print("命令：/context /compact /clear /memory /help；"
              "「#内容」写长期记忆；exit 退出。")
    else:
        print(f"未知命令 {cmd}，可用：/context /compact /clear /memory /help")


def _memory_status(agent: ReActAgent) -> str:
    """记忆层检视：常驻记忆文件 + 召回库状态。"""
    lines = ["记忆状态："]
    for provider in agent.context.providers:
        if getattr(provider, "name", "") == "常驻记忆":
            for path in provider.paths:
                exists = os.path.isfile(path)
                size = os.path.getsize(path) if exists else 0
                lines.append(f"  常驻文件 {path}  {'✓ ' + str(size) + ' 字' if exists else '（不存在）'}")
        if getattr(provider, "name", "") == "召回记忆":
            state = "已禁用（记忆库不可用）" if provider._disabled else "正常"
            lines.append(f"  召回库：{state}（类型 {provider.memory_types}）")
    return "\n".join(lines)


def _quick_remember(agent: ReActAgent, content: str) -> None:
    """「#内容」快捷写入：直接落盘项目级 MEMORY.md（常驻，重启仍在），
    并热更新常驻记忆，下一轮立即生效——对标 Claude Code 的 # 快捷记忆。"""
    if not content:
        print("用法：#要记住的内容")
        return
    path = str(MEMORY_FILE)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"- {content}\n")
    for provider in agent.context.providers:
        if getattr(provider, "name", "") == "常驻记忆":
            if path not in provider.paths:
                provider.paths.append(path)
            provider.reload()
    print(f"已写入长期记忆：{content}")


if __name__ == "__main__":
    main()
