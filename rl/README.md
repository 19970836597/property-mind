# 物业催缴策略 RL 模块

这是一个 CPU 可运行的预算型上下文 Bandit，用于在真实催缴动作之前生成
户级建议。它不训练 LLM，也不会发送短信、拨打电话或生成函件。

## 模块结构

| 文件 | 职责 |
|---|---|
| `loader.py` | 读取欠费、缴费和应收表，生成匿名案例与历史回款曲线 |
| `response.py` | 离散时间回款生存模型，模拟 90 天累计回款 |
| `features.py` | 将账龄、金额、原因和缴费历史转成固定特征向量 |
| `safety.py` | 争议核账、账龄升级、法务门槛和联系冷却 |
| `trainer.py` | 三场景模拟和 LinUCB 参数训练 |
| `policy.py` | 动作评分、三场景非劣性检查和规则回退 |
| `allocation.py` | 在努力点预算内选择每户动作 |
| `heuristic.py` | 现行 `priority_guide.md` 规则基线 |
| `evaluator.py` | RL 与规则策略的配对回放、置信区间和验收 |
| `storage.py` | 策略 JSON 与匿名 Outcome JSONL 持久化 |
| `tool.py` | 可选的只读 Agent 能力 `collection_rl_policy` |

## 运行

```powershell
# 训练并生成共享策略文件
.venv\Scripts\python.exe scripts\rl\train_collection_policy.py

# 三套回款假设下与现行规则对比
.venv\Scripts\python.exe scripts\rl\evaluate_collection_policy.py `
  --policy outputs\rl\collection_policy_v1.json

# 仅预览建议
.venv\Scripts\python.exe scripts\rl\show_recommendations.py
```

训练产物位于 `outputs/rl/<run_id>/`，同时把最新策略写入
`outputs/rl/collection_policy_v1.json`。这些目录不会进入 Git。

## 推荐逻辑

```text
本地业务表
  → 匿名案例与特征
  → 安全动作过滤
  → LinUCB 预测回款并对预算做动态规划分配
  → 与现行规则比较三种回款场景
  → 任一场景不占优则回退规则
  → 输出人工审核建议
```

动作包括不处理、温馨提醒、人工核账、电话回访、正式催缴和法务评审。
默认努力点为 `0/1/2/3/5/10`，每周期预算 20 点。复核点按动作分别为
7、14 或 30 天，但模拟统一累计 90 天回款。

## 功能开关

默认 `COLLECTION_RL_ENABLED=false`，主 Agent 不注册 RL 工具。
人工确认策略可用后再设置：

```powershell
$env:COLLECTION_RL_ENABLED="true"
$env:COLLECTION_RL_POLICY="D:\path\to\collection_policy_v1.json"
```

工具只返回人工审核建议，不执行催缴动作。

## 测试

```powershell
.venv\Scripts\python.exe -m pytest tests\rl -q
.venv\Scripts\python.exe -m compileall -q rl scripts\rl tests\rl
```

测试覆盖回款模拟、部分回款、特征稳定性、安全矩阵、联系冷却、预算上限、
策略保存加载、训练可复现性、隐私字段排除和默认功能开关。

## 数据边界

策略文件只保存模型参数、特征版本、聚合回款曲线和配置哈希，不保存业主姓名、
电话、身份证或完整房产地址。使用者可以共享同一个策略文件，但应使用本地
欠费数据推理。

历史缴费表用于估计基础回款风险。当前没有真实催缴动作日志，因此动作效果仍是
可配置、可更新的业务假设。任何真实上线都应先做影子运行或受控试点。
