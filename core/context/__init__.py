# -*- coding: utf-8 -*-
"""上下文管理 —— 分层组装、预算核算、超限压缩、记忆自动注入。

这个包解决两件事
----------------
**1. 记忆不是工具，而是框架本身就有的。**
原来 ``tool/builtin/memory_tool.py`` 把记忆做成模型调用的工具（16 个参数、
9 个 action），零真实调用方，而且本项目的小模型根本传不对参。现在改由
provider 在组装上下文时**自动**注入：常驻记忆启动即载，相关记忆按轮召回，
模型的能力表里看不到任何 memory 工具。

**2. 上下文要有预算。**
原来 ``_build_messages`` 是三行平铺拼接，全仓库没有任何 token 估算或截断，
长会话必然撑爆窗口。现在分层记账、超限按固定顺序压缩。

用法::

    from core.context import ContextManager, MemoryFileProvider

    ctx = ContextManager(
        system_prompt=MAIN_SYSTEM_PROMPT,
        providers=[MemoryFileProvider(["MEMORY.md"])],
        llm=llm,
    )
    messages = ctx.build_messages("项目经理月度汇报", history)
    print(ctx.describe())

分层与压缩顺序见 ``manager.py`` 与 ``compactor.py`` 的模块文档。
"""

from .budget import (
    DEFAULT_CONTEXT_WINDOW,
    SAFETY_RATIO,
    ContextBudget,
    estimate_messages_tokens,
    estimate_tokens,
)
from .compactor import Compactor
from .manager import ContextManager
from .provider import (
    ContextProvider,
    EnvironmentProvider,
    MemoryFileProvider,
    MemoryRecallProvider,
)

__all__ = [
    "ContextManager",
    "ContextBudget",
    "ContextProvider",
    "Compactor",
    "MemoryFileProvider",
    "MemoryRecallProvider",
    "EnvironmentProvider",
    "estimate_tokens",
    "estimate_messages_tokens",
    "DEFAULT_CONTEXT_WINDOW",
    "SAFETY_RATIO",
]
