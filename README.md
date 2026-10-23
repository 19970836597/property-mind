# PropertyMind — 物业项目智能助手

面向物业管理场景的多 agent 助手：ReAct 主调度、催费/法务子 agent、
可插拔技能、分层上下文管理和自动记忆。默认 PPT 能力读取
`data/common/` 下的五张业务表，生成固定 10 页经营分析演示文稿。

## 快速开始

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env

python main.py
```

最简配置只需要一个可用的 LLM API Key（例如 `DEEPSEEK_API_KEY`）。
Qdrant、Neo4j、embedding 都是可选项，不可用时对应能力会自动降级。

## 目录结构

```text
main.py                     CLI 入口：装配能力表并启动主 agent
core/                       框架核心：LLM、能力路由、上下文、历史
agents/                     ReAct agent、规划器、子 agent
memory/                     记忆存储与 RAG
tools/                      工具实现（原 tool/）

skills/
  property/                 业务技能分组
    collection_assistant/   催费助手
    legal_assistant/        法务助手
    optimalstop37/          37% 决策技能
    ppt_skill/              固定物业 PPT 技能
      SKILL.md
      assets/               图标与上游说明
      templates/            上游模板资源
      scripts/              首方生成脚本 + ppt-master 6.7.2 运行时

data/
  common/                   五张物业业务 Excel
  legal/                    法务语料
  inbox/                    临时待处理文件
  references/               参考资料 PDF

docs/                       架构、目录说明和业务文档
scripts/
  dev/                      验证与实验脚本
  ops/                      PPT 生成、法务知识库入库
  tools/                    PPTX 预览工具

outputs/                    运行产物（gitignore）
  skills/                   技能运行目录
  projects/                 ppt-master 项目目录
  memory/                   记忆数据库
  letters/                  催缴函/律师函
  rag/                      RAG 临时文件
  analysis/                 数据分析结果

archive/deprecated/         仅用于回滚的旧代码，不参与自动发现
```

## 数据与产物

- 业务数据放在 `data/common/`，PPT 技能会自动读取。
- `outputs/` 全部是运行产物，不进入 Git；需要长期保存时请自行归档。
- `MEMORY.md` 是项目级常驻记忆，保持在项目根目录。
- 旧版 `make_deck_from_excel.py` 已归档到 `archive/deprecated/`，不再属于主流程。

如果旧版 `main.py` 仍在运行并占用 `memory_data/memory.db`，先退出该进程，
再删除这个旧目录；迁移时已把旧库副本保存为 `outputs/memory/memory.db.legacy`。

## 常用命令

```powershell
# 交互式助手
python main.py

# 一次性任务
python main.py --task "项目经理月度汇报"

# 直接生成固定 10 页 PPT
.venv\Scripts\python.exe scripts\ops\generate_property_deck.py --project "鸣X园"

# 上下文与记忆自检
.venv\Scripts\python.exe scripts\dev\verify_context.py
.venv\Scripts\python.exe scripts\dev\verify_memory.py

# 训练与评测物业催缴 RL 策略（只生成建议，不执行催缴）
.venv\Scripts\python.exe scripts\rl\train_collection_policy.py
.venv\Scripts\python.exe scripts\rl\evaluate_collection_policy.py
.venv\Scripts\python.exe scripts\rl\show_recommendations.py

# 预览生成的 PPTX
.venv\Scripts\python.exe scripts\tools\preview_deck.py outputs\skills\property_deck\<run>\deck.pptx

# 法务知识库入库
.venv\Scripts\python.exe scripts\ops\ingest_legal_kb.py
```

## PPT 技能说明

`skills/property/ppt_skill` 是自包含技能：

- 首方文件：`scripts/build_property_deck.py`、`scripts/property_metrics.py`、
  `scripts/svg_render.py`
- 上游运行时：其余 `scripts/` 文件来自 ppt-master 6.7.2，已整体并入
- 上游许可和署名文件：技能根目录的 `LICENSE`、`SPONSORS.md`、
  `SPONSORS_CN.md`，由 `attribution_guard.py` 校验

升级上游运行时前先运行：

```powershell
python skills\property\ppt_skill\scripts\attribution_guard.py
```

## 兼容性说明

本次目录整理有意调整了内部路径：

- Python 包 `tool.*` 改为 `tools.*`
- 技能从 `skills/buttin/` 移到 `skills/property/`
- 产物从 `skill_outputs/`、`projects/`、`memory_data/` 移到 `outputs/`

`python main.py`、`--task` 参数和 `skill_ppt_skill` 能力名保持不变。

## License

MIT
