# 架构说明

## 调用链

```text
main.py
  └─ CapabilityRouter
      ├─ collection_agent
      ├─ legal_agent
      ├─ data_file / data_analysis / calculator
      ├─ skill_ppt_skill
      └─ collection_rl_policy（可选，默认关闭）
```

主 agent 负责意图识别和调度；催费/法务子 agent 各自拥有独立 ReAct 循环和
专属工具。技能与工具都实现统一的 Capability 协议，由
`CapabilityRouter` 统一注册、匹配和执行。

## 催缴策略 RL 模块

`rl/` 是一个独立、CPU 可运行的预算型上下文 Bandit 模块。它使用欠费明细、
缴费流水和应收信息生成匿名案例，训练离散时间回款模型与 LinUCB 策略。
策略输出会经过安全过滤、预算分配和三场景非劣性检查；未通过检查时自动回退
到 `priority_guide.md` 的现行规则。

该模块默认不注册到主 Agent。设置 `COLLECTION_RL_ENABLED=true` 后，
`main.py` 才会增加只读工具 `collection_rl_policy`；它不会发送短信、拨打电话、
生成函件或执行其他催缴动作。详细命令与数据边界见 `rl/README.md`。

## 上下文与记忆

`core/context/` 按人格、常驻记忆、环境、召回记忆、历史和本轮输入分层组装，
并在超预算时按优先级压缩。`MEMORY.md` 固定在项目根，`memory/` 负责持久化
记忆后端。

## PPT 流水线

```text
data/common/*.xlsx
  → property_metrics.compute_metrics
  → svg_render.render_deck
  → preflight_text
  → svg_quality_checker
  → total_md_split
  → finalize_svg
  → svg_to_pptx
  → outputs/skills/<run>/deck.pptx
```

`ppt_skill` 的 `scripts/` 是合并后的运行时根目录，`attribution_guard.py`
负责校验上游署名和固定路径。
