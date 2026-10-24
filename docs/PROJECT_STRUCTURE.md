# 项目目录结构

## 顶层职责

| 路径 | 职责 |
|---|---|
| `main.py` | 唯一 CLI 入口，装配能力表并启动主 agent |
| `core/` | LLM、能力路由、上下文预算、历史消息等框架核心 |
| `agents/` | ReAct 主 agent、规划器和子 agent |
| `memory/` | 工作/情景/语义/感知记忆与 RAG |
| `tools/` | 可调用工具实现 |
| `rl/` | 催缴策略 RL：回款模型、LinUCB、预算分配、安全回退与评测 |
| `skills/property/` | 业务技能分组 |
| `data/` | 输入数据与参考资料 |
| `outputs/` | 运行产物，整体被 gitignore |
| `scripts/` | 开发验证、运维生成、预览和 RL 训练评测脚本 |
| `tests/` | 自动化测试，其中 `tests/rl/` 覆盖催缴 RL 模块 |
| `archive/deprecated/` | 仅回滚参考的旧代码 |

## 路径约定

- `data/common/`：PPT 主数据源
- `outputs/skills/`：技能运行产物
- `outputs/projects/`：ppt-master 项目
- `outputs/memory/`：记忆数据库
- `outputs/letters/`：函件
- `outputs/rag/`：RAG 临时文件
- `outputs/analysis/`：数据分析结果
- `outputs/rl/`：RL 策略、配置快照和评测报告（不进入 Git）
- `MEMORY.md`：项目级常驻记忆，固定在项目根

## 技能边界

技能目录只放说明、资源和脚本。`ppt_skill/scripts/` 合并了上游
ppt-master 运行时；`skills/loader.py` 会跳过 `scripts/` 和 `assets/`
中的 Markdown，避免把上游文档拼进 system prompt。

`rl/` 不是技能目录，不会参与技能自动发现。它由
`scripts/rl/` 下的独立命令训练和评测，并通过可选的
`collection_rl_policy` 工具接入主 Agent。
