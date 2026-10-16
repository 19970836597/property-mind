# -*- coding: utf-8 -*-
"""capability —— 能力抽象层（统一 Tool 与 Skill）

设计动机
--------
在旧架构里 `Skill` 继承 `Tool`，导致两个问题：

1. **概念混乱**：技能是「一份说明书 + 一条流水线」，工具是「一个确定性函数」。
   硬塞进同一个基类，会让 `get_params()` 之类的接口被迫变形。
2. **路由僵化**：Agent 只能从「工具表」里选东西，没法表达
   「这个意图该交给技能，那个意图该交给工具」。

新架构把两者提升为**同级的两种能力**：

    ┌──────────────── Capability（协议） ────────────────┐
    │  name / description / kind / tags / parameters     │
    │  run(params) -> str                                │
    └───────────────┬────────────────────┬───────────────┘
                    │                    │
              Tool │                    │ Skill
        （确定性函数/HTTP/计算）   （说明书+多阶段流水线）

Agent 的决策链变成：

    用户输入
      → 意图识别（要干什么）
      → 规划（分几步）
      → 能力匹配（从 skills + tools 里挑，可以混合）
      → 执行 → 观察 → 继续推理

因此 `Skill` **不再继承 `Tool`**，`Tool` 保持原样不被污染；
两者只在 `CapabilityRouter` 这一层被抹平差异。
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, runtime_checkable


class CapabilityKind(str, Enum):
    """能力种类：技能 or 工具。"""

    SKILL = "skill"
    TOOL = "tool"

    def __str__(self) -> str:  # pragma: no cover
        return self.value


@runtime_checkable
class Capability(Protocol):
    """能力协议。

    只要求四个属性 + 一个方法，**不要求继承任何基类**。
    `Tool` 与 `Skill` 各自实现它，互不依赖。
    """

    name: str
    description: str

    @property
    def kind(self) -> CapabilityKind:
        """能力种类。"""
        ...

    def get_params(self) -> Sequence[Any]:
        """返回参数定义列表（元素需有 name/type/description/required/default）。"""
        ...

    def run(self, params: Dict[str, Any]) -> str:
        """执行能力，返回文本结果。"""
        ...

    # 说明：tags 用可选方式提供，因此不写进 Protocol 的强制部分，
    # 由 capability_tags() 统一读取。


def capability_tags(capability: Any) -> List[str]:
    """安全读取能力的 tags（没有就返回空列表）。"""
    tags = getattr(capability, "tags", None)
    if not tags:
        return []
    if isinstance(tags, str):
        return [tags.strip()] if tags.strip() else []
    return [str(t) for t in tags]


def capability_kind(capability: Any) -> CapabilityKind:
    """安全推断能力种类。

    优先读 `kind` 属性；退化时按类名判断（Skill / Tool）。
    """
    kind = getattr(capability, "kind", None)
    if isinstance(kind, CapabilityKind):
        return kind
    if kind is not None:
        try:
            return CapabilityKind(str(kind).lower())
        except ValueError:
            pass
    # 退化推断
    name = type(capability).__name__.lower()
    if "skill" in name:
        return CapabilityKind.SKILL
    return CapabilityKind.TOOL


# ===================================================================
# schema 构造（Tool 与 Skill 共用，避免两套实现漂移）
# ===================================================================

def parameters_to_schema(params: Optional[Iterable[Any]]) -> Dict[str, Any]:
    """把参数定义列表转成 OpenAI function-calling 的 parameters 对象。

    容忍两类输入：
      - `ToolParameter`（pydantic 模型，有 name/type/description/required/default）
      - 普通 dict（{"name": ..., "type": ...}）
    """
    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param in params or []:
        if isinstance(param, dict):
            p_name = param.get("name")
            p_type = param.get("type", "string")
            p_desc = param.get("description", "")
            p_required = param.get("required", True)
            p_default = param.get("default")
        else:
            p_name = getattr(param, "name", None)
            p_type = getattr(param, "type", "string")
            p_desc = getattr(param, "description", "")
            p_required = getattr(param, "required", True)
            p_default = getattr(param, "default", None)

        if not p_name:
            continue

        entry: Dict[str, Any] = {"type": p_type, "description": p_desc}
        if p_default is not None:
            entry["default"] = p_default
        properties[p_name] = entry

        if p_required:
            required.append(p_name)

    return {"type": "object", "properties": properties, "required": required}


def build_function_schema(
    name: str,
    description: str,
    params: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    """构造单个 function-calling schema。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters_to_schema(params),
        },
    }


# ===================================================================
# CapabilityRouter：技能与工具的统一注册 + 匹配 + 执行
# ===================================================================

