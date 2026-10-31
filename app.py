# -*- coding: utf-8 -*-
"""物业项目经理Agent数字助理 — DeepSeek 式单列界面.

- 单列居中：标题 / 对话 / 输入框，一屏完成
- 快捷入口只在空对话时出现，开聊自动隐藏
- 「新对话」一键回到初始状态，可重新问，也可继续追问
- 「API 设置」默认收起，点开可换渠道/模型/Key，保存即生效并写入 .env
- 双模式：快速模式（主 Agent 调度）/ 协作模式（群聊，多成员协作 + @点名；
  群管理全在聊天框完成：群列表 / 建群 / 切换 / 加人 / 解散，与 CLI 的 /group 同口径）
- 长耗时调用带等待心跳，界面不再长时间无反应
"""

from __future__ import annotations

import glob
import os
import re
import sys
import threading
import traceback
from typing import Dict, List

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

import gradio as gr
from dotenv import load_dotenv

from core.paths import OUTPUTS_DIR, PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")

theme = gr.themes.Soft()

CSS = """
/* =====================================
   1. 页面整体：加宽并居中
   ===================================== */
.gradio-container {
    width: 95% !important;
    max-width: 1200px !important;
    margin: 0 auto !important;
    padding: 8px 24px 16px 24px !important;
    box-sizing: border-box !important;
}

/* =====================================
   2. 大标题：居中
   ===================================== */
#title {
    width: 100% !important;
    text-align: center !important;
    margin: 12px 0 4px 0 !important;
}

#title h1 {
    width: 100% !important;
    text-align: center !important;
    font-size: 22px !important;
    font-weight: 700 !important;
    margin: 0 !important;
}

#title p {
    color: #888 !important;
    font-size: 13px !important;
    margin: 4px 0 0 0 !important;
}

/* =====================================
   3. 状态栏
   ===================================== */
#status {
    text-align: center !important;
}

#status p {
    color: #aaa !important;
    font-size: 12px !important;
    margin: 0 0 6px 0 !important;
}

/* =====================================
   4. 快速模式 / 协作模式
   两种模式宽度一致，各占50%
   ===================================== */
.mode-radio {
    width: 100% !important;
    margin: 4px 0 8px 0 !important;
}

.mode-radio > div,
.mode-radio fieldset {
    display: flex !important;
    flex-direction: row !important;
    width: 100% !important;
    gap: 10px !important;
}

.mode-radio label {
    flex: 1 1 50% !important;
    width: 50% !important;
    min-width: 0 !important;
    justify-content: center !important;
    text-align: center !important;
    box-sizing: border-box !important;
}

/* =====================================
   5. 聊天框：固定高度，内部滚动
   ===================================== */
#chatbot {
    height: 360px !important;
    min-height: 300px !important;
    max-height: 360px !important;
    overflow: hidden !important;
    border: none !important;
    box-shadow: none !important;
    background: transparent !important;
}

/* 聊天内容区域 */
#chatbot > div,
#chatbot .wrap {
    height: 100% !important;
    min-height: 0 !important;
    max-height: 100% !important;
    overflow-y: auto !important;
    overflow-x: hidden !important;
}

/* 消息宽度 */
#chatbot .message {
    max-width: 100% !important;
}

/* 消息文字自动换行 */
#chatbot .message-content,
#chatbot .message p {
    white-space: pre-wrap !important;
    overflow-wrap: anywhere !important;
    word-break: break-word !important;
}

/* 表格内容 */
#chatbot table {
    display: block !important;
    overflow-x: auto !important;
    max-width: 100% !important;
}

/* 代码内容 */
#chatbot pre,
#chatbot code {
    white-space: pre-wrap !important;
    word-break: break-word !important;
    max-width: 100% !important;
}

/* =====================================
   6. 快捷入口和底部操作区域
   ===================================== */
#suggest {
    flex-wrap: wrap !important;
    gap: 8px !important;
    margin-top: 6px !important;
}

.gradio-container .row {
    gap: 8px !important;
}

footer {
    display: none !important;
}

/* =====================================
   7. 屏幕较小时自适应
   ===================================== */
@media (max-width: 768px) {
    .gradio-container {
        width: 100% !important;
        padding: 6px 10px 12px !important;
    }

    #title h1 {
        font-size: 19px !important;
    }

    #chatbot {
        height: 380px !important;
        min-height: 250px !important;
        max-height: 380px !important;
    }

    .mode-radio > div,
    .mode-radio fieldset {
        gap: 6px !important;
    }
}

/* =====================================
   8. 笔记本较矮屏幕适配
   ===================================== */
@media (max-height: 750px) {
    #chatbot {
        height: 350px !important;
        min-height: 250px !important;
        max-height: 350px !important;
    }

    #title {
        margin-top: 6px !important;
    }
}
"""

