import re
from typing import List, Optional

from core.llm import LLM

PLANNING_PROMPT = """\
你是一个任务规划专家。请将以下用户任务分解为一系列可执行的子步骤，并声明步骤之间的依赖关系。

要求：
1. 每个步骤应该是独立的、可执行的子任务
2. 步骤之间有清晰的逻辑顺序和依赖关系
3. 步骤数量适中，不要过细或过粗（通常3-7步）
4. 如果任务很简单无需拆分，直接输出一个步骤即可
5. 互不依赖的步骤应声明相同的依赖，以便并行执行

请严格按照以下格式输出计划（每行一步）：
步骤1: 具体描述 | depends_on: []
步骤2: 具体描述 | depends_on: [1]
步骤3: 具体描述 | depends_on: [1]
步骤4: 具体描述 | depends_on: [2,3]

用户任务: {task}
"""

STEP_EXECUTION_PROMPT = """\
你需要执行以下计划中的当前步骤，可以调用工具来辅助完成。

# 原始任务
{task}

# 完整计划
{plan}

# 已完成的步骤及结果
{history}

# 当前需要执行的步骤
{current_step}

请执行当前步骤，如果需要使用工具请调用，完成后给出该步骤的执行结果。
"""

class StepNode:
    """计划中的一个步骤节点"""

    def __init__(self, index: int, description: str, depends_on: List[int]):
        self.index = index
        self.description = description
        self.depends_on = depends_on
        self.status = "pending"
        self.result = ""
        self.error = None

    def __repr__(self):
        deps = f"depends_on={self.depends_on}" if self.depends_on else "no deps"
        return f"Step({self.index}: {self.description} [{self.status}] {deps})"

class Planner:
    """任务规划器 - 调用LLM将任务拆解为带依赖的StepNode列表"""

    def __init__(self, llm: LLM, prompt: Optional[str] = None):
        self.llm = llm
        self.prompt = prompt or PLANNING_PROMPT

    def plan(self, task: str, **kwargs) -> List[StepNode]:
        prompt = self.prompt.format(task=task)
        messages = [{"role": "user", "content": prompt}]
        try:
            response = self.llm.invoke(messages=messages, **kwargs)
            return self._parse_plan(response.content or "")
        except Exception as e:
            print(f"❌ 规划阶段出错: {e}")
            return []

    def _parse_plan(self, content: str) -> List[StepNode]:
        steps = []
        for line in content.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            match = re.match(
                r'^步骤\s*(\d+)\s*[:：]\s*(.+?)\s*\|\s*depends_on\s*[:：]\s*$$([^$$]*)\]',
                line
            )
            if match:
                idx = int(match.group(1))
                desc = match.group(2).strip()
                deps_str = match.group(3).strip()
                deps = [int(x.strip()) for x in deps_str.split(",") if x.strip().isdigit()] if deps_str else []
                steps.append(StepNode(idx, desc, deps))
                continue
            match = re.match(r'^步骤\s*\d+\s*[:：]\s*(.+)', line)
            if match:
                idx = len(steps) + 1
                steps.append(StepNode(idx, match.group(1).strip(), [idx - 1] if idx > 1 else []))
                continue
            match = re.match(r'^(\d+)[\.\)]\s*(.+)', line)
            if match:
                idx = int(match.group(1))
                steps.append(StepNode(idx, match.group(2).strip(), [idx - 1] if idx > 1 else []))
        return steps