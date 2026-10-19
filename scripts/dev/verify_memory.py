# -*- coding: utf-8 -*-
"""记忆链路诊断：写入 → 召回 → 落盘，三段定位"记不住"在哪一环。

用法::

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/dev/verify_memory.py
"""

import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {detail}" if detail else ""))


# ------------------------------------------------ 1. 工作记忆中文召回
def check_working_recall():
    from memory.base import MemoryConfig, MemoryItem
    from memory.types.working_memory import WorkingMemory

    wm = WorkingMemory(MemoryConfig())
    facts = [
        "用户偏好：汇报一律用简短格式，不要超过10页",
        "张三是阳光花园项目的项目经理",
        "收缴率口径按当月实收除以当月应收",
    ]
    for f in facts:
        wm.add(MemoryItem(
            content=f, memory_type="working", user_id="default_user",
            timestamp=datetime.now(), importance=0.6, metadata={},
        ))

    cases = [
        ("我的汇报格式偏好是什么", "简短格式"),
        ("阳光花园的项目经理是谁", "张三"),
        ("收缴率怎么算", "口径"),
    ]
    for query, expect in cases:
        hits = wm.retrieve(query=query, limit=3, user_id="default_user")
        top = hits[0].content if hits else ""
        check(f"召回「{query}」命中「{expect}」", expect in top, f"top: {top[:40]}")


# ------------------------------------------------ 2. MemoryManager 全链路
def check_manager():
    from memory.base import MemoryConfig
    from memory.memory_manger import MemoryManager

    try:
        manager = MemoryManager(
            config=MemoryConfig(), user_id="default_user",
            enable_working=True, enable_episodic=True,
            enable_semantic=False, enable_perceptual=False,
        )
    except Exception as exc:
        check("MemoryManager 构造", False, f"失败: {exc}")
        return

    manager.add_memory(
        content="用户是物业公司的运营主管，姓王",
        memory_type="working", importance=0.6, auto_classify=False,
    )
    hits = manager.retrieve_memories(
        query="用户的职位和姓氏", memory_types=["working", "episodic"],
        limit=3, min_importance=0.3,
    )
    top = str(getattr(hits[0], "content", "")) if hits else ""
    check("MemoryManager 写入→检索（中文）", "王" in top or "主管" in top,
          f"召回 {len(hits)} 条, top: {top[:40]}")


# ------------------------------------------------ 3. Provider 层（含降级）
def check_provider():
    from core.context import MemoryRecallProvider

    provider = MemoryRecallProvider()
    provider.remember("用户喜欢用中文数字汇报", importance=0.6)
    text = provider.collect("用户汇报有什么偏好")
    check("Provider remember→collect", bool(text), f"召回文本: {text[:60]}")
    check("Provider 未被禁用", not provider._disabled)


if __name__ == "__main__":
    print("=" * 60)
    check_working_recall()
    print("-" * 60)
    check_manager()
    print("-" * 60)
    check_provider()
    print("=" * 60)
    print(f"结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
    for name in FAIL:
        print(f"  ✗ {name}")
    sys.exit(1 if FAIL else 0)