GREET = (
    "您好，我是您的物业项目经理Agent数字助理。\n\n"
    "可以协助制作PPT，解决催费、法务和服务管理等相关任务。\n"
    "请在下方输入您的问题；上方可在快速 / 协作模式之间切换。"
)

TASK1 = "项目经理月度汇报，主数据源用 data/common 的应收信息"
TASK2 = "分析欠款账龄，给出催缴优先级"
TASK3 = "物业费催收有哪些法律红线？"

PROVIDER_CHOICES = [
    "auto（自动识别）",
    "deepseek", "qwen", "modelscope", "kimi",
    "zhipu", "openai", "ollama", "vllm",
]

_PLACEHOLDERS = frozenset({"xxx", "sk-xxx", "ms-xxx", "***", "none", "null", ""})


def _has_real(key: str) -> bool:
    v = (os.getenv(key) or "").strip()
    return bool(v) and v.lower() not in _PLACEHOLDERS


_AGENT = None
_ROUTER = None
_LLM = None
_INIT_ERROR: str | None = None
_MODE = "quick"  # quick | collab（界面可切换，与 CLI 的固定模式不同）
_REGISTRY = None

# 双模式历史隔离：两边各存各的，切换只换展示，不互相掺消息
_HIST_QUICK = [{"role": "assistant", "content": GREET}]
_HIST_COLLAB = None  # 首次进协作模式时初始化为群选择页
_COLLAB_PENDING = None  # None | "choose" | "create_name" | "create_members"
_COLLAB_DRAFT = ""


def _snapshot(history: list):
    """把当前展示的历史存回所属模式。"""
    global _HIST_QUICK, _HIST_COLLAB
    if _MODE == "collab":
        _HIST_COLLAB = list(history or [])
    else:
        _HIST_QUICK = list(history or [])


def reset_backend():
    global _AGENT, _ROUTER, _LLM, _INIT_ERROR, _REGISTRY
    _AGENT, _ROUTER, _LLM, _INIT_ERROR, _REGISTRY = None, None, None, None, None


def get_backend():
    global _AGENT, _ROUTER, _LLM, _INIT_ERROR
    if _AGENT is not None:
        return _AGENT
    if _INIT_ERROR is not None:
        return None
    try:
        from main import build_router
        from core.llm import LLM
        from agents.react_agent.react_agent import ReActAgent

        _LLM = LLM()
        _ROUTER = build_router(_LLM)
        _AGENT = ReActAgent(
            name="助手", llm=_LLM, router=_ROUTER,
            enable_intent_routing=True, enable_planning=False,
            max_steps=8, auto_remember=True, stream_output=False,
        )
        return _AGENT
    except Exception as exc:  # noqa: BLE001
        _INIT_ERROR = f"{type(exc).__name__}: {exc}"
        return None


def status_text() -> str:
    agent = get_backend()
    if agent is None:
        return f"后端未就绪：{_INIT_ERROR}"
    base = f"{_LLM.provider} / {_LLM.model}"
    if _MODE == "collab":
        if _REGISTRY is not None and _REGISTRY.active is not None:
            return f"{base} · 协作「{_REGISTRY.active_name}」"
        return f"{base} · 协作模式"
    return f"{base} · 快速模式"


# ---------------------------------------------------------------- 协作模式（群聊）
def get_registry():
    """协作模式的群注册表（懒加载，复用主 agent + 子 agent 工具）。"""
    global _REGISTRY, _INIT_ERROR
    if _REGISTRY is not None:
        return _REGISTRY
    agent = get_backend()
    if agent is None:
        return None
    try:
        from agents.groupchat import GroupRegistry

        sub_tools = [
            c for c in _ROUTER
            if getattr(c, "agent", None) is not None
        ]
        if not sub_tools:
            raise RuntimeError("当前没有可进群的子助手。")
        _REGISTRY = GroupRegistry(agent, sub_tools)
        return _REGISTRY
    except Exception as exc:  # noqa: BLE001
        _INIT_ERROR = f"{type(exc).__name__}: {exc}"
        return None


def with_speakers(chat_obj, answer: str) -> str:
    """群聊回答末尾附发言人——只在真的多人协作时显示。

    单人作答或 manager 兜底时不加标注，保持界面干净。
    """
    speakers = getattr(chat_obj, "last_speakers", None) or []
    if len(speakers) > 1:
        parts = ", ".join(
            f"{s['display']}（{s['reason']}）" for s in speakers
        )
        return f"{answer}\n\n---\n发言人：{parts}"
    return answer


