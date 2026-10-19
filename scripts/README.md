# 脚本目录

```text
scripts/
├─ dev/     verify_context.py / verify_memory.py / experiment_recall.py
├─ ops/     generate_property_deck.py / ingest_legal_kb.py
└─ tools/   preview_deck.py
```

所有脚本都从项目根解析路径，建议在项目根目录运行：

```powershell
.venv\Scripts\python.exe scripts\dev\verify_context.py
.venv\Scripts\python.exe scripts\dev\verify_memory.py
.venv\Scripts\python.exe scripts\ops\generate_property_deck.py --project "鸣X园"
.venv\Scripts\python.exe scripts\ops\ingest_legal_kb.py
.venv\Scripts\python.exe scripts\tools\preview_deck.py outputs\skills\...\deck.pptx
```

`archive/deprecated/make_deck_from_excel.py` 是旧版契约生成器，只用于回滚，
不属于当前主流程。