class CapabilityRouter:
    """能力路由中心。

    同时持有技能（Skill）与工具（Tool），对外提供：

      - ``schemas()``      统一 schema（LLM 看到的是一个扁平列表）
      - ``catalog()``      紧凑清单，用于「意图识别」这一步
      - ``resolve(name)``  按名字取回能力
      - ``match(...)``     按意图/名字/tag 匹配候选能力
      - ``execute(...)``   执行并把异常收敛成文本

    名字冲突时按 ``skill`` > ``tool`` 的顺序解析（技能更具体），
    并把冲突记录在 :attr:`conflicts` 里，避免静默覆盖。
    """

    def __init__(self, name: str = "capability_router"):
        self.name = name
        self._capabilities: Dict[str, Any] = {}
        self.conflicts: List[str] = []
        # 名字 → 真实注册名（用于别名解析）
        self._aliases: Dict[str, str] = {}

    # ---------------- 注册 ----------------

    def _register(self, capability: Any, aliases: Sequence[str] = ()) -> None:
        cap_name = getattr(capability, "name", None)
        if not cap_name:
            raise ValueError(f"能力缺少 name 属性: {capability!r}")

        existing = self._capabilities.get(cap_name)
        if existing is not None:
            old_kind = capability_kind(existing)
            new_kind = capability_kind(capability)
            # 技能优先：技能更具体，覆盖同名的通用工具
            if new_kind == CapabilityKind.SKILL and old_kind != CapabilityKind.SKILL:
                self._capabilities[cap_name] = capability
                self.conflicts.append(
                    f"'{cap_name}' 同时存在于 {old_kind}/{new_kind}，已保留 {new_kind}"
                )
            else:
                self.conflicts.append(
                    f"'{cap_name}' 重复注册（{old_kind} vs {new_kind}），保留先注册的 {old_kind}"
                )
        else:
            self._capabilities[cap_name] = capability

        for alias in aliases:
            if alias and alias != cap_name and alias not in self._aliases:
                self._aliases[alias] = cap_name

    def add_skill(self, skill: Any, aliases: Sequence[str] = ()) -> None:
        """注册一个技能。"""
        self._register(skill, aliases)

    def add_tool(self, tool: Any, aliases: Sequence[str] = ()) -> None:
        """注册一个工具。"""
        self._register(tool, aliases)

    def add(self, capability: Any, aliases: Sequence[str] = ()) -> None:
        """自动判断种类并注册。"""
        if capability_kind(capability) == CapabilityKind.SKILL:
            self.add_skill(capability, aliases)
        else:
            self.add_tool(capability, aliases)

    def add_all(self, capabilities: Iterable[Any]) -> None:
        for capability in capabilities:
            self.add(capability)

    # ---------------- 查询 ----------------

    def __len__(self) -> int:
        return len(self._capabilities)

    def __contains__(self, name: object) -> bool:
        return str(name) in self._capabilities or str(name) in self._aliases

    def __iter__(self):
        return iter(self._capabilities.values())

    def names(self) -> List[str]:
        return sorted(self._capabilities)

    def skills(self) -> List[Any]:
        return [c for c in self if capability_kind(c) == CapabilityKind.SKILL]

    def tools(self) -> List[Any]:
        return [c for c in self if capability_kind(c) == CapabilityKind.TOOL]

    def resolve(self, name: str) -> Optional[Any]:
        """按名字/别名取回能力。"""
        if name in self._capabilities:
            return self._capabilities[name]
        alias_target = self._aliases.get(name)
        if alias_target:
            return self._capabilities.get(alias_target)
        return None

    # 兼容旧调用习惯
    get = resolve

    def schemas(self, names: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """统一 schema 列表。

        Args:
            names: 只输出这些能力的 schema（意图识别后的过滤，省 token 又降误调用）。
        """
        targets = list(self) if names is None else [
            c for c in (self.resolve(n) for n in names) if c is not None
        ]
        return [
            build_function_schema(
                getattr(c, "name"), getattr(c, "description", ""), c.get_params()
            )
            for c in targets
        ]

    # 兼容 ReActAgent 旧接口
    def get_tools_schema(self, scopes: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        return self.schemas(scopes)

    def catalog(self, kind: Optional[CapabilityKind] = None) -> str:
        """紧凑能力清单，喂给「意图识别」那一步。

        不输出完整参数 schema，只给名字、种类、简述与标签 —— 目的是让模型
        用最少的 token 判断「该用哪个」，而不是直接开始调用。
        """
        lines: List[str] = []
        for capability in self:
            c_kind = capability_kind(capability)
            if kind is not None and c_kind != kind:
                continue
            desc = (getattr(capability, "description", "") or "").strip()
            if len(desc) > 110:
                desc = desc[:110] + "…"
            tags = capability_tags(capability)
            tag_str = f" [{'/'.join(tags)}]" if tags else ""
            lines.append(f"- {capability.name} ({c_kind.value}){tag_str}: {desc}")
        return "\n".join(lines)

    # ---------------- 匹配 ----------------

    def match(
        self,
        query: str = "",
        names: Optional[Sequence[str]] = None,
        tags: Optional[Sequence[str]] = None,
    ) -> List[Any]:
        """按名字 / 标签 / 关键词匹配候选能力。

        匹配优先级：
          1. 显式 names（意图识别给出的结果）
          2. tags 命中
          3. query 关键词与 名字/描述/标签 的粗略词面匹配
        """
        if names:
            return [c for c in (self.resolve(n) for n in names) if c is not None]

        candidates = list(self)

        if tags:
            wanted = {str(t).lower() for t in tags}
            hit = [c for c in candidates
                   if wanted & {t.lower() for t in capability_tags(c)}]
            if hit:
                return hit

        if not query:
            return candidates

        lowered = query.lower()
        # 收集查询里的双字窗口（中英文都适用）
        grams = {lowered[i:i + 2] for i in range(len(lowered) - 1)}
        grams = {g for g in grams if g.strip()}
        if not grams:
            return []

        scored: List[tuple] = []
        for capability in candidates:
            name = str(getattr(capability, "name", ""))
            desc = str(getattr(capability, "description", ""))
            tags = capability_tags(capability)
            haystack = " ".join([name, desc, " ".join(tags)]).lower()

            matched = sum(1 for g in grams if g in haystack)
            # 门槛随查询长度缩放：
            # 短查询只靠单个双字命中会大量误匹配（例如「随便问问」命中「问问」），
            # 所以要求命中比例足够高，且至少命中 2 个窗口。
            need = max(2, int(len(grams) * 0.5))
            score = matched
            if lowered in haystack:
                score += 5
            # 技能名/标签整体命中，权重更高
            if any(t.lower() and t.lower() in lowered for t in tags):
                score += 2

            if matched >= need or score >= 5:
                scored.append((score, capability))

        if not scored:
            return []
        scored.sort(key=lambda item: item[0], reverse=True)
        return [c for _score, c in scored]

    # ---------------- 执行 ----------------

    def execute(self, name: str, params: Dict[str, Any]) -> str:
        """执行能力；异常与未知名字都收敛成文本，不让 Agent 崩掉。"""
        capability = self.resolve(name)
        if capability is None:
            available = ", ".join(self.names()) or "（无）"
            return f"未找到能力 '{name}'。可用能力: {available}"

        try:
            return str(capability.run(params))
        except Exception as exc:
            kind = capability_kind(capability).value
            return f"{kind} '{name}' 执行失败: {type(exc).__name__}: {exc}"

    # 兼容旧调用习惯
    def execute_tool(self, name: str, input_text: str) -> str:
        capability = self.resolve(name)
        if capability is None:
            return f"未找到能力 '{name}'"
        params_defs = list(capability.get_params() or [])
        key = getattr(params_defs[0], "name", None) if params_defs else None
        return self.execute(name, {key or "request": input_text})

    # ---------------- 展示 ----------------

    def summary(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": getattr(c, "name", ""),
                "kind": capability_kind(c).value,
                "description": getattr(c, "description", ""),
                "tags": capability_tags(c),
            }
            for c in self
        ]

    def describe(self) -> str:
        lines = [
            f"能力路由中心 '{self.name}': "
            f"{len(self.skills())} 个技能 + {len(self.tools())} 个工具"
        ]
        for capability in self:
            kind = capability_kind(capability).value
            lines.append(f"  • [{kind}] {capability.name}")
        if self.conflicts:
            lines.append("  冲突:")
            lines.extend(f"    - {c}" for c in self.conflicts)
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"CapabilityRouter(skills={len(self.skills())}, "
            f"tools={len(self.tools())})"
        )


