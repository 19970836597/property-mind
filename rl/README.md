# 物业催缴策略 RL 模块

这是一个 CPU 可运行的预算型上下文 Bandit，用于在真实催缴动作之前生成
户级建议。它不训练 LLM，也不会发送短信、拨打电话或生成函件。

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

## 功能开关

默认 `COLLECTION_RL_ENABLED=false`，主 Agent 不注册 RL 工具。
人工确认策略可用后再设置：

```powershell
$env:COLLECTION_RL_ENABLED="true"
$env:COLLECTION_RL_POLICY="D:\path\to\collection_policy_v1.json"
```

工具只返回人工审核建议，不执行催缴动作。

## 数据边界

策略文件只保存模型参数、特征版本、聚合回款曲线和配置哈希，不保存业主姓名、
电话、身份证或完整房产地址。使用者可以共享同一个策略文件，但应使用本地
欠费数据推理。

历史缴费表用于估计基础回款风险。当前没有真实催缴动作日志，因此动作效果仍是
可配置、可更新的业务假设。任何真实上线都应先做影子运行或受控试点。
