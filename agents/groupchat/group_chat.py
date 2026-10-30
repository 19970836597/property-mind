# -*- coding: utf-8 -*-
"""群聊编排：manager（主编 + 书记员）+ 成员（只答题，不进群）。

单轮流程（定稿计划 §3）：

1. ``parse``：有 @ → 点名；无 @ → ``select_speakers`` 自荐打分；
2. ``run``：命中成员依次 ``run()``（串行，raw 暂存，**不进群**）；
3. ``rewrite``：manager 按习惯偏好改写整理（一次 LLM，失败降级原文）；
4. ``publish``：以一条 manager 消息发布进 ``SharedHistory``；
5. ``remember``：成员结论双写（私有 + 共享），manager 轮末只记表达偏好。

成员看到的 history 是共享历史的视图（带发言人前缀），各成员自己的
``_history`` 每轮重置为该视图——共享历史是唯一真相，不会出现
“我看到的和你看到的不一样”。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from core.history.message import Message, MessageRole
from core.paths import MEMORY_FILE

from .mention import extract_mentions
from .shared_memory import SharedMemory, SharedRecallProvider, build_shared_memory
from .speaker_selector import DEFAULT_THRESHOLD, select_speakers

__all__ = [
    "ChatTurn",
    "GroupMember",
    "GroupChatManager",
    "SharedHistory",
    "attach_shared_provider",
    "build_groupchat",
    "describe_pool",
    "detach_shared_provider",
]

#: 成员 raw 输出保护：与 SubAgentTool 同口径（主 agent 的 tool 结果无截断，
#: 这里自己截，防止大段输出撑爆共享历史与改写 prompt）
try:  # 优先复用子 agent 封装里的常量，避免两处漂移
    from agents.sub_agent_tool import MAX_OUTPUT_CHARS
except Exception:  # pragma: no cover - 兜底
    MAX_OUTPUT_CHARS = 8000

#: 默认 alias：canonical 名（= SubAgentTool 注册名）→ 可 @ 的名字
DEFAULT_ALIASES: Dict[str, List[str]] = {
    "collection_agent": ["催费助手", "催费"],
    "legal_agent": ["法务助手", "法务"],
}

#: 改写约束：只动格式表述，不动原意（数字/结论/动作一个不许增删）
REWRITE_PROMPT = """\
你是群聊的整理者。请把下面成员的原始回答整理成最终发布版。

用户表达偏好（没有则忽略）：
{habits}

成员原始回答：
{raw}

要求：
1. 只调整格式与表述（称呼、详略、列表/表格形式、单位口径按偏好来）；
2. 不增删任何数字、结论与建议动作，不改变原意；
3. 多个成员的回答要合并去重、保留信息完整；
4. 直接输出整理后的回答，不要解释、不要加前后缀。
"""

#: 习惯提取：只记表达偏好，不记业务事实（业务事实走共享库）
HABIT_EXTRACT_PROMPT = """\
从下面这轮对话里提取用户**表达偏好**（最多 2 条），只记这一类：
称呼、详略（简短/详细）、格式（表格/要点/段落）、单位口径等。

不要记：业务事实、任务内容、寒暄、助手给出的结论本身。
没有新的偏好就一个字都不输出。

对话：
用户：{input}
助手：{answer}