# ===================================================================
# 意图识别默认提示词
# ===================================================================

INTENT_RECOGNITION_PROMPT = """\
你是能力调度器。下面是系统当前可用的能力清单（skill = 技能，tool = 工具）：

{capabilities}

用户请求：{input}

请完成两件事：
1. 用一句话概括用户的**意图**（intent）。
2. 从清单中选出完成该请求**真正需要**的能力名字，可以同时选技能和工具；
   宁少勿多，不相关的一律不要选。如果都不需要，返回空列表。

只输出 JSON，不要任何解释文字或代码块围栏，格式：
{{"intent": "...", "capabilities": ["name1", "name2"]}}
"""


def parse_intent_response(text: str) -> Dict[str, Any]:
    """解析意图识别的 JSON 输出，容错常见脏格式。"""
    if not text:
        return {"intent": "", "capabilities": []}

    raw = text.strip()

    # 去掉 ```json ... ``` 围栏
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    # 截取第一个 JSON 对象
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"intent": "", "capabilities": []}

    if not isinstance(data, dict):
        return {"intent": "", "capabilities": []}

    caps = data.get("capabilities") or data.get("capability") or []
    if isinstance(caps, str):
        caps = [caps]
    caps = [str(c).strip() for c in caps if str(c).strip()]

    return {"intent": str(data.get("intent", "")).strip(), "capabilities": caps}
