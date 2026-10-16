# -*- coding: utf-8 -*-
"""core/context 模块的验证套件（不依赖 LLM 服务，纯逻辑可跑）。

用法::

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verify_context.py

覆盖计划里的检查 1/2/3/4/6：
    1. 预算核算 —— describe() 分层 token 量级合理
    2. 记忆注入 —— MEMORY.md 内容出现在组装出的 system 段
    3. 记忆库不可用时优雅降级 —— 不抛异常，agent 照常组装
    4. 压缩触发 —— 工具结果先被裁、人格/常驻记忆完好、序列仍合法
    6. MemoryTool 已从代码中移除

检查 5/7（E2E 跑 main.py、多轮累积）需要 ollama 在线，另行手动执行。
"""

import os
import sys
import tempfile
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.context import (  # noqa: E402
    Compactor,
    ContextBudget,
    ContextManager,
    MemoryFileProvider,
    MemoryRecallProvider,
    estimate_messages_tokens,
    estimate_tokens,
)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  —— {detail}" if detail else ""))


# ---------------------------------------------------------------- 1. 预算核算
def check_budget():
    b = ContextBudget(total=32000)
    b.record("人格", 800)
    b.record("历史对话", 5000)
    b.record("历史对话", 300)          # 同名层累加
    ok_used = b.used == 6100
    ok_over = not b.over_budget(1000) and b.over_budget(100000)
    print(b.describe())
    check("1. 预算核算（累加/超限判断）", ok_used and ok_over,
          f"used={b.used} usable={b.usable}")


# ---------------------------------------------------------------- 2. 记忆注入
def check_injection():
    marker = "验证标记：汇报一律用中文数字"
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(marker)
        path = fh.name
    try:
        ctx = ContextManager(
            system_prompt="你是测试助手。",
            providers=[MemoryFileProvider([path])],
        )
        msgs = ctx.build_messages("项目经理月度汇报", history=[])
        system = msgs[0]["content"]
        check("2. 记忆注入（MEMORY.md 进 system 段）", marker in system,
              f"system 段 {len(system)} 字")
        check("2b. 组装结构（system→user）",
              msgs[0]["role"] == "system" and msgs[-1]["role"] == "user"
              and len(msgs) == 2)
    finally:
        os.unlink(path)


# ------------------------------------------------- 3. 记忆库不可用 → 降级
def check_degradation():
    # 把 memory 包塞成 import 就炸的假模块，模拟 embedding/依赖全挂
    def _boom(item):
        raise RuntimeError(f"模拟：{item} 不可用")

    for mod_name in ("memory.base", "memory.memory_manger"):
        fake = types.ModuleType(mod_name)
        fake.__getattr__ = _boom          # PEP 562：模块级 __getattr__
        sys.modules[mod_name] = fake

    provider = MemoryRecallProvider(memory_types=["working", "episodic"])
    try:
        text = provider.collect("上月收缴率")
        ok = text == "" and provider._disabled
        # agent 主流程不受影响：ContextManager 照常组装
        ctx = ContextManager(system_prompt="人格", providers=[provider])
        msgs = ctx.build_messages("你好", history=[])
        check("3. 记忆库不可用时降级（不抛异常、agent 照常）",
              ok and msgs[-1]["content"] == "你好",
              f"collect 返回 {text!r}，disabled={provider._disabled}")
    finally:
        sys.modules.pop("memory.base", None)
        sys.modules.pop("memory.memory_manger", None)


# ---------------------------------------------------------------- 4. 压缩
def _valid_pairing(messages):
    """role=tool 必须紧跟带 tool_calls 的 assistant —— OpenAI 序列合法性。"""
    for i, m in enumerate(messages):
        if m.get("role") == "tool":
            prev = messages[i - 1] if i > 0 else {}
            if not (prev.get("role") == "assistant" and prev.get("tool_calls")):
                return False
    return True


def check_compaction():
    persona = "你是物业项目智能助手的总调度。"
    tool_dump = "行" * 4000                       # 模拟整表 dump
    messages = [{"role": "system", "content": persona}]
    for turn in range(12):                          # 12 轮假历史
        messages.append({"role": "user", "content": f"第{turn}轮问题，涉及收缴率与欠费明细"})
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": f"c{turn}", "type": "function",
                                         "function": {"name": "query",
                                                      "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{turn}",
                         "content": tool_dump})
        messages.append({"role": "assistant", "content": f"第{turn}轮结论：收缴率{90 - turn}%。"})

    budget = ContextBudget(total=32000)
    usable = budget.usable
    compactor = Compactor(llm=None, keep_recent_turns=4)   # 无 llm → 摘要退化
    compacted, actions = compactor.compact(messages, usable)

    before, after = estimate_messages_tokens(messages), estimate_messages_tokens(compacted)
    sys_ok = compacted[0]["role"] == "system" and persona in compacted[0]["content"]
    trimmed = any("裁剪" in a for a in actions)
    check("4a. 压缩触发且降到预算内", after <= usable,
          f"{before:,} → {after:,} (预算 {usable:,})，动作: {actions}")
    check("4b. 人格/常驻记忆完好", sys_ok)
    check("4c. 工具结果最先被裁", trimmed)
    check("4d. 消息序列仍合法（tool 配对完整）", _valid_pairing(compacted))


# ------------------------------------------------- 6. MemoryTool 移除
def check_memory_tool_gone():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tool", "builtin", "memory_tool.py",
    )
    check("6. memory_tool.py 已删除", not os.path.isfile(path),
          "文件仍在，待 Bash 恢复后 git rm" if os.path.isfile(path) else "")


if __name__ == "__main__":
    print("=" * 60)
    check_budget()
    print("-" * 60)
    check_injection()
    print("-" * 60)
    check_degradation()
    print("-" * 60)
    check_compaction()
    print("-" * 60)
    check_memory_tool_gone()
    print("=" * 60)
    print(f"结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
    for name in FAIL:
        print(f"  ✗ {name}")
    sys.exit(1 if FAIL else 0)