def run_group_text(line: str) -> str:
    """执行一条 /group 命令（与 CLI 的 _run_group_command 同口径）。"""
    reg = get_registry()
    if reg is None:
        return f"协作群聊不可用：`{_INIT_ERROR}`"
    rest = line.lstrip()[len("/group"):].strip()
    if not rest:
        return reg.describe_active()
    parts = rest.split(None, 1)
    verb, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
    try:
        if verb == "create":
            sub = arg.split(None, 1)
            if not sub:
                return "用法：/group create <群名> @成员…（例如 /group create 催收群 @催费 @法务）"
            chat_obj = reg.create(sub[0], raw_mentions=sub[1] if len(sub) > 1 else "")
            members = "、".join(m.display for m in chat_obj.members)
            return f"已建群「{sub[0]}」并切换为活跃群，成员：{members}（+ manager）。"
        elif verb == "list":
            return reg.describe()
        elif verb == "switch":
            if not arg:
                return "用法：/group switch <群名>"
            reg.switch(arg)
            return f"已切换到「{arg}」。"
        elif verb == "show":
            return reg.describe_active()
        elif verb == "add":
            chat_obj = reg.add_members(arg)
            return "已加入，当前成员：" + "、".join(m.display for m in chat_obj.members) + "。"
        elif verb == "remove":
            chat_obj = reg.remove_member(arg)
            return "已移除，当前成员：" + "、".join(m.display for m in chat_obj.members) + "。"
        elif verb == "disband":
            if not arg:
                return "用法：/group disband <群名>"
            return reg.disband(arg.split()[0])
        else:
            # 非子命令 → 整段当消息发给活跃群（兼容 CLI 老行为）
            return with_speakers(reg.active, reg.active.run(rest))
    except ValueError as exc:
        return f"群操作失败：{exc}"
    except Exception as exc:  # noqa: BLE001
        return f"群操作异常：{exc}"


def collab_greet() -> str:
    """切到协作模式时的提示语（含成员、@ 用法与聊天框群管理）。"""
    reg = get_registry()
    if reg is None:
        return f"协作模式初始化失败：`{_INIT_ERROR}`"
    chat_obj = reg.active
    members = "、".join(f"@{m.aliases[-1]}" for m in chat_obj.members) or "（无）"
    return (
        f"已进入协作模式，当前群「{reg.active_name}」，成员：{members}（+ manager）。\n"
        "直接提问由成员自荐作答；也可用 @点名（如 @催费 分析欠款账龄，@all 全员会诊）。\n"
        "群管理也在聊天框完成：群列表 / 建群 催收群 @催费 @法务 / 切换到XX / 回复 0 返回快速。\n"
        "（群聊多成员协作，回答稍慢，界面会显示等待进度。）"
    )


GROUP_CHAT_HELP = (
    "协作模式玩法（都在聊天框完成）：\n"
    "· 直接提问：成员自荐作答，manager 整理后统一发布\n"
    "· @点名：@催费 分析欠款账龄（@all 全员会诊）\n"
    "· 群列表 / 当前群：查看群\n"
    "· 建群 催收群 @催费 @法务：建群并进入\n"
    "· 切换到XX / 直接回群名：换群\n"
    "· 加 @法务 / 移除 @催费：调整活跃群成员\n"
    "· 解散XX：解散群（默认群不可解散）\n"
    "· 回复 0：返回快速模式"
)


def _resolve_group_name(want: str, names: List[str]):
    """群名消解：精确优先，否则唯一子串匹配；对不上返回 None（不硬猜）。"""
    want = (want or "").strip()
    if not want:
        return None
    if want in names:
        return want
    hit = [n for n in names if want in n or n in want]
    if len(hit) == 1:
        return hit[0]
    return None


def nl_to_group_cmd(text: str):
    """自然语言群管理 → /group 命令；不像管理的返回 None，走正常群聊。

    特殊返回值（调用方直接展示，不进 run_group_text）：
    * "__BACK__"      返回快速模式；  * "__HELP__" 玩法说明；
    * "__NEED_NAME__" 缺群名追问；    * "__ERR__..." 直接展示的错误。
    """
    reg = get_registry()
    t = (text or "").strip()
    if not t or t.lstrip().startswith("/group"):
        return None
    names = reg.names() if reg else []

    if t in ("0", "返回快速模式", "回快速模式", "返回快速", "退出群聊",
             "退出协作", "exit", "quit"):
        return "__BACK__"
    if t in ("群列表", "有哪些群", "列出群", "查看群", "群", "list"):
        return "/group list"
    if t in ("当前群", "查看当前群", "群信息", "现在在哪个群", "show"):
        return "/group show"
    if t in ("群帮助", "帮助", "怎么用", "群命令", "help"):
        return "__HELP__"

    m = re.match(r"^(建群|创建群|新建群|新建群聊|创建群聊)\s*(.*)$", t)
    if m:
        rest = m.group(2).strip()
        if not rest:
            return "__NEED_NAME__"
        return f"/group create {rest}"

    m = re.match(r"^(解散|删除群|解散群|删除)\s*(.+)$", t)
    if m:
        target = (m.group(2) or "").strip()
        if not target:
            return "__NEED_NAME__"
        resolved = _resolve_group_name(target, names)
        if resolved is None:
            return f"__ERR__没有群「{target}」，现有：{'、'.join(names) or '（无）'}。"
        return f"/group disband {resolved}"

    m = re.match(r"^(加|添加|拉|邀请)\s*(.+)$", t)
    if m and "@" in m.group(2):
        return f"/group add {m.group(2).strip()}"

    m = re.match(r"^(移除|删除|踢|移出)\s*(.+)$", t)
    if m and "@" in m.group(2):
        return f"/group remove {m.group(2).strip()}"

    # 切换：仅当能对上现有群名才算命令，对不上就当普通消息进群聊
    m = re.match(r"^(切换到|切换|进入|去|进)\s*(.+?)\s*(群)?$", t)
    if m:
        resolved = _resolve_group_name((m.group(2) or "").strip(), names)
        if resolved:
            return f"/group switch {resolved}"
        return None

    # 直接回复群名 → 切换
    if t in names:
        return f"/group switch {t}"
    return None


