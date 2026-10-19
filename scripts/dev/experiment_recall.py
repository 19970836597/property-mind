# -*- coding: utf-8 -*-
"""召回记忆实验台：问题你来出，实时看「召回了什么、怎么进的上下文」。

用法::

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/dev/experiment_recall.py
    # 加 --demo 可预置 3 条示例事实，方便直接开问

命令：
    记住 <内容>     写入一条事实（进工作记忆）
    任意其他输入    当作问题：展示召回结果 + 注入文本 + 组装后的 system 段
    清空           清空工作记忆（重新做实验）
    exit           退出
"""

import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from memory.base import MemoryConfig, MemoryItem  # noqa: E402
from memory.memory_manger import MemoryManager  # noqa: E402
from memory.types.working_memory import _char_bigrams  # noqa: E402
from core.context import (  # noqa: E402
    ContextManager,
    EnvironmentProvider,
    MemoryFileProvider,
    MemoryRecallProvider,
)

DEMO_FACTS = [
    "用户是物业公司的运营主管，姓王，大家都叫他王主管",
    "汇报偏好：一律用简短格式，正文不超过10页，数字用中文",
    "阳光花园项目的收缴率口径 = 当月实收 / 当月应收",
]


def build():
    """搭一条与真实 agent 完全相同的记忆链路。"""
    try:
        manager = MemoryManager(
            config=MemoryConfig(), user_id="default_user",
            enable_working=True, enable_episodic=True,
            enable_semantic=True, enable_perceptual=True,
        )
    except Exception as exc:
        print(f"⚠ MemoryManager 构造失败（实验只能用 Provider 降级路径）: {exc}")
        manager = None
    recall = MemoryRecallProvider(manager=manager)
    ctx = ContextManager(
        system_prompt="你是物业项目智能助手的总调度。",
        providers=[MemoryFileProvider(), EnvironmentProvider(), recall],
    )#md  环境：主要是时间信息  .记忆库搜索 
    return manager, recall, ctx


def show_recall(query: str, manager, recall: MemoryRecallProvider) -> None:
    """展示三层：原始召回 → provider 格式化 → 注入 system 段。"""
    print("\n" + "=" * 62)
    print(f"问题：{query}")
    print("=" * 62)

    # ---- 第 1 层：MemoryManager 原始召回（带重要性与相关度） ----
    if manager is None:
        print("（MemoryManager 不可用，跳过原始召回展示）")
    else:
        hits = manager.retrieve_memories(
            query=query, memory_types=["working", "episodic"],
            limit=3, min_importance=0.3,
        )
        qb = _char_bigrams(query.lower())
        print(f"\n【第 1 层 · 原始召回】{len(hits)} 条命中：")
        for i, item in enumerate(hits, 1):
            overlap = 0.0
            cb = _char_bigrams(str(item.content).lower())
            if qb and cb:
                overlap = len(qb & cb) / len(qb)
            print(f"  {i}. [{overlap:.0%} 重叠] (importance={item.importance:.1f}, "
                  f"{item.memory_type}) {item.content}")
        if not hits:
            print("  （无命中——问题与库存记忆都不相关，属正常）")

    # ---- 第 2 层：Provider 格式化输出 ----
    text = recall.collect(query)
    print("\n【第 2 层 · Provider 注入文本】")
    print(text if text else "（为空——召回到了但没过阈值，或无命中）")

    # ---- 第 3 层：组装进 system 段的样子 ----
    print("\n【第 3 层 · 组装后的 system 段】(▁ 标出召回记忆落点)")


def show_system(ctx: ContextManager, query: str) -> None:
    msgs = ctx.build_messages(query, history=[])
    system = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
    print("-" * 62)
    print(system if system else "（system 段为空）")
    print("-" * 62)


def main() -> None:
    if "--demo" in sys.argv:
        preload = True
    else:
        preload = False

    manager, recall, ctx = build()
    print("召回记忆实验台（链路与真实 agent 完全一致：working+episodic+semantic+perceptual）")
    print("命令：`记住 <内容>` 写入 / `清空` 重置 / 直接输入问题看召回 / exit 退出")

    if preload:
        for fact in DEMO_FACTS:
            recall.remember(fact, importance=0.6)
        print(f"\n已预置 {len(DEMO_FACTS)} 条示例事实（--demo）。直接开问，例如：")
        print("  · 谁是阳光花园的汇报负责人？")
        print("  · 我的汇报格式有什么偏好？")

    while True:
        try:
            line = input("\n实验> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line or line.lower() in ("exit", "quit", "退出"):
            break
        if line == "清空":
            if manager is not None:
                manager.clear_all_memories()
            print("已清空工作记忆。")
            continue
        if line.startswith("记住"):
            fact = line[2:].strip()
            ok = recall.remember(fact, importance=0.6)
            print("✓ 已写入" if ok else "✗ 写入失败（看上方 warning）")
            continue
        show_recall(line, manager, recall)
        show_system(ctx, line)


if __name__ == "__main__":
    main()
