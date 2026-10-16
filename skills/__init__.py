# -*- coding: utf-8 -*-
"""skills —— 可插拔技能模块

技能 = 一个包含 ``SKILL.md`` 的目录（可位于任意深度）。

    skills/
      buttin/                       ← 分组目录，本身不是技能
        ppt_skill/
          SKILL.md                  ← frontmatter 声明 name/description/execution/script
          references/*.md           ← 会被合并进技能指令
          scripts/build_deck.py     ← execution=script 时执行
        Optimalstop37-skill/
          SKILL.md
          scorecard.md

架构要点
--------
**Skill 不继承 Tool**。两者是同级能力，都实现 ``capability.Capability`` 协议。
Agent 走「意图识别 → 规划 → 能力匹配」，通过 ``CapabilityRouter``
统一调度技能与工具，不感知具体种类。

对外主要接口：
    CapabilityRouter           —— 能力路由中心：技能 + 工具统一注册/匹配/执行
    build_capability_router    —— 一次装载技能与工具（推荐入口）
    SkillRegister              —— 只管理技能的注册中心
    Skill                      —— 单个技能，实现 Capability 协议
"""

from core.capability import Capability, CapabilityKind, CapabilityRouter

from .loader import (
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
from .skill_register import (
    build_capability_router,
    build_skill_register,
    register_skills_into,
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