def _call_box(fn):
    """阻塞调用收敛为字典（供心跳线程使用，不抛错）。"""
    try:
        return {"ok": fn()}
    except Exception:
        return {"err": traceback.format_exc(limit=3)}


def _heartbeat(history: list, label: str, th: "threading.Thread", step: int = 4):
    """心跳：阻塞线程活着就每 step 秒刷新一次等待时长，避免界面长时间无反应。"""
    elapsed = 0
    while th.is_alive():
        th.join(timeout=step)
        if not th.is_alive():
            break
        elapsed += step
        history[-1] = {"role": "assistant",
                       "content": f"{label}（已等待 {elapsed} 秒，请稍候…）"}
        yield history, ""


def _collab_menu_text() -> str:
    """群选择页（对标 CLI choose_collaboration_group 的 1/2/0 列表）。"""
    reg = get_registry()
    if reg is None:
        return f"协作模式初始化失败：`{_INIT_ERROR}`"
    names = reg.names()
    lines = ["请选择协作群聊："]
    for i, name in enumerate(names, start=1):
        chat_obj = reg.groups.get(name)
        n = len(getattr(chat_obj, "members", []) or [])
        active = "，活跃" if name == reg.active_name else ""
        lines.append(f"  {i}. {name}（{n} 位成员{active}）")
    lines.append(f"  {len(names) + 1}. 新建群聊")
    lines.append("  0. 返回快速模式")
    lines.append("")
    lines.append("请回复序号（或直接回复群名切换）。")
    return "\n".join(lines)


def _enter_collab_fresh():
    """首次进入协作：显示群选择页。"""
    global _HIST_COLLAB, _COLLAB_PENDING, _COLLAB_DRAFT
    _COLLAB_PENDING = "choose"
    _COLLAB_DRAFT = ""
    _HIST_COLLAB = [{"role": "assistant", "content": _collab_menu_text()}]
    return _HIST_COLLAB


def _handle_collab_choice(text: str):
    """处理群选择页的输入。返回 (assistant 回复, 是否结束选择流程)。

    约定："__BACK_TO_QUICK__" 表示用户选 0，调用方负责切回快速模式。
    """
    global _COLLAB_PENDING, _COLLAB_DRAFT
    reg = get_registry()
    if reg is None:
        _COLLAB_PENDING = None
        return f"协作群聊不可用：`{_INIT_ERROR}`", True
    names = reg.names()
    t = (text or "").strip()
    if t == "0":
        return "__BACK_TO_QUICK__", True
    # 选群页也支持解散：解散/删除 催收群（默认群拒绝）
    m = re.match(r"^(解散|删除群|解散群|删除)\s*(.+)$", t)
    if m:
        target = (m.group(2) or "").strip()
        if not target:
            return f"请补充群名，例如：解散催收群。\n\n{_collab_menu_text()}", False
        resolved = _resolve_group_name(target, names)
        if resolved is None:
            return f"没有群「{target}」，现有：{'、'.join(names) or '（无）'}。\n\n{_collab_menu_text()}", False
        try:
            msg = reg.disband(resolved)
        except ValueError as exc:
            return f"{exc}\n\n{_collab_menu_text()}", False
        # 解散后刷新菜单（序号会变），仍停在选群页
        return f"{msg}\n\n{_collab_menu_text()}", False
    try:
        choice = int(t)
    except (TypeError, ValueError):
        choice = None
    if choice is not None:
        if 1 <= choice <= len(names):
            try:
                reg.switch(names[choice - 1])
            except (ValueError, KeyError) as exc:
                return f"切换失败：{exc}\n\n{_collab_menu_text()}", False
            _COLLAB_PENDING = None
            return collab_greet(), True
        elif choice == len(names) + 1:
            _COLLAB_PENDING = "create_name"
            return "群名（2~12字，仅中英/数字/下划线）：", False
        return f"无效选择，请输入 0~{len(names) + 1}。\n\n{_collab_menu_text()}", False
    if t in names:
        try:
            reg.switch(t)
        except (ValueError, KeyError) as exc:
            return f"切换失败：{exc}", False
        _COLLAB_PENDING = None
        return collab_greet(), True
    if t in ("新建群聊", "新建", "new"):
        _COLLAB_PENDING = "create_name"
        return "群名（2~12字，仅中英/数字/下划线）：", False
    return f"无效选择，请输入 0~{len(names) + 1} 或群名。\n\n{_collab_menu_text()}", False


