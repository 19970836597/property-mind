# 技能目录

技能发现规则：`skills/` 下任意深度、包含 `SKILL.md` 的目录都会注册为一个
技能。分组目录本身（例如 `skills/property/`）不是技能。

```text
skills/
└─ property/
   ├─ collection_assistant/   催费助手（LLM 技能）
   ├─ legal_assistant/        法务助手（LLM 技能）
   └─ ppt_skill/
      ├─ SKILL.md
      ├─ LICENSE
      ├─ SPONSORS.md
      ├─ SPONSORS_CN.md
      ├─ assets/
      ├─ templates/
      └─ scripts/
```

`ppt_skill/scripts/` 同时包含首方物业生成脚本和上游 ppt-master 6.7.2
运行时。首方脚本是 `build_property_deck.py`、`property_metrics.py`、
`svg_render.py`；升级上游时整体替换其余文件，不要改动这三个脚本。

`archive/deprecated/` 中的旧 `new-ppt-skill` 不参与技能发现。
