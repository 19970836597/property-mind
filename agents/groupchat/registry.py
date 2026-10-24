# -*- coding: utf-8 -*-
"""多群管理：建群 / 切换 / 增减成员 / 解散，单活跃群模型。

* manager 恒在（组织者，不可被 @、不可被移除），复用主 agent 同一实例；
* 多群并存，同时只活跃一个群；@ 解析与自荐只看活跃群；
* 每群独立 SharedHistory + 独立 SharedMemory（session_id=group-<群名>）；
* 底层 ReActAgent 对象多群共享：切换时把旧群的 SharedRecallProvider
  从成员 context.providers 摘掉、挂上新群的，否则群间串记忆。

群名规则：中英/数字/下划线，2~12 字（与 @ 中文名长度口径一致）。
默认群不可解散、不可改名；解散活跃群时自动切回默认群。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from .group_chat import (
    attach_shared_provider,
    build_groupchat,
    describe_pool,
    detach_shared_provider,
)
from .mention import extract_mentions

__all__ = ["GroupRegistry", "DEFAULT_GROUP_NAME", "GROUP_NAME_RE"]

#: 默认群名（启动即建，含全员，保证老行为不变）
DEFAULT_GROUP_NAME = "默认群"

#: 群名校验：中英/数字/下划线，2~12 字
GROUP_NAME_RE = re.compile(r"^[A-Za-z0-9_一-龥]{2,12}$")


class GroupRegistry:
    """群注册表：持有全部群 + 活跃群指针。"""

    def __init__(
        self,
        manager_agent: Any,
        sub_agent_tools: Sequence[Any],
        user_id: str = "default_user",
        default_group_name: str = DEFAULT_GROUP_NAME,
        threshold: float = 2.0,
        verbose: bool = False,
    ):
        self.manager_agent = manager_agent
        self.sub_agent_tools = list(sub_agent_tools or [])
        self.user_id = user_id
        self.default_group_name = default_group_name
        self.threshold = float(threshold)
        self.verbose = bool(verbose)

        self.groups: Dict[str, Any] = {}
        self.active_name: str = ""

        #: 全局 alias 表（建群解析 @ 用，覆盖全部可用成员）
        self.pool_alias_map: Dict[str, List[str]] = {}
        for info in describe_pool(self.sub_agent_tools):
            self.pool_alias_map[info["name"]] = (
                list(info["aliases"]) + list(info.get("tags", []))
            )

        # 启动即建默认群（含全员）
        self.create(default_group_name, member_names=None, _auto=True)
        self.switch(default_group_name)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @property
    def active(self) -> Any:
        """当前活跃群（GroupChatManager）。"""
        return self.groups.get(self.active_name)

    def names(self) -> List[str]:
        return list(self.groups.keys())

    def pool_hint(self) -> str:
        """可用成员提示（建群失败时告诉用户能 @ 谁）。"""
        parts = []
        for info in describe_pool(self.sub_agent_tools):
            show = info["aliases"][-1] if info["aliases"] else info["name"]
            parts.append(f"@{show}（{info['display']}）")
        return "、".join(parts) or "（暂无可用成员）"

    # ------------------------------------------------------------------
    # 建群 / 解散
    # ------------------------------------------------------------------

    def _check_name(self, name: str) -> str:
        name = str(name or "").strip()
        if not GROUP_NAME_RE.match(name):
            raise ValueError(
                f"群名「{name}」不合法：2~12 字，仅中英/数字/下划线。"
            )
        return name

    def create(
        self,
        name: str,
        member_names: Optional[Sequence[str]] = None,
        raw_mentions: str = "",
        _auto: bool = False,
    ) -> Any:
        """建群并自动切换到新群。

        Args:
            name: 群名。
            member_names: canonical 名列表（内部调用用）。
            raw_mentions: 用户 @ 原文如 "@催费 @法务"（CLI 用，优先于
                member_names；至少解析出 1 个有效成员，否则 fail-fast）。
        """
        name = self._check_name(name)
        if name in self.groups:
            raise ValueError(f"群「{name}」已存在，先 /group switch {name} 切换。")

        wanted: Optional[List[str]] = (
            list(member_names) if member_names else None
        )
        if raw_mentions:
            mentioned, _, unknown = extract_mentions(
                raw_mentions, self.pool_alias_map
            )
            if unknown:
                raise ValueError(
                    f"没有找到 {''.join('@' + u for u in unknown)}，"
                    f"可用成员：{self.pool_hint()}。"
                )
            if not mentioned:
                raise ValueError(
                    f"建群至少指定 1 个成员，例如："
                    f"/group create {name} {self.pool_hint()}。"
                )
            wanted = mentioned

        # member_names 直调路径同样校验有效性（fail-fast）
        pool = {info["name"] for info in describe_pool(self.sub_agent_tools)}
        if wanted:
            bad = [n for n in wanted if n not in pool]
            if bad:
                raise ValueError(
                    f"未知成员 {bad}，可用成员：{self.pool_hint()}。"
                )
        elif not pool:
            raise ValueError("当前没有可用成员，无法建群。")

        chat = build_groupchat(
            self.manager_agent,
            self.sub_agent_tools,
            session_id=f"group-{name}",
            user_id=self.user_id,
            group_name=name,
            member_names=wanted,  # None = 全员（默认群 / 不指定成员）
            threshold=self.threshold,
            verbose=self.verbose,
        )
        if not chat.members:
            raise ValueError("建群失败：没有可用成员。")
        self.groups[name] = chat
        if not _auto:
            self.switch(name)
        return chat

    def disband(self, name: str) -> str:
        """解散群。默认群拒绝；解散活跃群时自动切回默认群。"""
        name = str(name or "").strip()
        if name == self.default_group_name:
            raise ValueError("默认群不可解散。")
        chat = self.groups.get(name)
        if chat is None:
            raise ValueError(f"没有群「{name}」。")
        if name == self.active_name:
            detach_shared_provider(chat)
        del self.groups[name]
        if name == self.active_name:
            self.switch(self.default_group_name)
            return f"已解散「{name}」，已切回「{self.default_group_name}」。"
        return f"已解散「{name}」。"

    # ------------------------------------------------------------------
    # 切换（provider 摘挂是关键）
    # ------------------------------------------------------------------

    def switch(self, name: str) -> Any:
        """切换活跃群：旧群摘 provider → 新群挂 provider。"""
        name = str(name or "").strip()
        chat = self.groups.get(name)
        if chat is None:
            raise ValueError(
                f"没有群「{name}」，可用：{ '、'.join(self.names())}。"
            )
        old = self.groups.get(self.active_name)
        if old is not None and old is not chat:
            detach_shared_provider(old)
        self.active_name = name
        attach_shared_provider(chat)
        return chat

    # ------------------------------------------------------------------
    # 活跃群成员增减
    # ------------------------------------------------------------------

    def add_members(self, raw_mentions: str) -> Any:
        """给活跃群加人（@ 解析，fail-fast）。"""
        chat = self.active
        mentioned, _, unknown = extract_mentions(
            raw_mentions or "", self.pool_alias_map
        )
        if unknown:
            raise ValueError(
                f"没有找到 {''.join('@' + u for u in unknown)}，"
                f"可用成员：{self.pool_hint()}。"
            )
        if not mentioned:
            raise ValueError("请指定要加的成员，例如：/group add @法务。")
        have = {m.name for m in chat.members}
        added = []
        # 从模板池按 canonical 名重建成员（与 build_groupchat 同口径）
        by_name = {}
        for tool in self.sub_agent_tools:
            agent = getattr(tool, "agent", None)
            tname = str(getattr(tool, "name", "") or "").strip()
            if agent is not None and tname:
                by_name[tname] = (tool, agent)
        from .group_chat import DEFAULT_ALIASES, GroupMember

        for cname in mentioned:
            if cname in have:
                continue
            if cname not in by_name:
                continue
            tool, agent = by_name[cname]
            display = str(getattr(agent, "name", "") or cname).strip()
            aliases = list(DEFAULT_ALIASES.get(cname, [display]))
            chat.members.append(GroupMember(
                name=cname, agent=agent, display=display, aliases=aliases,
            ))
            chat.alias_map.setdefault(cname, aliases)
            have.add(cname)
            added.append(display)
        if not added:
            raise ValueError("要加的成员已在群里，无需重复添加。")
        attach_shared_provider(chat)
        return chat

    def remove_member(self, raw_mentions: str) -> Any:
        """从活跃群移除成员（至少保留 1 人）。"""
        chat = self.active
        mentioned, _, unknown = extract_mentions(
            raw_mentions or "", self.pool_alias_map
        )
        if unknown:
            raise ValueError(
                f"没有找到 {''.join('@' + u for u in unknown)}。"
            )
        if not mentioned:
            raise ValueError("请指定要移除的成员，例如：/group remove @催费。")
        have = {m.name for m in chat.members}
        if len(have) - len([n for n in mentioned if n in have]) < 1:
            raise ValueError("群里至少保留 1 个成员，不能全移除。")
        chat.members = [m for m in chat.members if m.name not in mentioned]
        for cname in mentioned:
            chat.alias_map.pop(cname, None)
        return chat

    # ------------------------------------------------------------------
    # 展示
    # ------------------------------------------------------------------

    def describe(self) -> str:
        lines = ["群列表："]
        for name in self.names():
            chat = self.groups[name]
            mark = "（活跃）" if name == self.active_name else ""
            members = "、".join(m.display for m in chat.members) or "（无）"
            lines.append(
                f"  • {name}{mark}：{members}（{len(chat.history.turns)} 条发言）"
            )
        return "\n".join(lines)

    def describe_active(self) -> str:
        chat = self.active
        if chat is None:
            return "（无活跃群）"
        members = ", ".join(
            f"{m.display}({m.name})" for m in chat.members
        ) or "（无）"
        lines = [
            f"  当前群「{self.active_name}」manager 恒在",
            f"  群聊成员：{members}",
            f"  共享历史：{len(chat.history.turns)} 条发言",
            f"  表达偏好：{len(getattr(chat, 'habits', []))} 条",
        ]
        for h in getattr(chat, "habits", []):
            lines.append(f"    - {h}")
        return "\n".join(lines)
