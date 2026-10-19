import json
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

from core.agent import Agent
from core.config import Config
from core.llm import LLM
from core.history.message import Message
from core.paths import MEMORY_FILE
from tools.register import ToolRegister

from core.capability import (
    CapabilityRouter,
    INTENT_RECOGNITION_PROMPT,
    capability_kind,
    parse_intent_response,
)
from core.context import (
    ContextManager,
    EnvironmentProvider,
    MemoryFileProvider,
    MemoryRecallProvider,
)

from .planner import Planner, StepNode, STEP_EXECUTION_PROMPT
from .executor import TaskScheduler

#: 本 Agent 的默认人格 —— **主 agent 的身份就定义在这里**。
#:
#: 为什么写在这个文件里：ReActAgent 既当主 agent 也当子 agent，
#: 但「主 agent 是谁」此前散落在 main.py 的装配代码里，而本模块原来
#: 那段泛化的"你是一个具备推理和行动能力的AI助手"是**死代码**
#: （没有任何地方 import，基类会退化成 core/agent.py 的一句话提示词）。
#: 现在把总调度人格落到这里：不传 system_prompt 构造出来的就是主 agent。
#: 子 agent 另有身份（技能包文档），由 build_sub_agent 显式传入覆盖。
MAIN_SYSTEM_PROMPT = """\
你是物业项目智能助手的总调度。你的职责是理解用户意图，把任务委托给最合适的能力，
并在结果返回后向用户转述结论。你不需要、也不应该亲自执行催费/法务的业务细节。

系统里的能力分两类：
- skill（技能）：一份带有流程说明的专业能力，输入自然语言需求，内部会按流程完成多阶段任务。
- tool（工具）：一个确定性的功能函数，参数明确，返回确定结果。
其中 *_agent 是**子助手**（内部自带专业流程与工具），把需求原文转交给它即可。

能力分派原则：
1. 欠费、欠款、催缴、催收、账龄、优先级、催缴函/通知 → 委托 collection_agent（催费助手）。
   request 参数写清用户原始需求（含涉及的楼盘/期间等背景）。
2. 法律、法规、民法典条款、诉讼时效、起诉、合规红线、律师函 → 委托 legal_agent（法务助手）。
3. 月度经营汇报、经营分析 PPT、"项目经理月度汇报" → 调用 skill_ppt_skill。
   request 里必须写明：「主数据源用 data/common 的 2、应收信息.xlsx」。
   用户明确指定了其他数据文件时才用别的；**不要自行猜测文件名**。
4. 37%法则、最优停止、候选评分决策 → 调用 skill_optimalstop37_skill。
5. 一般的数据查询/表格分析/计算 → data_file / data_analysis / calculator。

执行规则：
- 先进行思考（Reasoning），再决定是否需要调用能力（Action）；用系统提供的 tool_calls 功能调用。
- 优先选择最贴切的那一个能力，不要为了凑步骤而调用不相关的能力。
- 子助手（*_agent）返回的结果已经是专业结论，忠实、完整地转述给用户，不要重复执行其内部步骤。
- 用户追问同一话题时，再次调用对应子助手即可（子助手保留会话上下文）。
- 已获得足够信息时直接给出最终答案，不再调用能力。
- 用户问题与物业无关时，直接礼貌回答，不强行调用能力。
"""

#: 兼容旧名字（本模块曾以 SYSTEM_PROMPT 导出一段泛化提示词）
SYSTEM_PROMPT = MAIN_SYSTEM_PROMPT


def _default_memory_paths() -> List[str]:
    """常驻记忆文件的查找顺序：**用户级 → 项目级**。

    顺序即优先级的反面：后读到的离当前项目更近、更具体，所以排在后面、
    在 system 段里离用户输入更近。这与 Claude Code 的 CLAUDE.md 分级
    （用户级 ~/.claude → 项目级仓库内）是同一思路。

    环境变量 ``AGENT_MEMORY_FILE`` 可追加一个额外路径，便于测试与部署覆盖。
    找不到的路径由 MemoryFileProvider 自己跳过，这里不做存在性检查。
    """
    paths = [
        os.path.join(os.path.expanduser("~"), ".agent", "MEMORY.md"),
        str(MEMORY_FILE),
    ]
    extra = os.getenv("AGENT_MEMORY_FILE")
    if extra:
        paths.append(extra)
    return paths

