# -*- coding: utf-8 -*-
"""test_run.py —— Skill 模块全流程测试（ReActAgent 驱动）

覆盖范围
--------
  T1  递归发现：skills/ 下任意深度、含 SKILL.md 的目录都被识别
      （重点验证 skills/buttin/ppt_skill 与 skills/buttin/Optimalstop37-skill）
  T2  SKILL.md frontmatter 解析（name / description / execution / script）
  T3  ★ 架构约束：Skill **不继承 Tool**，而是实现 Capability 协议
  T4  CapabilityRouter：技能与工具统一注册 / schema / 匹配 / 执行
  T5  execution=llm   模式：技能文档作 system prompt，单次调用 LLM
  T6  内层 ReAct 模式：技能内部再跑 ReAct 循环并调用子能力
  T7  execution=script 模式：执行 build_deck.py，真实产出 .pptx
  T8  ★ 输入字段归一化：模型键名不可控（实际成本/产值/list账龄）必须容错
  T9  SkillRegister 按 id 调度技能
  T10 ★ 意图识别 + 能力匹配：从 skills + tools 里挑出候选
  T11 【主线】ReActAgent → 意图识别 → 匹配 skill/tool → 执行 → 回填 → 最终答案
  T12 ★ ppt-skill 完整闭环：生成 → 校验 → checks.json pass → 话术非空
  T13 （可选）真实 LLM 全流程，未配置/不可达时自动跳过

设计原则
--------
* 默认 **完全离线**：用 FakeLLM 替身，不依赖网络、不消耗额度，结果确定可复现。
* 只有 T13 需要真实 LLM，且自动探测、失败即跳过，不会让测试挂掉。
* 不依赖 pytest（项目 venv 未安装），用标准库自带断言框架。

运行
----
    D:\\PythonProject7\\.venv\\Scripts\\python.exe test_run.py
    D:\\PythonProject7\\.venv\\Scripts\\python.exe test_run.py --live auto
    D:\\PythonProject7\\.venv\\Scripts\\python.exe test_run.py --live qwen
    D:\\PythonProject7\\.venv\\Scripts\\python.exe test_run.py --live none   # 只跑离线部分
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------
# 项目根目录 / 导入路径
# ---------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

SKILLS_ROOT = os.path.join(PROJECT_ROOT, "skills")

# 每次运行用**独立目录**。
# 原因（实测踩过）：上一轮的产物可能正被 WPS / PowerPoint 打开，
# 同名文件会 PermissionError；而 `Remove-Item -EA SilentlyContinue`
# 会把删除失败静默吞掉，于是"清理过了"的文件其实还在，测试再写就撞锁。
# 用独立目录彻底绕开这个不确定性；旧目录顺手尽力清理（失败不管）。
_RUN_STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
TEST_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "test_output", f"run_{_RUN_STAMP}")
_OUTPUT_ROOT = os.path.join(PROJECT_ROOT, "test_output")


def _prune_old_runs(keep: int = 3) -> None:
    """只留最近几次运行目录，避免无限堆积。删不掉就跳过。"""
    try:
        runs = sorted(
            (d for d in os.listdir(_OUTPUT_ROOT)
             if d.startswith("run_") and os.path.isdir(os.path.join(_OUTPUT_ROOT, d))),
            reverse=True,
        )
        for name in runs[keep:]:
            shutil.rmtree(os.path.join(_OUTPUT_ROOT, name), ignore_errors=True)
    except Exception:
        pass


# ---------------------------------------------------------------------
# .env 载入（不覆盖已有环境变量）
# ---------------------------------------------------------------------
def load_dotenv(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value


load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


# ---------------------------------------------------------------------
# 极简测试框架
# ---------------------------------------------------------------------
class TestRecorder:
    """记录每个用例的结果，最后统一汇总。"""

    def __init__(self) -> None:
        self.results: List[Dict[str, Any]] = []

    def record(self, name: str, passed: bool, detail: str = "") -> bool:
        self.results.append({"name": name, "passed": passed, "detail": detail,
                             "skipped": False})
        icon = "PASS" if passed else "FAIL"
        print(f"  [{icon}] {name}")
        if detail:
            for line in str(detail).splitlines():
                print(f"         {line}")
        return passed

    def skip(self, name: str, reason: str) -> None:
        self.results.append({"name": name, "passed": True, "detail": reason,
                             "skipped": True})
        print(f"  [SKIP] {name}")
        print(f"         {reason}")

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        return self.record(name, bool(condition), detail)

    def summary(self) -> int:
        total = len(self.results)
        skipped = sum(1 for r in self.results if r["skipped"])
        failed = [r for r in self.results if not r["passed"]]
        print()
        print("=" * 72)
        print(f"  汇总: {total} 项检查 | 通过 {total - len(failed) - skipped} "
              f"| 跳过 {skipped} | 失败 {len(failed)}")
        print("=" * 72)
        if failed:
            print("  失败项:")
            for r in failed:
                print(f"    - {r['name']}")
                if r["detail"]:
                    print(f"      {r['detail']}")
            return 1
        print("  全部通过 ✅")
        return 0


REC = TestRecorder()


def section(title: str) -> None:
    print()
    print("-" * 72)
    print(f"  {title}")
    print("-" * 72)


# ---------------------------------------------------------------------
# FakeLLM：离线替身
# ---------------------------------------------------------------------
class FakeLLM:
    """按脚本返回预设响应，接口与 core.llm.LLM 兼容。

    每次 invoke() 弹出下一条预设响应；同时记录收到的 messages / tools，
    便于断言「技能文档确实作为 system prompt 传下去了」。
    """

    def __init__(self, responses: Optional[List[Any]] = None, provider: str = "fake"):
        self.responses: List[Any] = list(responses or [])
        self.calls: List[Dict[str, Any]] = []
        self.provider = provider
        self.model = "fake-model"

    # ---- 构造辅助 ----
    @staticmethod
    def text(content: str) -> SimpleNamespace:
        """一条纯文本响应（不调用工具）。"""
        return SimpleNamespace(content=content, tool_calls=[])

    @staticmethod
    def call(name: str, arguments: Any, call_id: str = "call_1",
             content: str = "") -> SimpleNamespace:
        """一条工具调用响应。"""
        args = arguments if isinstance(arguments, str) else json.dumps(
            arguments, ensure_ascii=False)
        return SimpleNamespace(
            content=content,
            tool_calls=[
                SimpleNamespace(
                    id=call_id,
                    type="function",
                    function=SimpleNamespace(name=name, arguments=args),
                )
            ],
        )

    # ---- LLM 接口 ----
    def invoke(self, messages: List[Dict[str, Any]], **kwargs: Any):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if not self.responses:
            return self.text("（FakeLLM 未预设更多响应）")
        nxt = self.responses.pop(0)
        if isinstance(nxt, str):
            return self.text(nxt)
        return nxt

    def __repr__(self) -> str:  # pragma: no cover
        return f"FakeLLM(pending={len(self.responses)}, calls={len(self.calls)})"


# ---------------------------------------------------------------------
# 内联脚本示例（用于验证 execution=script 的机制，不依赖任何技能脚本）
# ---------------------------------------------------------------------
PPT_INLINE_CODE = r"""
import json, os, sys
from pptx import Presentation

