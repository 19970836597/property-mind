# 文件记忆召回实验

该实验使用独立集合 `file_memory_recall_eval_v1`，不会清空或改写生产记忆集合。
默认 embedding 为本地
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`（384 维）。

## 运行

```powershell
$env:HF_HUB_OFFLINE="1"
$env:TRANSFORMERS_OFFLINE="1"
.venv\Scripts\python.exe scripts\dev\experiment_rag_recall.py --tiers 0,1,2,3
```

脚本会生成 500 道题、850 份文件语料和四档混淆集合，然后将结果写入
`outputs/memory_recall/run-<时间>/`。评测不使用盲测标签调阈值；阈值只在
350 道开发题上选择，随后用于 150 道盲测题。

## 指标口径

- 证据召回：问题的全部必要事实都出现在最终证据中。
- 证据精准率：正确返回证据数 / 全部返回证据数。
- 逐题完整召回：所有必要事实均被当前问题召回的题目占比。
- 无答案误答率：本应拒答却返回证据的题目占比。
- 单次问题最多返回 5 条证据，低分结果不填充。

## 当前结果

2026-10-27 的冻结运行结果：

| 混淆层 | 证据召回 | 证据精准率 | 逐题完整召回 | 无答案误答率 |
|---|---:|---:|---:|---:|
| D0 | 100.00% | 100.00% | 100.00% | 0.00% |
| D1 | 100.00% | 100.00% | 100.00% | 0.00% |
| D2 | 100.00% | 100.00% | 100.00% | 0.00% |
| D3 | 100.00% | 100.00% | 100.00% | 0.00% |

结果文件：
`outputs/memory_recall/run-20261027-144256/report.md`。

这是固定合成基准上的经验结果，不代表对所有真实文件永久保证 100%。
接入正式 `RAGTool` 前，还需要用真实脱敏文件补充外部验证。

## 正式 RAG 接入

默认检索模式为 `hybrid`，旧向量检索仍可通过环境变量回退：

```env
RAG_RETRIEVER_MODE=hybrid
RAG_HYBRID_COLLECTION=rag_knowledge_base_local_minilm_384_v1
RAG_HYBRID_MIN_SCORE=0.30
```

混合模式会使用与当前 embedding 模型维度一致的版本化集合。正式
`rag.search` 与 `rag.ask` 的参数不变；查询内的项目编号、日期和百分比会作为
硬约束，缺少这些标识符的高分片段不会进入最终上下文。

切换到 `RAG_RETRIEVER_MODE=legacy` 可回退旧检索。修改 embedding 模型后必须
使用新的集合名并重新入库，不能把不同模型的向量混入同一个 Qdrant collection。

本地模型已经下载后可增加以下配置，避免启动时反复探测 Hugging Face：

```env
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

## 个人记忆集合版本

情景记忆和语义记忆也会按当前 embedding 模型及维度选择独立集合，例如：

```text
episodic_vectors_paraphrase_multilingual_minilm_l12_v2_384_v1
semantic_vectors_paraphrase_multilingual_minilm_l12_v2_384_v1
```

旧的 `memory_vectors` 是 1024 维历史集合，不再用于当前 384 维模型。
SQLite 中已有的情景记忆会在向量索引缺失时自动重新 embedding；也可以通过
`EPISODIC_VECTOR_COLLECTION` 或 `SEMANTIC_VECTOR_COLLECTION` 显式覆盖集合名。