def sync_mode_ui():
    """chat 链路收尾：把模式单选同步为 _MODE（覆盖输入框输 0 返回快速的路径）。"""
    return gr.update(value=("协作模式" if _MODE == "collab" else "快速模式"))


def refresh_status():
    """聊完一轮后刷新状态行。"""
    return status_text()


def switch_mode(history: list, label: str):
    """模式切换：先存当前展示，再换另一边的历史；两边互不干扰。"""
    global _MODE, _HIST_COLLAB, _COLLAB_PENDING, _COLLAB_DRAFT
    _snapshot(history)
    _MODE = "collab" if "协作" in str(label or "") else "quick"
    if _MODE == "collab":
        reg = get_registry()
        if reg is None:
            if _HIST_COLLAB is None:
                _HIST_COLLAB = []
            _HIST_COLLAB = _HIST_COLLAB + [{
                "role": "assistant",
                "content": f"协作模式初始化失败：`{_INIT_ERROR}`",
            }]
            return _HIST_COLLAB, status_text()
        if _HIST_COLLAB is None:
            # 首次进入：对话式选群（对标 CLI 的 1/2/0 列表）
            _enter_collab_fresh()
        _reset_files()
        return _HIST_COLLAB, status_text()
    _reset_files()
    return _HIST_QUICK, status_text()


def latest_files(n: int = 3) -> List[str]:
    files: List[str] = []
    for pat in ("**/*.pptx", "**/*.docx"):
        files.extend(glob.glob(str(OUTPUTS_DIR / pat), recursive=True))
    files = [f for f in files if os.path.isfile(f)]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[:n]


def _all_artifacts() -> List[str]:
    """outputs 下全部产物（绝对路径集合，用于对话前后对比找新增）。"""
    out: List[str] = []
    for pat in ("**/*.pptx", "**/*.docx"):
        out.extend(glob.glob(str(OUTPUTS_DIR / pat), recursive=True))
    return [os.path.abspath(f) for f in out if os.path.isfile(f)]


def _new_since(before: List[str]) -> List[str]:
    """只返回 before 快照之后新增的产物（按修改时间排序）。"""
    seen = set(before or [])
    fresh = [f for f in _all_artifacts() if f not in seen]
    fresh.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return fresh


# 当前对话本轮新增的产物（产物下载只显示这些，不翻历史旧账）
_LAST_NEW_FILES: List[str] = []
# 对话开始时 outputs 快照（新对话/切换模式时刷新，用于对比找新增）
_BASELINE_FILES: List[str] = []


def _set_last_files(files) -> List[str]:
    """记录本轮新增产物并返回（供界面刷新下载区）。"""
    global _LAST_NEW_FILES
    _LAST_NEW_FILES = list(files or [])
    return _LAST_NEW_FILES


def _reset_files():
    """新对话/切换模式：清空下载区，并以当前 outputs 为基线。"""
    global _LAST_NEW_FILES, _BASELINE_FILES
    _BASELINE_FILES = _all_artifacts()
    _LAST_NEW_FILES = []
    return []


def _collect_new_files() -> List[str]:
    """把相对基线新增的产物追加到下载区（去重），返回当前下载区内容。"""
    global _LAST_NEW_FILES
    for f in _new_since(_BASELINE_FILES):
        if f not in _LAST_NEW_FILES:
            _LAST_NEW_FILES.append(f)
    _LAST_NEW_FILES.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return list(_LAST_NEW_FILES)


def _set_mode(mode: str):
    """切换当前模式（chat 协作分支内调用，避免函数内 global 声明冲突）。"""
    global _MODE
    _MODE = mode


def _set_collab_flow(pending, draft=""):
    """推进选群/建群流程状态。"""
    global _COLLAB_PENDING, _COLLAB_DRAFT
    _COLLAB_PENDING = pending
    _COLLAB_DRAFT = draft


