# PropertyMind — 物业项目智能助手

面向物业管理场景的多 agent 助手：ReAct 主调度、催费/法务子 agent、
可插拔技能、分层上下文管理、自动记忆和预算型催缴策略 RL。默认 PPT 能力读取
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
main.py                     CLI 入口：装配能力表并启动主 agent / 群聊
core/                       框架核心：LLM、能力路由、上下文、历史
agents/                     ReAct agent、规划器、子 agent、群聊
memory/                     记忆存储与 RAG
tools/                      工具实现（原 tool/）
rl/                         催缴策略 RL：回款模型、LinUCB、预算分配与评测

skills/
  property/                 业务技能分组
    collection_assistant/   催费助手
    legal_assistant/        法务助手
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
  rl/                       催缴策略训练、评测与建议预览

tests/rl/                   RL 模块自动化测试

outputs/                    运行产物（gitignore）
  skills/                   技能运行目录
  projects/                 ppt-master 项目目录
  memory/                   记忆数据库
  letters/                  催缴函/律师函
  rag/                      RAG 临时文件
  analysis/                 数据分析结果
  rl/                       策略文件、配置快照、评测报告

archive/deprecated/         仅用于回滚的旧代码，不参与自动发现
```

## 数据与产物

- 业务数据放在 `data/common/`，PPT 技能会自动读取。
- `outputs/` 全部是运行产物，不进入 Git；需要长期保存时请自行归档。
- `outputs/rl/collection_policy_v1.json` 是可重新生成的共享策略文件，不含客户明细。
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

# 群聊自动化测试（@解析 / 自荐打分 / 双层记忆 / 建群多群，纯逻辑不调 LLM）
.venv\Scripts\python.exe -m pytest tests/test_groupchat_mention.py tests/test_groupchat_selector.py tests/test_groupchat_memory.py tests/test_groupchat_flow.py tests/test_groupchat_registry.py -v

# 建群全流程离线验证（建群→切换→@跨群→解散）
.venv\Scripts\python.exe scripts\dev\verify_groupchat.py

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

## 对话模式

交互式 CLI 启动时先选择对话模式，直接回车默认快速模式：

- **快速模式**：普通消息由主 Agent 调度子 Agent；`@` 按普通文本处理。
- **协作模式**：先选择默认群、现有群或新建群，所有普通消息进入当前群聊。

模式绑定本次 CLI 运行，创建后不可切换，也不提供 `/mode` 命令；需要另一种模式时
请退出并重新启动。`python main.py --task "..."` 保持一次性任务行为，固定使用快速模式。

## 协作模式与群聊

选择协作模式后，可在聊天中用 `@` 点名成员，或使用 `/group` 前缀管理群聊：

```text
@催费助手 分析下欠款账龄
@法务助手 物业费诉讼时效是多久
@all 欠费催收有哪些法律红线要注意
/group 催缴函怎么写才合规
```

规则（详见 `agents/groupchat/`）：

- 有 `@` 时仅被 @ 者回应（支持一次 @ 多个、`@all`）；写错名字会提示并自动分配。
- 无 `@` 时各成员按自身 router（工具/技能的词面+tag）自荐打分，没人举手则主 agent 兜底直答。
- manager 收成员原始输出、按用户表达偏好改写整理后，以一条 manager 消息统一发布（群里只有整理版，可溯源 raw 只在 verbose 日志）。
- 记忆三层：成员私有库（领域状态，他人不可见）+ 群共享库（结论双写，所有人可见，`episodic` 落盘重启仍在）+ manager 习惯库（表达偏好，落盘 `MEMORY.md` 带 `[表达偏好:群名]` 前缀）。

### 建群与多群

```text
/group create 催收群 @催费 @法务   建群并自动切换（manager恒在，不用指定）
/group list                        列出全部群（名称/成员/活跃标记/轮数）
/group switch 催收群                切换活跃群
/group show                        看活跃群详情
/group add @法务                   给活跃群加人
/group remove @催费                从活跃群移除人（至少留1人）
/group disband 催收群               解散群（默认群不可解散）
```

- 同时只活跃一个群，`@` 解析与自荐只看活跃群；`@` 到非本群成员会提示并群内自荐。
- 每群独立共享历史与共享记忆（`session_id=group-<群名>`）；切换群时自动摘挂共享召回 provider，不串记忆。
- 已知局限：成员**私有**召回库暂跨群共享（`MemoryRecallProvider.remember` 无 session 参数），后续版本再隔离。

## 催缴策略 RL

`rl/` 使用欠费明细、缴费流水和应收信息训练预算型上下文 Bandit，为每户生成
催缴动作建议。计算只依赖项目现有的 pandas、numpy 和 scikit-learn，不需要
GPU，也不会训练或替换 LLM。

默认流程：

```text
欠费数据 → 匿名状态特征 → LinUCB 动作评分 → 预算分配
        → 三场景非劣性检查 → 人工审核建议
```

争议户和未知账龄户强制核账，账龄不足 180 天不能进入法务评审。学习策略若在
保守、基准或乐观任一场景中不如现行规则，会自动回退到 `priority_guide.md`
策略。当前历史数据没有真实催缴动作标签，因此动作效果是可更新假设，不能将
模拟结果视为真实回款提升。

默认 `COLLECTION_RL_ENABLED=false`，主 Agent 不会显示 RL 建议。需要预览时：

```powershell
# 训练并生成共享策略
.venv\Scripts\python.exe scripts\rl\train_collection_policy.py

# 与现行规则做三场景回放
.venv\Scripts\python.exe scripts\rl\evaluate_collection_policy.py --episodes 1000

# 只在测试控制台预览建议
.venv\Scripts\python.exe scripts\rl\show_recommendations.py

# 经人工验收后临时启用 Agent 工具
$env:COLLECTION_RL_ENABLED="true"
```

多人使用时共享策略 JSON 即可，每个人用本地业务数据推理；不得共享业主姓名、
电话、身份证或完整房产地址。启用后的工具也只返回人工审核建议，不执行催缴。
详细接口和配置见 `rl/README.md`。

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
