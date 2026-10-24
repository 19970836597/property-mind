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
      └─ collection_rl_policy  可选催缴策略建议（默认关闭）

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
from agents.conversation_mode import (
    ConversationMode,
    ConversationSession,
    choose_collaboration_group,
    choose_conversation_mode,
)
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


#: 群聊分流用：邮箱地址里的 @ 不算点名，先剔除
_EMAIL_RE = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9-.]+")


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
    # PPT 技能套网关固定主数据源（见 PropertyPptSkill）
    router.add_tool(PropertyPptSkill(_load_ppt_skill_v2(llm)))

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


def _run_groupchat_once(groupchat, text: str) -> None:
    """执行一轮群聊并打印发言人与路由信息。

    群聊的最终回答由 manager 发布（非流式），这里整段打印；
    调试信息沿用单聊口径：谁回了、为什么是他。
    """
    answer = groupchat.run(text)
    name = getattr(groupchat, "group_name", "") or ""
    prefix = f"「{name}」" if name else ""
    print(f"\n群聊回答{prefix}:\n", answer)
    if groupchat.last_speakers:
        parts = ", ".join(
            f"{s['display']}（{s['reason']}）" for s in groupchat.last_speakers
        )
        print(f"发言人: {parts}")
    else:
        print("发言人: manager（无人举手，兜底直答）")


def _run_group_command(registry, line: str) -> None:
    """`/group` 二级分发：群管理走这里，纯消息才进群聊。

    用法：
        /group create <群名> @成员…  建群并自动切换（manager恒在，不用指定）
        /group list                   列出全部群
        /group switch <群名>          切换活跃群
        /group show                   看活跃群详情
        /group add @成员              给活跃群加人
        /group remove @成员           从活跃群移除人（至少留1人）
        /group disband <群名>         解散群（默认群不可解散）
        /group <消息>                 给活跃群发消息（兼容老行为）
    """
    rest = line.lstrip()[len("/group"):].strip()
    if not rest:
        print(_groupchat_status(registry.active))
        return
    parts = rest.split(None, 1)
    verb, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")

    try:
        if verb == "create":
            sub = arg.split(None, 1)
            if not sub:
                print("用法：/group create <群名> @成员…（例如 /group create 催收群 @催费 @法务）")
                return
            chat = registry.create(sub[0], raw_mentions=sub[1] if len(sub) > 1 else "")
            print(f"已建群「{sub[0]}」并切换为活跃群，成员："
                  + "、".join(m.display for m in chat.members) + "（+ manager）。")
        elif verb == "list":
            print(registry.describe())
        elif verb == "switch":
            if not arg:
                print("用法：/group switch <群名>")
                return
            registry.switch(arg)
            print(f"已切换到「{arg}」。")
        elif verb == "show":
            print(_groupchat_status(registry.active))
        elif verb == "add":
            chat = registry.add_members(arg)
            print("已加入，当前成员："
                  + "、".join(m.display for m in chat.members) + "。")
        elif verb == "remove":
            chat = registry.remove_member(arg)
            print("已移除，当前成员："
                  + "、".join(m.display for m in chat.members) + "。")
        elif verb == "disband":
            if not arg:
                print("用法：/group disband <群名>")
                return
            print(registry.disband(arg.split()[0]))
        else:
            # 不是子命令 → 整段当消息发给活跃群（兼容老行为）
            _run_groupchat_once(registry.active, rest)
    except ValueError as exc:
        print(f"群操作失败：{exc}")
    except Exception as exc:
        print(f"群操作异常：{exc}")


def _is_groupchat_input(text: str, groupchat=None) -> bool:
    """是否走群聊：显式 @ 点名（点中已知 alias 或疑似名字）。

    邮箱地址里的 @ 不算（先剔除，免得「发到 zhang@example.com」
    被误判进群还多一句「没有找到 @example」的提示）。
    /group 前缀由调用方在斜杠命令之前预拦截，这里不处理。
    """
    scrubbed = _EMAIL_RE.sub("", text or "")
    if "@" not in scrubbed:
        return False
    if groupchat is None:
        return True
    from agents.groupchat import extract_mentions
    mentioned, _, unknown = extract_mentions(scrubbed, groupchat.alias_map)
    return bool(mentioned or unknown)


