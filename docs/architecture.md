# 架构说明

## 调用链

```text
main.py
  └─ CapabilityRouter
      ├─ collection_agent
      ├─ legal_agent
      ├─ data_file / data_analysis / calculator
      ├─ skill_ppt_skill
      └─ skill_optimalstop37_skill
```

主 agent 负责意图识别和调度；催费/法务子 agent 各自拥有独立 ReAct 循环和
专属工具。技能与工具都实现统一的 Capability 协议，由
`CapabilityRouter` 统一注册、匹配和执行。

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