def chat(message: str, history: list):
    history = list(history or [])
    if not (message or "").strip():
        yield history, ""
        return
    text = message.strip()

    # ---- 协作模式：先走选群/建群流程，再进活跃群 ----
    if _MODE == "collab":
        reg = get_registry()
        if reg is None:
            history = history + [
                {"role": "user", "content": message},
                {"role": "assistant",
                 "content": f"协作群聊不可用：`{_INIT_ERROR}`"},
            ]
            _snapshot(history)
            yield history, ""
            return
        if _COLLAB_PENDING == "choose":
            history = history + [{"role": "user", "content": message}]
            reply, _ = _handle_collab_choice(text)
            if reply == "__BACK_TO_QUICK__":
                # 选 0：回快速模式，对话只留快速历史，协作停在选群页
                _snapshot(history[:-1])
                _set_mode("quick")
                yield _HIST_QUICK, ""
                return
            history = history + [{"role": "assistant", "content": reply}]
            _snapshot(history)
            yield history, ""
            return
        if _COLLAB_PENDING == "create_name":
            name = text
            try:
                from agents.groupchat.registry import GROUP_NAME_RE
                if not GROUP_NAME_RE.match(name):
                    raise ValueError("群名不合法：2~12 字，仅中英/数字/下划线。")
                if name in reg.names():
                    raise ValueError(f"群「{name}」已存在，换个名字或直接回复群名切换。")
            except ValueError as exc:
                history = history + [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": f"{exc}\n\n请重新输入群名："},
                ]
                _snapshot(history)
                yield history, ""
                return
            _set_collab_flow("create_members", name)
            history = history + [
                {"role": "user", "content": message},
                {"role": "assistant",
                 "content": f"成员（例如 @催费 @法务，至少一个，可用：{reg.pool_hint()}）："},
            ]
            _snapshot(history)
            yield history, ""
            return
        if _COLLAB_PENDING == "create_members":
            try:
                chat_obj = reg.create(_COLLAB_DRAFT, raw_mentions=text)
                members = "、".join(m.display for m in chat_obj.members)
                reply = (f"已进入协作群聊「{chat_obj.group_name}」。"
                         f"成员：{members}（+ manager）。\n"
                         "直接提问由成员自荐作答；也可用 @点名（如 @催费 分析欠款账龄，@all 全员会诊）。")
                _set_collab_flow(None)
            except ValueError as exc:
                reply = f"建群失败：{exc}\n\n请重新输入成员："
            except Exception as exc:  # noqa: BLE001
                reply = f"建群异常：{exc}"
                _set_collab_flow(None)
            history = history + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": reply},
            ]
            _snapshot(history)
            yield history, ""
            return
        if text.lstrip().startswith("/group"):
            history = history + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": "处理中…"},
            ]
            yield history, ""
            box = _call_box(lambda: run_group_text(text))
            history[-1] = {"role": "assistant",
                           "content": box.get("ok") or f"执行失败：\n```\n{box.get('err')}\n```"}
            _snapshot(history)
            yield history, ""
            return
        # 自然语言群管理（群列表 / 建群 / 切换 / 加人 / 解散 / 0返回）
        nl_cmd = nl_to_group_cmd(text)
        if nl_cmd is not None:
            if nl_cmd == "__BACK__":
                _snapshot(history + [{"role": "user", "content": message}])
                _set_mode("quick")
                yield _HIST_QUICK, ""
                return
            if nl_cmd == "__HELP__":
                history = history + [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": GROUP_CHAT_HELP},
                ]
                _snapshot(history)
                yield history, ""
                return
            if nl_cmd == "__NEED_NAME__":
                history = history + [
                    {"role": "user", "content": message},
                    {"role": "assistant",
                     "content": "请补充完整，例如：建群 催收群 @催费 @法务"},
                ]
                _snapshot(history)
                yield history, ""
                return
            if nl_cmd.startswith("__ERR__"):
                history = history + [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": nl_cmd[len("__ERR__"):]},
                ]
                _snapshot(history)
                yield history, ""
                return
            history = history + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": "处理中…"},
            ]
            yield history, ""
            box = _call_box(lambda: run_group_text(nl_cmd))
            history[-1] = {"role": "assistant",
                           "content": box.get("ok") or f"执行失败：\n```\n{box.get('err')}\n```"}
            _snapshot(history)
            yield history, ""
            return
        history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": "群聊讨论中…"},
        ]
        yield history, ""
        th = threading.Thread(
            target=lambda: None, daemon=True)  # 占位，下方替换为真实调用
        # 真实群聊调用（成员依次作答 + manager 整理，耗时较长）
        box: dict = {}
        th = threading.Thread(
            target=lambda: box.update(
                _call_box(lambda: with_speakers(reg.active, reg.active.run(text)))),
            daemon=True)
        th.start()
        yield from _heartbeat(history, "群聊讨论中", th)
        th.join()
        if "ok" in box:
            history[-1] = {"role": "assistant", "content": box["ok"]}
        else:
            history[-1] = {"role": "assistant",
                           "content": f"执行失败：\n```\n{box.get('err')}\n```"}
        _snapshot(history)
        yield history, ""
        return

    # ---- 快速模式：主 Agent 调度 ----
    agent = get_backend()
    if agent is None:
        history = history + [
            {"role": "user", "content": message},
            {"role": "assistant",
             "content": f"后端未就绪：`{_INIT_ERROR}`\n请点下方「API 设置」检查 Key 后重试。"},
        ]
        _snapshot(history)
        yield history, ""
        return
    history = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": "思考中…"},
    ]
    yield history, ""
    box = {}
    th = threading.Thread(
        target=lambda: box.update(_call_box(lambda: agent.run(message.strip()))),
        daemon=True)
    th.start()
    yield from _heartbeat(history, "思考中", th)
    th.join()
    if "ok" in box:
        answer = box["ok"]
        history[-1] = {"role": "assistant", "content": answer or "（空回答）"}
    else:
        err = box.get("err")
        history[-1] = {"role": "assistant",
                       "content": "执行失败：\n```\n" + str(err) + "\n```"}
    yield history, ""