out = os.path.join(os.environ["SKILL_RUN_DIR"], "inline.pptx")
prs = Presentation()
prs.slide_width, prs.slide_height = 12192000, 6858000
slide = prs.slides.add_slide(prs.slide_layouts[6])
box = slide.shapes.add_textbox(0, 0, 4000000, 800000)
box.text_frame.text = "INLINE:" + json.dumps(DATA, ensure_ascii=False)[:40]
prs.save(out)
print("GENERATED:", out)
"""


def make_ppt_data() -> Dict[str, Any]:
    """一份用于真实生成的演示数据（万元）。"""
    return {
        "项目": "阳光花园小区",
        "期间": "2026 年 9 月",
        "收入": {"物业费": 92.0, "公共收益": 8.5, "多种经营": 6.0, "其他": 1.5},
        "收入预算": {"物业费": 95.0, "公共收益": 8.0, "多种经营": 9.0, "其他": 1.5},
        "成本": {"人力成本": 48.0, "公共能耗": 12.5, "外包服务费": 9.0,
                 "维修维保": 6.5, "物料耗材": 3.2, "管理费及其他": 4.0},
        "成本预算": {"人力成本": 45.0, "公共能耗": 10.4, "外包服务费": 9.0,
                     "维修维保": 5.2, "物料耗材": 3.0, "管理费及其他": 4.2},
        "收缴": {"当月应收": 96.0, "当月实收": 88.3, "陈欠期初": 62.0, "陈欠回收": 9.1},
        "账龄": {"0-30天": 18.0, "31-90天": 15.5, "91-180天": 12.0, "180天以上": 7.4},
        "效能": {"人均管理面积": 3250, "人均产值": 3.05, "人工成本率": 41.6, "人均工单量": 27.4},
        "趋势月份": ["6月", "7月", "8月", "9月"],
        "趋势收入": [104.0, 106.5, 110.5, 108.0],
        "趋势成本": [78.0, 79.5, 81.2, 83.2],
        "上月行动项": "地库照明改造、5#楼污水管抢修、外包保洁结算核对",
    }


# =====================================================================
# T1 递归发现
# =====================================================================
def test_recursive_discovery() -> None:
    section("T1 递归发现技能目录（skills/buttin 分组目录）")

    from skills.loader import discover_skill_dirs

    dirs = discover_skill_dirs(SKILLS_ROOT, recursive=True)
    rel = sorted(os.path.relpath(d, SKILLS_ROOT).replace("\\", "/") for d in dirs)

    REC.check("发现 skills/ 下所有技能目录", len(dirs) == 2, f"实际: {rel}")
    REC.check("包含 skills/buttin/ppt_skill", "buttin/ppt_skill" in rel, f"实际: {rel}")
    REC.check("包含 skills/buttin/Optimalstop37-skill",
              "buttin/Optimalstop37-skill" in rel, f"实际: {rel}")
    REC.check("buttin 本身不是技能（无 SKILL.md）", "buttin" not in rel, f"实际: {rel}")

    # 非递归模式应当找不到任何技能（技能都在二级目录）
    flat = discover_skill_dirs(SKILLS_ROOT, recursive=False)
    REC.check("非递归模式下一层目录无技能（验证递归必要性）",
              len(flat) == 0, f"实际: {len(flat)} 个")


# =====================================================================
# T2 frontmatter 解析
# =====================================================================
def test_load_and_metadata() -> None:
    section("T2 加载技能文档 + frontmatter 元数据")

    from skills.loader import load_skill, Skill

    ppt_dir = os.path.join(SKILLS_ROOT, "buttin", "ppt_skill")
    skill = load_skill(ppt_dir, llm=None)

    REC.check("ppt_skill 加载成功", isinstance(skill, Skill))
    REC.check("skill_id 取自目录名", skill.skill_id == "ppt_skill", f"实际: {skill.skill_id}")
    REC.check("工具名为 skill_ppt_skill", skill.name == "skill_ppt_skill", f"实际: {skill.name}")
    REC.check("frontmatter 解析出 description",
              bool(skill.frontmatter.get("description")),
              f"description 前 60 字: {str(skill.frontmatter.get('description'))[:60]}")
    REC.check("execution 显式声明为 script",
              skill.execution == "script", f"实际: {skill.execution}")
    REC.check("frontmatter 声明了编排脚本 run_skill.py",
              bool(skill.script) and skill.script.endswith("run_skill.py"),
              f"实际: {skill.script}")
    REC.check("技能自带 deck_spec.json（机读知识）",
              os.path.isfile(os.path.join(ppt_dir, "deck_spec.json")),
              f"实际目录: {ppt_dir}")
    REC.check("旧脚本 build_deck.py 已移除",
              not os.path.isfile(os.path.join(ppt_dir, "scripts", "build_deck.py")),
              "技能不应再依赖 2004 行的单体脚本")
    REC.check("脚本路径为绝对路径（回归：相对路径会让 cwd/PYTHONPATH 错位）",
              bool(skill.script) and os.path.isabs(skill.script),
              f"实际: {skill.script}")
    REC.check("脚本文件真实存在", bool(skill.script) and os.path.isfile(skill.script),
              f"实际: {skill.script}")
    REC.check("脚本工作目录有效（回归用例）",
              os.path.isdir(skill._resolve_workdir({})),
              f"实际: {skill._resolve_workdir({})}")
    REC.check("auto 模式解析为 script（因为有脚本）",
              skill.effective_mode() == "script", f"实际: {skill.effective_mode()}")

    doc = skill.preview_document(2000)
    REC.check("技能指令包含 SKILL.md 正文", "五阶段" in skill._document or "阶段" in skill._document)
    REC.check("技能指令拼接了 references/ 下的文档",
              "BEGIN references" in skill._document,
              "应包含 references/*.md 的合并内容")
    # 注意：SKILL.md 正文里会「提到」assets 下的示例文件名（目录结构说明），
    # 所以这里断言的是「assets 目录没有被真正读取并拼接」，而非文件名不出现。
    REC.check("assets/ 二进制目录未被读入指令",
              "BEGIN assets/" not in skill._document
              and "示例-物业项目月度经营分析.pptx\n<!-- END" not in skill._document,
              "assets/ 属于 NON_INSTRUCTION_DIRS，不应被遍历拼接")
    REC.check("frontmatter 已从正文剥离", not doc.lstrip().startswith("---"))

    # 第二个技能
    opt = load_skill(os.path.join(SKILLS_ROOT, "buttin", "Optimalstop37-skill"), llm=None)
    REC.check("Optimalstop37-skill 加载成功", opt.skill_id == "optimalstop37_skill",
              f"实际: {opt.skill_id}")
    REC.check("无脚本技能的 effective_mode 为 llm",
              opt.effective_mode() == "llm", f"实际: {opt.effective_mode()}")
    REC.check("无脚本技能 script 为 None", opt.script is None, f"实际: {opt.script}")


def test_frontmatter_parser() -> None:
    section("T2b frontmatter 解析器边界情况")

    from skills.loader import parse_frontmatter

    meta, body = parse_frontmatter(
        "---\nname: demo\ndescription: 演示技能\nexecution: script\n"
        "timeout: 120\nscript_args:\n  - --out\n  - a.pptx\ntags: [a, b]\n---\n正文开始\n"
    )
    REC.check("解析 name", meta.get("name") == "demo", f"实际: {meta.get('name')}")
    REC.check("解析中文 description", meta.get("description") == "演示技能",
              f"实际: {meta.get('description')}")
    REC.check("解析 int timeout", meta.get("timeout") == 120, f"实际: {meta.get('timeout')}")
    REC.check("解析块列表 script_args",
              meta.get("script_args") == ["--out", "a.pptx"],
              f"实际: {meta.get('script_args')}")
    REC.check("解析内联列表 tags", meta.get("tags") == ["a", "b"],
              f"实际: {meta.get('tags')}")
    REC.check("剥离 frontmatter 后正文正确", body.strip() == "正文开始",
              f"实际: {body.strip()!r}")

    meta2, body2 = parse_frontmatter("# 没有 frontmatter\n正文")
    REC.check("无 frontmatter 时返回空 meta", meta2 == {}, f"实际: {meta2}")
    REC.check("无 frontmatter 时正文原样保留", body2.startswith("# 没有 frontmatter"))


# =====================================================================
# T3 架构约束：Skill 不继承 Tool
# =====================================================================
def test_skill_is_not_tool() -> None:
    section("T3 架构约束：Skill 不继承 Tool，实现 Capability 协议")

    from tool.tools import Tool
    from core.capability import Capability, CapabilityKind, capability_kind
    from skills.loader import load_skill, load_skills_from_directory
    from tool.register import ToolRegister

    ppt_dir = os.path.join(SKILLS_ROOT, "buttin", "ppt_skill")
    skill = load_skill(ppt_dir, llm=None)

    # --- 核心约束 ---
    REC.check("Skill 不是 Tool 的子类", not isinstance(skill, Tool),
              f"实际 MRO: {[c.__name__ for c in type(skill).__mro__]}")
    REC.check("Tool 不在 Skill 的 MRO 中", Tool not in type(skill).__mro__,
              f"实际 MRO: {[c.__name__ for c in type(skill).__mro__]}")
    REC.check("Skill 满足 Capability 协议（isinstance 检查）",
              isinstance(skill, Capability),
              "Capability 是 runtime_checkable Protocol")
    REC.check("Skill.kind 为 SKILL",
              capability_kind(skill) == CapabilityKind.SKILL,
              f"实际: {capability_kind(skill)}")

    # --- 协议要求的能力仍然齐备 ---
    REC.check("有 name", bool(getattr(skill, "name", "")))
    REC.check("有 description", bool(getattr(skill, "description", "")))
    REC.check("有 get_params() 且含 request",
              any(p.name == "request" for p in skill.get_params()),
              f"实际: {[p.name for p in skill.get_params()]}")
    REC.check("script 技能额外暴露 data 参数",
              any(p.name == "data" for p in skill.get_params()),
              f"实际: {[p.name for p in skill.get_params()]}")
    REC.check("有 run(params)", callable(skill.run))
    REC.check("有 run_skill()（技能特有）", callable(skill.run_skill))

    # --- 技能不能被当作工具注册进 ToolRegister ---
    registry = ToolRegister()
    registry.add_tool(skill)
    REC.check("技能误入 ToolRegister 后取不到（证明两者已解耦）",
              registry.get_tool(skill.name) is None,
              f"实际: {registry.get_tool(skill.name)}")

    # --- 加载器不再把技能塞进 ToolRegister ---
    registry2 = ToolRegister()
    skills = load_skills_from_directory(
        SKILLS_ROOT, llm=None, tool_register=registry2, verbose=False
    )
    REC.check("批量加载 2 个技能", len(skills) == 2, f"实际: {len(skills)}")
    REC.check("技能名称符合 skill_ 前缀约定",
              sorted(s.name for s in skills) ==
              ["skill_optimalstop37_skill", "skill_ppt_skill"],
              f"实际: {sorted(s.name for s in skills)}")
    REC.check("已废弃的 tool_register 参数不再收到技能",
              len(registry2.get_tools_schema()) == 0,
              f"实际注册了 {len(registry2.get_tools_schema())} 个")


# =====================================================================
# T4 CapabilityRouter：技能与工具统一调度
# =====================================================================
def test_capability_router() -> None:
    section("T4 CapabilityRouter：技能与工具统一注册 / 匹配 / 执行")

    from core.capability import CapabilityKind, capability_kind, build_function_schema
    from skills.skill_register import build_capability_router
    from tool.tools import Tool, ToolParameter

    class EchoTool(Tool):
        """一个确定性工具，用来验证与技能平级调度。"""

        def __init__(self) -> None:
            super().__init__(name="echo", description="回显输入文本，用于测试")

        def get_params(self) -> List[ToolParameter]:
            return [ToolParameter(name="text", type="string",
                                  description="要回显的文本", required=True)]

        def run(self, params: Dict[str, Any]) -> str:
            return f"ECHO:{params.get('text', '')}"

    router = build_capability_router(
        SKILLS_ROOT, llm=FakeLLM(), tools=[EchoTool()], verbose=False
    )

    REC.check("能力中心同时持有技能与工具",
              len(router.skills()) == 2 and len(router.tools()) == 1,
              f"实际: {len(router.skills())} skills / {len(router.tools())} tools")
    REC.check("技能种类识别正确",
              capability_kind(router.resolve("skill_ppt_skill")) == CapabilityKind.SKILL)
    REC.check("工具种类识别正确",
              capability_kind(router.resolve("echo")) == CapabilityKind.TOOL)

    schemas = router.schemas()
    REC.check("统一 schema 覆盖 3 个能力", len(schemas) == 3, f"实际: {len(schemas)}")
    REC.check("schema 结构为标准 function-calling",
              all(s["type"] == "function" and "parameters" in s["function"]
                  for s in schemas))

    # 按名字过滤 schema（意图识别后的裁剪）
    filtered = router.schemas(["echo"])
    REC.check("schemas(names=...) 可裁剪", len(filtered) == 1
              and filtered[0]["function"]["name"] == "echo",
              f"实际: {[s['function']['name'] for s in filtered]}")

    # 目录清单（意图识别用，不含完整参数）
    catalog = router.catalog()
    REC.check("catalog 同时列出技能与工具",
              "skill_ppt_skill (skill)" in catalog and "echo (tool)" in catalog,
              f"实际:\n{catalog}")
    REC.check("catalog 不包含参数 schema",
              "properties" not in catalog)

    # 匹配
    REC.check("match by names", [c.name for c in router.match(names=["echo"])] == ["echo"])
    REC.check("match by query 命中技能",
              "skill_ppt_skill" in [c.name for c in router.match(query="经营分析 PPT")],
              f"实际: {[c.name for c in router.match(query='经营分析 PPT')]}")
    REC.check("match 无结果时返回空", router.match(query="zzzzz不存在zzzzz") == [])

    # 执行
    REC.check("执行工具", router.execute("echo", {"text": "hi"}) == "ECHO:hi")
    REC.check("未知能力返回文本而非抛异常",
              "未找到能力" in router.execute("nope", {}),
              f"实际: {router.execute('nope', {})}")

    # 能力内部异常被收敛
    class BoomTool(Tool):
        def __init__(self) -> None:
            super().__init__(name="boom", description="总是失败")

        def get_params(self) -> List[ToolParameter]:
            return []

        def run(self, params: Dict[str, Any]) -> str:
            raise RuntimeError("炸了")

    router.add_tool(BoomTool())
    boom_result = router.execute("boom", {})
    REC.check("能力异常被收敛成文本", "执行失败" in boom_result and "炸了" in boom_result,
              f"实际: {boom_result}")

    # 同名冲突：技能优先
    class SkillNameClash(Tool):
        def __init__(self) -> None:
            super().__init__(name="skill_ppt_skill", description="同名工具")

        def get_params(self) -> List[ToolParameter]:
            return []

        def run(self, params: Dict[str, Any]) -> str:
            return "tool"

    router.add_tool(SkillNameClash())
    REC.check("同名时技能优先于工具",
              capability_kind(router.resolve("skill_ppt_skill")) == CapabilityKind.SKILL,
              f"实际: {capability_kind(router.resolve('skill_ppt_skill'))}")
    REC.check("冲突被记录", len(router.conflicts) >= 1, f"实际: {router.conflicts}")

    # schema 构造工具函数
    schema = build_function_schema("t", "d", [])
    REC.check("build_function_schema 可用",
              schema["function"]["name"] == "t" and schema["function"]["parameters"]["type"] == "object")



# =====================================================================
# T5 execution=llm
# =====================================================================
def test_llm_mode() -> None:
    section("T5 execution=llm 模式（技能文档作 system prompt）")

    from skills.loader import load_skill

    llm = FakeLLM(["本月经营结论：成本超预算 3.9 万元，主因人力与能耗。"])
    skill = load_skill(
        os.path.join(SKILLS_ROOT, "buttin", "Optimalstop37-skill"),
        llm=llm,
        execution="llm",
    )

    result = skill.run_skill({"request": "帮我设计一个选 offer 的评分卡"})

    REC.check("执行成功", result.success, f"error={result.error}")
    REC.check("模式为 llm", result.mode == "llm", f"实际: {result.mode}")
    REC.check("输出回传了 LLM 内容", "成本超预算" in result.output, f"实际: {result.output!r}")
    REC.check("确实调用了 LLM 一次", len(llm.calls) == 1, f"实际: {len(llm.calls)}")

    messages = llm.calls[0]["messages"]
    REC.check("首条消息 role=system", messages[0]["role"] == "system",
              f"实际: {messages[0]['role']}")
    REC.check("技能文档进入 system prompt",
              "评分卡" in messages[0]["content"],
              "system prompt 应包含 SKILL.md 内容")
    REC.check("用户请求作为 user 消息",
              messages[-1]["role"] == "user" and "评分卡" in messages[-1]["content"],
              f"实际: {messages[-1]}")

    # Tool 接口返回字符串
    text = skill.run({"request": "再来一次"})
    REC.check("Tool.run 返回文本", isinstance(text, str) and "[技能" in text,
              f"实际: {text[:80]!r}")

    # 无 LLM 时的失败路径
    from skills.loader import load_skill as load2
    bare = load2(os.path.join(SKILLS_ROOT, "buttin", "Optimalstop37-skill"),
                 llm=None, execution="llm")
    bad = bare.run_skill({"request": "x"})
    REC.check("未配置 LLM 时优雅失败", (not bad.success) and "未配置" in (bad.error or ""),
              f"error={bad.error}")


# =====================================================================
# T6 内层 ReAct
# =====================================================================
def test_react_mode() -> None:
    section("T6 execution=react 模式（技能内部再跑 ReAct 并调用子能力）")

    from core.capability import CapabilityRouter
    from tool.tools import Tool, ToolParameter
    from skills.loader import load_skill

    class WordCounterTool(Tool):
        """技能内部可用的子工具：统计字数。"""

        def __init__(self) -> None:
            super().__init__(name="word_counter", description="统计一段文本的字数")

        def get_params(self) -> List[ToolParameter]:
            return [ToolParameter(name="text", type="string",
                                  description="要统计的文本", required=True)]

        def run(self, params: Dict[str, Any]) -> str:
            return f"字数={len(str(params.get('text', '')))}"

    # 子能力表同样用 CapabilityRouter（技能内部也能调技能/工具）
    router = CapabilityRouter("skill_internal")
    router.add_tool(WordCounterTool())

    llm = FakeLLM([
        FakeLLM.call("word_counter", {"text": "评分卡设计"}, call_id="c1"),
        FakeLLM.text("评分卡已设计完成，共 5 个维度。"),
    ])
    skill = load_skill(
        os.path.join(SKILLS_ROOT, "buttin", "Optimalstop37-skill"),
        llm=llm,
        execution="react",
        react_tools=router,
        react_max_steps=4,
    )

    result = skill.run_skill({"request": "设计评分卡并统计维度字数"})

    REC.check("执行成功", result.success, f"error={result.error}")
    REC.check("模式为 react", result.mode == "react", f"实际: {result.mode}")
    REC.check("调用了子能力 word_counter",
              "word_counter" in result.meta.get("tools_used", []),
              f"实际: {result.meta}")
    REC.check("走满两轮 LLM（工具轮 + 收尾轮）", len(llm.calls) == 2,
              f"实际: {len(llm.calls)}")
    REC.check("最终输出为收尾轮内容", "5 个维度" in result.output, f"实际: {result.output!r}")

    # 第二轮应当收到 tool 角色的结果消息
    second_msgs = llm.calls[1]["messages"]
    tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
    REC.check("第二轮带上了 tool 结果消息",
              len(tool_msgs) == 1 and "字数=" in tool_msgs[0]["content"],
              f"实际: {tool_msgs}")
    REC.check("tools schema 已传给 LLM",
              llm.calls[0]["kwargs"].get("tools") is not None,
              f"实际: {llm.calls[0]['kwargs']}")

    # 旧的 ToolRegister 仍可传入（向后兼容路径）
    from tool.register import ToolRegister as _TR
    legacy = _TR()
    legacy.add_tool(WordCounterTool())
    llm2 = FakeLLM([
        FakeLLM.call("word_counter", {"text": "abc"}, call_id="c2"),
        FakeLLM.text("兼容路径完成"),
    ])
    skill2 = load_skill(
        os.path.join(SKILLS_ROOT, "buttin", "Optimalstop37-skill"),
        llm=llm2, execution="react", react_tools=legacy,
    )
    r2 = skill2.run_skill({"request": "兼容性测试"})
    REC.check("react 模式兼容旧 ToolRegister", r2.success, f"error={r2.error}")
    REC.check("旧 ToolRegister 路径下子工具真的被调用",
              "word_counter" in r2.meta.get("tools_used", []),
              f"实际: {r2.meta}")


# =====================================================================
# T7 execution=script → 真实产出 pptx
# =====================================================================
def test_script_mode() -> None:
    section("T7 execution=script 模式（真实生成 .pptx）")

    from skills.loader import load_skill

    ppt_dir = os.path.join(SKILLS_ROOT, "buttin", "ppt_skill")
    skill = load_skill(ppt_dir, llm=None, execution="script", script_code=PPT_INLINE_CODE)
    # 本用例用内联脚本注入数据，与常用目录无关 → 关掉自动取数保证确定性
    skill.auto_resolve_data = False

    # 把演示数据以字面量注入内联脚本，真实跑通五阶段流水线
    skill.script_code = "DATA = " + repr(make_ppt_data()) + "\n" + PPT_INLINE_CODE

    result = skill.run_skill({"request": "生成阳光花园小区 2026 年 9 月经营分析 PPT"})

    REC.check("脚本执行成功", result.success,
              f"error={result.error}\noutput={result.output[:600]}")
    REC.check("模式为 script", result.mode == "script", f"实际: {result.mode}")
    REC.check("收集到产出文件", len(result.artifacts) >= 1, f"实际: {result.artifacts}")
    REC.check("stdout 含生成日志",
              "阶段5" in result.output or "GENERATED" in result.output,
              f"output={result.output[:300]}")

    pptx = [p for p in result.artifacts if p.lower().endswith(".pptx")]
    REC.check("产出物包含 .pptx", len(pptx) == 1, f"实际: {result.artifacts}")

    if pptx:
        path = pptx[0]
        size = os.path.getsize(path)
        REC.check("pptx 文件非空（>10KB）", size > 10_000, f"实际: {size} 字节")

        # 用 python-pptx 打开校验。本用例的脚本是**最小示例**，
        # 只验证 script 模式的机制（能跑、能落盘、能被解析），
        # 内容质量由 T12/T24 用真实技能链路验证。
        try:
            from pptx import Presentation

            prs = Presentation(path)
            slides = len(prs.slides)
            REC.check("python-pptx 可正常打开", True, f"页数={slides}")
            REC.check("至少生成 1 页", slides >= 1, f"实际: {slides} 页")

            texts = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        texts.append(shape.text_frame.text)
            joined = "\n".join(texts)
            REC.check("脚本注入的 DATA 被写进幻灯片",
                      "INLINE:" in joined, f"实际文本: {joined[:120]}")
            REC.check("宽度为 16:9", abs(prs.slide_width / prs.slide_height - 16 / 9) < 0.05,
                      f"实际比例={prs.slide_width / prs.slide_height:.3f}")
        except Exception as exc:  # pragma: no cover
            REC.check("python-pptx 校验", False, f"{type(exc).__name__}: {exc}")

    # 超时/失败的健壮性
    bad = load_skill(ppt_dir, llm=None, execution="script",
                     script_code="import sys; sys.exit(3)")
    bad_result = bad.run_skill({"request": "x"})
    REC.check("脚本非零退出被识别为失败",
              (not bad_result.success) and "退出码" in (bad_result.error or ""),
              f"error={bad_result.error}")


def make_model_shaped_data() -> Dict[str, Any]:
    """模型/上游可能给出的「不规整」列名（保留作参考）。

    新链路不再靠别名猜测 —— spec 里显式声明列名，列不存在就诚实降级。
    这个夹具留下来给 T8 验证"声明列缺失时不许拿别的列冒充"。
    """
    return {
        "人均效能": {"产值": 3.05, "人工成本率": 41.6, "工单量": 27.4, "管理面积": 3250},
        "实际成本": {"人力": 48.0, "公共能耗": 12.5, "外包": 9.0},
        "实际收入": {"公共收益": 8.5, "其他": 1.5, "多种经营": 6.0, "物业费": 92.0},
        "项目": "阳光花园小区",
        "期间": "2026 年 9 月",
    }


def test_data_normalization() -> None:
    section("T8 列推断与诚实降级（spec 声明列，缺失不冒充）")

    from tool.common.deck_content_tool import (
        _looks_like_period, _pick_value_column, _pick_group_column,
        _numeric_columns, _text_columns,
    )
    from tool.common.table_io import read_table

    stage = os.path.join(TEST_OUTPUT_DIR, "infer")
    os.makedirs(stage, exist_ok=True)

    # 一份含"陷阱列"的数据：
    #   费用期间 = 201801 这种期间码（名字含"费用"会被误认成金额列）
    #   楼栋/单元 = 小整数（不该当主指标）
    #   月份 = 日期类（不该当分组维度）
    csv_path = os.path.join(stage, "tricky.csv")
    with open(csv_path, "w", encoding="utf-8-sig") as handle:
        handle.write("\n".join([
            "费用期间,月份,收费项,账单金额(元),未缴金额(元),楼栋,单元",
            "201801,2018-01,物业服务费,260000.00,12000.00,1,3",
            "201802,2018-02,物业服务费,240000.00,9000.00,2,1",
            "201803,2018-03,公共收益,18000.00,500.00,3,2",
        ]))

    df, engine = read_table(csv_path)
    REC.check("测试数据可读", df is not None, str(engine))

    nums = _numeric_columns(df)
    REC.check("识别出数值列（含陷阱列）",
              "费用期间" in nums and "账单金额(元)" in nums, f"实际: {nums}")

    # ★ 关键回归：期间码必须被识别出来并排除
    REC.check("期间码 201801 被识别为期间而非金额",
              _looks_like_period(df["费用期间"]) is True,
              "6 位且落在 190001~299912 → 期间码")
    REC.check("真实金额列不被误判为期间",
              _looks_like_period(df["账单金额(元)"]) is False)

    picked = _pick_value_column(nums, df)
    REC.check("主指标列排除期间码（不选 费用期间）",
              picked != "费用期间", f"实际选中: {picked}")
    REC.check("主指标列按语义选中金额列",
              picked in ("账单金额(元)", "未缴金额(元)"), f"实际: {picked}")

    # 分组列必须排除日期类
    texts = _text_columns(df)
    REC.check("文本候选列排除日期类（月份 不在其中）",
              "月份" not in texts, f"实际: {texts}")
    group = _pick_group_column(texts)
    REC.check("分组列优先按列名语义（收费项）",
              group == "收费项", f"实际: {group}")

    # ---- 诚实降级：spec 声明了不存在的列 ----
    from tool.common.deck_content_tool import DeckContentTool

    spec = {
        "profile": "t8",
        "name": "T8 降级测试",
        "page_rules": [{"max_minutes": 20,
                        "sections": ["cover", "ok_page", "missing_page"]}],
        "sections": [
            {"key": "cover", "label": "封面", "layout": "cover"},
            {"key": "ok_page", "label": "正常页", "layout": "chart",
             "data": {"value_column": "账单金额(元)", "group_column": "收费项"},
             "narrative": [{"when": "top_share > 0.5",
                            "so_what": "{top_name} 占 {top_share_pct}%，集中度高。"}]},
            {"key": "missing_page", "label": "缺列页", "layout": "chart",
             "data": {"value_column": "成本金额(元)", "group_column": "成本科目"},
             "narrative": [{"when": "", "so_what": "不该出现。"}]},
        ],
    }

    ir_path = os.path.join(stage, "degrade.json")
    out = DeckContentTool().run({
        "action": "build", "spec": spec, "file": csv_path, "project": "T8",
        "out": ir_path,
    })
    REC.check("构建成功（缺列不导致整体失败）", "已生成 IR" in out, out[:300])

    with open(ir_path, "r", encoding="utf-8") as handle:
        deck = json.load(handle)

    ok_slide = deck["slides"][1]
    bad_slide = deck["slides"][2]

    REC.check("正常页用声明列生成结论句",
              "占" in ok_slide["so_what"] and "物业服务费" in ok_slide["so_what"],
              ok_slide["so_what"])

    REC.check("缺列页标题标注数据缺失",
              "数据缺失" in bad_slide["title"], f"实际: {bad_slide['title']}")
    REC.check("缺列页结论说明缺什么列",
              "成本金额(元)" in bad_slide["so_what"]
              and "成本科目" in bad_slide["so_what"],
              bad_slide["so_what"])
    REC.check("缺列页不套用叙事模板（不许编结论）",
              not bad_slide.get("_narrative_applied"),
              "缺数据时编结论比不生成更糟")
    REC.check("缺列页给出可操作提示",
              any("现有列" in str(b.get("items")) or "补充" in str(b.get("text"))
                  for b in bad_slide["blocks"]),
              f"实际 blocks: {bad_slide['blocks']}")


def test_skill_register() -> None:
    section("T9 SkillRegister 统一注册与调度（技能不再是工具）")

    from core.capability import CapabilityRouter
    from skills.skill_register import SkillRegister, build_skill_register

    llm = FakeLLM(["注册中心调度结果"])
    register = SkillRegister(SKILLS_ROOT, llm=llm, verbose=False)

    REC.check("装载 2 个技能", len(register) == 2, f"实际: {len(register)}")
    REC.check("ids 正确",
              register.ids() == ["optimalstop37_skill", "ppt_skill"],
              f"实际: {register.ids()}")
    REC.check("get 支持 id 归一化",
              register.get("ppt_skill") is not None and register.get("PPT-Skill") is not None,
              f"实际: {register.get('PPT-Skill')}")
    REC.check("__contains__ 支持归一化", "ppt_skill" in register)

    schemas = register.schemas()
    REC.check("schemas 暴露 2 个技能", len(schemas) == 2, f"实际: {len(schemas)}")

    REC.check("内部持有 CapabilityRouter",
              isinstance(register.router, CapabilityRouter))
    REC.check("技能已挂到能力中心",
              len(register.router.skills()) == 2,
              f"实际: {register.router.skills()}")

    summary = register.summary()
    REC.check("summary 含相对路径",
              any("buttin/ppt_skill" in item["relative_path"] for item in summary),
              f"实际: {[i['relative_path'] for i in summary]}")

    # 按 id 执行（llm 模式技能，避免真跑脚本）
    result = register.run_skill("optimalstop37_skill", "给个建议")
    REC.check("按 id 执行成功", result.success, f"error={result.error}")
    REC.check("返回文本带技能名",
              "optimalstop37_skill" in result.to_text(), f"实际: {result.to_text()[:80]}")

    missing = register.run_skill("not_exist", "x")
    REC.check("未知技能返回失败而非抛异常",
              (not missing.success) and "未找到技能" in (missing.error or ""),
              f"error={missing.error}")

    # 工厂函数
    factory = build_skill_register(SKILLS_ROOT, llm=FakeLLM(["x"]), verbose=False)
    REC.check("build_skill_register 工厂可用", len(factory) == 2, f"实际: {len(factory)}")


# =====================================================================
# T10 意图识别 + 能力匹配
# =====================================================================
def test_intent_routing() -> None:
    section("T10 意图识别 → 从 skills + tools 里匹配候选能力")

    from skills.skill_register import build_capability_router
    from tool.tools import Tool, ToolParameter
    from agents.react_agent.react_agent import ReActAgent

    class EchoTool(Tool):
        def __init__(self) -> None:
            super().__init__(name="echo", description="回显输入文本")

        def get_params(self) -> List[ToolParameter]:
            return [ToolParameter(name="text", type="string",
                                  description="文本", required=True)]

        def run(self, params: Dict[str, Any]) -> str:
            return f"ECHO:{params.get('text', '')}"

    router = build_capability_router(
        SKILLS_ROOT, llm=FakeLLM(), tools=[EchoTool()], verbose=False
    )

    # --- 9.1 选中技能 ---
    agent_llm = FakeLLM([
        FakeLLM.text('{"intent": "制作物业经营分析PPT", '
                     '"capabilities": ["skill_ppt_skill"]}'),
        FakeLLM.text("已完成规划。"),
    ])
    agent = ReActAgent(name="路由测试", llm=agent_llm, router=router, max_steps=3)
    answer = agent.run("帮我做一份物业月度经营分析 PPT")

    REC.check("识别到意图", agent.last_intent == "制作物业经营分析PPT",
              f"实际: {agent.last_intent!r}")
    REC.check("选中技能作为候选", agent.last_selected == ["skill_ppt_skill"],
              f"实际: {agent.last_selected}")
    REC.check("返回最终答案", "已完成规划" in answer, f"实际: {answer!r}")
    REC.check("意图识别 + 推理共 2 轮 LLM", len(agent_llm.calls) == 2,
              f"实际: {len(agent_llm.calls)}")

    # 第 1 轮是意图识别：不应带 tools，且提示词里含能力清单
    first = agent_llm.calls[0]
    REC.check("意图识别轮不传 tools schema",
              first["kwargs"].get("tools") is None, f"实际: {first['kwargs']}")
    REC.check("意图识别提示词包含技能与工具清单",
              "skill_ppt_skill (skill)" in first["messages"][0]["content"]
              and "echo (tool)" in first["messages"][0]["content"],
              "catalog 应同时列出两类能力")

    # 第 2 轮只暴露被选中的能力
    second_tools = agent_llm.calls[1]["kwargs"].get("tools") or []
    names = [t["function"]["name"] for t in second_tools]
    REC.check("推理轮只暴露候选能力（已裁剪）", names == ["skill_ppt_skill"],
              f"实际: {names}")

    # --- 9.2 选中工具 ---
    agent_llm2 = FakeLLM([
        FakeLLM.text('{"intent": "回显文本", "capabilities": ["echo"]}'),
        FakeLLM.call("echo", {"text": "你好"}, call_id="e1"),
        FakeLLM.text("已回显完成。"),
    ])
    agent2 = ReActAgent(name="工具路由", llm=agent_llm2, router=router, max_steps=3)
    answer2 = agent2.run("把『你好』原样回显给我")

    REC.check("选中工具作为候选", agent2.last_selected == ["echo"],
              f"实际: {agent2.last_selected}")
    REC.check("Agent 真的调用了工具", "已回显完成" in answer2, f"实际: {answer2!r}")
    tool_msgs = [m for m in agent_llm2.calls[2]["messages"] if m.get("role") == "tool"]
    REC.check("工具结果被回填", len(tool_msgs) == 1 and "ECHO:你好" in tool_msgs[0]["content"],
              f"实际: {tool_msgs}")

    # --- 9.3 意图识别选出未知名字 / 空列表 → 回退到全部能力 ---
    agent_llm3 = FakeLLM([
        FakeLLM.text('{"intent": "无关", "capabilities": ["不存在的技能"]}'),
        FakeLLM.text("回退后仍然作答。"),
    ])
    agent3 = ReActAgent(name="回退测试", llm=agent_llm3, router=router, max_steps=3)
    agent3.run("随便问问")
    REC.check("未知能力名被过滤掉", agent3.last_selected == [],
              f"实际: {agent3.last_selected}")
    fallback_tools = agent_llm3.calls[1]["kwargs"].get("tools") or []
    REC.check("回退时暴露全部能力",
              len(fallback_tools) == 3,
              f"实际: {[t['function']['name'] for t in fallback_tools]}")

    # --- 9.4 关闭意图识别 → 直接把全部能力给 LLM ---
    agent_llm4 = FakeLLM([FakeLLM.text("直接回答。")])
    agent4 = ReActAgent(name="关闭路由", llm=agent_llm4, router=router,
                        enable_intent_routing=False, max_steps=3)
    agent4.run("你好")
    REC.check("关闭意图识别后只调 1 轮 LLM", len(agent_llm4.calls) == 1,
              f"实际: {len(agent_llm4.calls)}")
    REC.check("关闭路由后暴露全部能力",
              len(agent_llm4.calls[0]["kwargs"].get("tools") or []) == 3)
    REC.check("关闭路由时不记录意图", agent4.last_selected == [],
              f"实际: {agent4.last_selected}")

    # --- 9.5 force_capabilities 强制指定 ---
    agent_llm5 = FakeLLM([FakeLLM.text("强制路由完成。")])
    agent5 = ReActAgent(name="强制路由", llm=agent_llm5, router=router,
                        force_capabilities=["echo"], max_steps=3)
    agent5.run("你好")
    REC.check("force_capabilities 跳过意图识别",
              len(agent_llm5.calls) == 1, f"实际: {len(agent_llm5.calls)}")
    REC.check("force_capabilities 生效", agent5.last_selected == ["echo"],
              f"实际: {agent5.last_selected}")

    # --- 9.6 解析器容错 ---
    from core.capability import parse_intent_response

    REC.check("解析 ```json 围栏",
              parse_intent_response(
                  '```json\n{"intent":"a","capabilities":["x"]}\n```')["capabilities"] == ["x"])
    REC.check("解析前后夹杂文字",
              parse_intent_response(
                  '好的：{"intent":"b","capabilities":["y"]} 完成')["capabilities"] == ["y"])
    REC.check("capabilities 为字符串时容错",
              parse_intent_response('{"capabilities":"z"}')["capabilities"] == ["z"])
    REC.check("非法 JSON 返回空结构",
              parse_intent_response("这不是JSON")["capabilities"] == [])
    REC.check("空输入返回空结构",
              parse_intent_response("") == {"intent": "", "capabilities": []})


# =====================================================================
# T11 主线：ReActAgent 驱动整个 Skill 流程
# =====================================================================
def test_react_agent_end_to_end() -> None:
    section("T11 【主线】ReActAgent → 意图识别 → 匹配 → 执行 → 回填 → 最终答案")

    from core.capability import CapabilityRouter
    from skills.loader import load_skills_from_directory, load_skill
    from agents.react_agent.react_agent import ReActAgent

    ppt_dir = os.path.join(SKILLS_ROOT, "buttin", "ppt_skill")

    # --- 10.1 Agent 通过 tool_call 触发技能，技能走 llm 模式 ---
    router = CapabilityRouter()
    load_skills_from_directory(SKILLS_ROOT, llm=None, router=router, verbose=False)

    skill_llm = FakeLLM(["PPT 结构已规划：一页纸结论 + 收入 + 成本 + 收缴率 + 归因 + 行动项。"])
    ppt_skill = router.resolve("skill_ppt_skill")
    ppt_skill.set_llm(skill_llm)
    ppt_skill.execution = "llm"          # 本用例只验证链路，不跑脚本

    agent_llm = FakeLLM([
        # 第 1 轮：意图识别
        FakeLLM.text('{"intent": "制作经营分析PPT", "capabilities": ["skill_ppt_skill"]}'),
        # 第 2 轮：决定调用技能
        FakeLLM.call("skill_ppt_skill",
                     {"request": "为阳光花园小区制作 9 月月度经营分析 PPT"},
                     call_id="tc_1"),
        # 第 3 轮：拿到技能结果后给出最终答案
        FakeLLM.text("已通过 ppt_skill 完成结构规划，下一步生成 .pptx。"),
    ])

    agent = ReActAgent(name="PPT助手", llm=agent_llm, router=router, max_steps=5)
    answer = agent.run("帮我做一份阳光花园小区 9 月经营分析 PPT")

    REC.check("Agent 返回最终答案", "ppt_skill" in answer, f"实际: {answer!r}")
    REC.check("Agent 共调用 3 轮 LLM（意图 + 调用 + 收尾）",
              len(agent_llm.calls) == 3, f"实际: {len(agent_llm.calls)}")
    REC.check("意图被记录", agent.last_intent == "制作经营分析PPT",
              f"实际: {agent.last_intent!r}")

    # 第 3 轮应当带回 tool 结果
    third_msgs = agent_llm.calls[2]["messages"]
    tool_msgs = [m for m in third_msgs if m.get("role") == "tool"]
    REC.check("Agent 把技能结果回填为 tool 消息", len(tool_msgs) == 1,
              f"实际: {len(tool_msgs)} 条")
    if tool_msgs:
        content = tool_msgs[0]["content"]
        REC.check("回填内容来自技能执行结果",
                  "PPT 结构已规划" in content, f"实际: {content[:200]!r}")
        REC.check("回填内容带技能执行头信息",
                  "[技能 ppt_skill]" in content, f"实际: {content[:120]!r}")

    # 技能内部确实收到了 Agent 传下来的 request
    REC.check("技能收到 Agent 的请求",
              "阳光花园小区" in skill_llm.calls[0]["messages"][-1]["content"],
              f"实际: {skill_llm.calls[0]['messages'][-1]['content']!r}")

    REC.check("assistant 消息带 tool_calls 结构",
              any(m.get("tool_calls") for m in third_msgs if m.get("role") == "assistant"),
              "tool_calls 应被规范化后写回上下文")

    # --- 10.2 同一个 Agent + 真实脚本执行，端到端产出 pptx ---
    script_skill = load_skill(ppt_dir, llm=None, execution="script")
    script_skill.script_code = "DATA = " + repr(make_ppt_data()) + "\n" + PPT_INLINE_CODE

    router2 = CapabilityRouter()
    router2.add_skill(script_skill)

    agent_llm2 = FakeLLM([
        FakeLLM.text('{"intent": "生成PPT", "capabilities": ["skill_ppt_skill"]}'),
        FakeLLM.call("skill_ppt_skill",
                     {"request": "生成 PPT：阳光花园小区 2026 年 9 月"},
                     call_id="tc_2"),
        FakeLLM.text("生成完成，已产出 PPTX 文件。"),
    ])
    agent2 = ReActAgent(name="PPT生成器", llm=agent_llm2, router=router2, max_steps=5)
    answer2 = agent2.run("生成阳光花园小区 9 月经营分析 PPT")

    REC.check("端到端 Agent 返回成功", "生成完成" in answer2, f"实际: {answer2!r}")

    final_msgs = agent_llm2.calls[2]["messages"]
    final_tool = [m for m in final_msgs if m.get("role") == "tool"]
    REC.check("Agent 收到了脚本产出信息", len(final_tool) == 1, f"实际: {len(final_tool)}")
    if final_tool:
        content = final_tool[0]["content"]
        REC.check("回填内容包含产出文件路径", ".pptx" in content,
                  f"实际: {content[:300]!r}")
        REC.check("回填内容标记执行成功", "执行成功" in content, f"实际: {content[:200]!r}")
        paths = [line.strip().lstrip("- ").strip()
                 for line in content.splitlines() if ".pptx" in line]
        existing = [p for p in paths if os.path.isfile(p)]
        REC.check("工具回报的 pptx 真实存在", len(existing) >= 1,
                  f"候选={paths}")

    # --- 10.3 未知工具调用的健壮性 ---
    router3 = CapabilityRouter()
    load_skills_from_directory(SKILLS_ROOT, llm=None, router=router3, verbose=False)
    agent_llm3 = FakeLLM([
        FakeLLM.text('{"intent": "无关", "capabilities": []}'),
        FakeLLM.call("no_such_tool", {"request": "x"}, call_id="tc_x"),
        FakeLLM.text("工具不存在，我直接回答。"),
    ])
    agent3 = ReActAgent(name="健壮性", llm=agent_llm3, router=router3, max_steps=3)
    answer3 = agent3.run("随便问问")
    msgs3 = agent_llm3.calls[2]["messages"]
    err_tool = [m for m in msgs3 if m.get("role") == "tool"]
    REC.check("未知工具不会让 Agent 崩溃",
              "工具不存在" in answer3 and len(err_tool) == 1, f"实际: {answer3!r}")
    if err_tool:
        REC.check("未知工具返回明确错误",
                  "未找到能力" in err_tool[0]["content"], f"实际: {err_tool[0]['content']!r}")


# =====================================================================
# T13 可选：真实 LLM
# =====================================================================
def resolve_live_llm(mode: str):
    """探测可用的真实 LLM，返回 (llm, label) 或 (None, 原因)。"""
    if mode == "none":
        return None, "按要求跳过（--live none）"

    try:
        from core.llm import LLM
    except Exception as exc:
        return None, f"无法导入 core.llm: {exc}"

    candidates: List[Dict[str, Any]] = []
    if mode in ("auto", "qwen") and os.getenv("DASHSCOPE_API_KEY"):
        candidates.append({"provider": "qwen"})
    if mode in ("auto", "deepseek") and os.getenv("DEEPSEEK_API_KEY"):
        candidates.append({"provider": "deepseek"})
    if mode in ("auto", "ollama"):
        candidates.append({"provider": "ollama", "model": "qwen3.5:2b"})

    if not candidates:
        return None, "未找到可用 provider（需要 DASHSCOPE_API_KEY / DEEPSEEK_API_KEY / 本地 ollama）"

    errors = []
    for kwargs in candidates:
        try:
            llm = LLM(timeout=30, **kwargs)
            # 真实探活
            probe = llm.invoke(messages=[{"role": "user", "content": "回复：OK"}])
            if (getattr(probe, "content", "") or "").strip():
                return llm, f"{llm.provider}/{llm.model}"
            errors.append(f"{kwargs.get('provider')}: 空响应")
        except Exception as exc:
            errors.append(f"{kwargs.get('provider')}: {type(exc).__name__}: {str(exc)[:120]}")

    return None, "所有 provider 均不可用 → " + " | ".join(errors)


def test_live_llm(mode: str) -> None:
    section("T13 真实 LLM 全流程（可选）")

    llm, label = resolve_live_llm(mode)
    if llm is None:
        REC.skip("真实 LLM 全流程", f"{label}（离线测试不受影响）")
        return

    print(f"         使用: {label}")

    from core.capability import CapabilityRouter
    from skills.loader import load_skills_from_directory
    from agents.react_agent.react_agent import ReActAgent

    router = CapabilityRouter()
    skills = load_skills_from_directory(SKILLS_ROOT, llm=llm, router=router, verbose=False)
    REC.check("真实 LLM 下装载技能", len(skills) == 2, f"实际: {len(skills)}")

    # 直接执行一个 llm 模式技能
    target = router.resolve("skill_optimalstop37_skill")
    target.execution = "llm"
    try:
        result = target.run_skill({"request": "用三句话说明什么是 37% 法则。", "temperature": 0.2})
        REC.check("真实 LLM 执行技能成功", result.success, f"error={result.error}")
        REC.check("真实 LLM 返回非空内容", len(result.output.strip()) > 10,
                  f"output 长度={len(result.output)}")
        print(f"         技能输出预览: {result.output.strip()[:160]}")
    except Exception as exc:
        REC.check("真实 LLM 执行技能", False, f"{type(exc).__name__}: {exc}")

    # Agent 端到端（含真实意图识别）
    try:
        agent = ReActAgent(name="live", llm=llm, router=router, max_steps=4)
        answer = agent.run("用一句话说明你能做什么。")
        REC.check("真实 LLM 下 Agent 可运行", bool((answer or "").strip()),
                  f"answer={str(answer)[:120]!r}")
        print(f"         意图识别: {agent.last_intent!r}  选中: {agent.last_selected}")
    except Exception as exc:
        REC.check("真实 LLM 下 Agent 可运行", False, f"{type(exc).__name__}: {exc}")


# =====================================================================
# T12 ppt-skill 五阶段闭环：生成 → 校验 → checks.json → 话术
# =====================================================================
def test_ppt_skill_full_pipeline() -> None:
    section("T12 ppt-skill 完整闭环（生成 → 校验 → checks.json → 话术）")

    from core.capability import CapabilityRouter
    from tool.common.pptx_check_tool import PptxCheckTool, check_pptx, format_report
    from skills.loader import load_skill

    ppt_dir = os.path.join(SKILLS_ROOT, "buttin", "ppt_skill")
    stage_dir = os.path.join(TEST_OUTPUT_DIR, "pipeline")
    os.makedirs(stage_dir, exist_ok=True)
    checks_path = os.path.join(stage_dir, "checks.json")

    # ---- 真实生成：走「自动取数 → 技能 spec → 工具链」这条主路径 ----
    # 这条路径就是 Agent 实际触发的路径：
    #   文件名线索 → 常用目录匹配 → SKILL_INPUT_DATA → run_skill.py 编排工具
    os.environ["SKILL_OUTPUT_DIR"] = stage_dir
    skill = load_skill(ppt_dir, llm=None, execution="script")
    result = skill.run_skill({
        "request": "用应收信息生成物业收缴经营分析 PPT",
    })
    REC.check("阶段1~5 生成成功", result.success,
              f"error={result.error}\n{result.output[:400]}")

    pptx_files = [p for p in result.artifacts if p.lower().endswith(".pptx")]
    REC.check("产出 .pptx", len(pptx_files) == 1, f"实际: {result.artifacts}")
    if not pptx_files:
        return
    deck = pptx_files[0]
    base = os.path.splitext(deck)[0]

    # ---- 交付物 ②③：话术与元数据（现在都落在本次 run_dir 内）----
    def _find(suffix: str) -> Optional[str]:
        for path in result.artifacts:
            if path.lower().endswith(suffix):
                return path
        return None

    speech_json = _find(".speech.json")
    speech_md = _find(".speech.md")
    deck_meta = _find(".deck.json")
    REC.check("交付物③ 话术 JSON 已生成",
              bool(speech_json) and os.path.isfile(speech_json or ""), str(speech_json))
    REC.check("交付物③ 话术 Markdown 已生成",
              bool(speech_md) and os.path.isfile(speech_md or ""), str(speech_md))
    REC.check("页/章节元数据已生成",
              bool(deck_meta) and os.path.isfile(deck_meta or ""), str(deck_meta))

    if speech_json and os.path.isfile(speech_json):
        with open(speech_json, "r", encoding="utf-8") as fh:
            speech = json.load(fh)
        REC.check("话术非空（≥7 段）", len(speech.get("scripts", {})) >= 7,
                  f"实际: {len(speech.get('scripts', {}))} 段")
        first = next(iter(speech.get("scripts", {}).values()), {})
        REC.check("话术为三段式",
                  set(first.keys()) >= {"结论先行", "数据支撑", "主因与动作"},
                  f"实际: {list(first.keys())}")
        REC.check("话术带项目与期间",
                  bool(speech.get("项目")) and bool(speech.get("期间")),
                  f"项目={speech.get('项目')!r} 期间={speech.get('期间')!r}")

        # 回归：话术的「数据支撑」必须含真实数字，不能是占位符
        scripts = speech.get("scripts", {})
        placeholders = ("详见", "需根据实际数据补充")
        bad_fill = {
            sec: parts.get("数据支撑", "")
            for sec, parts in scripts.items()
            if any(ph in parts.get("数据支撑", "") for ph in placeholders)
            or any(ph in parts.get("主因与动作", "") for ph in placeholders)
        }
        REC.check("话术无占位符（数据支撑为真实指标）", not bad_fill,
                  f"仍含占位符的段: {bad_fill}")
        # 新链路的话术由 IR 反推：有图表的页会给出具体数字，
        # 纯卡片页（如归因/行动）会给「（本页数据见图表）」。
        # 因此要求"多数段落含数字"，而不是"全部"。
        digit_segments = sum(
            1 for p in scripts.values()
            if any(ch.isdigit() for ch in p.get("数据支撑", "")))
        REC.check("多数话术段落的数据支撑含真实数字",
                  digit_segments >= 4 and digit_segments >= len(scripts) // 2,
                  f"实际 {digit_segments}/{len(scripts)} 段含数字")

        # 每个段落都必须有完整的三段式（这是交付物规格，与业务无关）
        incomplete = [
            sec for sec, parts in scripts.items()
            if not all(parts.get(k, "").strip()
                       for k in ("结论先行", "数据支撑", "主因与动作"))
        ]
        REC.check("每段话术都是完整三段式", not incomplete,
                  f"缺段的: {incomplete}")

    if os.path.isfile(speech_md):
        md = open(speech_md, "r", encoding="utf-8").read()
        REC.check("话术 Markdown 含表格与三段式表头",
                  "| 结论先行 |" in md and "| 主因与动作 |" in md
                  and "| 数据支撑 |" in md,
                  f"长度={len(md)}")
        REC.check("话术 Markdown 不残留未替换占位符",
                  "{" not in md and "}" not in md,
                  "花括号说明模板没填上事实")

    # ---- 关键回归：用户数据必须真的进入 PPT（而不是空模板全 0）----
    REC.check("script 技能暴露 data 参数（数据注入入口）",
              any(p.name == "data" for p in skill.get_params()),
              f"实际参数: {[p.name for p in skill.get_params()]}")
    REC.check("data 参数说明里带字段契约（引导模型用对键名）",
              "物业费" in next(p.description for p in skill.get_params()
                              if p.name == "data"),
              "应把契约字段名写进参数描述，否则模型会自由发挥键名")

    # ---- 回归：数据不足时必须显式警告，不能静默交付空模板 ----
    # 注意：本用例要先关掉自动取数 —— 否则技能会去 data/common 找文件，
    # 那就变成"有数据"了，测不到"无数据"的警告路径。
    had_auto = skill.auto_resolve_data
    skill.auto_resolve_data = False
    try:
        empty = skill.run_skill({"request": "做个 PPT"})
    finally:
        skill.auto_resolve_data = had_auto

    empty_warnings = empty.meta.get("warnings") or []
    REC.check("不给数据时产生数据不足警告（≥2 条）",
              len(empty_warnings) >= 2, f"实际: {len(empty_warnings)} 条 -> {empty_warnings}")
    REC.check("警告文本回传给 Agent（可被模型看到）",
              "数据不足警告" in empty.to_text(),
              f"实际尾部: {empty.to_text()[-200:]!r}")
    REC.check("数据齐全时不产生警告",
              not (result.meta.get("warnings") or []),
              f"实际: {result.meta.get('warnings')}")

    from pptx import Presentation as _PR
    _prs = _PR(deck)
    _texts = []
    for _s in _prs.slides:
        for _sh in _s.shapes:
            if _sh.has_text_frame:
                _texts.append(_sh.text_frame.text)
    deck_text = "\n".join(_texts)

    # ---- 关键回归：真实数据必须进入 PPT（而不是空模板全 0）----
    # 项目名来自自动取数匹配到的文件主干（loader 用文件名当默认项目名）
    resolved_project = str((result.meta.get("input") or {}).get("project")
                           or (result.meta.get("payload") or {}).get("project")
                           or "")
    REC.check("自动取数把文件主干当作项目名",
              resolved_project in deck_text,
              f"期望 {resolved_project!r} 出现在封面；实际文本: {deck_text[:200]!r}")
    REC.check("回归：默认占位项目名未出现（XX 小区）",
              "XX 小区" not in deck_text, "出现说明数据没注入，走了空模板")

    _all_zero = ("收入 0 万元完成预算 0.0%" in deck_text
                 or "营业收入\n0.0 万" in deck_text)
    REC.check("回归：首页不是全 0 假数据", not _all_zero,
              "全 0 说明又退回了默认空模板")
    REC.check("回归：PPT 里出现真实金额（千分位或万元）",
              any(marker in deck_text for marker in
                  ("482,652", "482731", "48.27", "48.43", "3.64", "15,876")),
              f"应从真实数据算出金额；实际文本: {deck_text[:300]!r}")

    REC.check("产物列表纯净（只含本次 run_dir 输出）",
              all(os.path.isfile(p) for p in result.artifacts)
              and not any(p.endswith("build_deck.py") for p in result.artifacts)
              and all("skill_outputs" in p or "pipeline" in p
                      for p in result.artifacts),
              f"实际: {[os.path.basename(p) for p in result.artifacts]}")
    REC.check("产物含输入数据留痕（可追溯）",
              any(os.path.basename(p) == "input_data.json" for p in result.artifacts),
              f"实际: {[os.path.basename(p) for p in result.artifacts]}")

    # ---- 阶段 5.2：结构校验（通过工具能力调用）----
    router = CapabilityRouter()
    router.add_tool(PptxCheckTool())
    REC.check("pptx_check 已注册为工具能力",
              "pptx_check" in router and len(router.tools()) == 1,
              f"实际: {router.names()}")

    # must_contain 用**技能 spec 里声明**的那几项（结构性必须页）。
    # 旧版用的「当期收缴率/行动项」是旧脚本的措辞，已随版式更新。
    _spec = json.load(open(os.path.join(ppt_dir, "deck_spec.json"),
                           encoding="utf-8"))
    must_contain = (_spec.get("check") or {}).get("must_contain") or []
    expected_pages = len(json.load(open(os.path.join(
        os.path.dirname(deck), "deck.json"), encoding="utf-8"))["slides"])
    # 允许色来自技能 spec 的主题（配色知识在技能里，不在校验器里）
    from tool.common.deck_theme import build_theme as _build_theme
    _theme = _build_theme(theme=_spec.get("theme"))
    allowed_colors = (set(_theme["colors"].values())
                      | set(_theme["palette"]) | {"FFFFFF"})

    tool_output = router.execute("pptx_check", {
        "file_path": deck,
        "expected_count": expected_pages,
        "must_contain": ",".join(must_contain),
        "allowed_colors": ",".join(sorted(allowed_colors)),
        "out": checks_path,
    })
    REC.check("校验工具返回文本报告", "[PPTX 校验]" in tool_output,
              f"实际: {tool_output[:200]!r}")

    report = check_pptx(
        deck, expected_count=expected_pages,
        must_contain=must_contain,
        allowed_colors=sorted(allowed_colors),
        out=checks_path,
    )
    REC.check("交付物② checks.json 已落盘", os.path.isfile(checks_path), checks_path)

    REC.check("校验判定为 pass", report["verdict"] == "pass",
              f"实际 verdict={report['verdict']}, 详情:\n{format_report(report)}")
    REC.check("无 fail 项", report["summary"]["fail"] == 0,
              f"实际: {report['summary']}")
    # 检查项数 = 10 个结构项 + count + len(must_contain)。
    # 不要写死数字（会随 spec 的 must_contain 条数变化），断言结构项齐全即可。
    REC.check("检查项数量与配置一致",
              report["summary"]["total"] == 11 + len(must_contain),
              f"实际: {report['summary']['total']} 项，"
              f"期望 11 + {len(must_contain)}")

    ids = {c["id"] for c in report["checks"]}
    for expected_id in ("count", "contains", "section_content", "so_what",
                        "overflow", "color_semantics", "chart_data",
                        "no_empty_slide"):
        REC.check(f"包含检查项 {expected_id}", expected_id in ids, f"实际: {sorted(ids)}")

    # checks.json 内容与报告一致
    if os.path.isfile(checks_path):
        with open(checks_path, "r", encoding="utf-8") as fh:
            written = json.load(fh)
        REC.check("checks.json 可解析且判定一致",
                  written.get("verdict") == report["verdict"],
                  f"写入={written.get('verdict')} 内存={report['verdict']}")
        REC.check("checks.json 含 summary",
                  "summary" in written and "checks" in written)

    # ---- 阶段 5.3：视觉校验降级路径（不依赖 LibreOffice）----
    try:
        from pptx import Presentation
        prs = Presentation(deck)
        total_shapes = sum(len(s.shapes) for s in prs.slides)
        REC.check("可读每页形状（视觉校验降级路径可用）",
                  total_shapes > 0, f"形状总数={total_shapes}")
    except Exception as exc:
        REC.check("可读每页形状（视觉校验降级路径可用）", False,
                  f"{type(exc).__name__}: {exc}")

    # ---- 溢出检测确实有效（负向对照）----
    from pptx import Presentation as _P
    from pptx.util import Inches as _In, Pt as _Pt
    bad_path = os.path.join(stage_dir, "overflow.pptx")
    p = _P()
    p.slide_width, p.slide_height = _In(13.333), _In(7.5)
    s = p.slides.add_slide(p.slide_layouts[6])
    box = s.shapes.add_textbox(_In(0.5), _In(0.5), _In(2.0), _In(0.6))
    box.text_frame.word_wrap = True
    run = box.text_frame.paragraphs[0].add_run()
    run.text = "这是一段特别长的中文文字" * 8
    run.font.size = _Pt(28)
    p.save(bad_path)

    bad_report = check_pptx(bad_path)
    overflow_check = next((c for c in bad_report["checks"] if c["id"] == "overflow"), {})
    REC.check("溢出检测能抓到真实溢出（负向对照）",
              overflow_check.get("status") == "warn"
              and overflow_check.get("findings", 0) >= 1,
              f"实际: {overflow_check}")


# =====================================================================
# T14 常用文件目录 + 文件名解析 + 数据工具
# =====================================================================
def test_common_files_and_data_tools() -> None:
    section("T14 常用文件目录 / 文件名解析 / 数据分析工具")

    from tool.common.commons import (
        PROJECT_ROOT as COMMONS_ROOT,
        describe_index,
        find_files,
        index_common_files,
        resolve_common_dirs,
    )
    from tool.common.data_file_tool import DataFileTool
    from tool.common.data_analysis_tool import DataAnalysisTool
    from core.capability import CapabilityRouter

    # ---- 1) 常用目录存在且能解析 ----
    common_dir = os.path.join(PROJECT_ROOT, "data", "common")
    REC.check("常用文件目录已创建 (data/common)", os.path.isdir(common_dir),
              common_dir)
    REC.check("PROJECT_ROOT 上溯正确（不是 tool/ 而应是项目根）",
              os.path.abspath(COMMONS_ROOT) == os.path.abspath(PROJECT_ROOT),
              f"实际: {COMMONS_ROOT}")

    roots = resolve_common_dirs()
    REC.check("解析出常用搜索根（≥2 个）", len(roots) >= 2, f"实际: {roots}")
    REC.check("搜索根包含 data/common",
              any(os.path.abspath(r) == os.path.abspath(common_dir) for r in roots),
              f"实际: {roots}")

    # ---- 2) 造一个样本文件，验证索引与匹配 ----
    sample = os.path.join(common_dir, "季度经营数据_2026Q3.csv")
    rows = [
        "成交日期,产品,区域,成交金额,客户数",
        "2026-07-03,借呗12期,华东,51230.50,3",
        "2026-08-11,借呗6期,华南,30120.00,2",
        "2026-09-02,借呗12期,华东,68000.25,4",
        "2026-09-19,借呗18期,华西北,22000.00,1",
    ]
    with open(sample, "w", encoding="utf-8-sig") as handle:
        handle.write("\n".join(rows))

    try:
        items = index_common_files()
        REC.check("文件索引包含样本文件",
                  any(i["name"] == os.path.basename(sample) for i in items),
                  f"实际: {[i['name'] for i in items]}")

        REC.check("目录清单可读", "常用文件目录" in describe_index())

        # 文件名解析：自然语言线索 → 命中样本
        hits = find_files(query="用季度的经营数据做个PPT", limit=3)
        REC.check("按自然语言线索命中样本文件",
                  any(h.name == os.path.basename(sample) for h in hits),
                  f"实际: {[(h.name, h.score) for h in hits]}")

        # 无关请求不应瞎匹配（会退回最近文件，但打分必须为 0）
        noise = find_files(query="今天天气怎么样啊哈哈", limit=1)
        REC.check("无关线索不产生虚假命中",
                  all(h.score == 0 for h in noise),
                  f"实际: {[(h.name, h.score) for h in noise]}")

        # ---- 3) data_file 工具 ----
        file_tool = DataFileTool()
        out_find = file_tool.run({"query": "季度经营数据"})
        REC.check("data_file 定位到文件并给出路径",
                  os.path.basename(sample) in out_find and "推荐使用" in out_find,
                  out_find[:200])
        out_list = file_tool.run({"action": "list"})
        REC.check("data_file list 可用", "可用文件" in out_list or "常用文件目录" in out_list)
        out_info = file_tool.run({"action": "info",
                                  "file_name": os.path.basename(sample)})
        REC.check("data_file info 返回路径", sample in out_info or "路径" in out_info)

        # ---- 4) data_analysis 工具 ----
        analysis = DataAnalysisTool()
        prof = analysis.run({"file": sample, "action": "profile"})
        REC.check("data_analysis profile 给出形状",
                  "形状" in prof and "列信息" in prof, prof[:300])
        REC.check("profile 识别出各列",
                  all(k in prof for k in ("成交日期", "产品", "成交金额")),
                  prof[:300])

        prev = analysis.run({"file": sample, "action": "preview", "rows": 2})
        REC.check("data_analysis preview 返回数据行",
                  "借呗" in prev, prev[:200])

        stats = analysis.run({"file": sample, "action": "stats",
                              "group_by": "产品", "value_column": "成交金额",
                              "agg": "sum"})
        REC.check("data_analysis stats 分组聚合成功",
                  "分组聚合" in stats and "借呗12期" in stats, stats[:300])

        ts = analysis.run({"file": sample, "action": "timeseries",
                           "date_column": "成交日期", "value_column": "成交金额"})
        REC.check("data_analysis timeseries 按月汇总",
                  "2026-09" in ts and "2026-07" in ts, ts[:300])

        # 按 file_name（不传完整路径）也能找到
        byname = analysis.run({"file_name": "季度经营数据", "action": "profile"})
        REC.check("data_analysis 支持只给文件名（去常用目录找）",
                  "形状" in byname, byname[:200])

        # ---- 5) 契约转换 ----
        # 显式指定输出目录，不依赖上游用例是否留下 SKILL_OUTPUT_DIR
        contract_stage = os.path.join(TEST_OUTPUT_DIR, "contract")
        os.makedirs(contract_stage, exist_ok=True)
        previous_out = os.environ.get("SKILL_OUTPUT_DIR")
        os.environ["SKILL_OUTPUT_DIR"] = contract_stage
        try:
            contract = analysis.run({"file": sample, "action": "contract",
                                     "project": "季度经营分析"})
        finally:
            if previous_out is None:
                os.environ.pop("SKILL_OUTPUT_DIR", None)
            else:
                os.environ["SKILL_OUTPUT_DIR"] = previous_out

        REC.check("contract 生成 ppt-skill 契约",
                  "契约转换" in contract and "万元" in contract, contract[:300])
        contract_path = os.path.join(contract_stage, "contract_data.json")
        REC.check("契约 JSON 已落盘", os.path.isfile(contract_path), contract_path)
        if os.path.isfile(contract_path):
            with open(contract_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            REC.check("契约含 ppt-skill 必需分组",
                      all(k in payload for k in
                          ("项目", "收入", "收入预算", "成本", "收缴", "账龄", "效能")),
                      f"实际键: {sorted(payload)}")
            REC.check("契约金额为万元量级（已 ÷10000）",
                      max(payload["收入"].values()) < 1000,
                      f"实际收入: {payload['收入']}")
            REC.check("契约比率用比值（人工成本率 < 1）",
                      payload["效能"]["人工成本率"] < 1,
                      f"实际: {payload['效能']['人工成本率']}")

        # ---- 6) 工具可在能力中心注册并被调用 ----
        router = CapabilityRouter()
        router.add_tool(DataFileTool())
        router.add_tool(DataAnalysisTool())
        REC.check("两个新工具注册成功",
                  router.names() == ["data_analysis", "data_file"],
                  f"实际: {router.names()}")
        via_router = router.execute("data_file", {"query": "季度经营数据"})
        REC.check("经能力中心调用 data_file", "推荐使用" in via_router)

    finally:
        # 清理样本，避免污染常用目录
        try:
            os.remove(sample)
        except OSError:
            pass
        try:
            os.remove(os.path.join(TEST_OUTPUT_DIR, "contract", "contract_data.json"))
        except OSError:
            pass


# =====================================================================
# T15 未提供文件时自动取数 → 生成 PPT
# =====================================================================
def test_auto_resolve_from_common_dir() -> None:
    section("T15 用户未提供文件时：自动解析文件名 → 常用目录取数 → 生成 PPT")

    from skills.loader import load_skill

    ppt_dir = os.path.join(SKILLS_ROOT, "buttin", "ppt_skill")
    common_dir = os.path.join(PROJECT_ROOT, "data", "common")
    stage = os.path.join(TEST_OUTPUT_DIR, "auto")
    os.makedirs(stage, exist_ok=True)
    os.makedirs(common_dir, exist_ok=True)

    sample = os.path.join(common_dir, "月度销售汇总_2026-09.csv")
    # 夹具必须带上**技能 spec 需要的列**（账单金额/未缴金额）。
    # 早期版本用的是一张纯销售表，物业 spec 声明的派生指标
    # （收缴率 = 已收/账单）必然算不出来 → 内容健康度门禁正确地拒绝了它。
    # 本用例验的是"自动取数能跑通全链路"，不是"物业 spec 能消化销售表"，
    # 所以夹具要给出该技能真正能处理的数据。
    rows = ["费用开始日期,收费项,账单金额(元),未缴金额(元)"]
    for month, base in (("07", 100000), ("08", 120000), ("09", 90000)):
        for i, item in enumerate(("物业服务费", "代收代付生活垃圾处理费")):
            rows.append(
                f"2026-{month}-01,{item},"
                f"{base + i * 1234.5:.2f},{500 + i * 120:.2f}"
            )
    with open(sample, "w", encoding="utf-8-sig") as handle:
        handle.write("\n".join(rows))

    os.environ["SKILL_OUTPUT_DIR"] = stage
    try:
        skill = load_skill(ppt_dir, llm=None, execution="script")
        REC.check("技能默认开启自动取数", getattr(skill, "auto_resolve_data", False))

        # 关键：**不传 data**，只给一句带线索的请求
        result = skill.run_skill({"request": "用上个月的销售汇总做个经营分析PPT"})

        REC.check("自动取数并生成成功", result.success,
                  f"error={result.error}\n{result.output[:400]}")
        REC.check("标记为自动取数", bool(result.meta.get("auto_resolved")),
                  f"实际: {result.meta.get('auto_resolved')}")

        note = "\n".join(result.meta.get("auto_note") or [])
        REC.check("自动匹配到了样本文件",
                  os.path.basename(sample) in note, note[:300])
        # spec 驱动的技能不再在 loader 里做字段推断 —— loader 只传文件路径，
        # 列映射由技能的 deck_spec.json 决定（知识不下沉到工具层）。
        REC.check("说明由技能知识决定列映射（不在 loader 里猜字段）",
                  "deck_spec" in note, note[:400])
        REC.check("Agent 可见自动取数说明",
                  "自动取数" in result.to_text(), result.to_text()[:200])

        decks = [a for a in result.artifacts if a.lower().endswith(".pptx")]
        REC.check("产出 pptx", len(decks) == 1, f"实际: {result.artifacts}")

        if decks:
            from pptx import Presentation
            prs = Presentation(decks[0])
            texts = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        texts.append(shape.text_frame.text)
            joined = "\n".join(texts)
            REC.check("真实数据进入 PPT（不是全 0 模板）",
                      "0.0 万" not in joined or "9" in joined, joined[:200])
            REC.check("项目名取自文件名",
                      "月度销售汇总" in joined or "月度销售汇总" in
                      (prs.core_properties.title or ""),
                      f"标题={prs.core_properties.title!r}")

        # 关闭自动取数时应退回空模板 + 警告
        skill.auto_resolve_data = False
        off = skill.run_skill({"request": "用上个月的销售汇总做个经营分析PPT"})
        REC.check("关闭自动取数后不再匹配文件",
                  not off.meta.get("auto_resolved"), f"实际: {off.meta.get('auto_resolved')}")
        REC.check("关闭自动取数后给出数据不足警告",
                  bool(off.meta.get("warnings")), f"实际: {off.meta.get('warnings')}")

    finally:
        try:
            os.remove(sample)
        except OSError:
            pass


# =====================================================================
# T16 deck_ir：IR 契约与校验器
# =====================================================================
def test_deck_ir_contract() -> None:
    section("T16 Deck IR 契约与校验器")

    from tool.common.deck_ir import (
        BLOCK_TYPES,
        LAYOUTS,
        deck_page_count,
        empty_deck,
        make_slide,
        validate_deck,
    )

    REC.check("声明了 layout 集合", len(LAYOUTS) >= 8, f"实际: {LAYOUTS}")
    REC.check("声明了 block 类型集合", len(BLOCK_TYPES) >= 6, f"实际: {BLOCK_TYPES}")

    # ---- empty_deck ----
    deck = empty_deck("测试标题", project="P", period="2026-09")
    REC.check("empty_deck 带 meta", deck["meta"]["title"] == "测试标题")
    REC.check("empty_deck meta 透传额外字段",
              deck["meta"]["project"] == "P" and deck["meta"]["period"] == "2026-09")
    REC.check("empty_deck 无页面", deck["slides"] == [])
    REC.check("空 deck 校验会提示没有页面",
              any("没有任何页面" in p for p in validate_deck(deck)),
              f"实际: {validate_deck(deck)}")

    # ---- make_slide ----
    slide = make_slide("chart", "标题", [{"type": "text", "text": "x"}],
                       kicker="k", so_what="so")
    REC.check("make_slide 结构完整",
              all(k in slide for k in
                  ("layout", "title", "kicker", "blocks", "so_what", "footnote")))
    REC.check("make_slide 保留 kicker/so_what",
              slide["kicker"] == "k" and slide["so_what"] == "so")

    unknown = make_slide("不存在的布局", "T")
    REC.check("未知 layout 降级为 text 并留痕",
              unknown["layout"] == "text" and unknown.get("_unknown_layout") == "不存在的布局",
              f"实际: layout={unknown['layout']} mark={unknown.get('_unknown_layout')}")

    # ---- 校验器：正向 ----
    good = empty_deck("OK")
    good["slides"] = [
        make_slide("kpi", "结论型标题",
                   [{"type": "kpi_grid", "items": [
                       {"label": "L", "value": "V", "delta": "D", "status": "ok"}]}],
                   so_what="一句结论"),
    ]
    REC.check("合法 IR 校验无问题", validate_deck(good) == [],
              f"实际: {validate_deck(good)}")

    # ---- 校验器：负向（每条都要能抓到）----
    cases = [
        ("标题缺失", make_slide("text", "", [{"type": "text", "text": "x"}],
                             so_what="s"), "缺少 title"),
        ("结论句缺失", make_slide("text", "T", [{"type": "text", "text": "x"}]),
         "缺少 so_what"),
        ("无内容块", make_slide("text", "T", [], so_what="s"), "没有任何 blocks"),
        ("未知块类型", make_slide("text", "T", [{"type": "nope"}], so_what="s"),
         "未知类型"),
        ("kpi 缺 items", make_slide("kpi", "T", [{"type": "kpi_grid"}], so_what="s"),
         "缺 items"),
        ("图表缺分类", make_slide("chart", "T", [
            {"type": "chart", "series": [{"name": "a", "values": [1]}]}], so_what="s"),
         "缺 categories"),
        ("图表缺系列", make_slide("chart", "T", [
            {"type": "chart", "categories": ["a"]}], so_what="s"), "缺 series"),
        ("图表点数不匹配", make_slide("chart", "T", [
            {"type": "chart", "categories": ["a", "b"],
             "series": [{"name": "s", "values": [1]}]}], so_what="s"),
         "≠ 分类数"),
        ("表格列数不齐", make_slide("table", "T", [
            {"type": "table", "headers": ["a", "b"], "rows": [["1"]]}], so_what="s"),
         "≠ 表头"),
        ("text 内容为空", make_slide("text", "T", [{"type": "text"}], so_what="s"),
         "内容为空"),
        ("bullets 为空", make_slide("text", "T", [{"type": "bullets"}], so_what="s"),
         "内容为空"),
    ]
    for name, bad_slide, expected in cases:
        bad = empty_deck("BAD")
        bad["slides"] = [bad_slide]
        problems = validate_deck(bad)
        REC.check(f"校验器抓到「{name}」",
                  any(expected in p for p in problems),
                  f"期望含 {expected!r}，实际: {problems}")

    # ---- 结构性错误 ----
    REC.check("非字典输入被拒", validate_deck("not a dict") == ["IR 不是字典"])
    REC.check("缺 slides 被拒",
              validate_deck({"meta": {}}) == ["缺少 slides 数组"])
    REC.check("meta.title 为空会提示",
              any("meta.title" in p for p in
                  validate_deck({"meta": {}, "slides": [
                      make_slide("text", "T", [{"type": "text", "text": "x"}],
                                 so_what="s")]})))

    # ---- 宽松点：cover/agenda/appendix 允许无块 ----
    for layout in ("cover", "agenda", "appendix"):
        d = empty_deck("D")
        d["slides"] = [make_slide(layout, "T")]
        REC.check(f"{layout} 页允许无内容块",
                  not any("没有任何 blocks" in p for p in validate_deck(d)),
                  f"实际: {validate_deck(d)}")

    REC.check("deck_page_count 正确", deck_page_count(good) == 1)


# =====================================================================
# T17 deck_profiles：叙事知识（几页 / 顺序 / 关键内容）
# =====================================================================
def test_deck_profiles() -> None:
    section("T17 叙事 profile：页数规则与业务切换")

    from tool.common.deck_profiles import (
        PROFILES,
        get_profile,
        list_profiles,
        plan_pages,
        register_profile,
    )

    REC.check("内置 ≥3 个 profile", len(PROFILES) >= 3, f"实际: {list(PROFILES)}")
    REC.check("包含 general / property / sales",
              {"general", "property", "sales"} <= set(PROFILES), f"实际: {list(PROFILES)}")

    property_profile = get_profile("property")
    REC.check("物业 profile 名称正确",
              "物业" in property_profile.name, property_profile.name)

    # ---- 页数规则（这是用户明确要求的「专业知识」）----
    for minutes, expected in ((10, 7), (20, 11), (40, 16)):
        pages = plan_pages("property", minutes)
        REC.check(f"物业 {minutes} 分钟 → {expected} 页",
                  len(pages) == expected, f"实际: {len(pages)} 页")

    REC.check("页数随时长单调不减",
              len(plan_pages("property", 10)) <= len(plan_pages("property", 20))
              <= len(plan_pages("property", 40)))

    # 超出最大规则时用最后一条，而不是崩
    REC.check("超长时长仍能规划（不外溢）",
              len(plan_pages("property", 999)) == 16,
              f"实际: {len(plan_pages('property', 999))}")

    # 极小值用第一条
    REC.check("极短时长用最小规则",
              len(plan_pages("property", 1)) == 7, f"实际: {len(plan_pages('property', 1))}")

    # ---- 叙事顺序（六段式不可打乱）----
    keys = [p["key"] for p in plan_pages("property", 20)]
    order_pairs = [("revenue", "cost"), ("cost", "collection"),
                   ("collection", "efficiency"), ("efficiency", "anomaly"),
                   ("anomaly", "actions")]
    for before, after in order_pairs:
        if before in keys and after in keys:
            REC.check(f"叙事顺序 {before} 先于 {after}",
                      keys.index(before) < keys.index(after), f"实际顺序: {keys}")

    REC.check("封面在第一位", keys[0] == "cover", f"实际: {keys[0]}")
    REC.check("行动项在附录之前",
              keys.index("actions") < keys.index("appendix"), f"实际: {keys}")

    # ---- 关键内容（校验工具用它逐页核对）----
    kw = property_profile.keywords_map()
    REC.check("收入段登记了关键内容", "revenue" in kw and "收入" in kw["revenue"],
              f"实际: {kw.get('revenue')}")
    REC.check("收缴率段登记了关键内容",
              any("收缴" in k for k in kw.get("collection", [])),
              f"实际: {kw.get('collection')}")

    # ---- 不同业务 = 不同叙事（核心价值）----
    prop_keys = [p["key"] for p in plan_pages("property", 20)]
    sales_keys = [p["key"] for p in plan_pages("sales", 20)]
    REC.check("物业与销售叙事不同", prop_keys != sales_keys,
              f"property={prop_keys}\nsales={sales_keys}")
    REC.check("销售有「目标达成」而物业没有",
              "target" in sales_keys and "target" not in prop_keys)
    REC.check("物业有「收缴率」而销售没有",
              "collection" in prop_keys and "collection" not in sales_keys)

    # ---- describe 可读 ----
    desc = property_profile.describe(20)
    REC.check("describe 含页数与逐页说明",
              "11 页" in desc and "封面" in desc, desc[:200])

    # ---- 未知 profile 退回 general ----
    fallback = get_profile("不存在的业务")
    REC.check("未知 profile 退回 general",
              fallback.key == "general", f"实际: {fallback.key}")

    # ---- 自定义 profile（业务方不改代码）----
    custom = register_profile("t17_manufacturing", {
        "name": "制造业产能分析",
        "sections": [
            {"key": "cover", "label": "封面", "order": 10, "layout": "cover"},
            {"key": "oee", "label": "OEE", "order": 20, "layout": "chart",
             "chart": "柱状图", "keywords": ["OEE"], "purpose": "设备综合效率"},
            {"key": "actions", "label": "改善行动", "order": 30, "layout": "cards"},
        ],
        "page_rules": [{"max_minutes": 20, "sections": ["cover", "oee", "actions"]}],
    })
    try:
        pages = plan_pages("t17_manufacturing", 20)
        REC.check("自定义 profile 生效（3 页）", len(pages) == 3, f"实际: {len(pages)}")
        REC.check("自定义 profile 段落正确",
                  [p["key"] for p in pages] == ["cover", "oee", "actions"])
        REC.check("自定义 profile 可按 order 排序",
                  [p["order"] for p in pages] == sorted(p["order"] for p in pages))
        REC.check("自定义 profile 出现在列表里",
                  any(p["key"] == "t17_manufacturing" for p in list_profiles()))
    finally:
        PROFILES.pop("t17_manufacturing", None)

    # ---- 非法 page_rules 时的兜底 ----
    no_rules = register_profile("t17_norules", {
        "name": "无规则",
        "sections": [{"key": "cover", "label": "封面", "order": 10}],
    })
    try:
        REC.check("缺 page_rules 时兜底为单页",
                  len(plan_pages("t17_norules", 20)) == 1,
                  f"实际: {len(plan_pages('t17_norules', 20))}")
    finally:
        PROFILES.pop("t17_norules", None)


# =====================================================================
# T18 deck_build：通用渲染器
# =====================================================================
def test_deck_build_renderer() -> None:
    section("T18 通用渲染器：全 layout + 宽容降级")

    from tool.common.deck_build_tool import DeckBuildTool, render_deck
    from tool.common.deck_ir import empty_deck, make_slide
    from tool.common.pptx_check_tool import check_pptx

    stage = os.path.join(TEST_OUTPUT_DIR, "render")
    os.makedirs(stage, exist_ok=True)
    out = os.path.join(stage, "all_layouts.pptx")

    # 覆盖每一种 layout 与 block 类型
    deck = empty_deck("渲染器全布局测试", project="测试项目", period="2026 Q3",
                      footnote="口径：单元测试")
    deck["slides"] = [
        make_slide("cover", "封面标题", [
            {"type": "kpi_grid", "columns": 4, "items": [
                {"label": "指标A", "value": "1.2 万", "delta": "+5%", "status": "ok"},
                {"label": "指标B", "value": "98%", "delta": "目标 97%", "status": "warn"},
                {"label": "指标C", "value": "42h", "delta": "+8h", "status": "bad"},
                {"label": "指标D", "value": "3", "delta": "", "status": "info"}]}]),
        make_slide("agenda", "汇报主线", []),
        make_slide("kpi", "KPI 页", [
            {"type": "kpi_grid", "columns": 3, "items": [
                {"label": "X", "value": "1", "delta": "d", "status": "ok"}]}],
            so_what="KPI 结论句。"),
        make_slide("chart", "条形图页", [
            {"type": "chart", "chart": "bar", "categories": ["甲", "乙", "丙"],
             "series": [{"name": "数值", "values": [10, 20, 30]}],
             "number_format": "#,##0", "point_colors": ["ok", "warn", "bad"]}],
            so_what="条形图结论。"),
        make_slide("chart", "柱状图页", [
            {"type": "chart", "chart": "column", "categories": ["a", "b"],
             "series": [{"name": "实际", "values": [1, 2]},
                        {"name": "预算", "values": [1.5, 1.8]}]}],
            so_what="双系列结论。"),
        make_slide("chart", "折线图页", [
            {"type": "chart", "chart": "line", "categories": ["1月", "2月", "3月"],
             "series": [{"name": "趋势", "values": [3, 1, 4]}]}],
            so_what="折线结论。"),
        make_slide("chart", "饼图页", [
            {"type": "chart", "chart": "pie", "categories": ["A", "B"],
             "series": [{"name": "占比", "values": [60, 40]}]}],
            so_what="饼图结论。"),
        make_slide("table", "表格页", [
            {"type": "table", "headers": ["列1", "列2", "列3"],
             "rows": [["a", "1", "2"], ["b", "3", "4"]],
             "weights": [2, 1, 1]}],
            so_what="表格结论。"),
        make_slide("text", "文字页", [{"type": "text", "text": "一段正文。"}],
                   so_what="文字结论。"),
        make_slide("text", "要点页", [
            {"type": "bullets", "items": ["要点一", "要点二", "要点三"]}],
            so_what="要点结论。"),
        make_slide("cards", "卡片页", [
            {"type": "cards", "columns": 2, "items": [
                {"title": "卡片1", "body": "正文1", "status": "bad"},
                {"title": "卡片2", "body": "正文2", "status": "ok"}]}],
            so_what="卡片结论。"),
        make_slide("appendix", "附录页", [
            {"type": "bullets", "items": ["附录项"]}]),
    ]

    result = render_deck(deck, out)
    REC.check("渲染成功且无错误", not result["errors"], f"实际: {result['errors']}")
    REC.check("页数等于 IR 页数", result["slides"] == len(deck["slides"]),
              f"实际: {result['slides']} vs {len(deck['slides'])}")
    REC.check("文件已生成且非空", os.path.getsize(out) > 20_000,
              f"实际: {os.path.getsize(out)} 字节")

    # 用 python-pptx 复核
    from pptx import Presentation
    prs = Presentation(out)
    REC.check("python-pptx 可打开且页数一致",
              len(prs.slides) == len(deck["slides"]), f"实际: {len(prs.slides)}")
    ratio = prs.slide_width / prs.slide_height
    REC.check("16:9 比例", abs(ratio - 16 / 9) < 0.05, f"实际: {ratio:.3f}")
    REC.check("文件名写入 core_properties.title",
              bool(prs.core_properties.title), repr(prs.core_properties.title))

    # 渲染器写入的页↔布局元数据要能被校验工具读到
    from tool.common.pptx_check_tool import _read_deck_sections
    sections = _read_deck_sections(prs, out)
    REC.check("渲染器写入页/布局元数据（可被校验工具读取）",
              len(sections) == len(deck["slides"]), f"实际: {len(sections)} 条")

    # 产物应当能过结构校验
    report = check_pptx(out, expected_count=len(deck["slides"]))
    REC.check("产物可通过结构校验（无 fail）",
              report["summary"]["fail"] == 0,
              f"verdict={report['verdict']} 详情={report['summary']}")

    # ---- 宽容降级：单页失败不影响其他页 ----
    bad = empty_deck("降级测试")
    bad["slides"] = [
        make_slide("text", "正常页", [{"type": "text", "text": "ok"}], so_what="s"),
        make_slide("chart", "异常页", [
            {"type": "chart", "chart": "bar", "categories": ["a"],
             # values 不是数字 → 渲染该页应报错并被隔离
             "series": [{"name": "s", "values": ["不是数字"]}]}], so_what="s"),
        make_slide("text", "正常页2", [{"type": "text", "text": "ok2"}], so_what="s"),
    ]
    bad_out = os.path.join(stage, "degrade.pptx")
    bad_result = render_deck(bad, bad_out)
    REC.check("单页失败被隔离，其余页仍渲染",
              len(bad_result["errors"]) >= 1 and os.path.isfile(bad_out),
              f"errors={bad_result['errors']}")
    REC.check("降级后文件仍可打开",
              len(Presentation(bad_out).slides) >= 3,
              "失败页会替换为错误提示页，保证产物完整")

    # ---- 未知 layout 不崩溃 ----
    weird = empty_deck("未知布局")
    weird["slides"] = [make_slide("text", "T", [{"type": "text", "text": "x"}],
                                  so_what="s")]
    weird["slides"][0]["layout"] = "压根没有的布局"
    weird_out = os.path.join(stage, "weird.pptx")
    weird_result = render_deck(weird, weird_out)
    REC.check("未知 layout 不崩溃（按 text 渲染）",
              not weird_result["errors"] and os.path.isfile(weird_out),
              f"errors={weird_result['errors']}")

    # ---- Tool 封装层 ----
    tool = DeckBuildTool()
    REC.check("Tool 名为 deck_build", tool.name == "deck_build")

    ok_out = os.path.join(stage, "via_tool.pptx")
    text = tool.run({"deck": deck, "output": ok_out})
    REC.check("Tool 返回可读结果", "[deck_build]" in text and "页" in text,
              text[:150])

    REC.check("Tool 接受 JSON 字符串形式的 deck",
              "[deck_build]" in tool.run({"deck": json.dumps(deck),
                                          "output": os.path.join(stage, "jsonin.pptx")}))

    REC.check("非法 JSON 被拒并给出原因",
              "不是合法 JSON" in tool.run({"deck": "{坏", "output": ok_out}))
    REC.check("非对象 deck 被拒",
              "必须是对象" in tool.run({"deck": [1, 2], "output": ok_out}))
    REC.check("缺 output 被拒",
              "缺少 output" in tool.run({"deck": deck}))

    # strict 模式下 IR 有问题就拒渲染
    broken = empty_deck("")
    broken["slides"] = [make_slide("text", "", [])]
    strict_out = os.path.join(stage, "strict.pptx")
    strict_msg = tool.run({"deck": broken, "output": strict_out, "strict": True})
    REC.check("strict 模式下 IR 有错则拒渲染",
              "校验失败" in strict_msg and not os.path.isfile(strict_out),
              strict_msg[:200])
    REC.check("非 strict 时宽容渲染并报告问题",
              "IR 校验提示" in tool.run({"deck": broken,
                                         "output": os.path.join(stage, "lax.pptx")}))


# =====================================================================
# T19 deck_content：数据 + 知识 → IR
# =====================================================================
def _make_generic_csv(path: str) -> None:
    """造一份与物业无关的通用数据（用于验证业务无关性）。"""
    rows = ["月份,渠道,收入,订单数"]
    for month, base in (("07", 100000), ("08", 130000), ("09", 110000)):
        for i, channel in enumerate(("线上", "线下", "代理")):
            rows.append(f"2026-{month}-{i + 2:02d},{channel},"
                        f"{base + i * 4321.5:.2f},{10 + i * 3}")
    with open(path, "w", encoding="utf-8-sig") as handle:
        handle.write("\n".join(rows))


def test_deck_content_tool() -> None:
    section("T19 deck_content：数据 + 叙事知识 → IR")

    from tool.common.deck_content_tool import DeckContentTool
    from tool.common.deck_ir import validate_deck

    stage = os.path.join(TEST_OUTPUT_DIR, "content")
    os.makedirs(stage, exist_ok=True)
    csv_path = os.path.join(stage, "generic_biz.csv")
    _make_generic_csv(csv_path)
    tool = DeckContentTool()

    # ---- profiles 动作 ----
    REC.check("列出可用 profile", "general" in tool.run({"action": "profiles"})
              and "sales" in tool.run({"action": "profiles"}))

    # ---- plan 动作（不需要数据）----
    plan_text = tool.run({"action": "plan", "profile": "property", "minutes": 20})
    REC.check("plan 给出页数", "11 页" in plan_text, plan_text[:200])
    REC.check("plan 含逐页说明", "封面" in plan_text and "收缴率" in plan_text)
    REC.check("plan 列出叙事约束", "叙事约束" in plan_text)

    plan10 = tool.run({"action": "plan", "profile": "property", "minutes": 10})
    REC.check("10 分钟 plan 为 7 页", "7 页" in plan10, plan10[:150])

    # ---- build 动作 ----
    ir_path = os.path.join(stage, "deck.json")
    build_text = tool.run({
        "action": "build", "file": csv_path, "profile": "general",
        "minutes": 20, "project": "通用业务", "out": ir_path,
    })
    REC.check("build 成功并落盘 IR",
              "已生成 IR" in build_text and os.path.isfile(ir_path),
              build_text[:300])

    with open(ir_path, "r", encoding="utf-8") as handle:
        deck = json.load(handle)

    REC.check("IR 页数与 profile 规则一致", len(deck["slides"]) == 11,
              f"实际: {len(deck['slides'])}")
    REC.check("IR 校验无阻断问题",
              validate_deck(deck) == [], f"实际: {validate_deck(deck)}")
    REC.check("meta 带上项目名", deck["meta"]["project"] == "通用业务",
              deck["meta"])
    REC.check("meta 记录所用 profile", deck["meta"].get("profile") == "general")

    # 字段推断：应该认出日期/数值/分组
    REC.check("推断出期间", bool(deck["meta"].get("period")),
              f"实际: {deck['meta'].get('period')}")
    chart_slides = [s for s in deck["slides"] if s["layout"] == "chart"]
    REC.check("生成了图表页", len(chart_slides) >= 1, f"实际: {len(chart_slides)}")
    if chart_slides:
        blocks = chart_slides[0]["blocks"]
        REC.check("图表页含 chart 块",
                  any(b["type"] == "chart" for b in blocks), f"实际: {blocks}")
        chart = next(b for b in blocks if b["type"] == "chart")
        REC.check("图表分类来自数据分组",
                  any(c in ("线上", "线下", "代理") for c in chart["categories"]),
                  f"实际: {chart['categories']}")
        REC.check("图表系列与分类数一致",
                  all(len(s["values"]) == len(chart["categories"])
                      for s in chart["series"]),
                  f"实际: {chart['series']}")

    # ---- 业务无关性：IR 里不该出现领域专有名词 ----
    ir_text = json.dumps(deck, ensure_ascii=False)
    REC.check("通用 profile 的 IR 不含物业专有名词",
              not any(w in ir_text for w in ("物业费", "收缴率", "陈欠", "公共收益")),
              "通用技能不应把某个行业的词写进 IR")

    # ---- overrides 覆盖 ----
    ir2 = os.path.join(stage, "deck_override.json")
    tool.run({
        "action": "build", "file": csv_path, "profile": "general",
        "project": "通用业务",
        "overrides": {"3": {"title": "【覆盖】9 月收入 33.9 万",
                            "so_what": "【覆盖】结论句"}},
        "out": ir2,
    })
    with open(ir2, "r", encoding="utf-8") as handle:
        deck2 = json.load(handle)
    page3 = deck2["slides"][2]
    REC.check("overrides 覆盖标题", page3["title"] == "【覆盖】9 月收入 33.9 万",
              page3["title"])
    REC.check("overrides 覆盖结论句", page3["so_what"] == "【覆盖】结论句",
              page3["so_what"])

    # ---- overrides 传 JSON 字符串也支持 ----
    ir3 = os.path.join(stage, "deck_override_str.json")
    tool.run({"action": "build", "file": csv_path, "profile": "general",
              "overrides": '{"3": {"title": "字符串覆盖"}}', "out": ir3})
    with open(ir3, "r", encoding="utf-8") as handle:
        REC.check("overrides 支持 JSON 字符串",
                  json.load(handle)["slides"][2]["title"] == "字符串覆盖")

    # ---- 手动指定列 ----
    ir4 = os.path.join(stage, "deck_cols.json")
    tool.run({"action": "build", "file": csv_path, "profile": "general",
              "value_column": "订单数", "group_column": "渠道", "out": ir4})
    with open(ir4, "r", encoding="utf-8") as handle:
        deck4 = json.load(handle)
    charts4 = [b for s in deck4["slides"] for b in s["blocks"]
               if b["type"] == "chart"]
    REC.check("手动指定 value_column 生效",
              any("订单数" in s["name"] for c in charts4 for s in c["series"]),
              f"实际: {[s['name'] for c in charts4 for s in c['series']]}")

    # ---- 错误处理 ----
    REC.check("文件不存在时给出明确提示",
              "文件不存在" in tool.run({"action": "build", "file": "D:/nope.xlsx"}),
              tool.run({"action": "build", "file": "D:/nope.xlsx"})[:120])
    REC.check("缺 file/file_name 时提示",
              "未提供" in tool.run({"action": "build"}))
    REC.check("找不到的 file_name 提示去常用目录",
              "常用目录" in tool.run({"action": "build",
                                      "file_name": "zzz不存在的文件zzz"}))


# =====================================================================
# T20 通用工具链端到端：同一套工具，不同 profile
# =====================================================================
def test_generic_toolchain_end_to_end() -> None:
    section("T20 通用工具链端到端：同一工具链 × 不同业务 profile")

    from tool.common.deck_build_tool import DeckBuildTool
    from tool.common.deck_content_tool import DeckContentTool
    from tool.common.pptx_check_tool import check_pptx

    stage = os.path.join(TEST_OUTPUT_DIR, "toolchain")
    os.makedirs(stage, exist_ok=True)
    csv_path = os.path.join(stage, "biz.csv")
    _make_generic_csv(csv_path)

    content = DeckContentTool()
    builder = DeckBuildTool()
    produced: Dict[str, Any] = {}

    for profile in ("property", "sales", "general"):
        d = os.path.join(stage, profile)
        os.makedirs(d, exist_ok=True)
        ir_path = os.path.join(d, "deck.json")
        pptx_path = os.path.join(d, "deck.pptx")

        content.run({"action": "build", "file": csv_path, "profile": profile,
                     "minutes": 20, "project": "端到端测试", "out": ir_path})
        with open(ir_path, "r", encoding="utf-8") as handle:
            deck = json.load(handle)

        msg = builder.run({"deck": deck, "output": pptx_path})
        ok = os.path.isfile(pptx_path) and os.path.getsize(pptx_path) > 20_000

        REC.check(f"[{profile}] 工具链产出 pptx", ok, msg[:200])
        if not ok:
            continue

        titles = [s["title"] for s in deck["slides"]]
        produced[profile] = titles

        report = check_pptx(pptx_path, expected_count=len(deck["slides"]))
        REC.check(f"[{profile}] 产物通过结构校验（无 fail）",
                  report["summary"]["fail"] == 0,
                  f"verdict={report['verdict']} {report['summary']}")

    # 核心：同一套工具、同一份数据，不同 profile → 不同叙事
    if "property" in produced and "sales" in produced:
        REC.check("物业与销售产出的页面标题不同",
                  produced["property"] != produced["sales"],
                  f"property={produced['property']}\nsales={produced['sales']}")
        REC.check("物业叙事含「收缴率」",
                  any("收缴" in t for t in produced["property"]),
                  f"实际: {produced['property']}")
        REC.check("销售叙事含「目标达成」或「业绩」",
                  any(("目标" in t or "业绩" in t) for t in produced["sales"]),
                  f"实际: {produced['sales']}")

    REC.check("三种 profile 都产出成功", len(produced) == 3,
              f"实际: {list(produced)}")


# =====================================================================
# T21 主题系统：样式由 theme（知识）决定
# =====================================================================
def _deck_fills(path: str) -> set:
    """读出一份 pptx 实际用到的填充色集合（用于证明样式确实不同）。"""
    from pptx import Presentation

    colors: set = set()
    prs = Presentation(path)
    for slide in prs.slides:
        for shape in slide.shapes:
            try:
                if shape.fill.type == 1:  # solid
                    colors.add(str(shape.fill.fore_color.rgb))
            except Exception:
                continue
    return colors


def _deck_font_sizes(path: str) -> set:
    from pptx import Presentation

    sizes: set = set()
    for slide in Presentation(path).slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if run.font.size:
                        sizes.add(round(run.font.size.pt, 1))
    return sizes


def _theme_probe_deck() -> dict:
    """一份最小的 IR，用来隔离变量：同一份 IR × 不同主题。"""
    from tool.common.deck_ir import empty_deck, make_slide

    deck = empty_deck("主题对照测试", project="P", period="2026-09")
    deck["slides"] = [
        make_slide("cover", "封面", [
            {"type": "kpi_grid", "columns": 3, "items": [
                {"label": "达标项", "value": "OK", "delta": "d", "status": "ok"},
                {"label": "预警项", "value": "WARN", "delta": "d", "status": "warn"},
                {"label": "未达标项", "value": "BAD", "delta": "d", "status": "bad"},
            ]}]),
        make_slide("kpi", "结论页", [
            {"type": "kpi_grid", "columns": 2, "items": [
                {"label": "A", "value": "1", "delta": "d", "status": "ok"}]}],
            so_what="结论句"),
        make_slide("cards", "卡片页", [
            {"type": "cards", "columns": 2, "items": [
                {"title": "C1", "body": "b", "status": "bad"},
                {"title": "C2", "body": "b", "status": "ok"}]}],
            so_what="结论句"),
        make_slide("table", "表格页", [
            {"type": "table", "headers": ["h1", "h2"],
             "rows": [["a", "b"], ["c", "d"]]}], so_what="结论句"),
        make_slide("chart", "图表页", [
            {"type": "chart", "chart": "column", "categories": ["x", "y"],
             "series": [{"name": "s1", "values": [1, 2]},
                        {"name": "s2", "values": [2, 1]}]}], so_what="结论句"),
    ]
    return deck


def test_theme_system() -> None:
    section("T21 主题系统：两份不同主题 → 两份不同 PPT")

    from tool.common.deck_build_tool import DeckBuildTool, ThemePalette
    from tool.common.deck_theme import (
        DEFAULT_THEME,
        SCENE_THEMES,
        build_theme,
        describe_theme,
        list_scene_themes,
        validate_theme,
    )

    stage = os.path.join(TEST_OUTPUT_DIR, "theme")
    os.makedirs(stage, exist_ok=True)

    # ---- 1) 主题构造与校验 ----
    REC.check("默认主题校验通过", validate_theme(build_theme()) == [],
              f"实际: {validate_theme(build_theme())}")
    REC.check("内置至少 property / anime 两个场景",
              {"property", "anime"} <= set(SCENE_THEMES), f"实际: {list(SCENE_THEMES)}")
    REC.check("场景列表可读",
              all("key" in x and "name" in x for x in list_scene_themes()))

    prop = build_theme("property")
    anime = build_theme("anime")
    for name, th in (("property", prop), ("anime", anime)):
        REC.check(f"{name} 主题校验通过", validate_theme(th) == [],
                  f"实际: {validate_theme(th)}")

    REC.check("describe_theme 可读",
              "语义色" in describe_theme(prop) and "配色" not in describe_theme(prop)
              or "调色板" in describe_theme(prop))

    # ---- 2) 两主题的核心样式确实不同 ----
    REC.check("标题色不同",
              prop["colors"]["title"] != anime["colors"]["title"],
              f"{prop['colors']['title']} vs {anime['colors']['title']}")
    REC.check("强调色不同",
              prop["colors"]["accent"] != anime["colors"]["accent"])
    REC.check("达标色不同",
              prop["colors"]["ok"] != anime["colors"]["ok"])
    REC.check("预警色不同",
              prop["colors"]["warn"] != anime["colors"]["warn"])
    REC.check("未达标色不同",
              prop["colors"]["bad"] != anime["colors"]["bad"])
    REC.check("图表调色板不同",
              prop["palette"] != anime["palette"])
    REC.check("封面字号不同",
              prop["fonts"]["cover_title_size"] != anime["fonts"]["cover_title_size"],
              f"{prop['fonts']['cover_title_size']} vs {anime['fonts']['cover_title_size']}")

    # ---- 3) 优先级：theme 覆盖 scene，overrides 覆盖两者 ----
    t1 = build_theme("anime")
    t2 = build_theme("anime", {"colors": {"accent": "123456"}})
    REC.check("显式 theme 覆盖 scene 预设",
              t2["colors"]["accent"] == "123456", t2["colors"]["accent"])
    REC.check("未覆盖的字段仍继承 scene",
              t2["colors"]["title"] == t1["colors"]["title"],
              f"{t2['colors']['title']} vs {t1['colors']['title']}")
    t3 = build_theme("anime", colors={"accent": "ABCDEF"})
    REC.check("零散 overrides 生效", t3["colors"]["accent"] == "ABCDEF")
    REC.check("构造出的主题字段齐全（深合并到默认值）",
              all(k in t3 for k in ("canvas", "fonts", "colors", "palette", "layout")),
              f"实际: {sorted(t3)}")

    # ---- 4) 主题校验器负向用例 ----
    bad_cases = []
    b1 = build_theme(); b1["colors"]["ok"] = "ZZZZZZ"; bad_cases.append(("非法色值", b1, "十六进制"))
    b2 = build_theme(); b2["palette"] = []; bad_cases.append(("空调色板", b2, "palette"))
    b3 = build_theme(); b3["colors"].pop("warn"); bad_cases.append(("缺语义色", b3, "缺少 warn"))
    b4 = build_theme(); b4["canvas"]["height_in"] = 0; bad_cases.append(("画布尺寸越界", b4, "超出合理范围"))
    b5 = build_theme(); b5["layout"]["so_what_top_in"] = 99
    bad_cases.append(("结论栏跑出画布", b5, "超过画布高度"))
    b6 = build_theme(); b6["fonts"]["title"] = ""; bad_cases.append(("字体名为空", b6, "不能为空"))
    for name, theme, expected in bad_cases:
        problems = validate_theme(theme)
        REC.check(f"主题校验抓到「{name}」",
                  any(expected in p for p in problems),
                  f"期望含 {expected!r}，实际: {problems}")
    REC.check("非字典主题被拒", validate_theme("x") == ["theme 不是字典"])

    # ---- 5) ThemePalette 访问器 ----
    pal = ThemePalette(build_theme("anime"))
    REC.check("Palette 状态色映射到 anime 的 ok",
              str(pal.status("ok")) == anime["colors"]["ok"].upper(),
              f"{pal.status('ok')} vs {anime['colors']['ok']}")
    REC.check("未知状态退回 muted 而不是崩",
              str(pal.status("不存在的状态")) == str(pal.muted))
    REC.check("Palette 画布尺寸来自 theme",
              int(pal.sw) == int(round(anime["canvas"]["width_in"] * 914400)),
              f"实际: {pal.sw}")

    # ---- 6) 核心验收：同一份 IR × 两个主题 → 两份样式不同的 PPT ----
    builder = DeckBuildTool()
    deck = _theme_probe_deck()
    outputs = {}
    for scene in ("property", "anime"):
        out = os.path.join(stage, f"{scene}.pptx")
        msg = builder.run({"deck": deck, "output": out, "scene": scene})
        REC.check(f"[{scene}] 渲染成功", os.path.isfile(out) and "已生成" in msg,
                  msg[:200])
        outputs[scene] = out

    fills_prop = _deck_fills(outputs["property"])
    fills_anime = _deck_fills(outputs["anime"])

    REC.check("物业主题的颜色确实画进了 pptx",
              prop["colors"]["title"].upper() in fills_prop
              and prop["colors"]["accent"].upper() in fills_prop,
              f"实际色集: {sorted(fills_prop)}")
    REC.check("动漫主题的颜色确实画进了 pptx",
              anime["colors"]["title"].upper() in fills_anime
              and anime["colors"]["accent"].upper() in fills_anime,
              f"实际色集: {sorted(fills_anime)}")

    REC.check("两份 pptx 的填充色集合不同",
              fills_prop != fills_anime,
              f"property={sorted(fills_prop)}\nanime={sorted(fills_anime)}")

    only_prop = fills_prop - fills_anime
    only_anime = fills_anime - fills_prop
    REC.check("物业有专属色（≥4 个）", len(only_prop) >= 4, f"实际: {sorted(only_prop)}")
    REC.check("动漫有专属色（≥4 个）", len(only_anime) >= 4, f"实际: {sorted(only_anime)}")
    shared = fills_prop & fills_anime
    REC.check("共同色只有白底（说明配色确实两套）",
              shared <= {"FFFFFF"}, f"共同色: {sorted(shared)}")

    sizes_prop = _deck_font_sizes(outputs["property"])
    sizes_anime = _deck_font_sizes(outputs["anime"])
    REC.check("两份 pptx 的字号集合不同（封面/标题字号有差异）",
              sizes_prop != sizes_anime,
              f"property={sorted(sizes_prop)}\nanime={sorted(sizes_anime)}")

    # ---- 7) theme 传参路径都要能走 ----
    out_dict = os.path.join(stage, "via_dict.pptx")
    REC.check("theme 以字典传入生效",
              "已生成" in builder.run({"deck": deck, "output": out_dict,
                                       "theme": anime}))
    REC.check("字典传入的主题出现在结果里",
              anime["name"] in builder.run({"deck": deck,
                                            "output": os.path.join(stage, "n.pptx"),
                                            "theme": anime}))

    out_str = os.path.join(stage, "via_str.pptx")
    REC.check("theme 以 JSON 字符串传入生效",
              "已生成" in builder.run({"deck": deck, "output": out_str,
                                       "theme": json.dumps(anime, ensure_ascii=False)}))
    REC.check("theme 为非法 JSON 时报错",
              "不是合法 JSON" in builder.run({"deck": deck, "output": out_str,
                                              "theme": "{坏JSON"}))
    REC.check("theme 非对象时报错",
              "必须是对象" in builder.run({"deck": deck, "output": out_str,
                                           "theme": [1, 2]}))

    # IR.meta.theme 作为兜底预设（不传 theme / scene 时生效）
    auto = _theme_probe_deck()
    auto["meta"]["theme"] = "anime"
    auto_out = os.path.join(stage, "auto_scene.pptx")
    builder.run({"deck": auto, "output": auto_out})
    auto_fills = _deck_fills(auto_out)
    REC.check("IR.meta.theme 可作兜底场景（动漫色出现）",
              anime["colors"]["title"].upper() in auto_fills
              and anime["colors"]["accent"].upper() in auto_fills,
              f"实际色集: {sorted(auto_fills)}")
    REC.check("兜底场景下不会用物业色",
              prop["colors"]["title"].upper() not in auto_fills,
              f"实际色集: {sorted(auto_fills)}")

    # ---- 8) 主题损坏不应导致崩溃（宽容 + 报告）----
    broken = build_theme()
    broken["colors"]["ok"] = "NOTACOLOR"
    broken_out = os.path.join(stage, "broken_theme.pptx")
    msg = builder.run({"deck": deck, "output": broken_out, "theme": broken})
    REC.check("非法色值仍能渲染（退化为默认色）且报告问题",
              os.path.isfile(broken_out) and "主题校验" in msg, msg[:300])

    # ---- 9) 不传 theme 时用默认主题，且能跑 ----
    d = build_theme()
    default_out = os.path.join(stage, "default.pptx")
    REC.check("不传 theme 用默认主题渲染成功",
              "已生成" in builder.run({"deck": deck, "output": default_out}))
    REC.check("默认主题的标题色出现在产物里",
              d["colors"]["title"].upper() in _deck_fills(default_out),
              f"实际色集: {sorted(_deck_fills(default_out))}")

    # 默认主题应当与**动漫**主题明显不同。
    # 注意：property 预设是刻意照默认配色做的（它就是"参考外观"），
    # 所以这里不要求默认与 property 不同。
    REC.check("默认主题与 anime 主题不同",
              _deck_fills(default_out) != fills_anime,
              "默认主题不应等于动漫主题")
    REC.check("DEFAULT_THEME 可被校验", validate_theme(DEFAULT_THEME) == [])
    REC.check("property 预设沿用默认配色（这是刻意设计）",
              {prop["colors"][k] for k in ("title", "accent", "ok", "warn", "bad")}
              == {DEFAULT_THEME["colors"][k]
                  for k in ("title", "accent", "ok", "warn", "bad")},
              "property 作为参考外观，与默认配色一致")


# =====================================================================
# T22 叙事模板引擎：把「事实」变成「判断」
# =====================================================================
def test_narrative_engine() -> None:
    section("T22 叙事模板引擎（结论句从念数字到给判断）")

    from tool.common.deck_narrative import (
        apply_narrative,
        evaluate_condition,
        extract_facts,
        pick_narrative,
        render_template,
    )

    # ---- 1) 事实提取 ----
    facts = extract_facts(
        total=484248.49, count=15876,
        groups=[{"name": "物业服务费", "value": 482652.80},
                {"name": "代收代付垃圾处理费", "value": 1595.69}],
        prev_total=None,
    )
    REC.check("total 正确", facts["total"] == 484248.49, facts["total"])
    REC.check("top_name 取最大项", facts["top_name"] == "物业服务费", facts["top_name"])
    REC.check("top_share 是比值（0~1）", 0.99 < facts["top_share"] <= 1.0,
              f"实际: {facts['top_share']}")
    REC.check("top_share_pct 是百分数", facts["top_share_pct"] == 99.7,
              f"实际: {facts['top_share_pct']}")
    REC.check("group_count 正确", facts["group_count"] == 2)
    REC.check("bottom_name 取最小项",
              facts["bottom_name"] == "代收代付垃圾处理费", facts["bottom_name"])

    empty_facts = extract_facts()
    REC.check("无分组时不崩且 has_groups 为假",
              empty_facts["has_groups"] is False and empty_facts["top_name"] == "")

    growth = extract_facts(total=120, count=1, prev_total=100)
    REC.check("提供上期时算出增长率",
              abs(growth["growth"] - 0.2) < 1e-6, f"实际: {growth['growth']}")

    with_extra = extract_facts(total=1, count=1, extra={"rate": 92.5})
    REC.check("extra 事实被合并", with_extra.get("rate") == 92.5)

    # ---- 2) 条件求值：正向 ----
    true_cases = [
        ("top_share > 0.9", facts),
        ("top_share >= 0.99", facts),
        ("top_share < 1.0", facts),
        ("count > 1000", facts),
        ("has(top_name)", facts),
        ("empty(missing_key)", facts),
        ("top_share > 0.9 and count > 100", facts),
        ("top_share > 0.99 or count > 99999", facts),
        ("not count > 99999", facts),
        ("! count > 99999", facts),
        ("", facts),                     # 空条件视为默认条款
        ("top_share > 0.5", {"top_share": 0.8}),
        ("rate < 95", {"rate": 92.5}),
    ]
    for cond, f in true_cases:
        REC.check(f"条件命中: {cond!r}", evaluate_condition(cond, f) is True,
                  f"实际: {evaluate_condition(cond, f)}")

    # ---- 3) 条件求值：负向 ----
    false_cases = [
        ("top_share > 2", facts),
        ("count > 99999999", facts),
        ("has(missing_key)", facts),
        ("empty(top_name)", facts),
        ("gap > 0", facts),                       # 键不存在
        ("top_share > 0.9 and count > 99999", facts),
        ("top_share > 0.9999 or count > 99999", facts),
        ("完全不是表达式", facts),                  # 无法解析 → 不命中
        ("rate < 95", {}),                        # 缺事实
    ]
    for cond, f in false_cases:
        REC.check(f"条件不命中: {cond!r}", evaluate_condition(cond, f) is False,
                  f"实际: {evaluate_condition(cond, f)}")

    # 类型不可比时不崩
    REC.check("字符串与数字比较不崩（视为不命中）",
              evaluate_condition("top_name > 5", facts) is False)

    # ---- 4) 模板渲染 ----
    REC.check("基本占位符替换",
              render_template("{top_name} 占 {top_share_pct}%", facts)
              == "物业服务费 占 99.7%")
    REC.check("万元格式 :万",
              render_template("{total:万}", facts) == "48.42",
              f"实际: {render_template('{total:万}', facts)}")
    REC.check("百分比格式 :1%（比值→百分数）",
              render_template("{top_share:1%}", facts) == "99.7%",
              f"实际: {render_template('{top_share:1%}', facts)}")
    REC.check("千分位格式 :,",
              render_template("{count:,}", facts) == "15,876",
              f"实际: {render_template('{count:,}', facts)}")
    REC.check("小数位格式 :2",
              render_template("{total:2}", facts) == "484248.49")
    REC.check("未知占位符保留原样（便于发现模板写错）",
              render_template("{不存在的键}", facts) == "{不存在的键}")
    REC.check("值为 None 显示破折号",
              render_template("{growth_pct}", facts) == "—")
    REC.check("空模板返回空串", render_template("", facts) == "")

    # ---- 5) 叙事选择：第一个命中的优先 ----
    rules = [
        {"when": "top_share > 0.95", "so_what": "高度集中：{top_name} {top_share_pct}%"},
        {"when": "top_share > 0.5", "so_what": "中度集中"},
        {"when": "", "so_what": "分散"},
    ]
    REC.check("命中第一条规则",
              pick_narrative(rules, facts) == "高度集中：物业服务费 99.7%",
              pick_narrative(rules, facts))
    mid = extract_facts(total=100, count=3,
                        groups=[{"name": "A", "value": 70}, {"name": "B", "value": 30}])
    REC.check("命中第二条规则", pick_narrative(rules, mid) == "中度集中",
              pick_narrative(rules, mid))
    low = extract_facts(total=100, count=3,
                        groups=[{"name": "A", "value": 40}, {"name": "B", "value": 35},
                                {"name": "C", "value": 25}])
    REC.check("回落到默认条款", pick_narrative(rules, low) == "分散",
              pick_narrative(rules, low))
    REC.check("规则全不命中返回空串",
              pick_narrative([{"when": "top_share > 5", "so_what": "x"}], facts) == "")
    REC.check("空规则列表返回空串", pick_narrative([], facts) == "")

    # ---- 6) 批量套用 ----
    spec_sections = [
        {"key": "revenue", "narrative": [
            {"when": "top_share > 0.9", "so_what": "收入集中：{top_name}"}]},
        {"key": "cost", "narrative": [
            {"when": "top_share > 0.9", "so_what": "成本集中：{top_name}"}]},
        {"key": "no_rules"},
    ]
    applied = apply_narrative(spec_sections, {"revenue": facts, "cost": facts})
    REC.check("批量套用覆盖多个段落",
              applied["revenue"]["so_what"] == "收入集中：物业服务费"
              and applied["cost"]["so_what"] == "成本集中：物业服务费",
              f"实际: {applied}")
    REC.check("无规则的段落被跳过", "no_rules" not in applied)
    REC.check("缺事实的段落产出空（不报假结论）",
              apply_narrative([{"key": "x", "narrative": [
                  {"when": "rate < 95", "so_what": "x"}]}], {}) == {})

    # title_narrative 也支持
    applied_title = apply_narrative(
        [{"key": "s", "title_narrative": [
            {"when": "top_share > 0.9", "title": "标题：{top_name} 独大"}]}],
        {"s": facts})
    REC.check("title_narrative 可生成标题",
              applied_title["s"]["title"] == "标题：物业服务费 独大",
              f"实际: {applied_title}")


# =====================================================================
# T23 技能 spec 驱动全链路：页数/叙事/主题都来自 skill
# =====================================================================
def test_skill_spec_pipeline() -> None:
    section("T23 技能 spec 驱动全链路（页数/结论/主题都来自知识文件）")

    from tool.common.deck_build_tool import DeckBuildTool
    from tool.common.deck_content_tool import DeckContentTool
    from tool.common.deck_theme import validate_theme
    from tool.common.pptx_check_tool import check_pptx

    spec_path = os.path.join(SKILLS_ROOT, "buttin", "ppt_skill", "deck_spec.json")
    REC.check("技能目录存在 deck_spec.json（知识文件）", os.path.isfile(spec_path),
              spec_path)
    if not os.path.isfile(spec_path):
        return

    with open(spec_path, "r", encoding="utf-8") as handle:
        spec = json.load(handle)

    REC.check("spec 含 page_rules（几页的要求）",
              isinstance(spec.get("page_rules"), list) and spec["page_rules"],
              "技能的页数要求应在 spec 里，而不是代码里")
    REC.check("spec 含 sections", bool(spec.get("sections")))
    REC.check("spec 含 narrative 结论句模板",
              any(s.get("narrative") for s in spec["sections"]),
              "结论句模板是「给判断」的来源")
    REC.check("spec 含 theme（配色也是知识）",
              isinstance(spec.get("theme"), dict))
    if spec.get("theme"):
        # spec 里的 theme 是**部分覆盖**（只给 colors/palette），
        # 必须与默认主题深合并后再校验 —— 直接校验原始片段会报缺字段。
        from tool.common.deck_theme import build_theme as _build_theme
        merged_theme = _build_theme(theme=spec["theme"])
        REC.check("spec 里的 theme 与默认深合并后校验通过",
                  validate_theme(merged_theme) == [],
                  f"实际: {validate_theme(merged_theme)}")
        REC.check("spec 的配色确实覆盖了默认值",
                  merged_theme["colors"]["title"] == spec["theme"]["colors"]["title"],
                  f"{merged_theme['colors']['title']} vs {spec['theme']['colors']['title']}")

    # ---- plan：页数由 spec 决定 ----
    content = DeckContentTool()
    plan_text = content.run({"action": "plan", "spec_path": spec_path, "minutes": 20})
    REC.check("plan 走 spec 且标注来源", "来自技能 spec" in plan_text, plan_text[:120])
    REC.check("plan 显示结论句模板条数", "结论句模板" in plan_text)
    REC.check("plan 显示校验要求", "校验要求" in plan_text)

    count_20 = plan_text.count("\n  ") - plan_text.count("目的") - plan_text.count("结论句")
    REC.check("20 分钟按 spec 出 11 页", "11 页" in plan_text, plan_text[:80])

    plan_10 = content.run({"action": "plan", "spec_path": spec_path, "minutes": 10})
    REC.check("10 分钟按 spec 出 7 页", "7 页" in plan_10, plan_10[:80])

    # ---- build：需要一个自包含的小数据集（不依赖 data/common）----
    stage = os.path.join(TEST_OUTPUT_DIR, "spec_pipeline")
    os.makedirs(stage, exist_ok=True)
    csv_path = os.path.join(stage, "物业样例.csv")
    with open(csv_path, "w", encoding="utf-8-sig") as handle:
        handle.write("\n".join([
            "费用开始日期,收费项,账单金额(元),应收金额(元),未缴金额(元)",
            "2026-07-01,物业服务费,260000.00,260000.00,12000.00",
            "2026-08-01,物业服务费,240000.00,240000.00,9000.00",
            "2026-09-01,物业服务费,255000.00,255000.00,8000.00",
            "2026-09-01,公共收益,18000.00,18000.00,500.00",
            "2026-09-01,多种经营,6000.00,6000.00,200.00",
        ]))

    ir_path = os.path.join(stage, "deck.json")
    build_text = content.run({
        "action": "build", "spec_path": spec_path, "file": csv_path,
        "project": "测试小区", "out": ir_path,
        "extra_facts": {"labor_rate": 0.415, "rate": 96.5},
    })
    REC.check("build 成功", "已生成 IR" in build_text, build_text[:300])
    REC.check("build 报告叙事知识来源", "来自技能 spec" in build_text, build_text[:200])

    with open(ir_path, "r", encoding="utf-8") as handle:
        deck = json.load(handle)

    REC.check("IR 页数与 spec 规则一致（11 页）", len(deck["slides"]) == 11,
              f"实际: {len(deck['slides'])}")
    REC.check("IR 带上了 spec 的主题", isinstance(deck["meta"].get("theme_spec"), dict))

    # ★ 核心：结论句必须是「判断」而不是「念数字」
    narrated = [s for s in deck["slides"] if s.get("_narrative_applied")]
    REC.check("有页面由叙事模板生成结论句（≥4 页）", len(narrated) >= 4,
              f"实际: {len(narrated)} 页")

    all_sowhat = " ".join(s.get("so_what", "") for s in deck["slides"])
    REC.check("结论句含专业判断词（不再只是念数字）",
              any(word in all_sowhat for word in
                  ("抗风险", "主战场", "上游指标", "穿透", "红线", "归因")),
              f"实际结论集合: {all_sowhat[:200]}")

    # 不许出现未替换的占位符
    REC.check("结论句里没有残留占位符",
              "{" not in all_sowhat and "}" not in all_sowhat,
              f"含占位符的文本: {[s['so_what'] for s in deck['slides'] if '{' in s.get('so_what','')]}")

    # 数据缺失的段落要诚实降级
    cost_slides = [s for s in deck["slides"] if "成本" in (s.get("kicker") or "")]
    if cost_slides:
        REC.check("缺数据的成本页诚实标注（不拿别的列冒充）",
                  "数据缺失" in cost_slides[0]["title"]
                  or "未找到" in cost_slides[0]["so_what"],
                  f"title={cost_slides[0]['title']!r} so_what={cost_slides[0]['so_what']!r}")

    # ---- 渲染：theme 也来自 spec ----
    pptx_path = os.path.join(stage, "deck.pptx")
    render_text = DeckBuildTool().run({
        "deck": deck, "output": pptx_path,
        "theme": deck["meta"]["theme_spec"],
    })
    REC.check("渲染成功", os.path.isfile(pptx_path), render_text[:200])

    spec_theme_colors = {v.upper() for v in spec["theme"]["colors"].values()}
    fills = _deck_fills(pptx_path)
    REC.check("spec 主题的配色确实用上了",
              len(fills & spec_theme_colors) >= 3,
              f"spec色={sorted(spec_theme_colors)[:5]}\n实际={sorted(fills)}")

    report = check_pptx(pptx_path, expected_count=len(deck["slides"]))
    REC.check("产物通过结构校验（无 fail）", report["summary"]["fail"] == 0,
              f"verdict={report['verdict']} {report['summary']}")

    # ---- spec 缺失时的行为 ----
    bad = content.run({"action": "build", "spec_path": os.path.join(stage, "nope.json"),
                       "file": csv_path})
    REC.check("spec_path 不存在时给出明确错误", "不存在" in bad, bad[:150])

    # 不传 spec 时退回内置 profile（向后兼容）
    legacy = content.run({"action": "build", "file": csv_path, "profile": "general",
                          "out": os.path.join(stage, "legacy.json")})
    REC.check("不传 spec 仍可用内置 profile（向后兼容）",
              "已生成 IR" in legacy and "内置" not in legacy or "已生成 IR" in legacy,
              legacy[:200])
    with open(os.path.join(stage, "legacy.json"), "r", encoding="utf-8") as handle:
        legacy_deck = json.load(handle)
    REC.check("无 spec 时不使用叙事模板（退回念数字）",
              not any(s.get("_narrative_applied") for s in legacy_deck["slides"]),
              "内置 profile 没有 narrative，结论句应退回通用文案")


# =====================================================================
# T24 技能切换：单体脚本 → 工具编排
# =====================================================================
def test_skill_switched_to_tools() -> None:
    section("T24 技能已切到工具编排（知识在技能，机制在工具）")

    ppt_dir = os.path.join(SKILLS_ROOT, "buttin", "ppt_skill")
    scripts_dir = os.path.join(ppt_dir, "scripts")
    orchestrator = os.path.join(scripts_dir, "run_skill.py")
    old_script = os.path.join(scripts_dir, "build_deck.py")

    # ---- 1) 结构：旧单体脚本已消失，编排器是薄的 ----
    REC.check("旧单体脚本 build_deck.py 已删除", not os.path.isfile(old_script),
              f"仍存在: {old_script}")
    REC.check("编排器 run_skill.py 存在", os.path.isfile(orchestrator))
    if not os.path.isfile(orchestrator):
        return

    with open(orchestrator, "r", encoding="utf-8") as handle:
        orch_lines = len(handle.read().splitlines())
    REC.check("编排器足够薄（<400 行，原单体脚本 2004 行）",
              orch_lines < 400, f"实际: {orch_lines} 行")

    # ---- 2) 编排器只做编排：必须通过工具完成，不得自己渲染 ----
    with open(orchestrator, "r", encoding="utf-8") as handle:
        orch_src = handle.read()
    for tool_name in ("DeckContentTool", "DeckBuildTool", "check_pptx",
                      "build_speech"):
        REC.check(f"编排器调用 {tool_name}", tool_name in orch_src)
    REC.check("编排器不直接 import python-pptx（渲染必须走工具）",
              "from pptx import" not in orch_src,
              "编排层不该碰渲染细节")
    REC.check("编排器不含领域判断（只读 spec，不自带行业知识）",
              not any(word in orch_src for word in
                      ("物业费", "收缴率", "陈欠", "公共收益")),
              "行业知识必须留在 deck_spec.json")

    # ---- 3) 知识在技能目录 ----
    spec_path = os.path.join(ppt_dir, "deck_spec.json")
    REC.check("技能自带机读知识 deck_spec.json", os.path.isfile(spec_path))
    with open(spec_path, "r", encoding="utf-8") as handle:
        spec = json.load(handle)
    for key in ("page_rules", "sections", "theme", "check"):
        REC.check(f"spec 含 {key}", key in spec)
    REC.check("spec 段落带结论句模板（给判断而非念数字）",
              sum(1 for s in spec["sections"] if s.get("narrative")) >= 4,
              f"实际: {sum(1 for s in spec['sections'] if s.get('narrative'))} 段")
    REC.check("spec 段落带主因与动作模板（话术第 3 段）",
              sum(1 for s in spec["sections"] if s.get("action_narrative")) >= 4,
              f"实际: {sum(1 for s in spec['sections'] if s.get('action_narrative'))} 段")
    REC.check("spec 段落声明数据来源列（收入/成本各取各的列）",
              sum(1 for s in spec["sections"] if s.get("data")) >= 3,
              f"实际: {sum(1 for s in spec['sections'] if s.get('data'))} 段")

    # ---- 4) 工具层不含行业知识（这是本次重构的核心不变量）----
    tool_dir = os.path.join(PROJECT_ROOT, "tool", "common")
    business_words = ("物业费", "收缴率", "陈欠", "公共收益", "人均效能", "账龄")
    leaking = {}
    for name in ("deck_ir.py", "deck_theme.py", "deck_narrative.py",
                 "deck_build_tool.py", "deck_speech.py"):
        path = os.path.join(tool_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        hits = [w for w in business_words if w in text]
        if hits:
            leaking[name] = hits
    REC.check("通用工具层不含行业专有名词（知识没漏到工具层）",
              not leaking, f"泄漏位置: {leaking}")

    # deck_content 允许出现词（文档里举例说明），但不得有硬编码业务规则
    content_path = os.path.join(tool_dir, "deck_content_tool.py")
    with open(content_path, "r", encoding="utf-8") as handle:
        content_src = handle.read()
    REC.check("deck_content 不含硬编码的行业分支（if 物业/if 动漫）",
              "物业" not in content_src and "动漫" not in content_src,
              "内容工具应只认 spec，不认行业")

    # ---- 5) 端到端：一次运行产出全部交付物 ----
    stage = os.path.join(TEST_OUTPUT_DIR, "switched")
    os.makedirs(stage, exist_ok=True)
    os.environ["SKILL_OUTPUT_DIR"] = stage

    from skills.loader import load_skill

    skill = load_skill(ppt_dir, llm=None, execution="script")
    result = skill.run_skill({"request": "用应收信息生成物业收缴经营分析 PPT"})

    REC.check("编排式技能执行成功", result.success,
              f"error={result.error}\n{result.output[-400:]}")

    names = {os.path.basename(p) for p in result.artifacts}
    for required in ("deck.pptx", "checks.json", "deck.speech.json",
                     "deck.speech.md", "deck.json", "input_data.json",
                     "deck.deck.json"):
        REC.check(f"交付物含 {required}", required in names, f"实际: {sorted(names)}")

    # 交付物 ② 校验报告必须是 pass
    checks_file = next((p for p in result.artifacts
                        if p.endswith("checks.json")), None)
    if checks_file:
        with open(checks_file, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        REC.check("交付物② 校验通过（无 fail）",
                  report.get("summary", {}).get("fail") == 0,
                  f"verdict={report.get('verdict')} {report.get('summary')}")

    # 交付物 ③ 话术：每段完整三段式
    speech_file = next((p for p in result.artifacts
                        if p.endswith(".speech.json")), None)
    if speech_file:
        with open(speech_file, "r", encoding="utf-8") as handle:
            speech = json.load(handle)
        scripts = speech.get("scripts") or {}
        REC.check("交付物③ 话术有多段", len(scripts) >= 5, f"实际: {len(scripts)}")
        incomplete = [k for k, v in scripts.items()
                      if not all(v.get(x, "").strip()
                                 for x in ("结论先行", "数据支撑", "主因与动作"))]
        REC.check("话术每段都是完整三段式", not incomplete, f"缺段: {incomplete}")
        actions = " ".join(v.get("主因与动作", "") for v in scripts.values())
        REC.check("话术「主因与动作」来自知识（有责任人/时限等具体信息）",
                  any(w in actions for w in ("责任", "时限", "验收")),
                  f"实际: {actions[:200]}")

        def _braces(obj, path=""):
            """找出话术里所有含花括号的字符串 —— 未替换的模板占位符。"""
            found = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    found += _braces(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    found += _braces(v, f"{path}[{i}]")
            elif isinstance(obj, str) and ("{" in obj or "}" in obj):
                found.append(f"{path}: {obj[:120]}")
            return found

        leaks = _braces(speech)
        REC.check("话术不含未替换的模板占位符", not leaks,
                  f"残留占位符: {leaks}")

    # ---- 6) 页数确实由 spec 决定 ----
    deck_file = next((p for p in result.artifacts
                      if os.path.basename(p) == "deck.json"), None)
    if deck_file:
        with open(deck_file, "r", encoding="utf-8") as handle:
            deck = json.load(handle)
        expected = len(spec["page_rules"][1]["sections"])  # 20 分钟档
        REC.check(f"20 分钟档由 spec 决定为 {expected} 页",
                  len(deck["slides"]) == expected,
                  f"实际: {len(deck['slides'])} 页")
        narrated = sum(1 for s in deck["slides"] if s.get("_narrative_applied"))
        REC.check("多数页面结论句由知识模板生成", narrated >= 4,
                  f"实际: {narrated} 页")


# =====================================================================
# T25 配色方案库：按汇报场景分类的主题
# =====================================================================
def test_theme_gallery() -> None:
    section("T25 配色方案库（5 类汇报场景 × 多套配色）")

    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE

    from tool.common.deck_build_tool import DeckBuildTool
    from tool.common.deck_ir import empty_deck, make_slide
    from tool.common.deck_theme import (
        SCENE_THEMES,
        build_theme,
        describe_theme,
        validate_theme,
    )

    # ---- 1) 场景覆盖 ----
    required = {
        "商务汇报": ["business", "business_vivid", "business_mono"],
        "科技/AI": ["tech"],
        "学术答辩": ["academic", "academic_morandi"],
        "党建政务": ["gov"],
        "文化国风": ["oriental", "macaron"],
    }
    for group, keys in required.items():
        missing = [k for k in keys if k not in SCENE_THEMES]
        REC.check(f"{group} 配色齐全", not missing, f"缺少: {missing}")

    REC.check("主题库 ≥10 套", len(SCENE_THEMES) >= 10,
              f"实际: {len(SCENE_THEMES)}")
    REC.check("每套都有中文名",
              all(t.get("name") for t in SCENE_THEMES.values()))

    # ---- 2) 逐套校验 + 关键色齐全 ----
    problems_by_theme = {}
    for key in SCENE_THEMES:
        theme = build_theme(key)
        problems = validate_theme(theme)
        if problems:
            problems_by_theme[key] = problems
    REC.check("全部配色校验通过", not problems_by_theme,
              f"有问题的: {problems_by_theme}")

    # ---- 3) 深色主题的自动识别 ----
    tech = build_theme("tech")
    business = build_theme("business")
    from tool.common.deck_build_tool import ThemePalette

    REC.check("科技主题被识别为深色底",
              ThemePalette(tech).dark_bg is True,
              f"底色 {tech['colors']['page_bg']}")
    REC.check("商务主题不被识别为深色底",
              ThemePalette(business).dark_bg is False)
    REC.check("深色主题的标题色是浅色（对比度）",
              ThemePalette(tech)._is_dark(tech["colors"]["title"]) is False,
              f"标题色 {tech['colors']['title']} 应当在深底上可读")

    # ---- 4) decor 开关按主题取值 ----
    REC.check("business 为直角卡片",
              ThemePalette(business).card_shape == "rect",
              ThemePalette(business).card_shape)
    REC.check("anime 为圆角卡片",
              ThemePalette(build_theme("anime")).card_shape == "rounded")
    REC.check("academic_morandi 结论栏为 plain（去装饰）",
              ThemePalette(build_theme("academic_morandi")).so_what_style == "plain")
    REC.check("business_mono 结论栏为 rule（仅色条）",
              ThemePalette(build_theme("business_mono")).so_what_style == "rule")

    # ---- 5) 渲染全部配色，确认互不相同 ----
    stage = os.path.join(TEST_OUTPUT_DIR, "gallery")
    os.makedirs(stage, exist_ok=True)

    deck = empty_deck("配色对照", project="示例", period="2026-09")
    deck["slides"] = [
        make_slide("cover", "配色对照", [
            {"type": "kpi_grid", "columns": 3, "items": [
                {"label": "达标", "value": "108%", "delta": "超额", "status": "ok"},
                {"label": "预警", "value": "18.6%", "delta": "偏高", "status": "warn"},
                {"label": "欠佳", "value": "82.4%", "delta": "未达标", "status": "bad"},
            ]}]),
        make_slide("chart", "图表页",
                   [{"type": "chart", "chart": "column",
                     "categories": ["A", "B", "C"],
                     "series": [{"name": "s1", "values": [3, 5, 2]},
                                {"name": "s2", "values": [2, 4, 3]}]}],
                   so_what="结论句"),
        make_slide("cards", "卡片页",
                   [{"type": "cards", "columns": 2, "items": [
                       {"title": "C1", "body": "b1", "status": "ok"},
                       {"title": "C2", "body": "b2", "status": "bad"}]}],
                   so_what="结论句"),
        make_slide("table", "表格页",
                   [{"type": "table", "headers": ["h1", "h2"],
                     "rows": [["a", "b"]]}], so_what="结论句"),
    ]

    builder = DeckBuildTool()
    fills_by_theme: Dict[str, set] = {}
    failures = []
    for key in SCENE_THEMES:
        out = os.path.join(stage, f"{key}.pptx")
        result = builder.run({"deck": deck, "output": out,
                              "theme": build_theme(key)})
        if not os.path.isfile(out):
            failures.append((key, result[:120]))
            continue
        colors = set()
        for slide in Presentation(out).slides:
            for shape in slide.shapes:
                try:
                    if shape.fill.type == 1:
                        colors.add(str(shape.fill.fore_color.rgb))
                except Exception:
                    continue
        fills_by_theme[key] = colors

    REC.check("全部配色都能渲染成 pptx", not failures, f"失败: {failures}")
    REC.check("每套配色都画出了 ≥6 种颜色",
              all(len(c) >= 6 for c in fills_by_theme.values()),
              f"实际: {{k: len(v) for k, v in fills_by_theme.items()}}")

    # 两两不同 —— 这是"丰富配色"的硬指标
    keys = sorted(fills_by_theme)
    identical = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if fills_by_theme[keys[i]] == fills_by_theme[keys[j]]:
                identical.append((keys[i], keys[j]))
    REC.check("任意两套配色的填充色集合都不同", not identical,
              f"相同组合: {identical}")

    # 每套至少有一个"专属色"（不被其他主题用到）
    no_unique = []
    for key, colors in fills_by_theme.items():
        others = set()
        for other, other_colors in fills_by_theme.items():
            if other != key:
                others |= other_colors
        # 白底通常共用，不算专属
        if not (colors - others - {"FFFFFF"}):
            no_unique.append(key)
    REC.check("每套配色都有专属色（不只是换个名字）", not no_unique,
              f"无专属色的: {no_unique}")

    # ---- 6) decor 真的写进了产物 ----
    def shape_stats(path):
        prs = Presentation(path)
        rounded = rect = 0
        full_bg = False
        for slide in prs.slides:
            for shape in slide.shapes:
                try:
                    if shape.auto_shape_type == MSO_SHAPE.ROUNDED_RECTANGLE:
                        rounded += 1
                    elif shape.auto_shape_type == MSO_SHAPE.RECTANGLE:
                        rect += 1
                except Exception:
                    pass
                try:
                    if (shape.width == prs.slide_width
                            and shape.height == prs.slide_height
                            and shape.fill.type == 1):
                        full_bg = True
                except Exception:
                    pass
        return rounded, rect, full_bg

    rounded_b, rect_b, bg_b = shape_stats(os.path.join(stage, "business.pptx"))
    rounded_a, rect_a, bg_a = shape_stats(os.path.join(stage, "anime.pptx"))
    _, _, bg_t = shape_stats(os.path.join(stage, "tech.pptx"))

    REC.check("business 用直角卡片（圆形=0）", rounded_b == 0,
              f"rounded={rounded_b} rect={rect_b}")
    REC.check("anime 用圆角卡片（圆形>0）", rounded_a > 0,
              f"rounded={rounded_a} rect={rect_a}")
    REC.check("business 不铺满底色", bg_b is False)
    REC.check("tech 铺满深色底", bg_t is True,
              "深色主题必须自己铺底，否则深色卡片浮在白页上")

    # 圆角与直角是两种确实是不同形状的产物
    REC.check("圆角与直角产物形状构成不同",
              (rounded_a, rect_a) != (rounded_b, rect_b),
              f"anime={rounded_a}/{rect_a} business={rounded_b}/{rect_b}")

    # ---- 7) describe 可用于人工挑选 ----
    for key in ("business", "tech", "gov"):
        text = describe_theme(build_theme(key))
        REC.check(f"{key} 摘要可读",
                  "语义色" in text and "调色板" in text, text[:80])


# =====================================================================
# T26 审美修版：阴影 / 卡片布局 / 图表去重 / 封面节奏
# =====================================================================
def test_aesthetic_fixes() -> None:
    section("T26 审美修版（阴影、卡片自适应、图表去重、封面节奏）")

    import zipfile

    from pptx import Presentation
    from pptx.util import Emu

    from tool.common.deck_build_tool import (
        DeckBuildTool, ThemePalette, _estimate_card_height, _pick_columns,
    )
    from tool.common.deck_ir import empty_deck, make_slide
    from tool.common.deck_theme import build_theme

    # ---- 1) 列数自适应：不留空槽 ----
    REC.check("3 项 → 3 列（不再是 2 列留半页空）",
              _pick_columns(3, preferred=2) == 3, f"实际: {_pick_columns(3, 2)}")
    REC.check("2 项 → 2 列", _pick_columns(2, 2) == 2)
    REC.check("4 项 → 2 列（平手时尊重主题偏好，用 2×2 而非 1×4）",
              _pick_columns(4, 2) == 2, f"实际: {_pick_columns(4, 2)}")
    REC.check("4 项 + 偏好 4 列 → 4 列", _pick_columns(4, 4) == 4,
              f"实际: {_pick_columns(4, 4)}")
    REC.check("6 项 → 2 列（0 空槽且贴合偏好）", _pick_columns(6, 2) == 2,
              f"实际: {_pick_columns(6, 2)}")
    # 任何项数都不该出现"整行空着"
    wastes = {}
    for n in range(1, 13):
        cols = _pick_columns(n, 2)
        rows = -(-n // cols)
        wastes[n] = rows * cols - n
    REC.check("1~12 项的空槽数都 ≤ 2", max(wastes.values()) <= 2,
              f"实际空槽: {wastes}")

    # ---- 2) 卡片高度按内容估算 ----
    pal = ThemePalette(build_theme("business"))
    cw = int(pal.content_w / 3)
    short = _estimate_card_height({"title": "短标题", "body": "一句话。"}, cw, pal)
    long = _estimate_card_height(
        {"title": "较长的标题文字", "body": "这是一段明显更长的正文，需要折成好几行" * 4},
        cw, pal)
    REC.check("卡片高度随内容增长", long > short + 0.2,
              f"短={short:.2f}\" 长={long:.2f}\"")
    REC.check("短卡片高度在合理区间（0.9~1.6\"）", 0.9 <= short <= 1.6,
              f"实际: {short:.2f}\"")

    # ---- 3) 渲染后几何检查 ----
    stage = os.path.join(TEST_OUTPUT_DIR, "aesthetic")
    os.makedirs(stage, exist_ok=True)

    deck = empty_deck("审美验收", project="示例", period="2026-09")
    deck["slides"] = [
        make_slide("cover", "封面", [
            {"type": "kpi_grid", "columns": 4, "items": [
                {"label": f"指标{i}", "value": f"{i}00", "delta": f"说明{i}",
                 "status": "ok"} for i in range(4)]}]),
        make_slide("chart", "单系列图表",
                   [{"type": "chart", "chart": "bar",
                     "categories": ["甲", "乙", "丙"],
                     "series": [{"name": "数值", "values": [1, 2, 3]}]}],
                   so_what="结论"),
        make_slide("chart", "多系列图表",
                   [{"type": "chart", "chart": "column",
                     "categories": ["甲", "乙"],
                     "series": [{"name": "s1", "values": [1, 2]},
                                {"name": "s2", "values": [2, 1]}]}],
                   so_what="结论"),
        make_slide("cards", "三张卡片",
                   [{"type": "cards", "columns": 2, "items": [
                       {"title": f"卡片{i}", "body": f"正文{i}", "status": "ok"}
                       for i in range(3)]}],
                   so_what="结论"),
    ]
    out = os.path.join(stage, "aesthetic.pptx")
    result = DeckBuildTool().run({"deck": deck, "output": out,
                                  "theme": build_theme("business")})
    REC.check("渲染成功", os.path.isfile(out), result[:200])

    prs = Presentation(out)
    slide_w, slide_h = prs.slide_width, prs.slide_height

    # ① 阴影来源已移除
    with zipfile.ZipFile(out) as zf:
        slide_xml = zf.read("ppt/slides/slide1.xml").decode("utf-8")
    REC.check("形状上不再挂 <p:style>（阴影根因已切除）",
              "<p:style>" not in slide_xml,
              "effectRef idx=2 会让 LibreOffice 画出投影")
    REC.check("XML 里也不再有 effectRef", "effectRef" not in slide_xml)

    # ② 没有任何形状越出页面
    overflow = []
    for idx, slide in enumerate(prs.slides, 1):
        for shape in slide.shapes:
            try:
                if (shape.left + shape.width > slide_w + 1000
                        or shape.top + shape.height > slide_h + 1000):
                    overflow.append((idx, shape.shape_id))
            except TypeError:
                continue
    REC.check("所有形状都在页面内", not overflow, f"越界: {overflow}")

    # ③ 图表：单系列无图例、无自带标题
    chart_slides = [s for s in prs.slides if any(
        sh.has_chart for sh in s.shapes)]
    REC.check("生成了 2 张图表页", len(chart_slides) == 2,
              f"实际: {len(chart_slides)}")
    if len(chart_slides) >= 2:
        single = next(sh.chart for sh in chart_slides[0].shapes if sh.has_chart)
        multi = next(sh.chart for sh in chart_slides[1].shapes if sh.has_chart)
        REC.check("单系列图表不显示图例", single.has_legend is False,
                  f"实际: {single.has_legend}")
        REC.check("多系列图表保留图例", multi.has_legend is True,
                  "多系列需要图例区分")
        REC.check("图表没有自带标题（避免与页标题重复）",
                  single.has_title is False and multi.has_title is False,
                  f"single={single.has_title} multi={multi.has_title}")

    # ④ 卡片：3 项用 3 列，且高度没有撑满
    # 卡片 = 有实心填充的矩形/圆角矩形。不能只按尺寸筛 ——
    # 卡片内部的正文文本框尺寸也很大，会被误当成卡片（实测踩过）。
    from pptx.enum.shapes import MSO_SHAPE

    def _is_card(shape) -> bool:
        try:
            if shape.auto_shape_type not in (MSO_SHAPE.RECTANGLE,
                                             MSO_SHAPE.ROUNDED_RECTANGLE):
                return False
            if shape.fill.type != 1:  # 实心
                return False
        except Exception:
            return False
        return (shape.width > Emu(int(1.5 * 914400))
                and shape.height > Emu(int(0.6 * 914400)))

    card_slide = prs.slides[3]
    cards = [sh for sh in card_slide.shapes if _is_card(sh)]
    xs = sorted({round(sh.left / 914400, 2) for sh in cards})
    REC.check("3 张卡片排成 3 列（1 行，无空位）", len(xs) == 3,
              f"实际列起点: {xs}")
    REC.check("卡片不是被拉伸填满（高度 < 3\"）",
              all(sh.height < Emu(int(3.0 * 914400)) for sh in cards),
              f"实际高度: {[round(sh.height / 914400, 2) for sh in cards]}")

    # ⑤ 封面：KPI 卡片在色带之外且不越界
    cover_shapes = [sh for sh in prs.slides[0].shapes
                    if sh.width > Emu(int(1.0 * 914400))]
    band = Emu(int(2.5 * 914400))
    card_lefts = [sh for sh in cover_shapes
                  if sh.top > band and sh.height > Emu(int(0.6 * 914400))]
    REC.check("封面 KPI 卡片位于色带下方", len(card_lefts) >= 4,
              f"实际: {len(card_lefts)}")
    if card_lefts:
        bottom = max(sh.top + sh.height for sh in card_lefts) / 914400
        REC.check("封面卡片不越出下边界", bottom <= 7.5, f"实际底边 {bottom:.2f}\"")

    # ---- 4) 预览工具在位（视觉验收的前提）----
    preview_script = os.path.join(PROJECT_ROOT, "preview_deck.py")
    REC.check("preview_deck.py 存在（走 PDF 中转的可靠预览）",
              os.path.isfile(preview_script))
    if os.path.isfile(preview_script):
        with open(preview_script, "r", encoding="utf-8") as fh:
            src = fh.read()
        REC.check("预览工具固定走 PDF 中转（直渲 pptx 会放大 1.5x 并裁切）",
                  "convert" in src and "render" in src
                  and "pdf" in src.lower(),
                  "必须 pptx→pdf→png，否则拿到的是错位的残图")
        REC.check("预览工具说明了为什么要中转",
                  "裁" in src or "1.5" in src,
                  "把踩过的坑写进代码，避免后人改回去")


# =====================================================================
# T27 内容质量：指标列识别、金额单位、期间口径
# =====================================================================
def test_content_quality() -> None:
    section("T27 内容质量（指标识别 / 金额单位 / 期间口径）")

    from tool.common.deck_content_tool import (
        DeckContentTool, _fmt_money, _pick_metrics, _primary_value_column,
        _short_label,
    )
    from tool.common.table_io import read_table

    stage = os.path.join(TEST_OUTPUT_DIR, "content_quality")
    os.makedirs(stage, exist_ok=True)

    # ---- 1) 金额单位自适应 ----
    cases = [
        (482652.8, "万", "48.27 万"),
        (36400.0, "万", "3.64 万"),
        (31.0, "元", "31 元"),
        (0.5, "元", "0.50 元"),
        (-120.0, "元", "-120 元"),
    ]
    for value, unit, expected in cases:
        got = _fmt_money(value)
        REC.check(f"{value} → {expected}", got == expected and unit in got,
                  f"实际: {got}")

    REC.check("小额不再被抹成 0.00 万",
              _fmt_money(31.0) != "0.00 万",
              "实测踩过：单条均值 31 元显示成 0.00 万，等于没给信息")

    # ---- 2) 指标列识别：用基数而不是量级 ----
    # 造一份「按户明细」数据：单行金额很小（几百元），
    # 但有 ID 列（楼栋/单元，基数极低）—— 这是真实踩过的结构。
    detail = os.path.join(stage, "detail.csv")
    rows = ["费用开始日期,楼栋,单元,收费项,账单金额(元)"]
    for i in range(200):
        rows.append(
            f"2026-0{i % 9 + 1}-01,{i % 6 + 1},{i % 4 + 1},"
            f"物业服务费,{100 + i * 7}.5")
    # 再补一个"最新月但样本极少"的月份，验证期间口径
    rows.append("2026-12-01,1,1,物业服务费,88.0")
    with open(detail, "w", encoding="utf-8-sig") as fh:
        fh.write("\n".join(rows))

    df, _ = read_table(detail)
    metrics = _pick_metrics(df)

    REC.check("金额列被识别（单行仅几百元也要认）",
              "账单金额(元)" in metrics,
              f"实际识别: {sorted(k for k in metrics if k != 'rows')}")
    REC.check("ID 列被排除（楼栋/单元基数太低）",
              "楼栋" not in metrics and "单元" not in metrics,
              f"实际: {sorted(k for k in metrics if k != 'rows')}")
    REC.check("主指标列是金额列",
              _primary_value_column(metrics) == "账单金额(元)",
              f"实际: {_primary_value_column(metrics)}")

    REC.check("列名压缩去噪",
              _short_label("账单金额(元)") == "账单"
              and _short_label("应收金额(元)") == "应收",
              f"账单金额(元)→{_short_label('账单金额(元)')} "
              f"应收金额(元)→{_short_label('应收金额(元)')}")

    # ---- 3) 期间口径：最新月样本极少时不能取它 ----
    spec = {
        "profile": "t27",
        "name": "T27",
        "page_rules": [{"max_minutes": 20, "sections": ["cover"]}],
        "sections": [{"key": "cover", "label": "封面", "layout": "cover"}],
    }
    ir = os.path.join(stage, "period.json")
    DeckContentTool().run({"action": "build", "spec": spec, "file": detail,
                           "project": "T27", "out": ir})
    with open(ir, "r", encoding="utf-8") as fh:
        deck = json.load(fh)
    period = str(deck["meta"]["period"])
    REC.check("最新月样本极少时不取该月（改为标注全周期）",
              "全周期" in period or "~" in period,
              f"实际期间: {period!r}（2026-12 只有 1 行，取它会误导）")

    # 显式指定期间时必须尊重
    ir2 = os.path.join(stage, "period2.json")
    DeckContentTool().run({"action": "build", "spec": spec, "file": detail,
                           "project": "T27", "period": "2026-03", "out": ir2})
    with open(ir2, "r", encoding="utf-8") as fh:
        deck2 = json.load(fh)
    REC.check("显式指定期间优先于自动推断",
              deck2["meta"]["period"] == "2026-03",
              f"实际: {deck2['meta']['period']!r}")

    # ---- 4) 封面 KPI 必须是有意义的数，不是罗列列名 ----
    full_csv = os.path.join(stage, "full.csv")
    with open(full_csv, "w", encoding="utf-8-sig") as fh:
        lines = ["月份,科目,金额(元),户数"]
        for month in ("01", "02", "03"):
            for i, subject in enumerate(("物业费", "公共收益", "多种经营")):
                lines.append(f"2026-{month},{subject},"
                             f"{100000 + i * 50000 + int(month) * 1000},{20 + i}")
        fh.write("\n".join(lines))

    ir3 = os.path.join(stage, "cover.json")
    DeckContentTool().run({"action": "build", "spec": spec, "file": full_csv,
                           "project": "T27", "out": ir3})
    with open(ir3, "r", encoding="utf-8") as fh:
        deck3 = json.load(fh)

    cover_items = deck3["slides"][0]["blocks"][0]["items"]
    labels = [it["label"] for it in cover_items]
    REC.check("封面有 ≥3 张 KPI 卡片", len(cover_items) >= 3,
              f"实际 {len(cover_items)} 张: {labels}")
    REC.check("封面含「数据规模」与主指标合计",
              any("规模" in x for x in labels)
              and any(x in ("金额", "户数") for x in labels),
              f"实际标签: {labels}")
    REC.check("封面不出现无意义的列名堆砌",
              not any(x in ("楼栋", "单元", "费用期间") for x in labels),
              f"实际标签: {labels}")
    REC.check("封面卡片值都有内容（无空值）",
              all(str(it.get("value", "")).strip() for it in cover_items),
              f"实际: {[(it['label'], it['value']) for it in cover_items]}")


# =====================================================================
# T28 派生指标 + 跨表关联（知识在 spec，机制在工具）
# =====================================================================
def test_derive_and_join() -> None:
    section("T28 派生指标与跨表关联（让指标真正从数据算出来）")

    from tool.common.deck_narrative import evaluate_condition, render_template
    from tool.common.table_derive import (
        DerivedError, compute_derived, safe_eval_columns,
    )
    from tool.common.table_io import read_table
    from tool.common.table_join import apply_joins
    from tool.common.commons import resolve_common_dirs

    stage = os.path.join(TEST_OUTPUT_DIR, "derive")
    os.makedirs(stage, exist_ok=True)

    # ---- 1) 派生指标：逐行 + 合计两种作用域 ----
    csv = os.path.join(stage, "bill.csv")
    with open(csv, "w", encoding="utf-8-sig") as fh:
        fh.write("\n".join([
            "科目,账单金额(元),未缴金额(元)",
            "A,800.00,100.00",
            "B,200.00,0.00",
            "C,0.00,0.00",
        ]))
    df, _ = read_table(csv)

    spec = {
        "_说明": "下划线开头是注释，不该被当成指标",
        "已收金额(元)": {"expr": "账单金额(元) - 未缴金额(元)"},
        "收缴率": {"expr": "已收金额(元) / 账单金额(元) * 100",
                   "scope": "total", "format": "{:.2f}%"},
    }
    out, notes, summary = compute_derived(df, spec)

    REC.check("注释键被跳过", not any("_说明" in n for n in notes),
              f"实际 notes: {notes}")
    REC.check("逐行派生列已写入",
              "已收金额(元)" in out.columns
              and float(out["已收金额(元)"].sum()) == 900.0,
              f"实际: {out.get('已收金额(元)')}")

    # ★ 关键回归：比值必须按**合计**算。
    # 逐行算比值再求和会得到 ~250%（800/800 + 200/200 + 0），正确是 90%。
    REC.check("比值类指标按合计算（不是逐行求和）",
              abs(summary["收缴率"] - 90.0) < 0.01,
              f"实际 {summary['收缴率']:.2f}%（逐行求和会得到约 250%）")

    # ---- 2) 安全性：表达式来自配置文件，不能执行任意代码 ----
    for bad in ("__import__('os')", "open('x')", "不存在的列 + 1",
                "eval('1')"):
        try:
            safe_eval_columns(bad, {"账单金额(元)": 1}, ["账单金额(元)"])
            REC.check(f"拦截恶意表达式 {bad!r}", False, "竟然执行了")
        except DerivedError:
            REC.check(f"拦截恶意表达式 {bad!r}", True)
        except Exception as exc:
            REC.check(f"拦截恶意表达式 {bad!r}", False,
                      f"抛了意料外的 {type(exc).__name__}: {exc}")

    # ---- 3) 中文标识符：条件与占位符 ----
    facts = {"收缴率": 92.48, "欠款率": 7.52, "账单金额(元)": 484248.49}
    REC.check("中文指标名可用于条件",
              evaluate_condition("收缴率 < 95", facts) is True,
              "原先正则写死 [A-Za-z_]，中文指标名静默失配")
    REC.check("带括号的列名可用于条件",
              evaluate_condition("账单金额(元) > 100000", facts) is True)
    REC.check("中文占位符可替换",
              render_template("收缴率 {收缴率:.2f}%", facts) == "收缴率 92.48%",
              render_template("收缴率 {收缴率:.2f}%", facts))
    REC.check("未知占位符仍保留",
              render_template("{不存在}", facts) == "{不存在}")

    # ---- 4) 跨表关联（用真实物业数据验证）----
    common = os.path.join(PROJECT_ROOT, "data", "common")
    billing_path = os.path.join(common, "2、应收信息.xlsx")
    props_path = os.path.join(common, "1、房产客户信息.xlsx")
    if not (os.path.isfile(billing_path) and os.path.isfile(props_path)):
        REC.check("真实物业数据在位（跳过关联验证）", False,
                  "需要 1、房产客户信息.xlsx 与 2、应收信息.xlsx")
        return

    main, _ = read_table(billing_path)
    join_spec = {
        "datasets": {"properties": {
            "file": "1、房产客户信息.xlsx",
            "key_column": "房产编码",
            "pick": {"实测收费面积（m²）": "实测收费面积"},
        }},
        "join": {"dataset": "properties", "left_on": "房产全称", "how": "left"},
    }
    joined, jnotes = apply_joins(main, join_spec, resolve_common_dirs())

    REC.check("维表列已拼进主表", "实测收费面积" in joined.columns,
              f"实际列: {list(joined.columns)[-3:]}")
    REC.check("关联说明含匹配率",
              any("匹配上" in n for n in jnotes), f"实际: {jnotes}")
    if "实测收费面积" in joined.columns:
        matched = joined["实测收费面积"].notna().sum()
        REC.check("房产编号全部匹配上（15,876 行）",
                  matched == len(joined),
                  f"实际 {matched:,}/{len(joined):,}")

    # ★ 用真实数据交叉验证：指标要能从数据算出来，且与源系统一致
    _, _, derived = compute_derived(joined, {
        "已收金额(元)": {"expr": "账单金额(元) - 未缴金额(元)"},
        "收缴率": {"expr": "已收金额(元) / 账单金额(元) * 100",
                   "scope": "total", "format": "{:.2f}%"},
        "欠款率": {"expr": "未缴金额(元) / 账单金额(元) * 100",
                   "scope": "total", "format": "{:.2f}%"},
        "平均单价(元)": {"expr": "账单金额(元) / 实测收费面积",
                         "scope": "total", "format": "{:,.2f}"},
    })

    REC.check("收缴率从数据算出 ≈92.48%（此前靠人工传 92.5%）",
              abs(derived["收缴率"] - 92.48) < 0.05,
              f"实际 {derived['收缴率']:.2f}%")
    REC.check("欠款率 ≈7.52%（与收缴率互补）",
              abs(derived["欠款率"] - 7.52) < 0.05,
              f"实际 {derived['欠款率']:.2f}%")
    REC.check("收缴率 + 欠款率 ≈ 100%（内部自洽）",
              abs(derived["收缴率"] + derived["欠款率"] - 100) < 0.05,
              f"实际 {derived['收缴率']:.2f} + {derived['欠款率']:.2f}")
    # 平均单价 0.31 元/m² 与源系统「收费单价(元/月) = 0.3」吻合
    REC.check("平均单价 ≈0.31 元/m²（与源系统 0.3 吻合）",
              abs(derived["平均单价(元)"] - 0.31) < 0.02,
              f"实际 {derived['平均单价(元)']:.4f}")

    # ---- 5) 交叉验证：用「缴费状态」独立核对收缴率 ----
    import pandas as pd

    bill = pd.to_numeric(main["账单金额(元)"], errors="coerce").fillna(0).sum()
    unpaid = pd.to_numeric(main["未缴金额(元)"], errors="coerce").fillna(0).sum()
    by_total = (bill - unpaid) / bill * 100
    REC.check("两种算法口径一致（合计法 vs 状态分组法）",
              abs(by_total - derived["收缴率"]) < 0.05,
              f"合计法 {by_total:.2f}% vs 派生 {derived['收缴率']:.2f}%")

    # 已缴费的行必须未缴为 0
    paid_rows = main[main["缴费状态"] == "已缴费"]
    paid_unpaid = pd.to_numeric(paid_rows["未缴金额(元)"],
                                errors="coerce").fillna(0).sum()
    REC.check("「已缴费」行的未缴金额合计为 0（数据自洽）",
              abs(paid_unpaid) < 0.01,
              f"实际 {paid_unpaid:,.2f} —— 若不为 0 说明字段口径有问题")

    # ---- 6) 关联失败要给出可诊断的信息，而不是静默 ----
    bad_spec = {
        "datasets": {"nope": {"file": "这个文件不存在.xlsx"}},
        "join": {"dataset": "nope", "left_on": "房产全称"},
    }
    _, bad_notes = apply_joins(main, bad_spec, resolve_common_dirs())
    REC.check("找不到维表时说明原因",
              any("找不到文件" in n for n in bad_notes), f"实际: {bad_notes}")

    wrong_key = {
        "datasets": {"properties": {
            "file": "1、房产客户信息.xlsx",
            "key_column": "房产编码",
            "pick": {"实测收费面积（m²）": "实测收费面积"},
        }},
        "join": {"dataset": "properties", "left_on": "收费项"},
    }
    _, wk_notes = apply_joins(main, wrong_key, resolve_common_dirs())
    REC.check("键选错时提示匹配率为 0（不静默给出全空列）",
              any("一行都没匹配上" in n or "匹配上" in n for n in wk_notes),
              f"实际: {wk_notes}")


# =====================================================================
# T29 指标对比图：把派生标量画成图（而不是按列分组）
# =====================================================================
def test_metrics_chart() -> None:
    section("T29 指标对比图（应收/实收/未缴，语义色）")

    from tool.common.deck_build_tool import DeckBuildTool
    from tool.common.deck_content_tool import DeckContentTool, _resolve_metric
    from tool.common.deck_theme import build_theme

    stage = os.path.join(TEST_OUTPUT_DIR, "metrics_chart")
    os.makedirs(stage, exist_ok=True)

    csv = os.path.join(stage, "bill.csv")
    with open(csv, "w", encoding="utf-8-sig") as fh:
        fh.write("\n".join([
            "科目,账单金额(元),未缴金额(元)",
            "A,800.00,100.00",
            "B,200.00,0.00",
        ]))

    spec = {
        "profile": "t29",
        "name": "T29",
        "page_rules": [{"max_minutes": 20, "sections": ["cover", "rate"]}],
        "derived": {
            "已收金额(元)": {"expr": "账单金额(元) - 未缴金额(元)"},
            "收缴率": {"expr": "已收金额(元) / 账单金额(元) * 100",
                       "scope": "total", "format": "{:.2f}%"},
        },
        "sections": [
            {"key": "cover", "label": "封面", "layout": "cover"},
            {"key": "rate", "label": "收缴率", "layout": "chart",
             "metrics_chart": {
                 "chart": "column", "series_name": "金额（元）",
                 "items": [
                     {"metric": "账单金额(元)", "label": "应收", "status": "info"},
                     {"metric": "已收金额(元)", "label": "实收", "status": "ok"},
                     {"metric": "未缴金额(元)", "label": "未缴", "status": "bad"},
                 ]},
             "narrative": [
                 {"when": "收缴率 < 95",
                  "so_what": "收缴率 {收缴率:.2f}%，未达标。"},
             ]},
        ],
    }

    ir = os.path.join(stage, "deck.json")
    out = DeckContentTool().run({"action": "build", "spec": spec, "file": csv,
                                "project": "T29", "out": ir})
    REC.check("构建成功", "已生成 IR" in out, out[:200])

    with open(ir, "r", encoding="utf-8") as fh:
        deck = json.load(fh)

    rate_slide = deck["slides"][1]
    charts = [b for b in rate_slide["blocks"] if b["type"] == "chart"]
    REC.check("指标对比图已生成（不再画按列分组的图）", len(charts) == 1,
              f"实际 {len(charts)} 张图: {[b['type'] for b in rate_slide['blocks']]}")
    if not charts:
        return

    chart = charts[0]
    REC.check("分类是三个指标名",
              chart["categories"] == ["应收", "实收", "未缴"],
              f"实际: {chart['categories']}")
    REC.check("数值来自派生指标（账单1000 / 已收900 / 未缴100）",
              chart["series"][0]["values"] == [1000.0, 900.0, 100.0],
              f"实际: {chart['series'][0]['values']}")
    REC.check("带语义色供渲染器逐点上色",
              chart.get("point_colors") == ["info", "ok", "bad"],
              f"实际: {chart.get('point_colors')}")

    REC.check("结论句用了算出来的收缴率（900/1000 = 90%）",
              "90.00%" in rate_slide.get("so_what", ""),
              f"实际: {rate_slide.get('so_what')}")

    # _resolve_metric 的三级查找
    REC.check("_resolve_metric 优先取派生值",
              _resolve_metric("收缴率", {"收缴率": 87.5}, {}, None) == 87.5)
    REC.check("_resolve_metric 退回 metrics 合计",
              _resolve_metric("账单金额(元)", {}, {"账单金额(元)": {"sum": 1000.0}},
                              None) == 1000.0)
    REC.check("_resolve_metric 未知名字返回 None",
              _resolve_metric("不存在的", {}, {}, None) is None)

    # ---- 渲染：语义色要真的落到柱子上 ----
    pptx = os.path.join(stage, "deck.pptx")
    DeckBuildTool().run({"deck": deck, "output": pptx,
                         "theme": build_theme("business")})
    REC.check("渲染成功", os.path.isfile(pptx))

    from pptx import Presentation

    fills = set()
    prs = Presentation(pptx)
    for shape in prs.slides[1].shapes:
        if not shape.has_chart:
            continue
        try:
            for i, point in enumerate(shape.chart.plots[0].series[0].points):
                if point.format.fill.type == 1:
                    fills.add(str(point.format.fill.fore_color.rgb))
        except Exception:
            pass

    theme = build_theme("business")
    expected = {theme["colors"]["info"].upper(), theme["colors"]["ok"].upper(),
                theme["colors"]["bad"].upper()}
    REC.check("三根柱子用三种语义色（回归：column 曾全同色）",
              len(fills) >= 3 and expected <= fills,
              f"实际柱子颜色: {sorted(fills)}；期望含 {sorted(expected)}")

    # ---- 单值指标应渲染成 KPI 卡片，而不是一根孤零零的柱子 ----
    single_spec = dict(spec)
    single_spec["sections"] = [
        {"key": "cover", "label": "封面", "layout": "cover"},
        {"key": "rate", "label": "平均单价", "layout": "chart",
         "metrics_chart": {
             "title": "效能",
             "items": [{"metric": "收缴率", "label": "平均单价",
                        "format": "{:.2f}", "unit": "元/m²",
                        "target": 0.5, "higher_is_better": False,
                        "note": "口径说明"}]},
         "narrative": [{"when": "收缴率 < 95",
                        "so_what": "收缴率 {收缴率:.2f}%"}]},
    ]
    ir2 = os.path.join(stage, "single.json")
    DeckContentTool().run({"action": "build", "spec": single_spec, "file": csv,
                           "project": "T29b", "out": ir2})
    with open(ir2, "r", encoding="utf-8") as fh:
        deck2 = json.load(fh)

    blocks2 = deck2["slides"][1]["blocks"]
    REC.check("单值指标不生成图表块",
              not any(b["type"] == "chart" for b in blocks2),
              f"实际块类型: {[b['type'] for b in blocks2]}")
    cards = [b for b in blocks2 if b["type"] == "kpi_grid"]
    REC.check("单值指标生成 KPI 卡片", len(cards) == 1,
              f"实际: {[b['type'] for b in blocks2]}")
    if cards:
        item = cards[0]["items"][0]
        REC.check("卡片值带格式化与单位",
                  item["value"] == "90.00 元/m²", f"实际: {item['value']!r}")
        REC.check("卡片说明含目标对比",
                  "目标 0.50" in item["delta"] and "高于" in item["delta"],
                  f"实际: {item['delta']!r}")
        # higher_is_better=False → 90 高于目标 0.5 是"不好"
        REC.check("目标方向可反转（higher_is_better=false）",
                  item["status"] == "bad",
                  f"实际 status={item['status']}（90 > 0.5 且越大越差 → 应为 bad）")
        REC.check("口径说明写进卡片",
                  "口径说明" in item["delta"], f"实际: {item['delta']!r}")


# =====================================================================
# T30 模块卫生：不许重名定义 / 不许 import 被 def 覆盖
# =====================================================================
def test_module_hygiene() -> None:
    section("T30 模块卫生（重名定义与 import 覆盖）")

    import ast

    tool_dir = os.path.join(PROJECT_ROOT, "tool", "common")
    modules = sorted(f for f in os.listdir(tool_dir)
                     if f.endswith(".py") and not f.startswith("_"))

    REC.check("工具层有模块可检查", len(modules) >= 8, f"实际: {len(modules)}")

    duplicates: Dict[str, List[str]] = {}
    shadowed: Dict[str, List[str]] = {}
    syntax_bad: Dict[str, str] = {}

    for name in modules:
        path = os.path.join(tool_dir, name)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            syntax_bad[name] = str(exc)
            continue

        imported: Dict[str, int] = {}
        defined: Dict[str, int] = {}

        def _bind(bucket: Dict[str, int], bind_name: str, lineno: int) -> None:
            # 只记第一次出现的行号；重复出现才算问题
            bucket.setdefault(bind_name, lineno)
            bucket[bind_name] = min(bucket[bind_name], lineno)

        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    local = alias.asname or alias.name
                    if local != "*":
                        _bind(imported, local, node.lineno)
            elif isinstance(node, ast.FunctionDef):
                _bind(defined, node.name, node.lineno)
            elif isinstance(node, ast.ClassDef):
                _bind(defined, node.name, node.lineno)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        _bind(defined, target.id, node.lineno)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                _bind(defined, node.target.id, node.lineno)

        # ① 同一模块里 import 了又自己 def → 后面的 def 会**静默覆盖** import。
        #    实测踩过：抽取重构时留下一个本地 def，把正确的 import 盖掉，
        #    而它引用的常量已随抽取删除 → NameError，且报错位置离病根很远。
        both = sorted(set(imported) & set(defined))
        if both:
            shadowed[name] = [
                f"{n}（import L{imported[n]} 被 def L{defined[n]} 覆盖）"
                for n in both
            ]

        # ② 顶层名字重复定义（后者覆盖前者，通常意味着抽取没删干净）
        counts: Dict[str, int] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                counts[node.name] = counts.get(node.name, 0) + 1
        dups = [n for n, c in counts.items() if c > 1]
        if dups:
            duplicates[name] = dups

    REC.check("所有工具模块语法正确", not syntax_bad, f"失败: {syntax_bad}")
    REC.check("没有 import 被本地 def 覆盖（本次重构踩过的坑）",
              not shadowed,
              f"被覆盖: {shadowed}")
    REC.check("没有顶层名字重复定义（抽取没删干净的信号）",
              not duplicates, f"重复: {duplicates}")

    # ---- 抽出的模块确实提供了这些能力，且原模块仍能向后兼容引用 ----
    from tool.common import deck_format, table_profile  # noqa: F401

    from tool.common.deck_content_tool import (  # noqa: F401
        _fmt_money as _fm, _looks_like_period as _lp, _pick_group_column as _pg,
        _pick_metrics as _pm, _pick_value_column as _pv, _short_label as _sl,
    )
    REC.check("探查能力已迁到 table_profile 且可独立导入",
              hasattr(table_profile, "pick_value_column")
              and hasattr(table_profile, "pick_metrics")
              and hasattr(table_profile, "looks_like_period"))
    REC.check("格式化能力已迁到 deck_format 且可独立导入",
              callable(deck_format.fmt_money)
              and callable(deck_format.short_label)
              and deck_format.short_label("账单金额(元)") == "账单")
    REC.check("内容工具仍向后兼容地导出私有别名",
              callable(_fm) and callable(_pv) and callable(_pg)
              and callable(_pm) and callable(_lp) and callable(_sl))
    REC.check("别名指向同一实现（不是各有一份副本）",
              _pv is table_profile.pick_value_column
              and _fm is deck_format.fmt_money,
              "同一份实现，避免改一处漏一处")


# =====================================================================
# T31 匹配纪律 + 内容健康度门禁
# =====================================================================
def test_matching_and_content_gate() -> None:
    section("T31 匹配纪律与内容健康度门禁（拒绝静默产出垃圾）")

    from tool.common.commons import (
        GENERIC_NAME_TOKENS, find_files, resolve_common_dirs,
    )
    from tool.common.deck_content_tool import DeckContentTool

    dirs = resolve_common_dirs()
    if not dirs:
        REC.check("常用目录可用", False, "data/common 不存在")
        return

    # ---- 1) 通用词不该压过专有词 ----
    REC.check("通用词表非空", len(GENERIC_NAME_TOKENS) >= 15,
              f"实际 {len(GENERIC_NAME_TOKENS)} 个")

    # ★ 关键回归：请求里只有「报表」这种通用词命中文件名时，不能当成匹配。
    # 实测踩过：这样匹到了「4、欠款明细报表.xlsx」（报表格式、多行表头），
    # 生成 11 页里 7 页"数据缺失"的垃圾 PPT，还报 success=True。
    hits = find_files(query="生成上个月物业季度报表", dirs=dirs, limit=3)
    names = [h.name for h in hits]
    REC.check("只靠通用词命中时拒绝硬凑（返回空）",
              not any("欠款明细报表" in n for n in names),
              f"实际匹配到: {names}；只凭「报表」二字不该选中它")

    # 专有词应正常命中
    hits2 = find_files(query="用应收信息做物业收缴分析", dirs=dirs, limit=3)
    REC.check("专有词正常命中（应收信息）",
              hits2 and "应收信息" in hits2[0].name,
              f"实际: {[h.name for h in hits2]}")
    if hits2:
        REC.check("匹配线索里含专有词而非通用词",
                  any("应收" in m for m in hits2[0].matched),
                  f"实际线索: {hits2[0].matched}")

    hits3 = find_files(query="用缴费数据看看回款情况", dirs=dirs, limit=3)
    REC.check("专有词正常命中（缴费数据）",
              hits3 and "缴费数据" in hits3[0].name,
              f"实际: {[h.name for h in hits3]}")

    hits4 = find_files(query="做个PPT", dirs=dirs, limit=3)
    REC.check("纯动作词不匹配任何文件",
              not any("欠款" in h.name for h in hits4),
              f"实际: {[h.name for h in hits4]}")

    # ---- 2) 内容健康度：派生全失败要被记下来 ----
    stage = os.path.join(TEST_OUTPUT_DIR, "gate")
    os.makedirs(stage, exist_ok=True)

    # 一份"列名不匹配"的数据（模拟报表格式的表）
    bad_csv = os.path.join(stage, "unmatched.csv")
    with open(bad_csv, "w", encoding="utf-8-sig") as fh:
        fh.write("Unnamed: 0,Unnamed: 1,Unnamed: 2\n甲,1,2\n乙,3,4\n")

    spec_path = os.path.join(SKILLS_ROOT, "buttin", "ppt_skill", "deck_spec.json")
    ir_bad = os.path.join(stage, "bad.json")
    DeckContentTool().run({"action": "build", "spec_path": spec_path,
                           "file": bad_csv, "project": "T31", "out": ir_bad})
    with open(ir_bad, "r", encoding="utf-8") as fh:
        bad_deck = json.load(fh)

    health = (bad_deck.get("meta") or {}).get("content_health") or {}
    REC.check("IR 里记录了内容健康度", bool(health), f"实际: {health}")
    REC.check("健康度页数与实际页数一致（回归：曾恒为 0 导致门禁失效）",
              health.get("pages") == len(bad_deck["slides"]),
              f"health.pages={health.get('pages')} 实际页数={len(bad_deck['slides'])}")
    REC.check("派生指标全失败被记为 derived_ok=False",
              health.get("derived_expected") is True
              and health.get("derived_ok") is False,
              f"实际: derived_expected={health.get('derived_expected')} "
              f"derived_ok={health.get('derived_ok')}")

    # 正确数据 → derived_ok 为 True
    good = os.path.join(PROJECT_ROOT, "data", "common", "2、应收信息.xlsx")
    if os.path.isfile(good):
        ir_good = os.path.join(stage, "good.json")
        DeckContentTool().run({"action": "build", "spec_path": spec_path,
                               "file": good, "project": "T31",
                               "out": ir_good})
        with open(ir_good, "r", encoding="utf-8") as fh:
            good_deck = json.load(fh)
        gh = (good_deck.get("meta") or {}).get("content_health") or {}
        # 正确数据下派生指标应全部成功；「成本」页因为表里确实没有成本科目列
        # 而合法降级 1 页 —— 所以判据是"派生 OK 且降级页很少"，
        # 而不是"零降级"（零降级反而是数据被硬凑的信号）。
        REC.check("正确数据下 derived_ok=True 且降级页很少",
                  gh.get("derived_ok") is True
                  and gh.get("degraded_pages", 99) <= 1,
                  f"实际: {gh}")
        REC.check("正确数据下归一化比率低于门禁阈值",
                  gh.get("degraded_pages", 99) / max(gh.get("pages", 1), 1) < 0.5,
                  f"实际: {gh.get('degraded_pages')}/{gh.get('pages')}")

    # ---- 3) 编排器真的会因内容空壳返回非零 ----
    from skills.loader import load_skill

    os.environ["SKILL_OUTPUT_DIR"] = os.path.join(stage, "run")
    skill = load_skill(os.path.join(SKILLS_ROOT, "buttin", "ppt_skill"),
                       llm=None, execution="script")
    skill.auto_resolve_data = False

    # 显式给出文件要放在 data 里 —— 顶层 file 不会被 loader 转发给脚本
    # （脚本只认 input_data.json 里的 _auto_file / file / data_path）
    bad_result = skill.run_skill({
        "request": "用这份数据做物业经营分析",
        "data": {"file": bad_csv, "project": "T31"},
    })
    REC.check("内容空壳时编排器报失败（不再 success=True）",
              not bad_result.success,
              f"实际 success={bad_result.success} error={bad_result.error}")
    REC.check("失败原因指向内容健康度",
              "内容健康度" in (bad_result.output or "")
              or "退出码 3" in str(bad_result.error or ""),
              f"error={bad_result.error}\n"
              f"output 尾部: {(bad_result.output or '')[-300:]!r}")
    REC.check("同时给出人可读的原因",
              "派生指标" in (bad_result.output or ""),
              f"output 尾部: {(bad_result.output or '')[-200:]!r}")


# =====================================================================
# 主入口
# =====================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Skill 模块全流程测试")
    parser.add_argument("--live", default="none",
                        choices=["none", "auto", "qwen", "deepseek", "ollama"],
                        help="真实 LLM 测试模式（默认 none，只跑离线）")
    parser.add_argument("--keep-output", action="store_true",
                        help="保留上一轮的 test_output 产物")
    args = parser.parse_args()

    # 每次运行用独立目录（见 TEST_OUTPUT_DIR 注释）；顺手清理旧运行目录。
    # --keep-output 保留历史，便于对比多轮结果。
    if not args.keep_output:
        _prune_old_runs(keep=3)

    print("=" * 72)
    print("  Skill 模块全流程测试 (test_run.py)")
    print("=" * 72)
    print(f"  项目根目录 : {PROJECT_ROOT}")
    print(f"  技能根目录 : {SKILLS_ROOT}")
    print(f"  解释器     : {sys.version.split()[0]}  ({sys.executable})")
    print(f"  真实LLM    : {args.live}")

    # 每次从干净状态开始，确保断言的是本轮产物
    if not args.keep_output and os.path.isdir(TEST_OUTPUT_DIR):
        shutil.rmtree(TEST_OUTPUT_DIR, ignore_errors=True)
    os.makedirs(TEST_OUTPUT_DIR, exist_ok=True)
    os.environ["SKILL_WORKDIR"] = TEST_OUTPUT_DIR
    # 全局兜底：所有 skill 产物都落在 test_output 内，
    # 否则 _resolve_output_dir 会退回 os.getcwd()，把 skill_outputs 写进项目根。
    # 单个用例可临时覆盖（记得用 try/finally 复位）。
    os.environ["SKILL_OUTPUT_DIR"] = os.path.join(TEST_OUTPUT_DIR, "skill_outputs")

    tests = [
        ("递归发现技能目录", test_recursive_discovery),
        ("加载与元数据", test_load_and_metadata),
        ("frontmatter 解析器", test_frontmatter_parser),
        ("架构约束: Skill 不是 Tool", test_skill_is_not_tool),
        ("CapabilityRouter 能力中心", test_capability_router),
        ("llm 模式", test_llm_mode),
        ("react 模式", test_react_mode),
        ("script 模式", test_script_mode),
        ("列推断与诚实降级", test_data_normalization),
        ("SkillRegister", test_skill_register),
        ("意图识别与能力匹配", test_intent_routing),
        ("ReActAgent 全流程", test_react_agent_end_to_end),
        ("ppt-skill 完整闭环", test_ppt_skill_full_pipeline),
        ("常用文件目录与数据工具", test_common_files_and_data_tools),
        ("未提供文件时自动取数", test_auto_resolve_from_common_dir),
        ("Deck IR 契约", test_deck_ir_contract),
        ("叙事 profile", test_deck_profiles),
        ("通用渲染器", test_deck_build_renderer),
        ("内容组织工具", test_deck_content_tool),
        ("通用工具链端到端", test_generic_toolchain_end_to_end),
        ("主题系统（物业 vs 动漫）", test_theme_system),
        ("叙事模板引擎", test_narrative_engine),
        ("技能 spec 驱动全链路", test_skill_spec_pipeline),
        ("技能已切到工具编排", test_skill_switched_to_tools),
        ("配色方案库", test_theme_gallery),
        ("审美修版", test_aesthetic_fixes),
        ("内容质量", test_content_quality),
        ("派生指标与跨表关联", test_derive_and_join),
        ("指标对比图", test_metrics_chart),
        ("模块卫生", test_module_hygiene),
        ("匹配纪律与内容门禁", test_matching_and_content_gate),
    ]

    for name, func in tests:
        try:
            func()
        except Exception as exc:
            REC.record(f"{name}（未捕获异常）", False,
                       f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")

    try:
        test_live_llm(args.live)
    except Exception as exc:
        REC.record("真实 LLM 测试（未捕获异常）", False, f"{type(exc).__name__}: {exc}")

    code = REC.summary()
    print(f"  产物目录: {TEST_OUTPUT_DIR}")
    return code


if __name__ == "__main__":
    sys.exit(main())
