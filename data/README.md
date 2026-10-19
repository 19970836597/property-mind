# 数据目录

```text
data/
├─ common/       五张物业业务表（PPT 主数据源）
├─ legal/        法务知识库语料
├─ inbox/        临时/待处理文件
├─ references/   参考资料 PDF
└─ README.md
```

## 命名建议

文件名本身就是检索线索，建议带上业务名和期间：

```text
✅ 物业经营分析_阳光花园_2026Q3.xlsx
✅ 欠款明细报表_2026-09.xlsx
❌ 新建工作簿 (3).xlsx
```

默认搜索范围是 `data/common` 和 `data/inbox`；也可以用环境变量
`SKILL_COMMON_DIRS` 指向其他目录（多个目录用 `;` 分隔）。