PLANNING_SYSTEM_PROMPT = """\
你需要先判断用户意图，再把它拆解成可执行的步骤。

当前可用能力（skill = 技能，tool = 工具）：
{capabilities}

要求：
1. 每个步骤写清楚要达成什么目标，以及打算用哪个能力（若不需要能力就写"无"）。
2. 只能使用上面列出的能力名字，不要臆造。
3. 步骤数量适中（通常 2~6 步）。
"""


class ReActAgent(Agent):
    """ReAct Agent，具备推理和行动能力的AI助手。

    决策链（本架构的核心）::

        用户输入
          │
          ├─(可选) 1. 意图识别  _recognize_intent()
          │           用轻量提示词让 LLM 判断「要干什么」，并从
          │           skills + tools 的**统一能力清单**里挑出候选。
          │           这一步只看名字/描述/标签，所以很省 token。
          │
          ├─(可选) 2. 任务规划  _run_with_planning()
          │           把任务拆成带依赖的步骤，交给 TaskScheduler 调度。
          │
          ├─ 3. 能力匹配 + 执行  _run_react_loop()
          │           LLM 在「候选能力」的 schema 里选一个调用；
          │           CapabilityRouter 统一执行（技能/工具走同一入口）。
          │           ★ 技能和工具在这一步没有区别，Agent 不必关心种类。
          │
          └─ 4. 观察结果 → 继续推理，或给出最终答案

    技能与工具是同级能力（都实现 capability.Capability 协议），
    本 Agent 只依赖 `CapabilityRouter` 这一个抽象，不感知具体类型。
    """

    def __init__(
        self,
        name: str,
        llm: LLM,
        tool_register: Optional[ToolRegister] = None,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_steps: int = 5,
        enable_planning: bool = False,
        planning_prompt: Optional[str] = None,
        max_workers: int = 3,
        max_retries: int = 1,
        router: Optional[CapabilityRouter] = None,
        enable_intent_routing: bool = True,
        force_capabilities: Optional[Sequence[str]] = None,
        intent_max_attempts: int = 2,
        verbose: bool = False,
        context: Optional[ContextManager] = None,
        auto_remember: bool = False,
        stream_output: bool = False,
    ):
        # 不传 system_prompt 就是主 agent（总调度人格）；
        # 子 agent 由装配层传技能包文档覆盖
        super().__init__(name, llm, system_prompt or MAIN_SYSTEM_PROMPT, config)
        self.max_steps = max_steps
        self.enable_planning = enable_planning
        self.planning_prompt = planning_prompt
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.enable_intent_routing = enable_intent_routing
        self.force_capabilities = list(force_capabilities or [])
        # 小模型意图识别会返回空 content，允许多试几次（0/1 表示不重试）
        self.intent_max_attempts = max(1, int(intent_max_attempts))
        self.verbose = bool(verbose)

        # 统一走能力路由中心：router 优先，其次把 tool_register 包一层
        if router is not None:
            self.router = router
        else:
            self.router = CapabilityRouter(f"{name}_router")
            if tool_register is not None:
                for tool in getattr(tool_register, "_tools", {}).values():
                    self.router.add_tool(tool)

        # 保留旧属性名，兼容既有代码
        self.tool_register = tool_register if tool_register is not None else self.router

        # 本轮的路由结果（调试/测试可见）
        self.last_intent: str = ""
        self.last_selected: List[str] = []

        # 流式输出：开=最终回复边生成边打到终端（主 agent 用）；
        # 子 agent 不开——它们的回答是给主 agent 转述的观察，
        # 打出来会跟主 agent 的流式输出交错串台。
        self.stream_output = bool(stream_output)

        # 上下文管理：不传也要有，否则这个模块就成了"建了没人用"的摆设
        # （本仓已有先例：MemoryTool 零调用、DialogTree 零调用、
        #   Config.max_history_length 零读取）。
        #
        # 默认三件套 = Claude Code 的三层记忆机制：
        #   常驻记忆（MEMORY.md 启动即注入）+ 召回记忆（按轮检索）
        #   + 环境（当前日期——模型没有时钟，汇报场景会说错月份）。
        #   召回依赖 MemoryManager，provider 内部懒构造 + 失败自禁用，
        #   这里不必再兜一层。
        self.auto_remember = bool(auto_remember)
        self.context = context or ContextManager(
            system_prompt=self.system_prompt,
            providers=[
                MemoryFileProvider(_default_memory_paths()),
                EnvironmentProvider(),
                MemoryRecallProvider(),
            ],
            llm=llm,
            keep_recent_turns=max(2, int(
                getattr(self.config, "max_history_length", 100) // 25
            )),
            verbose=self.verbose,
        )
        self.last_raw_intent: str = ""

    # ================================================================
    # 能力表访问（兼容 router / tool_register 两种来源）
    # ================================================================

    def _capability_catalog(self) -> str:
        if hasattr(self.router, "catalog"):
            return self.router.catalog()
        return "\n".join(
            f"- {t.name}: {getattr(t, 'description', '')}"
            for t in getattr(self.router, "_tools", {}).values()
        )

    def _all_capability_names(self) -> List[str]:
        if hasattr(self.router, "names"):
            return list(self.router.names())
        return list(getattr(self.router, "_tools", {}).keys())

    def _capability_schemas(self, names: Optional[Sequence[str]] = None) -> List[Dict]:
        if hasattr(self.router, "schemas"):
            return self.router.schemas(names)
        # 退化：只有 ToolRegister
        schemas = self.tool_register.get_tools_schema()
        if not names:
            return schemas
        allowed = set(names)
        return [s for s in schemas if s.get("function", {}).get("name") in allowed]

    # ================================================================
    # 主入口
    # ================================================================

    def run(self, input_text: str, **kwargs) -> str:
        # 1) 意图识别 + 能力匹配
        selected = self._route_capabilities(input_text, **kwargs)

        if self.enable_planning:
            answer = self._run_with_planning(input_text, capabilities=selected, **kwargs)
        else:
            answer = self._run_react_loop(input_text, capabilities=selected, **kwargs)

        self._record_to_history(input_text, answer)

        # 轮末记忆沉淀：把「值得跨会话记住」的事实自动写进记忆库。
        # 只在主 agent 开（auto_remember=True，main.py 装配时传入），
        # 子 agent 不开——每个子步骤都提取一遍既费钱又是噪音。
        if self.auto_remember:
            self._maybe_remember(input_text, answer)

        return answer

    # ================================================================
    # 轮末记忆沉淀（对标 Claude Code 的自动记忆写入）
    # ================================================================

    _REMEMBER_EXTRACT_PROMPT = """\
从下面这轮对话里提取**值得跨会话长期记住**的事实，最多 3 条。

只提取明确的、将来对继续服务该用户有用的事实，例如：
用户身份/角色、明确说出的偏好（"以后都用简短格式"）、
项目背景（楼盘名、期间、口径）、对此前做法的修正要求。

不要提取：一次性的任务内容、寒暄、助手给出的结论本身。
没有值得记的就一个字都不输出。

对话：
用户：{input}
助手：{answer}

输出：每行一条事实，不加编号，不加解释。"""

    def _recall_provider(self) -> Optional[MemoryRecallProvider]:
        """从上下文来源里找召回记忆 provider（没有就返回 None）。"""
        for provider in getattr(self.context, "providers", []):
            if isinstance(provider, MemoryRecallProvider):
                return provider
        return None

    def _maybe_remember(self, input_text: str, answer: str) -> None:
        """提取并写入记忆。**任何失败都静默**——记忆是锦上添花，
        不能让一轮本来成功的对话在收尾时崩掉或变慢太多。

        写两条路（缺一不可）：
        * 工作记忆（MemoryRecallProvider.remember）——本进程内按轮召回；
        * 项目 MEMORY.md 落盘——工作记忆是纯内存的，**重启即失**。
          落盘 + reload 让沉淀的事实重启仍在、下轮即注入
          （这正是 Claude Code 的做法：自动记忆写文件）。
        """
        provider = self._recall_provider()
        if provider is None or provider._disabled:
            return
        try:
            response = self.llm.invoke(messages=[{
                "role": "user",
                "content": self._REMEMBER_EXTRACT_PROMPT.format(
                    input=input_text, answer=answer
                ),
            }])
            text = str(getattr(response, "content", "") or "").strip()
            if not text:
                # 思考型小模型可能把正文放在 reasoning_content
                text = str(
                    getattr(response, "reasoning_content", "") or ""
                ).strip()
            facts = [
                line.strip("-• \t")
                for line in text.splitlines()
                if line.strip() and len(line.strip()) >= 4
            ]
            if not facts:
                return
            for fact in facts[:3]:
                provider.remember(fact, importance=0.6, memory_type="working")
            self._append_memory_file(facts[:3])
        except Exception:
            # 记忆写入是尽力而为，不向上抛
            pass

    def _append_memory_file(self, facts: List[str]) -> None:
        """把沉淀的事实追加到项目 MEMORY.md，并热更新常驻记忆。"""
        if not facts:
            return
        try:
            path = str(MEMORY_FILE)
            new = [f.strip() for f in facts if f.strip()]
            if not new:
                return
            # 简单去重：已在文件里的一行不重复追加
            existing = ""
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as fh:
                    existing = fh.read()
            lines = [f"- {f}" for f in new if f"- {f}" not in existing]
            if not lines:
                return
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            for p in getattr(self.context, "providers", []):
                if getattr(p, "name", "") == "常驻记忆":
                    if path not in p.paths:
                        p.paths.append(path)
                    p.reload()
        except Exception:
            pass

    # ================================================================
    # 1. 意图识别 → 能力匹配
    # ================================================================

    def _route_capabilities(self, input_text: str, **kwargs) -> Optional[List[str]]:
        """让 LLM 判断意图，并从 skills + tools 里挑出候选能力。

        Returns:
            选中的能力名列表；返回 None 表示「不做过滤，暴露全部能力」。
        """
        self.last_intent = ""
        self.last_selected = []

        if not self.enable_intent_routing:
            return None

        if self.force_capabilities:
            selected = [n for n in self.force_capabilities if n in self.router]
            self.last_selected = selected
            return selected or None

        catalog = self._capability_catalog().strip()
        if not catalog:
            return None

        try:
            prompt = INTENT_RECOGNITION_PROMPT.format(
                capabilities=catalog, input=input_text
            )
        except (KeyError, IndexError, ValueError):
            # 用户自定义提示词缺少占位符时，退化成「清单一句话 + 原请求」
            prompt = (
                f"可用能力：\n{catalog}\n\n用户请求：{input_text}\n"
                '只输出 JSON：{"intent": "...", "capabilities": ["..."]}'
            )

        # 小模型（如 2b）会有概率返回空 content，因此重试 + 确定性降级。
        # 之前把这一步当硬闸门，导致「时好时坏」：失败时 last_selected 为空，
        # 行为与其他路径不一致，很难排查。
        attempts = max(1, int(self.intent_max_attempts))
        raw = ""
        parsed: Dict[str, Any] = {"intent": "", "capabilities": []}
        got_structured = False

        for attempt in range(attempts):
            try:
                response = self.llm.invoke(
                    messages=[{"role": "user", "content": prompt}],
                    **self._pass_through(kwargs),
                )
                raw = getattr(response, "content", None) or ""
                if not raw.strip():
                    # 思考型模型可能把内容放在 reasoning_content
                    raw = getattr(response, "reasoning_content", None) or ""
            except Exception as exc:
                if self.verbose:
                    print(f"  意图识别第 {attempt + 1} 次调用失败: {exc}")
                raw = ""

            parsed = parse_intent_response(raw)
            self.last_raw_intent = raw

            # 区分两种情况：
            #   * 结构化但空列表 {"capabilities": []} → **合法答案**，表示无需能力，
            #     不能重试（否则会吃掉后续响应，行为不可预期）
            #   * 完全没返回 / 解析不出 JSON → 模型抖动，才值得重试
            got_structured = "{" in raw and "}" in raw
            if got_structured:
                break

        self.last_intent = parsed.get("intent", "")

        known = set(self._all_capability_names())
        selected = [n for n in parsed.get("capabilities", []) if n in known]

        if got_structured:
            # 模型给了明确的答案就尊重它：
            # 有名字就用（过滤掉臆造的），空列表就表示「这个请求不需要能力」。
            # 不能因为空列表就去猜——空列表是合法信号，不是失败。
            self.last_selected = selected
            if not selected and self.verbose:
                print("  意图识别结果：无需调用任何能力")
            return selected or None

        # 走到这里说明模型没给出可解析的答案（抖动），才启用降级。
        matched = [
            getattr(c, "name", "")
            for c in self.router.match(query=input_text)
        ]
        matched = [n for n in matched if n]
        if matched and len(matched) < len(known):
            self.last_selected = matched
            self.last_intent = self.last_intent or "（关键词匹配降级）"
            return matched

        # 连关键词都匹配不上 → 不猜，暴露全部能力（fail-open）
        self.last_selected = []
        self.last_intent = self.last_intent or "（意图识别无结果，使用全部能力）"
        return None

    @staticmethod
    def _pass_through(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """只把 LLM 生成参数透传下去，不把内部参数（capabilities 等）传给 LLM。"""
        allowed = (
            "temperature", "max_tokens", "top_p",
            "frequency_penalty", "presence_penalty", "stop", "seed",
        )
        return {k: v for k, v in kwargs.items() if k in allowed and v is not None}

    # ================================================================
    # 2. 规划 + 调度模式
    # ================================================================

    def _run_with_planning(
        self,
        input_text: str,
        capabilities: Optional[Sequence[str]] = None,
        **kwargs
    ) -> str:
        template = self.planning_prompt or PLANNING_SYSTEM_PROMPT
        try:
            planner_system = template.format(capabilities=self._capability_catalog())
        except (KeyError, IndexError, ValueError):
            planner_system = template
        planner = Planner(self.llm, prompt=planner_system)
        steps = planner.plan(input_text, **self._pass_through(kwargs))
        if not steps:
            print("⚠️ 规划失败，回退到普通ReAct模式")
            return self._run_react_loop(input_text, capabilities=capabilities, **kwargs)

        print(f"\n📋 任务规划 ({len(steps)} 步):")
        for s in steps:
            deps = f" ← 依赖步骤{s.depends_on}" if s.depends_on else ""
            print(f"  {s.index}. {s.description}{deps}")

        scheduler = TaskScheduler(steps, max_workers=self.max_workers,
                                  max_retries=self.max_retries)
        layers = scheduler.get_execution_order()
        parallel_count = sum(1 for layer in layers if len(layer) > 1)
        print(f"  调度: {len(layers)} 层, {parallel_count} 层可并行\n")

        results_map: Dict[int, str] = {}

        def execute_step(step: StepNode) -> str:
            history = "\n".join(
                f"步骤{idx}: {_step_desc(steps, idx)}\n结果: {results_map.get(idx, '无')}\n"
                for idx in sorted(results_map.keys())
            )
            step_prompt = STEP_EXECUTION_PROMPT.format(
                task=input_text,
                plan="\n".join(f"{s.index}. {s.description}" for s in steps),
                history=history if history else "无",
                current_step=f"{step.index}. {step.description}",
            )
            return self._run_react_loop(
                step_prompt, capabilities=capabilities, **kwargs
            )

        def on_start(step: StepNode):
            total = len(steps)
            done = sum(1 for s in steps if s.status in ("done", "failed"))
            print(f"▶ [{done + 1}/{total}] 执行步骤 {step.index}: {step.description}")

        def on_done(step: StepNode, result: str):
            results_map[step.index] = result
            icon = "✅" if step.status == "done" else "❌"
            print(f"  {icon} 步骤 {step.index} 完成\n")

        scheduler.execute(execute_step, on_step_start=on_start, on_step_done=on_done)

        failed = [s for s in steps if s.status == "failed"]
        if failed:
            print(f"⚠️ {len(failed)} 个步骤执行失败: {[s.index for s in failed]}")

        print("🔄 综合所有步骤结果，生成最终回答...")
        history = "\n".join(
            f"步骤{s.index}: {s.description}\n结果: {s.result}\n"
            for s in steps
        )
        synthesis_prompt = (
            f"原始任务: {input_text}\n\n"
            f"执行计划及结果:\n{history}\n"
            f"请根据以上所有步骤的执行结果，给出原始任务的完整、准确的最终回答。"
        )
        return self._run_react_loop(
            synthesis_prompt, capabilities=capabilities, **kwargs
        )

    # ================================================================
    # 3. 核心 ReAct 循环（能力匹配 + 执行）
    # ================================================================

    def _run_react_loop(
        self,
        input_text: str,
        capabilities: Optional[Sequence[str]] = None,
        **kwargs
    ) -> str:
        steps = 0
        last_response_content = ""
        messages = self._build_messages(input_text)

        while steps < self.max_steps:
            steps += 1
            tool_schemas = self._capability_schemas(capabilities)

            if self.stream_output:
                content, reasoning, tool_calls = self._stream_llm(
                    messages, tool_schemas, **self._pass_through(kwargs)
                )
            else:
                response = self.llm.invoke(
                    messages=messages,
                    tools=tool_schemas if tool_schemas else None,
                    **self._pass_through(kwargs),
                )
                content = response.content or ""
                reasoning = getattr(response, "reasoning_content", None) or ""
                tool_calls = response.tool_calls

            last_response_content = content or ""

            if not tool_calls:
                # 思考型小模型可能把正文放在 reasoning_content（content 为空）
                if not last_response_content:
                    last_response_content = reasoning
                return last_response_content

            assistant_msg = {
                "role": "assistant",
                "content": content,
                "tool_calls": []
            }
            messages.append(assistant_msg)

            # 先解析全部调用、登记 id，再统一执行（顺序与 id 配对一致）
            parsed = []
            for tool_call in tool_calls:
                tool_name, tool_args = self._parse_tool_call(tool_call)
                assistant_msg["tool_calls"].append({
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(tool_args, ensure_ascii=False)
                    }
                })
                parsed.append((tool_call, tool_name, tool_args))

            results = self._execute_parallel(parsed)

            for (tool_call, _name, _args), result in zip(parsed, results):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

            # 工具结果只增不减，一张大表 dump 就可能撑爆窗口：
            # 每步过一次预算，超了先裁旧的工具结果（见 compactor 的顺序）
            messages = self.context.compact_in_place(messages)

        return last_response_content or "未能生成最终回答。"

    # ================================================================
    # LLM 调用与能力执行
    # ================================================================

    def _stream_llm(
        self,
        messages: List[Dict[str, Any]],
        tool_schemas: Optional[List[Dict]] = None,
        **kwargs: Any,
    ) -> tuple[str, str, List[Any]]:
        """流式调用 LLM：正文边生成边打终端，工具调用增量拼装。

        Returns:
            (content, reasoning_content, tool_calls)

        tool_calls 的流式拼装：provider 分片下发 ``function.arguments``，
        必须按 delta 的 index 累积拼接，拼完前 arguments 不完整、
        json.loads 必炸——这是流式工具调用最容易踩的坑。
        """
        parts: List[str] = []
        reasoning_parts: List[str] = []
        calls: Dict[int, Dict[str, Any]] = {}

        for chunk in self.llm.stream(
            messages=messages,
            tools=tool_schemas if tool_schemas else None,
            **kwargs,
        ):
            if chunk["type"] == "reasoning":
                reasoning_parts.append(chunk["content"])
                if self.verbose:
                    print(chunk["content"], end="", flush=True)
            elif chunk["type"] == "text":
                parts.append(chunk["content"])
                print(chunk["content"], end="", flush=True)
            elif chunk["type"] == "tool_calls":
                for delta in chunk["content"] or []:
                    idx = getattr(delta, "index", None) or 0
                    acc = calls.setdefault(idx, {
                        "id": "", "name": "", "arguments": "",
                    })
                    fn = getattr(delta, "function", None)
                    if getattr(delta, "id", None):
                        acc["id"] = delta.id
                    if fn is not None:
                        if getattr(fn, "name", None):
                            acc["name"] = fn.name
                        if getattr(fn, "arguments", None):
                            acc["arguments"] += fn.arguments
        if parts:
            print()  # 流式回复结束换行

        tool_calls = [
            SimpleNamespace(
                id=acc["id"],
                function=SimpleNamespace(
                    name=acc["name"], arguments=acc["arguments"]
                ),
            )
            for _, acc in sorted(calls.items())
        ]
        return "".join(parts), "".join(reasoning_parts), tool_calls

    def _execute_parallel(self, parsed) -> List[str]:
        """执行本轮全部能力调用：多个时并行（子 agent 各跑各的互不阻塞）。

        结果顺序与 parsed 一致（pool.map 语义），这样 tool 消息与
        tool_call_id 的配对顺序是确定的，不受线程完成先后影响。
        """
        if len(parsed) <= 1 or self.max_workers <= 1:
            return [self._execute_capability(name, args)
                    for _, name, args in parsed]

        names = ", ".join(name for _, name, _ in parsed)
        print(f"\n⚡ 并行执行 {len(parsed)} 个能力: {names}")
        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(parsed))
        ) as pool:
            return list(pool.map(
                lambda item: self._execute_capability(item[1], item[2]),
                parsed,
            ))

    # ================================================================
    # 辅助方法
    # ================================================================

    def _build_messages(self, input_text: str) -> List[Dict[str, Any]]:
        """组装本轮 messages —— 委托给 core/context。

        这里原来是「system + 全量历史 + user」的平铺拼接：没有记忆、没有
        token 预算、只增不减，长会话必然撑爆上下文窗口。现在交给
        ContextManager 分层组装并守预算，方法名与签名保持不变，
        所以 _run_react_loop 不用改。
        """
        return self.context.build_messages(input_text, self._history)

    def _parse_tool_call(self, tool_call) -> tuple[str, Dict]:
        tool_name = tool_call.function.name
        raw_args = tool_call.function.arguments
        try:
            tool_args = json.loads(raw_args)
        except json.JSONDecodeError:
            tool_args = {}
            if self.verbose:
                print(f"  ⚠ 工具 '{tool_name}' 参数不是合法 JSON，原始值前 200 字: "
                      f"{str(raw_args)[:200]}")
        if self.verbose:
            keys = list(tool_args.keys()) if isinstance(tool_args, dict) else type(tool_args).__name__
            print(f"  → 调用能力 '{tool_name}'，参数键: {keys}")
            if isinstance(tool_args, dict) and "data" not in tool_args:
                print("    ⚠ 未包含 data 参数（技能可能只能用默认模板）")
        return tool_name, tool_args

    def _execute_capability(self, name: str, params: Dict[str, Any]) -> str:
        """统一执行入口：技能与工具走同一条路。"""
        if hasattr(self.router, "execute"):
            return self.router.execute(name, params)

        # 退化路径：只拿到 ToolRegister
        tool = self.tool_register.get_tool(name)
        if tool is None:
            return f"未找到能力 '{name}'"
        try:
            return str(tool.run(params))
        except Exception as exc:
            return f"能力 '{name}' 执行失败: {exc}"

    # 兼容旧方法名
    def _execute_tool(self, name: str, params: Dict[str, Any]) -> str:
        return self._execute_capability(name, params)

    def _record_to_history(self, input_text: str, final_answer: str) -> None:
        self.add_message(Message(role="user", content=input_text))
        self.add_message(Message(role="assistant", content=final_answer))

    # ================================================================
    # 调试辅助
    # ================================================================

    def describe_capabilities(self) -> str:
        """打印人类可读的能力清单（技能/工具分组）。"""
        lines = [f"Agent '{self.name}' 可用能力:"]
        for capability in self.router:
            kind = capability_kind(capability).value
            lines.append(f"  • [{kind}] {capability.name} — "
                         f"{getattr(capability, 'description', '')[:70]}")
        return "\n".join(lines)


def _step_desc(steps: List[StepNode], idx: int) -> str:
    for s in steps:
        if s.index == idx:
            return s.description
    return ""
