# -*- coding: utf-8 -*-
"""Skill 模块 —— 技能加载与执行

设计目标
--------
1. **递归发现**：`skills/` 下任意深度的目录，只要包含 `SKILL.md` 就是一个技能。
   例如 `skills/property/ppt_skill` 会被自动注册。
2. **标准元数据**：解析 `SKILL.md` 顶部的 YAML frontmatter（name / description /
   execution / script / priority / always），缺失时按目录名与正文自动推断。
3. **多执行模式**：一个技能可以按三种方式执行
   - ``llm``    ：把技能文档当 system prompt，单次调用 LLM（原行为）
   - ``script`` ：执行技能自带脚本（如 ``ppt_skill/scripts/build_property_deck.py``），产出真实文件
   - ``react``  ：技能内部再跑一轮 ReAct 循环，允许调用子工具
   ``auto``（默认）会自动选择：有脚本走脚本，否则走 llm。

4. **可被 ReActAgent 调用**：`Skill` 继承 `Tool`，注册进 `ToolRegister` 后##
   Agent 就能通过 tool_calls 触发整个技能流程。

SKILL.md 支持的 frontmatter 字段
-------------------------------
    ---
    name: ppt-property-monthly-analysis   # 展示名（可选）
    description: 一句话能力描述            # 进入工具 schema，很重要
    execution: script                      # auto | llm | script | react
    script: scripts/build_deck.py          # execution=script 时执行
    script_args: ["--out", "deck.pptx"]    # 传给脚本的参数（可选）
    timeout: 300                           # 脚本超时秒数（可选）
    priority: 10                           # 排序权重（可选）
    ---
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.paths import PROJECT_ROOT
from tools.tools import ToolParameter
from tools.register import ToolRegister
from core.capability import (
    CapabilityKind,
    CapabilityRouter,
    build_function_schema,
)

__all__ = [
    "SkillError",
    "SkillResult",
    "SkillMeta",
    "Skill",
    "SkillRegister",
    "parse_frontmatter",
    "discover_skill_dirs",
    "load_skill",
    "load_skills_from_directory",
    "register_skills_from_directory",
]

# 递归发现时跳过的目录名（缓存 / 资源目录，不是技能本体）
SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".svn",
    ".hg",
    ".idea",
    ".vscode",
    ".pytest_cache",
    "node_modules",
}

# 不参与拼接进技能指令的目录（体积大 / 二进制 / 产出目录）
NON_INSTRUCTION_DIRS = {
    "assets",
    "asset",
    "scripts",
    "script",
    "preview",
    "previews",
    "output",
    "outputs",
    "data",
    "media",
    "images",
    "img",
}

#: Files at a skill root that are attribution/readme material, not instructions.
NON_INSTRUCTION_FILES = {
    "readme.md",
    "sponsors.md",
    "sponsors_cn.md",
}

# tool_calls 中最多回传给模型的字符数，避免上下文爆炸
MAX_TOOL_RESULT_CHARS = 12000


class SkillError(Exception):
    """技能加载或执行异常"""


# ===================================================================
# frontmatter 解析
# ===================================================================

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")
_LIST_RE = re.compile(r"^-\s*(.+)$")


def _coerce_scalar(raw: str) -> Any:
    """把 frontmatter 的字符串值转成合适的 Python 类型。"""
    value = raw.strip()
    if not value:
        return ""
    # 去引号
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    lowered = value.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "none", "~"):
        return None
    # 内联列表 [a, b, c]
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, (list, tuple)):
                return [_coerce_scalar(str(x)) for x in parsed]
        except (ValueError, SyntaxError):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [_coerce_scalar(p) for p in inner.split(",")]
    # 数字
    try:
        if re.fullmatch(r"-?\d+", value):
            return int(value)
        if re.fullmatch(r"-?\d+\.\d+", value):
            return float(value)
    except ValueError:  # pragma: no cover - 正则已保证
        pass
    return value


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """解析 SKILL.md 顶部的 YAML frontmatter。

    只支持框架需要的子集：`key: value`、块列表（`key:` + 若干 `- item`）、
    内联列表。不引入 PyYAML 依赖。

    Returns:
        (meta, body)：元数据字典 + 去掉 frontmatter 后的正文。
    """
    if not text:
        return {}, ""

    # 容忍 BOM
    if text.startswith("\ufeff"):
        text = text[1:]

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    meta: Dict[str, Any] = {}
    current_key: Optional[str] = None

    for raw_line in match.group(1).splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        stripped = line.strip()

        # 块列表项
        if stripped.startswith("- ") and current_key:
            item = _coerce_scalar(stripped[2:])
            existing = meta.get(current_key)
            if isinstance(existing, list):
                existing.append(item)
            else:
                meta[current_key] = [item]
            continue

        key_match = _KEY_RE.match(stripped)
        if key_match:
            key = key_match.group(1).strip().lower()
            value = key_match.group(2)
            if value.strip() == "":
                # 可能是块列表的开头
                meta[key] = []
                current_key = key
            else:
                meta[key] = _coerce_scalar(value)
                current_key = key
            continue

        # 续行：拼接到上一个 key
        if current_key and isinstance(meta.get(current_key), str):
            meta[current_key] = f"{meta[current_key]} {stripped}".strip()

    body = text[match.end():]
    return meta, body


# ===================================================================
# 技能目录发现
# ===================================================================

def discover_skill_dirs(skills_root: str, recursive: bool = True) -> List[str]:
    """递归找出所有包含 SKILL.md 的技能目录（按路径排序）。

    约定：只要目录里有 `SKILL.md` 就是技能。因此
    `skills/property` 本身不是技能（没有 SKILL.md），
    但 `skills/property/ppt_skill` 是。
    """
    if not os.path.isdir(skills_root):
        return []

    found: List[str] = []
    root = os.path.abspath(skills_root)

    if not recursive:
        for entry in sorted(os.listdir(root)):
            candidate = os.path.join(root, entry)
            if not os.path.isdir(candidate):
                continue
            if os.path.isfile(os.path.join(candidate, "SKILL.md")):
                found.append(candidate)
        return found

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIR_NAMES)
        if "SKILL.md" in filenames:
            found.append(dirpath)
            # 技能目录内部不再往下找嵌套技能
            dirnames[:] = []

    return sorted(found)


# ===================================================================
# 技能文档加载
# ===================================================================

def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _collect_markdown(skill_dir: str) -> List[Tuple[str, str]]:
    """收集技能目录下的 .md 文件（SKILL.md 优先，其余按相对路径排序）。

    跳过 assets/scripts 等非说明性目录。
    """
    collected: List[Tuple[str, str]] = []

    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIR_NAMES and d not in NON_INSTRUCTION_DIRS
        )
        for name in sorted(filenames):
            if not name.lower().endswith(".md"):
                continue
            if dirpath == skill_dir and name.lower() in NON_INSTRUCTION_FILES:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, skill_dir).replace("\\", "/")
            collected.append((rel, full))

    def sort_key(item: Tuple[str, str]) -> Tuple[int, str]:
        rel = item[0]
        # SKILL.md 永远第一
        return (0 if rel.lower() == "skill.md" else 1, rel.lower())

    collected.sort(key=sort_key)
    return collected


def load_skill_document(skill_dir: str) -> Tuple[Dict[str, Any], str]:
    """读取技能目录，返回 (frontmatter, 合并后的指令文本)。

    合并规则：SKILL.md 最前，其余 .md 按相对路径排序拼接。
    """
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        raise SkillError(f"技能目录缺少 SKILL.md: {skill_dir}")

    parts: List[str] = []
    meta: Dict[str, Any] = {}

    for rel, full in _collect_markdown(skill_dir):
        text = _read_text(full)
        if rel.lower() == "skill.md":
            file_meta, body = parse_frontmatter(text)
            meta.update(file_meta)
            text = body.strip()
        parts.append(f"<!-- BEGIN {rel} -->\n{text.strip()}\n<!-- END {rel} -->")

    return meta, "\n\n".join(parts)


def normalize_skill_id(name: str) -> str:
    """把目录名/技能名规范成可作为工具名的 id。"""
    slug = re.sub(r"[^0-9A-Za-z_]+", "_", (name or "").strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        slug = "skill"
    if slug[0].isdigit():
        slug = f"s_{slug}"
    return slug


def _first_heading(text: str) -> Optional[str]:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def _first_paragraph(text: str, limit: int = 160) -> Optional[str]:
    """取正文第一段非标题文字，作为 description 兜底。"""
    buffer: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if buffer:
                break
            continue
        if stripped.startswith(("#", "<!--", "|", "```", "---")):
            if buffer:
                break
            continue
        buffer.append(stripped)
    if not buffer:
        return None
    paragraph = re.sub(r"\s+", " ", " ".join(buffer)).strip()
    return paragraph[:limit]


# ===================================================================
# 执行结果
# ===================================================================

@dataclass
class SkillResult:
    """一次技能执行的完整结果。"""

    skill: str
    mode: str
    success: bool
    output: str = ""
    artifacts: List[str] = field(default_factory=list)
    error: Optional[str] = None
    duration_s: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        """序列化成回传给 Agent 的文本。"""
        status = "成功" if self.success else "失败"
        head = f"[技能 {self.skill}] 执行{status}（模式: {self.mode}，耗时 {self.duration_s:.2f}s）"
        lines = [head]

        if self.artifacts:
            lines.append("产出文件:")
            lines.extend(f"  - {path}" for path in self.artifacts)

        if self.error:
            lines.append(f"错误: {self.error}")

        if self.output:
            lines.append("结果:")
            lines.append(self.output)

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill": self.skill,
            "mode": self.mode,
            "success": self.success,
            "output": self.output,
            "artifacts": list(self.artifacts),
            "error": self.error,
            "duration_s": self.duration_s,
        }


@dataclass
class SkillMeta:
    """技能的静态元数据（不触发执行）。"""

    skill_id: str
    tool_name: str
    display_name: str
    description: str
    directory: str
    relative_path: str
    execution: str
    script: Optional[str]
    frontmatter: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "tool_name": self.tool_name,
            "display_name": self.display_name,
            "description": self.description,
            "directory": self.directory,
            "relative_path": self.relative_path,
            "execution": self.execution,
            "script": self.script,
        }


# ===================================================================
# Skill：**不继承 Tool**，实现 Capability 协议
# ===================================================================

class Skill:
    """基于 Markdown 技能文件构建的 Skill。

    架构定位
    --------
    Skill 与 Tool 是**同级能力**，都实现 `capability.Capability` 协议，
    但 Skill **不继承 Tool**：

        Tool  = 确定性能力（计算、HTTP、检索），接口是契约
        Skill = 说明书 + 流水线，接口是自然语言 + 可选脚本

    两者由 `capability.CapabilityRouter` 统一持有，Agent 在
    「意图识别 → 规划 → 能力匹配」之后决定用哪一个，可以混用。

    运行流程（典型）::

        ReActAgent
          ├─ 意图识别：这个请求要干什么？→ 选出候选能力
          ├─ 规划：分成几步
          └─ tool_call: skill_ppt_skill({"request": "..."})
               └─ Skill.run()
                    ├─ execution=script → 执行 scripts/build_deck.py → 产出 .pptx
                    ├─ execution=react  → 技能内部 ReAct 循环（可调用子能力）
                    └─ execution=llm    → 技能文档作 system prompt，单次调用 LLM
               └─ 结果文本回填给 Agent，Agent 继续推理或给出最终答案

    技能内不再次调用自己，避免无限递归。
    """

    #: 能力种类（capability.Capability 协议要求）
    kind = CapabilityKind.SKILL

    DEFAULT_SCRIPT_TIMEOUT = 300

    def __init__(
        self,
        skill_dir: str,
        skill_id: str,
        llm: Any,
        name: Optional[str] = None,
        description: Optional[str] = None,
        execution: str = "auto",
        script: Optional[str] = None,
        script_args: Optional[List[str]] = None,
        script_code: Optional[str] = None,
        script_timeout: Optional[int] = None,
        param_description: str = "用户的请求/输入。请把完整需求、原始数据与约束写在这里。",
        react_llm: Optional[Any] = None,
        react_tools: Optional[Any] = None,
        react_max_steps: int = 5,
        verbose: bool = False,
        tool_prefix: str = "skill_",
        meta: Optional[Dict[str, Any]] = None,
        tags: Optional[Sequence[str]] = None,
        auto_resolve_data: bool = True,
        **_: Any,
    ):
        if not os.path.isdir(skill_dir):
            raise SkillError(f"技能目录不存在: {skill_dir}")

        self.skill_dir = os.path.abspath(skill_dir)
        self.skill_id = normalize_skill_id(skill_id)
        self.tool_prefix = tool_prefix

        frontmatter, document = load_skill_document(self.skill_dir)
        self.frontmatter: Dict[str, Any] = dict(frontmatter)
        if meta:
            self.frontmatter.update(meta)
        self._document = document
        self._llm = llm

        # 展示名：参数 > frontmatter.name > 一级标题 > 目录名
        self.display_name = (
            name
            or self.frontmatter.get("name")
            or _first_heading(document)
            or self.skill_id
        )

        self.script = self._resolve_script(script)
        self.script_args = list(script_args or [])
        self.script_code = script_code
        self.script_timeout = int(script_timeout or self.DEFAULT_SCRIPT_TIMEOUT)
        self.execution = self._normalize_execution(execution)

        # ReAct 模式使用的 LLM / 能力表
        self.react_llm = react_llm or llm
        self.react_tools = react_tools
        self.react_max_steps = react_max_steps
        self.verbose = verbose
        # 用户没给数据时，是否自动去常用目录找文件并抽数
        self.auto_resolve_data = bool(auto_resolve_data)

        # ---- 实现 Capability 协议（不再继承 Tool）----
        self.name = f"{self.tool_prefix}{self.skill_id}"
        self.description = description or self._build_description()
        self.tags = [str(t) for t in (tags or self.frontmatter.get("tags") or [])]

        self._params: List[ToolParameter] = [
            ToolParameter(
                name="request",
                type="string",
                description=param_description,
                required=True,
            )
        ]

        # script 模式（尤其是「数据 → 文件」类技能）需要接收结构化数据。
        # 没有这个参数，Agent 只能触发脚本用默认空模板跑，
        # 结果就是一份全是 0 的假 PPT。
        if self.effective_mode() == "script" or self.script_code:
            self._params.append(
                ToolParameter(
                    name="data",
                    type="object",
                    description=self._build_data_param_description(),
                    required=False,
                    default=None,
                )
            )

    def _build_data_param_description(self) -> str:
        """构造 data 参数的说明。

        关键：把技能要求的**确切字段名**告诉模型。
        否则模型会自由发挥键名（实测同一请求两次分别给出
        「成本」和「实际成本」、「效能」和「人均效能」），
        技能就得靠别名去猜，既不可靠也难维护。
        """
        base = (
            "技能执行所需的结构化业务数据（JSON 对象）。"
            "请把用户提供的原始数据填进来；缺失的字段可省略，"
            "但不要留空，否则技能只能产出模板示例。"
        )
        hint = (self.frontmatter.get("data_schema")
                or self.frontmatter.get("data_contract"))
        if hint:
            return f"{base}\n字段说明：{hint}"
        return base

    # ---------------- 构造辅助 ----------------

    def _resolve_script(self, script: Optional[str]) -> Optional[str]:
        """定位脚本并**返回绝对路径**。

        返回相对路径会让后续的 cwd / PYTHONPATH 计算全部错位，
        因此这里统一 abspath。
        """
        candidate = script or self.frontmatter.get("script")
        if not candidate:
            # 默认脚本：优先编排器 run_skill.py，其次历史名 build_deck.py
            for default_name in ("run_skill.py", "build_deck.py"):
                default = os.path.join(self.skill_dir, "scripts", default_name)
                if os.path.isfile(default):
                    return os.path.abspath(default)
            return None

        candidate = str(candidate).strip()
        if not os.path.isabs(candidate):
            candidate = os.path.join(self.skill_dir, candidate)

        candidate = os.path.abspath(candidate)
        return candidate if os.path.isfile(candidate) else None

    def _normalize_execution(self, execution: Optional[str]) -> str:
        raw = (execution or self.frontmatter.get("execution") or "auto")
        mode = str(raw).strip().lower()
        if mode not in ("auto", "llm", "script", "react"):
            mode = "auto"
        return mode

    def _build_description(self) -> str:
        desc = self.frontmatter.get("description")
        if not desc:
            desc = _first_paragraph(self._document) or f"执行 '{self.display_name}' 技能。"
        return str(desc).strip()

    def effective_mode(self) -> str:
        """auto 模式下根据技能内容决定实际执行模式。"""
        if self.execution != "auto":
            return self.execution
        if self.script or self.script_code:
            return "script"
        return "llm"

    @property
    def meta(self) -> SkillMeta:
        rel = os.path.basename(self.skill_dir)
        parent = os.path.basename(os.path.dirname(self.skill_dir))
        return SkillMeta(
            skill_id=self.skill_id,
            tool_name=self.name,
            display_name=self.display_name,
            description=self.description,
            directory=self.skill_dir,
            relative_path=f"{parent}/{rel}" if parent else rel,
            execution=self.effective_mode(),
            script=self.script,
            frontmatter=dict(self.frontmatter),
        )

    # ---------------- Tool 接口 ----------------

    def get_params(self) -> List[ToolParameter]:
        return self._params

    def set_llm(self, llm: Any) -> None:
        """替换 LLM（测试或运行时热切换用）。"""
        self._llm = llm
        self.react_llm = llm

    def run(self, params: Dict[str, Any]) -> str:
        """Tool 入口：执行技能并返回文本结果。"""
        return self.run_skill(params).to_text()

    def run_skill(self, params: Optional[Dict[str, Any]] = None, **kwargs: Any) -> SkillResult:
        """执行技能，返回结构化结果（测试与编排使用）。

        ``params`` 里除 ``request`` 之外的 ``temperature`` / ``max_tokens`` /
        ``top_p`` 等键会被转发给 LLM；``workdir`` / ``output_dir`` 用于脚本模式。
        """
        params = dict(params or {})
        request = self._extract_request(params, kwargs)
        llm_kwargs = self._extract_llm_kwargs(params)

        mode = self.effective_mode()
        started = time.perf_counter()

        try:
            if mode == "script" and (self.script or self.script_code):
                result = self._run_script(request, params)
            elif mode == "react":
                result = self._run_react(request, params, llm_kwargs)
            else:
                result = self._run_llm(request, params, llm_kwargs)
        except Exception as exc:  # 技能失败不应炸掉整个 Agent
            result = SkillResult(
                skill=self.skill_id,
                mode=mode,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )

        result.duration_s = time.perf_counter() - started
        if self.verbose:
            print(result.to_text())
        return result

    # ---------------- 参数处理 ----------------

    #: 可以从 params 转发给 LLM 的生成参数
    LLM_KWARG_KEYS = (
        "temperature",
        "max_tokens",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "stop",
        "seed",
    )

    @classmethod
    def _extract_llm_kwargs(cls, params: Dict[str, Any]) -> Dict[str, Any]:
        return {k: params[k] for k in cls.LLM_KWARG_KEYS if params.get(k) is not None}

    @staticmethod
    def _extract_request(params: Dict[str, Any], kwargs: Dict[str, Any]) -> str:
        for key in ("request", "input", "input_text", "query", "task", "text"):
            value = params.get(key)
            if value:
                return str(value)
        if kwargs:
            for key in ("request", "input", "input_text", "query", "task"):
                value = kwargs.get(key)
                if value:
                    return str(value)
        # 退化：把整个 params 序列化，避免丢失信息
        return json.dumps(params, ensure_ascii=False) if params else ""

    def _build_messages(self, request: str) -> List[Dict[str, Any]]:
        system_prompt = (
            "你是以下技能的专家执行者。请严格按照技能文档的指示完成用户请求。\n\n"
            f"{self._document}"
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request},
        ]

    # ---------------- 模式 1：单次 LLM ----------------

    def _run_llm(self, request: str, params: Dict[str, Any],
                 llm_kwargs: Optional[Dict[str, Any]] = None) -> SkillResult:
        if self._llm is None:
            return SkillResult(
                skill=self.skill_id,
                mode="llm",
                success=False,
                error="未配置 LLM，无法执行 llm 模式",
            )

        response = self._llm.invoke(
            messages=self._build_messages(request), **(llm_kwargs or {})
        )
        content = getattr(response, "content", None) or ""
        return SkillResult(
            skill=self.skill_id,
            mode="llm",
            success=bool(content.strip()),
            output=content,
        )

    # ---------------- 模式 2：ReAct 循环 ----------------

    def _run_react(self, request: str, params: Dict[str, Any],
                   llm_kwargs: Optional[Dict[str, Any]] = None) -> SkillResult:
        llm = self.react_llm or self._llm
        if llm is None:
            return SkillResult(
                skill=self.skill_id,
                mode="react",
                success=False,
                error="未配置 LLM，无法执行 react 模式",
            )

        registry = self.react_tools or ToolRegister()
        messages = self._build_messages(request)
        last_content = ""
        used_tools: List[str] = []

        for _ in range(max(1, self.react_max_steps)):
            schemas = self._registry_schemas(registry)
            response = llm.invoke(
                messages=messages,
                tools=schemas or None,
                **(llm_kwargs or {}),
            )
            last_content = getattr(response, "content", None) or ""
            tool_calls = getattr(response, "tool_calls", None) or []

            if not tool_calls:
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": last_content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in tool_calls
                    ],
                }
            )

            for call in tool_calls:
                name, call_args = self._parse_tool_call(call)
                if name == self.name:
                    # 防止技能递归调用自身
                    output = "已跳过：技能不能在内部递归调用自己。"
                else:
                    output = self._registry_execute(registry, name, call_args)
                used_tools.append(name)
                if len(output) > MAX_TOOL_RESULT_CHARS:
                    output = output[:MAX_TOOL_RESULT_CHARS] + "\n...(结果已截断)"
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": output}
                )

        return SkillResult(
            skill=self.skill_id,
            mode="react",
            success=bool(last_content.strip()),
            output=last_content,
            meta={"tools_used": used_tools},
        )

    @staticmethod
    def _parse_tool_call(tool_call: Any) -> Tuple[str, Dict[str, Any]]:
        function = getattr(tool_call, "function", None)
        name = getattr(function, "name", "") if function else ""
        raw_args = getattr(function, "arguments", "") if function else ""
        if isinstance(raw_args, dict):
            return name, raw_args
        try:
            parsed = json.loads(raw_args) if raw_args else {}
        except (json.JSONDecodeError, TypeError):
            parsed = {}
        return name, parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _registry_schemas(registry: Any) -> List[Dict[str, Any]]:
        """兼容 CapabilityRouter 与旧 ToolRegister 两种能力表。"""
        if registry is None:
            return []
        for method_name in ("schemas", "get_tools_schema"):
            method = getattr(registry, method_name, None)
            if callable(method):
                return method() or []
        return []

    @staticmethod
    def _registry_execute(registry: Any, name: str, params: Dict[str, Any]) -> str:
        """执行子能力，兼容 CapabilityRouter / ToolRegister / 裸 dict。"""
        if registry is None:
            return f"未配置能力表，无法调用 '{name}'"

        # CapabilityRouter（推荐路径）
        execute = getattr(registry, "execute", None)
        if callable(execute):
            return str(execute(name, params))

        # 旧 ToolRegister：取 Tool 后自行执行
        getter = getattr(registry, "get_tool", None) or getattr(registry, "get", None)
        if callable(getter):
            target = getter(name)
            if target is None:
                return f"未找到能力 '{name}'"
            try:
                return str(target.run(params))
            except Exception as exc:
                return f"能力 '{name}' 执行失败: {exc}"

        return f"能力表不支持执行 '{name}'"

    # ---------------- 模式 3：执行脚本 ----------------

    def _run_script(self, request: str, params: Dict[str, Any]) -> SkillResult:
        artifacts: List[str] = []

        env = self._build_script_env(params)
        workdir = self._resolve_workdir(params)
        os.makedirs(workdir, exist_ok=True)

        output_dir = self._resolve_output_dir(params, workdir)
        os.makedirs(output_dir, exist_ok=True)

        run_id = f"{self.skill_id}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        run_dir = os.path.join(output_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)

        # ---- 数据注入：这是「真的制作 PPT」与「填个空模板」的分界线 ----
        # Agent 通过 params["data"] 传结构化数据（dict 或 JSON 字符串），
        # 这里落盘成 JSON，并通过 SKILL_INPUT_DATA 交给脚本。
        data_path = self._write_input_data(params, run_dir)

        # ---- 用户没给数据时：自动解析文件名 → 去常用目录找文件 → 抽数 ----
        auto_note: List[str] = []
        if data_path is None and self.auto_resolve_data:
            data_path, auto_note = self._auto_resolve_data(request, params, run_dir)

        if data_path:
            env["SKILL_INPUT_DATA"] = data_path

        # 脚本产物应当回到工作区，而不是散落在技能包里
        env["SKILL_OUTPUT_PATH"] = os.path.join(run_dir, "deck.pptx")
        env["SKILL_SPEECH_PATH"] = os.path.join(run_dir, "deck.speech.json")
        env["SKILL_CHECKS_PATH"] = os.path.join(run_dir, "checks.json")

        env.update(
            {
                "SKILL_ID": self.skill_id,
                "SKILL_DIR": self.skill_dir,
                "SKILL_OUTPUT_DIR": output_dir,
                "SKILL_RUN_DIR": run_dir,
                "SKILL_RUN_ID": run_id,
                "SKILL_REQUEST": request or "",
                # 脚本可据此决定是否允许回退到项目默认数据。
                "SKILL_AUTO_RESOLVE_DATA": "1" if self.auto_resolve_data else "0",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            }
        )

        if self.script_code:
            # 内联代码：把技能目录放进 sys.path，便于 import 同目录模块
            script_dir = os.path.dirname(self.script) if self.script else self.skill_dir
            if script_dir:
                env["SKILL_SCRIPT_DIR"] = script_dir
            executable = self._interpreter()
            cmd = [executable, "-c", self.script_code] + self.script_args
        else:
            executable = self._interpreter()
            cmd = [executable, self.script, *self.script_args]

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                cmd,
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.script_timeout,
            )
        except subprocess.TimeoutExpired:
            return SkillResult(
                skill=self.skill_id,
                mode="script",
                success=False,
                error=f"脚本执行超时（>{self.script_timeout}s）: {self.script or 'inline-code'}",
            )
        except OSError as exc:
            return SkillResult(
                skill=self.skill_id,
                mode="script",
                success=False,
                error=f"脚本启动失败: {exc}",
            )

        duration = time.perf_counter() - started
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        success = completed.returncode == 0

        # 收集产出：只认 run_dir 内的文件。
        # 注意：脚本默认输出位置改成工作区后，产物都落在 run_dir；
        # 之前还会去扫 workdir（=脚本目录），结果把 build_deck.py 这类
        # 技能自带的源文件也算成"产出"，属于噪音，已去掉。
        for dirpath, _dirnames, filenames in os.walk(run_dir):
            for fname in sorted(filenames):
                artifacts.append(os.path.join(dirpath, fname))

        # 检测「数据不足」：脚本往往能跑成功，但产出的是空模板。
        # 必须把这个信号显式回传给 Agent，否则模型会把一份全 0 的
        # 报告当成"已完成"交付给用户 —— 实测真实 LLM 就会这么做。
        warnings = self._detect_data_warnings(stdout, run_dir)

        output_parts: List[str] = []
        if auto_note:
            # 让 Agent 和用户都知道数据是自动找来的，来源可追溯
            output_parts.append(
                "📎 自动取数（用户未提供数据，已从常用文件目录提取）:\n"
                + "\n".join(f"  {line}" for line in auto_note)
            )
        if stdout:
            output_parts.append(stdout)
        if stderr:
            output_parts.append(f"[stderr]\n{stderr}")
        if warnings:
            output_parts.append(
                "⚠ 数据不足警告（产物可能只是空模板，请勿当成正式汇报交付）:\n"
                + "\n".join(f"  - {w}" for w in warnings)
            )

        return SkillResult(
            skill=self.skill_id,
            mode="script",
            success=success,
            output="\n".join(output_parts),
            artifacts=artifacts,
            error=None if success else f"脚本退出码 {completed.returncode}",
            duration_s=duration,
            meta={
                "run_dir": run_dir,
                "command": cmd,
                "warnings": warnings,
                "input_data": data_path,
                "auto_resolved": bool(auto_note),
                "auto_note": auto_note,
            },
        )

    # 与 build_deck 契约对应的必填数据组（仅用于「输入完全没给数据」的粗判）
    REQUIRED_DATA_GROUPS = {
        "收入": ("物业费", "公共收益", "多种经营", "其他"),
        "成本": ("人力成本", "公共能耗", "外包服务费", "维修维保",
                 "物料耗材", "管理费及其他"),
        "收缴": ("当月应收", "当月实收"),
        "账龄": ("0-30天", "31-90天", "91-180天", "180天以上"),
        "效能": ("人均管理面积", "人均产值", "人工成本率", "人均工单量"),
    }

    def _detect_data_warnings(self, stdout: str, run_dir: str) -> List[str]:
        """判断这次脚本产出是否基于空数据。

        设计取舍：**不去复刻脚本的字段归一化逻辑**。
        脚本自己已经会打「缺少数据组」提示，loader 只做两件事：

          1. 把脚本的缺数据提示提取成结构化信号；
          2. 检查项目名是否仍是占位值（脚本不会为这个报警）。

        刻意不做「逐组检查 input_data.json 的键」——
        模型传进来的键名千变万化（实际成本 / 人均效能 …），
        在 loader 里重做一遍归一化只会产生误报。
        """
        warnings: List[str] = []

        # 1) 脚本自己打的缺数据提示（最可信）
        for line in (stdout or "").splitlines():
            if "缺少数据组" in line or "待补" in line:
                warnings.append(line.strip())

        # 2) 项目名是否仍是占位值 —— 读脚本实际用到的 deck.deck.json
        project = ""
        meta_path = os.path.join(run_dir, "deck.deck.json")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8-sig") as handle:
                    project = str(json.load(handle).get("项目") or "").strip()
            except (json.JSONDecodeError, OSError):
                project = ""
        if project in ("XX 小区", "XX小区", ""):
            warnings.append(
                "未提供项目名称（当前为占位值），请在 request 里说明项目，"
                "或通过 data 传入「项目」字段"
            )

        # 3) 输入里完全没有任何数据分组 → 明确告知需要补数据
        input_path = os.path.join(run_dir, "input_data.json")
        if not os.path.isfile(input_path):
            warnings.append(
                "本次未收到任何结构化数据（data 参数为空），"
                "产出仅为模板示例，正式汇报前必须补真实数据"
            )

        # 去重保序
        seen = set()
        unique: List[str] = []
        for item in warnings:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    @staticmethod
    def _interpreter() -> str:
        return os.environ.get("SKILL_PYTHON") or sys.executable

    def _resolve_workdir(self, params: Dict[str, Any]) -> str:
        explicit = params.get("workdir") or params.get("output_dir")
        if explicit:
            return os.path.abspath(str(explicit))
        env_dir = os.environ.get("SKILL_WORKDIR")
        if env_dir:
            return os.path.abspath(env_dir)
        # 默认：脚本所在目录。这样 `python -c "import build_deck"` 之类的
        # 内联调用能直接 import 到脚本模块（cwd 在 sys.path 首位）。
        if self.script:
            return os.path.dirname(self.script)
        return os.path.join(self.skill_dir, "output")

    def _resolve_output_dir(self, params: Dict[str, Any], workdir: str) -> str:
        """决定脚本产出的落盘目录。

        关键：**默认写到工作区的 outputs/skills/，而不是技能包内部**。
        技能包是只读资产，往里面写产物既污染技能、又违反「技能可移植」的约定，
        而且 Agent 事后很难找到文件。
        """
        explicit = params.get("output_dir") or params.get("artifact_dir")
        if explicit:
            return os.path.abspath(str(explicit))

        env_dir = os.environ.get("SKILL_OUTPUT_DIR")
        if env_dir:
            return os.path.abspath(env_dir)

        # 工作区（会话目录）优先；退化到 workdir
        workspace = os.environ.get("SKILL_WORKSPACE") or str(PROJECT_ROOT)
        return os.path.join(os.path.abspath(workspace), "outputs", "skills")

    def _auto_resolve_data(
        self, request: str, params: Dict[str, Any], run_dir: str
    ) -> Tuple[Optional[str], List[str]]:
        """用户没给数据时，自动去常用目录找文件并抽成技能可用结构。

        流程（对应需求「自动解析文件名 → 从常用目录提取 → 再利用技能」）::

            ① 从 request / params["file_name"] 解析文件名线索
            ② 在 data/common、data/inbox 里模糊匹配文件
            ③ 读表（pandas 优先）
            ④ 转成技能契约 JSON
            ⑤ 落盘，交给脚本

        Returns:
            (契约 JSON 路径 或 None, 过程说明列表)
        """
        notes: List[str] = []

        # 显式指定的文件名优先于从自然语言里猜
        hint = str(params.get("file_name") or params.get("source_file") or "").strip()
        query = hint or request or ""
        if not query.strip():
            return None, notes

        try:
            from tools.common.commons import find_files, resolve_common_dirs
        except Exception as exc:  # pragma: no cover
            return None, [f"自动取数不可用（导入失败）: {exc}"]

        roots = resolve_common_dirs()
        if not roots:
            notes.append("常用文件目录不存在，跳过自动取数")
            return None, notes

        try:
            hits = find_files(query=query, dirs=roots, limit=1)
        except Exception as exc:
            return None, [f"自动取数失败（匹配异常）: {exc}"]

        if not hits:
            notes.append(f"常用目录里没有匹配 {query!r} 的文件")
            return None, notes

        source = hits[0]
        notes.append(
            f"自动匹配到文件: {source.name}（{source.size_kb}KB, "
            f"{source.modified:%Y-%m-%d}）← 线索 {query[:40]!r}"
        )

        # 只有「表格 → 契约」这一步是技能相关的，其余都是通用能力
        data_path = os.path.join(run_dir, "input_data.json")

        # spec 驱动的技能（自带 deck_spec.json）：**只需把文件路径交下去**，
        # 由技能的 spec 决定用哪列、怎么讲。不要在 loader 里替它做业务映射 ——
        # 那会把行业知识漏进工具层。
        if os.path.isfile(os.path.join(self.skill_dir, "deck_spec.json")):
            payload = {
                "_auto_file": source.path,
                "_auto_resolved": True,
                "_source_name": source.name,
                "project": params.get("project")
                or os.path.splitext(source.name)[0],
                "period": params.get("period"),
                "extra_facts": params.get("extra_facts") or {},
                "group_column": params.get("group_column"),
                "value_column": params.get("value_column"),
                "date_column": params.get("date_column"),
            }
            payload = {k: v for k, v in payload.items() if v is not None}
            with open(data_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            notes.append(
                "技能自带 deck_spec.json → 只传文件路径，由技能知识决定列映射"
            )
            return data_path, notes

        # 旧契约路径（向后兼容尚未迁移的技能）：表格 → 契约 JSON
        try:
            from tools.common.deck_contract import frame_to_contract
            from tools.common.table_io import HAS_PANDAS, read_table
        except Exception as exc:  # pragma: no cover
            return None, notes + [f"契约转换不可用（导入失败）: {exc}"]

        try:
            table, engine = read_table(source.path)
        except Exception as exc:
            return None, notes + [f"读取 {source.name} 失败: {exc}"]

        if table is None:
            return None, notes + [f"无法解析为表格: {engine}"]
        notes.append(f"读取引擎: {engine}")

        if not HAS_PANDAS:
            return None, notes + [
                "环境缺少 pandas，无法把表格转成技能契约（建议装 pandas）"
            ]

        try:
            payload, report = frame_to_contract(
                table,
                date_column=params.get("date_column"),
                value_column=params.get("value_column"),
                group_column=params.get("group_column"),
                project=params.get("project"),
                period=params.get("period"),
                period_mode=str(params.get("period_mode") or "all"),
            )
        except Exception as exc:
            return None, notes + [f"契约转换失败: {type(exc).__name__}: {exc}"]

        # 项目名：显式指定 > 文件名主干
        if not payload.get("项目") or payload["项目"] == "数据分析报告":
            payload["项目"] = os.path.splitext(source.name)[0]
        payload["_source"]["auto_resolved"] = True
        payload["_source"]["source_file"] = source.path

        with open(data_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

        notes.append("契约转换:")
        notes.extend(f"  {line}" for line in report.splitlines() if line.strip())
        return data_path, notes

    def _write_input_data(self, params: Dict[str, Any], run_dir: str) -> Optional[str]:
        """把 params 里的结构化数据落盘成 JSON，供脚本读取。

        支持三种来源（按优先级）：
          * ``params["data"]``   —— dict 或 JSON 字符串（Agent 传真实数据的主路径）
          * ``params["data_path"]`` —— 已存在的 JSON 文件路径
          * ``params["payload"]``  —— 兼容别名

        Returns:
            写入的 JSON 文件绝对路径；无数据时返回 None。
        """
        raw = params.get("data")
        if raw is None:
            raw = params.get("payload")

        data_path: Optional[str] = None

        existing = params.get("data_path")
        if isinstance(existing, str) and existing and os.path.isfile(existing):
            data_path = os.path.abspath(existing)

        if data_path is None and raw is not None:
            if isinstance(raw, dict):
                payload = raw
            elif isinstance(raw, str):
                text = raw.strip()
                if not text:
                    return None
                if text.startswith("{"):
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise SkillError(f"data 不是合法 JSON: {exc}") from exc
                elif os.path.isfile(text):
                    data_path = os.path.abspath(text)
                    payload = None
                else:
                    raise SkillError("data 字符串既不是 JSON 也不是存在的文件路径")
            else:
                raise SkillError(f"data 类型不支持: {type(raw).__name__}（需 dict 或 JSON 字符串）")

            if data_path is None:
                if not isinstance(payload, dict):
                    raise SkillError("data 必须是 JSON 对象（键值对）")
                data_path = os.path.join(run_dir, "input_data.json")
                with open(data_path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)

        return data_path

    def _build_script_env(self, params: Dict[str, Any]) -> Dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("SKILL_")}

        extra = self.frontmatter.get("script_env")
        if isinstance(extra, dict):
            env.update({str(k): str(v) for k, v in extra.items()})

        # 把脚本目录与技能目录放进 PYTHONPATH，保证脚本能 import 同目录模块
        path_parts: List[str] = []
        script_dir = os.path.dirname(self.script) if self.script else self.skill_dir
        for candidate in (self.skill_dir, script_dir):
            if candidate and candidate not in path_parts:
                path_parts.append(candidate)
        existing = env.get("PYTHONPATH")
        if existing:
            path_parts.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(path_parts)

        return env

    # ---------------- 调试辅助 ----------------

    def preview_document(self, limit: int = 400) -> str:
        """返回技能指令文本的预览（调试用）。"""
        return self._document[:limit]

    def __repr__(self) -> str:
        return (
            f"Skill(id={self.skill_id!r}, tool={self.name!r}, "
            f"mode={self.effective_mode()!r}, script={self.script!r})"
        )


# ===================================================================
# 从目录创建 Skill
# ===================================================================

def load_skill(
    skill_dir: str,
    llm: Any = None,
    skill_id: Optional[str] = None,
    tool_prefix: str = "skill_",
    **overrides: Any,
) -> Skill:
    """从单个目录加载一个 Skill（不注册）。"""
    skill_dir = os.path.abspath(skill_dir)
    if not os.path.isdir(skill_dir):
        raise SkillError(f"技能目录不存在: {skill_dir}")

    meta, _document = load_skill_document(skill_dir)

    # 目录名只是默认值；需要稳定能力名的技能可在 frontmatter 声明 id。
    resolved_id = skill_id or meta.get("id") or os.path.basename(skill_dir)

    # 注意：这里必须过滤掉值为 None 的键。否则会用 None 覆盖掉
    # Skill 内部的自动探测逻辑（例如 auto 发现 scripts/build_deck.py）。
    kwargs: Dict[str, Any] = {
        "name": meta.get("name"),
        "description": meta.get("description"),
        "execution": meta.get("execution", "auto"),
        "script": meta.get("script"),
        "script_args": meta.get("script_args"),
        "script_timeout": meta.get("timeout"),
        "tool_prefix": tool_prefix,
    }
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    kwargs.update({k: v for k, v in overrides.items() if v is not None})

    return Skill(skill_dir=skill_dir, skill_id=resolved_id, llm=llm, **kwargs)


# ===================================================================
# 批量加载
# ===================================================================

def load_skills_from_directory(
    skills_root: str,
    llm: Any = None,
    router: Optional[Any] = None,
    tool_register: Optional[ToolRegister] = None,
    recursive: bool = True,
    tool_prefix: str = "skill_",
    verbose: bool = True,
    **skill_defaults: Any,
) -> List[Skill]:
    """扫描 skills/ 目录，把每个包含 SKILL.md 的目录注册为一个 Skill。

    目录约定（支持任意深度）::

        skills/
          property/                   ← 分组目录，本身不是技能
            ppt_skill/
              SKILL.md
              scripts/build_property_deck.py

    Args:
        skills_root: 技能根目录。
        llm: 执行技能用的 LLM。
        router: ``capability.CapabilityRouter``；提供时把技能注册进能力路由中心。
        tool_register: 兼容参数。技能**不再是 Tool**，因此该参数已废弃，
            传入只会得到一条提示（技能应交给 router）。
        recursive: 是否递归搜索子目录（property/ 这类分组目录需要 True）。
        tool_prefix: 技能名前缀，默认 ``skill_``。

    Returns:
        已加载的 Skill 列表。
    """
    loaded: List[Skill] = []

    if not os.path.isdir(skills_root):
        if verbose:
            print(f"  ⚠  Skill 根目录不存在: {skills_root}")
        return loaded

    if tool_register is not None and router is None and verbose:
        print(
            "  ⚠  Skill 不再继承 Tool，tool_register 参数已废弃；"
            "请改用 router=CapabilityRouter(...)"
        )

    for skill_dir in discover_skill_dirs(skills_root, recursive=recursive):
        entry = os.path.relpath(skill_dir, skills_root).replace("\\", "/")
        try:
            skill = load_skill(
                skill_dir=skill_dir,
                llm=llm,
                tool_prefix=tool_prefix,
                **skill_defaults,
            )
        except Exception as exc:
            if verbose:
                print(f"  ❌ Skill 加载失败: {entry} → {exc}")
            continue

        loaded.append(skill)

        if router is not None:
            router.add_skill(skill)
            if verbose:
                print(
                    f"  ✅ Skill 已注册到能力中心: '{skill.name}' "
                    f"({skill.display_name}, 模式={skill.effective_mode()})"
                )
        elif verbose:
            print(f"  ✅ Skill 已加载: '{skill.name}' ({skill.display_name})")

    return loaded


def register_skills_from_directory(
    skills_root: str,
    llm: Any,
    router: Optional[Any] = None,
    **kwargs: Any,
) -> List[Skill]:
    """`load_skills_from_directory` 的语义化别名。"""
    return load_skills_from_directory(
        skills_root, llm=llm, router=router, **kwargs
    )


# ===================================================================
# SkillRegister：技能的集中注册中心（不再是 Tool 注册表）
# ===================================================================

class SkillRegister:
    """技能注册中心。

    注意：技能**不是 Tool**，所以这里持有的是技能集合，
    并可选地把它们挂到 ``capability.CapabilityRouter`` 上，与工具一起统一调度。

    暴露：
      - ``router``    ：能力路由中心（技能 + 工具），可直接交给 ReActAgent
      - ``schemas()`` ：技能自己的 tools schema（仅当需要独立暴露技能时用）
      - ``summary()`` ：技能清单
    """

    def __init__(
        self,
        skills_root: str,
        llm: Any = None,
        router: Optional[Any] = None,
        recursive: bool = True,
        tool_prefix: str = "skill_",
        verbose: bool = False,
        **skill_defaults: Any,
    ):
        self.skills_root = os.path.abspath(skills_root)
        self.llm = llm
        self.recursive = recursive
        self.tool_prefix = tool_prefix
        self.verbose = verbose
        self.skill_defaults = skill_defaults
        self.router = router if router is not None else CapabilityRouter("skills")
        self.skills: Dict[str, Skill] = {}
        self.load()

    # ---------------- 装载 ----------------

    def load(self) -> Dict[str, Skill]:
        """（重新）扫描并注册全部技能。"""
        for skill in load_skills_from_directory(
            self.skills_root,
            llm=self.llm,
            router=self.router,
            recursive=self.recursive,
            tool_prefix=self.tool_prefix,
            verbose=self.verbose,
            **self.skill_defaults,
        ):
            self.skills[skill.skill_id] = skill
        return self.skills

    def reload(self) -> Dict[str, Skill]:
        """清空后重新装载（技能文档改动后使用）。"""
        self.skills.clear()
        self.router = CapabilityRouter("skills")
        return self.load()

    # ---------------- 查询 ----------------

    def get(self, skill_id: str) -> Optional[Skill]:
        return self.skills.get(normalize_skill_id(skill_id))

    def __contains__(self, skill_id: object) -> bool:
        return normalize_skill_id(str(skill_id)) in self.skills

    def __len__(self) -> int:
        return len(self.skills)

    def __iter__(self) -> Iterable[Skill]:
        return iter(self.skills.values())

    def ids(self) -> List[str]:
        return sorted(self.skills)

    def names(self) -> List[str]:
        return [skill.name for skill in self]

    def schemas(self, names: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """技能自己的 schema 列表（不含工具）。"""
        targets = [s for s in self] if names is None else [
            s for s in (self.get(n) for n in names) if s is not None
        ]
        return [build_function_schema(s.name, s.description, s.get_params()) for s in targets]

    def summary(self) -> List[Dict[str, Any]]:
        return [skill.meta.to_dict() for skill in self]

    def describe(self) -> str:
        """人类可读的技能清单。"""
        lines = [f"已装载 {len(self.skills)} 个技能（root={self.skills_root}）:"]
        for skill in self:
            lines.append(
                f"  • {skill.skill_id}  [{skill.effective_mode()}]  "
                f"{skill.display_name}\n      能力名: {skill.name}"
            )
        return "\n".join(lines)

    # ---------------- 执行 ----------------

    def run_skill(self, skill_id: str, request: str, **params: Any) -> SkillResult:
        """按 id 执行技能，返回结构化结果。"""
        skill = self.get(skill_id)
        if skill is None:
            return SkillResult(
                skill=normalize_skill_id(skill_id),
                mode="none",
                success=False,
                error=f"未找到技能 '{skill_id}'，可用: {self.ids()}",
            )
        return skill.run_skill({"request": request, **params})

    def run(self, skill_id: str, request: str, **params: Any) -> str:
        """按 id 执行技能，返回文本结果。"""
        return self.run_skill(skill_id, request, **params).to_text()

    def __repr__(self) -> str:
        return (
            f"SkillRegister(root={self.skills_root!r}, "
            f"skills={self.ids()}, capabilities={self.router.names()})"
        )