def _groupchat_status(groupchat) -> str:
    """群聊检视：群名 + 共享历史轮数 + 习惯条目（/memory 追加显示）。"""
    try:
        name = getattr(groupchat, "group_name", "") or ""
        n_turns = len(groupchat.history.turns)
        n_habits = len(getattr(groupchat, "habits", []))
        members = ", ".join(
            f"{m.display}({m.name})" for m in groupchat.members
        ) or "（无）"
        lines = [
            f"  当前群「{name}」manager 恒在",
            f"  群聊成员：{members}",
            f"  共享历史：{n_turns} 条发言",
            f"  表达偏好：{n_habits} 条",
        ]
        if n_habits:
            lines.extend(f"    - {h}" for h in groupchat.habits)
        return "\n".join(lines)
    except Exception as exc:
        return f"  群聊状态读取失败: {exc}"


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

    # ---- 对话模式：交互式启动时先选，--task 固定走快速模式 ----
    if args.task:
        session = ConversationSession(ConversationMode.QUICK)
    else:
        selected_mode = choose_conversation_mode()
        if selected_mode is None:
            return
        session = ConversationSession(selected_mode)
        print(
            f"\n对话模式：{session.mode.display_name}"
            "（本会话不可切换）"
        )

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

    # ---- 协作模式：registry 持有多群 + 默认群；快速模式不装配群聊 ----
    registry = None
    groupchat = None
    if session.group_chat_enabled:
        # router 里只有 SubAgentTool 才有 .agent（Skill/Tool 都没有）。
        from agents.groupchat import GroupRegistry

        sub_tools = [
            c for c in router
            if getattr(c, "agent", None) is not None
        ]
        try:
            registry = GroupRegistry(agent, sub_tools)
        except Exception as exc:
            print(f"\n协作模式初始化失败：{exc}")
            print("本会话不会自动降级为快速模式，请修复后重新启动。")
            return

        groupchat = choose_collaboration_group(registry)
        if groupchat is None:
            print("已退出。")
            return
        group_members = ", ".join(
            f"@{m.aliases[-1]}" for m in groupchat.members
        ) or "（无）"

    # ---- 交互式 CLI：模式决定普通消息的固定入口 ----
    if session.group_chat_enabled:
        print(f"  • 群聊：普通消息进入当前群；可 @成员（如 {group_members}），"
              "manager 整理后统一发布")
        print("  • 建群：/group create <群名> @成员…（自动切换）；"
              "/group list/switch/show/add/remove/disband 管理群")
    else:
        print("  • 快速模式：普通消息由主 Agent 调度子 Agent；@ 按普通文本处理")
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

        # /group 群管理仅属于协作模式；快速模式不允许中途改变会话形态。
        if user_input.lstrip().startswith("/group"):
            if not session.group_chat_enabled:
                print(
                    "当前为快速模式，不能使用 /group；本会话模式不可切换，"
                    "请退出后重新启动并选择协作模式。"
                )
                continue
            _run_group_command(registry, user_input)
            groupchat = registry.active  # 切换/解散后刷新指针
            continue

        if user_input.startswith("/"):
            _run_command(agent, user_input, groupchat)
            continue
        if user_input.startswith("#"):
            _quick_remember(agent, user_input[1:].strip())
            continue

        def quick_handler(text: str) -> None:
            _run_once(agent, text)

        def group_handler(text: str) -> None:
            if groupchat is None:
                print("协作群聊不可用，请退出后重新启动。")
                return
            _run_groupchat_once(groupchat, text)

        session.dispatch(
            user_input,
            quick_handler=quick_handler,
            group_handler=group_handler,
        )


def _run_command(agent: ReActAgent, line: str, groupchat=None) -> None:
    """斜杠命令：上下文/记忆的检视与手动干预入口。

    群聊集成：/clear 同时清空群聊历史（否则群里还留着旧话）；
    /memory 追加群聊状态（发言轮数 + 习惯条目数）。
    """
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
        if groupchat is not None:
            groupchat.history.turns.clear()
            print("已清空会话历史（含群聊）。")
        else:
            print("已清空会话历史。")
    elif cmd == "/memory":
        print("\n" + _memory_status(agent))
        if groupchat is not None:
            print(_groupchat_status(groupchat))
    elif cmd == "/help":
        print("命令：/context /compact /clear /memory /help；"
              "「#内容」写长期记忆；exit 退出。")
        if groupchat is not None:
            print("当前为协作模式；普通消息进入活跃群，可用 @ 点名成员。\n"
                  "群管理：/group create <群名> @成员…（自动切换）"
                  "/list/switch/show/add/remove/disband。")
        else:
            print("当前为快速模式；普通消息由主 Agent 处理，@ 不触发群聊。")
        print("对话模式在本会话内不可切换；如需另一模式，请退出后重新启动。")
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