def new_chat():
    """新对话：只清当前模式的历史，另一边不动；协作停在选群页则重置。"""
    global _HIST_QUICK, _HIST_COLLAB, _COLLAB_PENDING, _COLLAB_DRAFT
    agent = get_backend()
    if _MODE == "collab":
        if _REGISTRY is not None:
            try:
                for grp in _REGISTRY.groups.values():
                    grp.history.turns.clear()
            except Exception:  # noqa: BLE001
                pass
        _HIST_COLLAB = None
        _COLLAB_PENDING = None
        _COLLAB_DRAFT = ""
        _enter_collab_fresh()
        return _HIST_COLLAB, "", _reset_files()
    if agent is not None:
        try:
            agent.clear_history()
        except Exception:  # noqa: BLE001
            pass
    _HIST_QUICK = [{"role": "assistant", "content": GREET}]
    return _HIST_QUICK, "", _reset_files()


# ---------------------------------------------------------------- API 设置
def _provider_label() -> str:
    p = (os.getenv("LLM_PROVIDER") or "auto").strip().lower() or "auto"
    if p == "auto":
        return PROVIDER_CHOICES[0]
    return p if p in PROVIDER_CHOICES else PROVIDER_CHOICES[0]


def current_settings():
    """回填设置面板：渠道 / 模型 / URL 明文，Key 只提示是否已配置."""
    from core.llm import DEFAULT_CONFIG

    provider = _provider_label()
    model = os.getenv("LLM_MODEL_ID") or ""
    url = os.getenv("LLM_BASE_URL") or ""
    key_hint = "请输入 API Key"
    p = (os.getenv("LLM_PROVIDER") or "auto").strip().lower() or "auto"
    if p in DEFAULT_CONFIG:
        cfg = DEFAULT_CONFIG[p]
        if _has_real(cfg.get("api_key_env", "")):
            key_hint = "已配置（留空则不改动）"
        if not url and os.getenv(cfg.get("base_url_env", "")):
            url = os.getenv(cfg["base_url_env"])
    elif _has_real("LLM_API_KEY"):
        key_hint = "已配置（留空则不改动）"
    return (
        status_text(),
        provider,
        model,
        gr.update(value="", placeholder=key_hint),
        url,
        _reset_files(),
    )


def _write_env(updates: Dict[str, str]) -> None:
    """更新 .env 文件并同步 os.environ，重启后依然有效。"""
    path = PROJECT_ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    out = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            out.append(line)
            continue
        k = s.split("=", 1)[0].strip()
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    for k, v in updates.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def save_api(provider_label: str, model: str, key: str, url: str):
    """保存 API 设置：写 .env → 重建后端 → 发一句最小请求验证连通."""
    from core.llm import DEFAULT_CONFIG

    provider = (provider_label or "").strip().lower()
    if provider.startswith("auto"):
        provider = "auto"
    if provider not in [c for c in PROVIDER_CHOICES if not c.startswith("auto")] + ["auto"]:
        return "未知的渠道选择。", status_text(), None, _reset_files()

    updates: Dict[str, str] = {"LLM_PROVIDER": provider}
    if (model or "").strip():
        updates["LLM_MODEL_ID"] = model.strip()
    cfg = DEFAULT_CONFIG.get(provider, {}) if provider != "auto" else {}
    if (key or "").strip():
        updates[cfg.get("api_key_env", "LLM_API_KEY")] = key.strip()
    if (url or "").strip():
        updates[cfg.get("base_url_env", "LLM_BASE_URL")] = url.strip()
    _write_env(updates)

    reset_backend()
    agent = get_backend()
    if agent is None:
        return (
            f"保存成功，但后端启动失败：`{_INIT_ERROR}`",
            status_text(),
            [{"role": "assistant", "content": GREET}],
            _reset_files(),
        )
    try:
        resp = _LLM.invoke([{"role": "user", "content": "只回复：OK"}],
                           max_tokens=8)
        text = (getattr(resp, "content", "") or "").strip() or "通道通"
        hint = f"已切换到 {_LLM.provider}/{_LLM.model}，连接正常：{text}"
    except Exception as exc:  # noqa: BLE001
        hint = f"已保存（{_LLM.provider}/{_LLM.model}），但连接失败：`{exc}`"
    try:
        agent.clear_history()
    except Exception:  # noqa: BLE001
        pass
    return hint, status_text(), [{"role": "assistant", "content": GREET}], _reset_files()


