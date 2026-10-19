# -*- coding: utf-8 -*-
"""Skill 模块的注册入口（facade）

架构说明
--------
    capability.py             —— 能力抽象层：Capability 协议 + CapabilityRouter
    skills/loader.py          —— 技能发现、解析、执行（Skill 实现 Capability 协议）
    skills/skill_register.py  —— 面向使用者的统一入口（本文件）

**Skill 不继承 Tool**。技能与工具是同级能力，只在 `CapabilityRouter`
这一层被抹平差异，由 Agent 在「意图识别 → 规划 → 能力匹配」后统一调度。

最简用法
--------
    from core.capability import CapabilityRouter
    from skills.skill_register import SkillRegister
    from tools.common.calculatortool import CalculatorTool
    from core.llm import LLM

    llm = LLM(provider="qwen")

    # 1) 建能力中心：技能 + 工具放进同一个路由
    router = CapabilityRouter()
    router.add_tool(CalculatorTool())
    skills = SkillRegister("skills", llm=llm, router=router)

    # 2) 直接执行某个技能
    print(skills.run("ppt_skill", "生成 9 月经营分析 PPT"))

    # 3) 交给 ReActAgent：意图识别 → 规划 → 匹配 skill 或 tool
    from agents.react_agent.react_agent import ReActAgent
    agent = ReActAgent(name="助手", llm=llm, router=router, enable_planning=True)
    agent.run("帮我做一份物业月度经营分析 PPT")
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

from core.capability import Capability, CapabilityKind, CapabilityRouter

from .loader import (  # noqa: F401  (对外转出，方便 from skills.skill_register import ...)
    Skill,
    SkillError,
    SkillMeta,
    SkillRegister,
    SkillResult,
    discover_skill_dirs,
    load_skill,
    load_skill_document,
    load_skills_from_directory,
    normalize_skill_id,
    parse_frontmatter,
    register_skills_from_directory,
)

__all__ = [
    "Capability",
    "CapabilityKind",
    "CapabilityRouter",
    "Skill",
    "SkillError",
    "SkillMeta",
    "SkillRegister",
    "SkillResult",
    "build_capability_router",
    "build_skill_register",
    "discover_skill_dirs",
    "load_skill",
    "load_skill_document",
    "load_skills_from_directory",
    "normalize_skill_id",
    "parse_frontmatter",
    "register_skills_from_directory",
    "register_skills_into",
]


def build_skill_register(
    skills_root: str = "skills",
    llm: Any = None,
    router: Optional[CapabilityRouter] = None,
    recursive: bool = True,
    verbose: bool = False,
    **skill_defaults: Any,
) -> SkillRegister:
    """构造技能注册中心的便捷工厂。

    Args:
        skills_root: 技能根目录，默认项目下的 ``skills/``。
        llm: 执行技能用的 LLM。
        router: 复用已有能力路由中心（例如已注册了 calculator / memory 工具）。
        recursive: 是否递归搜索（``skills/property/xxx`` 这类分组需要 True）。

    Returns:
        已装载完毕的 :class:`SkillRegister`。
    """
    return SkillRegister(
        skills_root=skills_root,
        llm=llm,
        router=router,
        recursive=recursive,
        verbose=verbose,
        **skill_defaults,
    )


def build_capability_router(
    skills_root: str = "skills",
    llm: Any = None,
    tools: Optional[Iterable[Any]] = None,
    router: Optional[CapabilityRouter] = None,
    recursive: bool = True,
    verbose: bool = False,
    **skill_defaults: Any,
) -> CapabilityRouter:
    """一次把「技能 + 工具」装进同一个能力路由中心。

    这是新架构下的推荐入口 —— 只有一张能力表，Agent 不需要区分来源。

    Example::

        router = build_capability_router(
            "skills", llm=llm, tools=[CalculatorTool()]
        )
        agent = ReActAgent(name="助手", llm=llm, router=router)
    """
    router = router or CapabilityRouter()
    for tool in tools or []:
        router.add_tool(tool)

    SkillRegister(
        skills_root=skills_root,
        llm=llm,
        router=router,
        recursive=recursive,
        verbose=verbose,
        **skill_defaults,
    )
    return router


def register_skills_into(
    router: CapabilityRouter,
    skills_root: str = "skills",
    llm: Any = None,
    recursive: bool = True,
    verbose: bool = False,
    **skill_defaults: Any,
) -> List[Skill]:
    """把技能注册进一个已有的能力路由中心，返回技能列表。

    适合「我已经有一堆工具，只想再加技能」的场景::

        router = CapabilityRouter()
        router.add_tool(CalculatorTool())
        register_skills_into(router, llm=llm)
    """
    return load_skills_from_directory(
        skills_root,
        llm=llm,
        router=router,
        recursive=recursive,
        verbose=verbose,
        **skill_defaults,
    )
