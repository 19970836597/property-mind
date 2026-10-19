---
name: ppt-master-property
id: ppt_skill
execution: script
script: scripts/build_property_deck.py
timeout: 600
metadata:
  version: "6.7.2"
  copyright: "Copyright (c) 2025-2026 Hugo He"
  license: "MIT"
  official_repository: "https://github.com/hugohe3/ppt-master"
  sponsors:
    - "SPONSORS.md"
    - "SPONSORS_CN.md"
description: 读取 data/common 下房产、应收、缴费、欠款、收缴率 5 张表，按固定 10 页 ppt-master 视觉模板生成物业收缴经营分析 PPT，并输出逐页演讲备注。触发词：月度汇报、经营分析、收缴率、欠费分析、物业 PPT。
---

# PPT Master Property Deck

本技能是 PropertyMind 的固定 PPT 生成入口，替代已停用的 `new-ppt-skill`。

## Upstream Runtime

`scripts/` 同时包含首方物业生成脚本和 ppt-master 6.7.2 运行时。运行任何上游脚本前，先确认署名完整性：

```text
python3 "${SKILL_DIR}/scripts/attribution_guard.py"
```

## 输入

- 默认读取 `data/common`：
  - `1、房产客户信息.xlsx`
  - `2、应收信息.xlsx`
  - `3、缴费数据.xlsx`
  - `4、欠款明细报表.xlsx`
  - `5、房产收缴率明细表.xlsx`
- 项目名和期间优先从 `input_data.json` 的 `project` / `period` 读取；缺失时从 request 解析，默认 `物业管理项目`、`2026年1-5月`。

## 输出

- 运行目录：`outputs/skills/<run>/`。
- `deck.pptx`：10 页、16:9、可编辑原生形状。
- `deck.speech.md`：与 10 页一一对应的演讲备注。

## 执行

入口脚本为 `scripts/build_property_deck.py`，由技能加载器以子进程执行。脚本会完成取数、渲染 SVG、最终质检、拆分备注、生成预览并导出 PPTX；任何缺表、缺列或导出失败均以非零退出码返回，不生成空模板。