输出：每行一条偏好，不加编号，不加解释。"""


@dataclass
class ChatTurn:
    """共享群里的一轮发言。"""

    speaker: str  # "user" / 成员 display 名 / "manager"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


class SharedHistory:
    """群共享历史：唯一真相。成员 raw 输出不进群，只有用户与 manager 消息。"""

    def __init__(self) -> None:
        self.turns: List[ChatTurn] = []

    def __len__(self) -> int:
        return len(self.turns)

    def add(self, speaker: str, content: str) -> None:
        content = str(content or "").strip()
        if content:
            self.turns.append(ChatTurn(speaker=speaker, content=content))

    def as_messages(self) -> List[Message]:
        """转成 ReActAgent 能直接用的 history 视图（发言人当前缀）。"""
        out: List[Message] = []
        for turn in self.turns:
            if turn.speaker == "user":
                out.append(Message(role=MessageRole.USER, content=turn.content))
            else:
                out.append(Message(
                    role=MessageRole.ASSISTANT,
                    content=f"[{turn.speaker}]: {turn.content}",
                ))
        return out

    def transcript(self, limit: int = 20) -> str:
        """最近若干轮的可读纪要（日志/调试用）。"""
        lines = [
            f"{t.speaker}: {t.content[:120]}"
            for t in self.turns[-max(1, limit):]
        ]
        return "\n".join(lines)


@dataclass
class GroupMember:
    """群成员：canonical 名 + 内部 ReActAgent + 可 @ 的 alias。"""

    name: str  # canonical，如 collection_agent
    agent: Any  # 内部 ReActAgent（从 SubAgentTool.agent 拆出来）
    display: str = ""  # 群里显示名，如 催费助手
    aliases: List[str] = field(default_factory=list)

    @property
    def router(self) -> Any:
        return getattr(self.agent, "router", None)

    def __post_init__(self) -> None:
        if not self.display:
            self.display = self.name


class GroupChatManager:
    """群聊 manager：选人 / 收 raw / 改写 / 发布 / 记习惯。

    manager 本人是主 agent 兼任：``manager_agent`` 的 LLM 用来改写与兜底，
    它的 ``_maybe_remember`` 不借用（那是业务事实沉淀），习惯另记。
    """

    def __init__(
        self,
        manager_agent: Any,
        members: Sequence[GroupMember],
        alias_map: Optional[Dict[str, List[str]]] = None,
        shared_history: Optional[SharedHistory] = None,
        shared_memory: Optional[SharedMemory] = None,
        session_id: str = "group-default",
        group_name: Optional[str] = None,
        threshold: float = DEFAULT_THRESHOLD,
        max_output_chars: int = MAX_OUTPUT_CHARS,
        verbose: bool = False,
    ):
        self.manager_agent = manager_agent
        self.members: List[GroupMember] = list(members or [])
        self.alias_map: Dict[str, List[str]] = dict(alias_map or {})
        for m in self.members:
            self.alias_map.setdefault(m.name, list(m.aliases))
        self.history = shared_history or SharedHistory()
        self.shared = shared_memory
        self.session_id = session_id
        #: 群名（习惯落盘前缀 + /memory 展示用；多群并存时区分习惯归属）
        self.group_name = group_name or "默认群"
        self.threshold = float(threshold)
        self.max_output_chars = int(max_output_chars)
        self.verbose = bool(verbose)

        #: 调试可见（对标 ReActAgent.last_intent / last_selected）
        self.last_speakers: List[Dict[str, Any]] = []
        self.last_unknown_mentions: List[str] = []

        #: manager 私有习惯库（表达偏好，内存 + MEMORY.md 落盘）
        self.habits: List[str] = []

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self, input_text: str, **kwargs: Any) -> str:
        """跑一轮群聊，返回发布到群里的 manager 消息正文。"""
        text = str(input_text or "").strip()
        if not text:
            return "（空消息，已忽略）"
        self.last_speakers = []
        self.last_unknown_mentions = []

        # 1) 选人：@ 点名优先，否则自荐
        mentioned, clean, unknown = extract_mentions(text, self.alias_map)
        self.last_unknown_mentions = unknown
        query = clean or text

        speakers = self._resolve_speakers(mentioned, query)

        # 2) 没人举手 → manager 兜底直答（等价于现在的单聊，不硬点名）
        if not speakers:
            answer = self._manager_answer(query, **kwargs)
            self.history.add("user", text)
            self.history.add("manager", answer)
            self._remember_habits(text, answer)
            return self._with_unknown_hint(answer)

        # 3) 命中成员依次 run（串行，保证发言顺序可读；raw 暂存不进群）
        self.history.add("user", text)
        raws: List[tuple] = []
        for member in speakers:
            raw = self._member_answer(member, query, **kwargs)
            raws.append((member, raw))
            self._write_conclusion(member, raw)

        # 4) 改写 + 5) 发布
        final = self._rewrite(raws)
        self.history.add("manager", final)
        self._remember_habits(text, final)
        return self._with_unknown_hint(final)

    # ------------------------------------------------------------------
    # 选人
    # ------------------------------------------------------------------

    def _resolve_speakers(
        self, mentioned: List[str], query: str
    ) -> List[GroupMember]:
        by_name = {m.name: m for m in self.members}
        if mentioned:
            speakers = [by_name[n] for n in mentioned if n in by_name]
            self.last_speakers = [
                {"name": m.name, "display": m.display,
                 "score": float("inf"), "reason": "用户@指定"}
                for m in speakers
            ]
            return speakers
        picked = select_speakers(self.members, query, threshold=self.threshold)
        self.last_speakers = [
            {"name": m.name, "display": m.display,
             "score": score, "reason": reason}
            for m, score, reason in picked
        ]
        return [m for m, _, _ in picked]

    # ------------------------------------------------------------------
    # 执行：成员 / manager 兜底
    # ------------------------------------------------------------------

    def _member_answer(
        self, member: GroupMember, query: str, **kwargs: Any
    ) -> str:
        """成员作答：history 换成共享视图，raw 截断保护。"""
        agent = member.agent
        try:
            agent._history = self.history.as_messages()
            raw = str(agent.run(query, **kwargs) or "")
        except Exception as exc:
            raw = f"（{member.display} 执行失败: {exc}）"
        raw = raw.strip() or f"（{member.display} 未给出回答）"
        if len(raw) > self.max_output_chars:
            raw = (
                raw[: self.max_output_chars]
                + f"\n\n[输出超长已截断，原文 {len(raw)} 字符。]"
            )
        if self.verbose:
            print(f"  [群聊] {member.display} raw({len(raw)}字) 已收")
        return raw

    def _manager_answer(self, query: str, **kwargs: Any) -> str:
        agent = self.manager_agent
        prev_stream = bool(getattr(agent, "stream_output", False))
        try:
            # 群聊由外层统一打印整段：先关掉 manager 的流式，
            # 否则流式打一遍、外层又打印一遍
            if prev_stream:
                agent.stream_output = False
            return str(agent.run(query, **kwargs) or "未能生成回答。")
        except Exception as exc:
            return f"群聊执行失败: {exc}"
        finally:
            if prev_stream:
                agent.stream_output = True

    # ------------------------------------------------------------------
    # 整理与发布
    # ------------------------------------------------------------------

    def _rewrite(self, raws: List[tuple]) -> str:
        """按习惯改写：一次 LLM 调用，失败降级用原文拼接。"""
        if len(raws) == 1:
            ((member, raw),) = raws
            combined = raw
            fallback = raw
        else:
            combined = "\n\n".join(
                f"【{m.display}】\n{r}" for m, r in raws
            )
            fallback = combined

        if not self.habits and len(raws) == 1:
            return fallback  # 无习惯 + 单人：原文即发布，省一次调用

        habits_text = "\n".join(f"- {h}" for h in self.habits) or "（暂无）"
        try:
            llm = self.manager_agent.llm
            response = llm.invoke(messages=[{
                "role": "user",
                "content": REWRITE_PROMPT.format(
                    habits=habits_text, raw=combined,
                ),
            }])
            text = str(getattr(response, "content", "") or "").strip()
            if not text:  # 思考型小模型兜底（全仓统一口径）
                text = str(getattr(response, "reasoning_content", "") or "").strip()
            return text or fallback
        except Exception:
            return fallback

    def _with_unknown_hint(self, answer: str) -> str:
        if self.last_unknown_mentions:
            names = "、".join(f"@{n}" for n in self.last_unknown_mentions)
            answer += f"\n\n（没有找到 {names}，已按内容自动分配。）"
        return answer

    # ------------------------------------------------------------------
    # 记忆：结论双写 + 习惯沉淀
    # ------------------------------------------------------------------

    def _write_conclusion(self, member: GroupMember, raw: str) -> None:
        """结论双写：成员私有库 + 群共享库（任何失败都静默）。"""
        if not raw:
            return
        # 私有：成员自己的召回 provider（领域偏好/内部状态，他人不可见）
        try:
            for provider in getattr(member.agent.context, "providers", []):
                if getattr(provider, "name", "") == "召回记忆":
                    provider.remember(raw, importance=0.6)
                    break
        except Exception:
            pass
        # 共享：所有人可见。记成 episodic——它是会话事件且 sqlite 落盘，
        # 重启后仍在；working 只放本进程，重启即失。
        try:
            if self.shared is not None:
                self.shared.remember(
                    raw, speaker=member.display,
                    importance=0.6, memory_type="episodic",
                    session_id=self.session_id,
                )
        except Exception:
            pass

    def _remember_habits(self, input_text: str, answer: str) -> None:
        """轮末习惯沉淀：只记表达偏好（内存 + MEMORY.md 落盘，静默失败）。"""
        try:
            llm = self.manager_agent.llm
            response = llm.invoke(messages=[{
                "role": "user",
                "content": HABIT_EXTRACT_PROMPT.format(
                    input=input_text, answer=answer,
                ),
            }])
            text = str(getattr(response, "content", "") or "").strip()
            if not text:
                text = str(getattr(response, "reasoning_content", "") or "").strip()
            new = [
                line.strip("-• \t")
                for line in text.splitlines()
                if line.strip() and len(line.strip()) >= 4
            ][:2]
            if not new:
                return
            for habit in new:
                if habit not in self.habits:
                    self.habits.append(habit)
            self._append_habit_file(new)
        except Exception:
            pass

    def _append_habit_file(self, habits: List[str]) -> None:
        """习惯落盘 MEMORY.md（带 [表达偏好:群名] 前缀，去重），并热更新常驻记忆。"""
        fresh = [h.strip() for h in habits if h.strip()]
        if not fresh:
            return
        try:
            path = str(MEMORY_FILE)
            prefix = f"[表达偏好:{self.group_name}]"
            existing = ""
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as fh:
                    existing = fh.read()
            lines = [
                f"- {prefix} {h}"
                for h in fresh
                if f"{prefix} {h}" not in existing
            ]
            if not lines:
                return
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            for p in getattr(self.manager_agent.context, "providers", []):
                if getattr(p, "name", "") == "常驻记忆":
                    if path not in p.paths:
                        p.paths.append(path)
                    p.reload()
        except Exception:
            pass


def build_groupchat(
    manager_agent: Any,
    sub_agent_tools: Sequence[Any],
    session_id: Optional[str] = None,
    user_id: str = "default_user",
    alias_map: Optional[Dict[str, List[str]]] = None,
    group_name: Optional[str] = None,
    member_names: Optional[Sequence[str]] = None,
    threshold: float = DEFAULT_THRESHOLD,
    verbose: bool = False,
) -> GroupChatManager:
    """装配群聊：从现有 SubAgentTool 拆出内部 ReActAgent 当成员。

    Args:
        manager_agent: 主 ReActAgent（兼任 manager，不新建 LLM）。
        sub_agent_tools: build_sub_agents() 的产物（SubAgentTool 列表，
            有 .agent 的才进群，PPT/数据工具天然排除）。
        session_id: 不传则按当天日期生成（单 session 预留多 session 接口）。
        group_name: 群名（习惯落盘前缀用；不传为「默认群」）。
        member_names: 只挑这些 canonical 名进群（建群用；不传则全员）。
    """
    members: List[GroupMember] = []
    merged_aliases: Dict[str, List[str]] = {}
    wanted = {str(n) for n in (member_names or [])}
    for tool in sub_agent_tools or []:
        agent = getattr(tool, "agent", None)
        if agent is None:
            continue
        name = str(getattr(tool, "name", "") or "").strip()
        if not name:
            continue
        if wanted and name not in wanted:
            continue
        display = str(getattr(agent, "name", "") or name).strip()
        aliases = list(DEFAULT_ALIASES.get(name, [display]))
        if alias_map and name in alias_map:
            for a in alias_map[name]:
                if a not in aliases:
                    aliases.append(a)
        merged_aliases[name] = aliases
        members.append(GroupMember(
            name=name, agent=agent, display=display, aliases=aliases,
        ))

    sid = session_id or f"group-{datetime.now().strftime('%Y-%m-%d')}"
    shared = build_shared_memory(user_id=user_id, session_id=sid)
    chat = GroupChatManager(
        manager_agent=manager_agent,
        members=members,
        alias_map=merged_aliases,
        shared_history=SharedHistory(),
        shared_memory=shared,
        session_id=sid,
        group_name=group_name or "默认群",
        threshold=threshold,
        verbose=verbose,
    )
    attach_shared_provider(chat)
    return chat


def attach_shared_provider(chat: GroupChatManager) -> None:
    """把某群的共享召回挂进其成员的 ContextManager。

    多群并存时由 registry 在切换群前后成对调用
    ``detach_shared_provider(old)`` / ``attach_shared_provider(new)``，
    否则两个群的共享结论会互相串台。
    """
    shared = getattr(chat, "shared", None)
    if shared is None:
        return
    for member in getattr(chat, "members", []) or []:
        try:
            providers = getattr(member.agent.context, "providers", None)
            if providers is None:
                continue
            for p in list(providers):
                if (getattr(p, "name", "") == SharedRecallProvider.name
                        and getattr(p, "shared", None) is not shared):
                    providers.remove(p)
            if not any(
                getattr(p, "name", "") == SharedRecallProvider.name
                and getattr(p, "shared", None) is shared
                for p in providers
            ):
                providers.append(SharedRecallProvider(shared))
        except Exception:
            pass


def detach_shared_provider(chat: GroupChatManager) -> None:
    """摘掉某群的共享召回（切换群时先调这个，再 attach 新群）。"""
    shared = getattr(chat, "shared", None)
    for member in getattr(chat, "members", []) or []:
        try:
            providers = getattr(member.agent.context, "providers", None)
            if providers is None:
                continue
            for p in list(providers):
                if (getattr(p, "name", "") == SharedRecallProvider.name
                        and (shared is None
                             or getattr(p, "shared", None) is shared)):
                    providers.remove(p)
        except Exception:
            pass


def describe_pool(sub_agent_tools: Sequence[Any]) -> List[Dict[str, str]]:
    """可用成员池：name/display/aliases（建群失败时提示用户用）。"""
    out: List[Dict[str, str]] = []
    for tool in sub_agent_tools or []:
        if getattr(tool, "agent", None) is None:
            continue
        name = str(getattr(tool, "name", "") or "").strip()
        if not name:
            continue
        agent = getattr(tool, "agent")
        display = str(getattr(agent, "name", "") or name).strip()
        aliases = list(DEFAULT_ALIASES.get(name, [display]))
        tags = list(getattr(tool, "tags", None) or [])
        out.append({
            "name": name, "display": display,
            "aliases": aliases, "tags": tags,
        })
    return out