with gr.Blocks(title="物业项目经理Agent数字助理") as demo:
    gr.HTML(
        '<div id="title"><h1>物业项目经理Agent数字助理</h1>'
        "<p>经营汇报 · 欠费催缴 · 法务合规 · 服务管理</p></div>"
    )
    status = gr.Markdown("启动中…", elem_id="status")

    # 双模式切换（对标 CLI 启动时的 1/2 选择；界面内可随时切换）
    mode_radio = gr.Radio(
        choices=["快速模式", "协作模式"], value="快速模式",
        show_label=False, elem_classes=["mode-radio"],
    )

    chatbot = gr.Chatbot(
        elem_id="chatbot",
        value=[{"role": "assistant", "content": GREET}],
        show_label=False,
        render_markdown=True,
    )

    # 快捷入口：空对话时可见，一旦开聊自动隐藏
    with gr.Row(elem_id="suggest") as suggest:
        s1 = gr.Button("项目经理月度汇报", size="sm")
        s2 = gr.Button("分析欠款账龄", size="sm")
        s3 = gr.Button("催收法律红线", size="sm")

    with gr.Row():
        msg = gr.Textbox(
            show_label=False, placeholder="请输入您的问题，回车发送…",
            lines=1, max_lines=4, scale=7,
        )
        send = gr.Button("发送", variant="primary", scale=1)
        fresh = gr.Button("新对话", scale=1)

    with gr.Row():
        gear = gr.Button("API 设置", size="sm", scale=1)
        files = gr.File(label="产物下载", file_count="multiple",
                        interactive=False, scale=5)

    # 设置面板默认收起，保持首页干净
    settings_visible = gr.State(False)
    with gr.Group(visible=False) as settings_box:
        with gr.Row():
            provider_dd = gr.Dropdown(choices=PROVIDER_CHOICES, label="渠道", scale=2)
            model_tb = gr.Textbox(label="模型", placeholder="如 deepseek-chat，留空用默认",
                                  scale=3)
        with gr.Row():
            key_tb = gr.Textbox(label="API Key", type="password",
                                placeholder="请输入 API Key", scale=3)
            url_tb = gr.Textbox(label="Base URL（可选）",
                                placeholder="留空用默认", scale=3)
        with gr.Row():
            save_btn = gr.Button("保存并连接", variant="primary", size="sm", scale=1)
            api_hint = gr.Markdown("", scale=5)

    def _toggle_suggest(history):
        show = not any(m.get("role") == "user" for m in (history or []))
        return gr.update(visible=show)

    def _toggle_settings(v):
        return gr.update(visible=not v), (not v)

    msg.submit(chat, [msg, chatbot], [chatbot, msg]).then(
        _toggle_suggest, chatbot, suggest).then(
        refresh_status, None, status).then(
        sync_mode_ui, None, mode_radio).then(
        lambda: _collect_new_files(), None, files)
    send.click(chat, [msg, chatbot], [chatbot, msg]).then(
        _toggle_suggest, chatbot, suggest).then(
        refresh_status, None, status).then(
        sync_mode_ui, None, mode_radio).then(
        lambda: _collect_new_files(), None, files)

    # 三个快捷按钮文本各自写死，不共用变量，不会错位
    s1.click(lambda h: list(chat(TASK1, h))[-1], chatbot, [chatbot, msg]).then(
        _toggle_suggest, chatbot, suggest).then(
        refresh_status, None, status).then(
        sync_mode_ui, None, mode_radio).then(
        lambda: _collect_new_files(), None, files)
    s2.click(lambda h: list(chat(TASK2, h))[-1], chatbot, [chatbot, msg]).then(
        _toggle_suggest, chatbot, suggest).then(
        refresh_status, None, status).then(
        sync_mode_ui, None, mode_radio).then(
        lambda: _collect_new_files(), None, files)
    s3.click(lambda h: list(chat(TASK3, h))[-1], chatbot, [chatbot, msg]).then(
        _toggle_suggest, chatbot, suggest).then(
        refresh_status, None, status).then(
        sync_mode_ui, None, mode_radio).then(
        lambda: _collect_new_files(), None, files)

    mode_radio.change(switch_mode, [chatbot, mode_radio],
                      [chatbot, status])

    fresh.click(new_chat, None, [chatbot, msg, files]).then(
        _toggle_suggest, chatbot, suggest)

    gear.click(_toggle_settings, settings_visible, [settings_box, settings_visible])
    save_btn.click(save_api,
                   [provider_dd, model_tb, key_tb, url_tb],
                   [api_hint, status, chatbot, files]).then(
        _toggle_suggest, chatbot, suggest)

    demo.load(current_settings, None,
              [status, provider_dd, model_tb, key_tb, url_tb, files]).then(
        lambda: list(_HIST_QUICK), None, chatbot)

if __name__ == "__main__":
    demo.queue(max_size=8).launch(inbrowser=True, theme=theme, css=CSS)
