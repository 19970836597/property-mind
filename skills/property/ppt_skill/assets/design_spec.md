<!-- ppt-master-schema: design-spec/v1 -->
# 鸣X园物业收缴经营分析 - Design Spec

## I. Project Information

| Item | Value |
| --- | --- |
| Project Name | 鸣X园物业收缴经营分析 |
| Canvas Format | PPT 16:9 (1280×720) |
| Page Count | 10 |
| Primary Language | zh-CN |
| Target Audience | 物业项目经理、区域运营负责人及财务/客服协同人员 |
| Communication Intent | 复盘2026年1-5月收缴表现，识别欠费结构与重点对象，推动下一阶段分层清欠 |
| Desired Audience Outcome | 管理层认可当前收缴差距的判断，明确5栋/6栋、49户零缴纳与前10户欠费户的优先顺序，并带着按原因分层的行动清单离场 |
| Core Message / Ask / Action | 综合收缴率86.26%、欠收6,240.80元；49户零缴纳贡献64.3%的欠收；5栋/6栋是最弱楼栋；质量遗留与恶意/困难两类占欠费87.4% |
| Delivery Context | 项目月度经营会现场汇报，投影为主，会后留档 |
| Artifact Afterlife | 作为月度经营会材料留档，并支持会后按楼栋、原因、住户跟进 |
| Reading Mode | balanced |
| Content Strategy | Balanced default: re-architect the supplied five-table corpus into a conclusion-first operating review, keeping every figure sourced and adding no external facts. |
| Design Style | pyramid + swiss-minimal; deep navy, warm off-white, muted gold; rigorous grid, sharp geometry, sparse line icons, no imagery |
| AI Image Acquisition Path | not applicable |
| Generation Mode | continuous |
| Spec Refinement | disabled |
| Speaker Notes | enabled — final Stage-2 proactive policy |
| Custom Animations | disabled — final Stage-2 proactive policy |
| Narration Audio | disabled — final Stage-2 proactive policy |
| Created Date | 2026-10-19 |

## II. Canvas Specification

| Property | Value |
| --- | --- |
| Format | ppt169 |
| Dimensions | 1280 × 720 |
| viewBox | `0 0 1280 720` |
| Margins | 72 px left/right; 56 px top/bottom |
| Content Area | x 72–1208, y 56–664 (1136 × 608) |

## III. Visual Theme

### Theme Style

- **Mode**: pyramid
- **Visual style**: swiss-minimal
- **Theme**: Conclusion-first operating review on a rigorous Swiss grid; warm paper field, deep navy structural planes, muted gold for the single decisive number.
- **Tone**: Calm, exacting, accountable; no ornament.

### Color Scheme

| Role | HEX | Purpose |
| --- | --- | --- |
| Background | #F7F5F0 | Warm paper field for every page |
| Secondary background | #E9E4D8 | Structural bands, table header field, quiet panels |
| Primary | #17324D | Deep navy for dominant planes, titles, and chart bases |
| Accent | #C08A2E | Muted gold for the decisive number, one rule, or one emphasis |
| Secondary accent | #4F7C8A | Slate teal for secondary series and quiet comparison |
| Body text | #1E2A33 | Near-black body copy |
| Secondary text | #5C6670 | Captions, annotations, source lines |
| Divider | #D6CFC2 | Hairline rules and table separators |
| Positive | #2E7D32 | Completed or healthy state |
| Warning | #B7791F | Watch state or emerging risk |
| Negative | #B23A2E | Arrears, shortfall, or urgent risk |

## IV. Typography System

### Font Plan

| Role | Character (Reference) | Primary | English if non-English | Fallback tail |
| --- | --- | --- | --- | --- |
| Title | Heavy geometric display | SimHei | Arial | sans-serif |
| Body | Neutral humanist sans | Microsoft YaHei | Arial | sans-serif |
| Data | Tabular numerals | Consolas | Consolas | monospace |
| Display | Architectural numeral | SimHei | Arial | sans-serif |
| KPI | Tabular display numeral | Consolas | Consolas | monospace |
| Cover | Display | SimHei | Arial | sans-serif |

- **Title stack**: SimHei, Arial, sans-serif
- **Body stack**: Microsoft YaHei, Arial, sans-serif
- **Data stack**: Consolas, monospace
- **Display stack**: SimHei, Arial, sans-serif
- **KPI stack**: Consolas, monospace
- **Cover stack**: SimHei, Arial, sans-serif

### Font Size Hierarchy

| Purpose | Anchor Size (px) |
| --- | ---: |
| Body | 24 |
| Title | 44 |
| Subtitle | 30 |
| Annotation | 18 |
| Cover | 64 |
| Lead | 28 |
| KPI | 96 |
| Data | 32 |
| Display | 72 |
| Footnote | 15 |

## V. Layout Principles

### Deck-wide Direction

- **Hierarchy direction**: One decisive number or assertion first, then a short evidence band; supporting detail sits low and small.
- **Composition tendency**: Asymmetric Swiss grid with one navy structural zone, one warm-paper field, one thin gold rule; charts wrap rather than sit in decorative cards.
- **Cross-page continuity**: A 1px navy rule at the top, a small page index in the lower right, and a recurring data anchor expressed through typography rather than a fixed badge.
- **Spacing posture**: Open; variable by rhythm, with dense evidence pages allowed to tighten the grid.
- **Spacing anchors**: page margin 72 px; block gap 32 px; column gutter 28 px; corner radius 0 px; body leading 36 px.

## VI. Icon Usage Specification

- **Primary bundled library**: tabler-outline
- **Stroke Width**: 2

| Icon Path | Suitable Scenarios |
| --- | --- |
| tabler-outline/building-community | Project base, buildings, community scale |
| tabler-outline/users | Households, occupancy, zero-payment cohort |
| tabler-outline/alert-triangle | Risk, arrears, weak buildings |
| tabler-outline/cash-banknote | Receivables, collection, cash |
| tabler-outline/target | Priority, action, focus |
| tabler-outline/chart-bar | Data pages, comparison |
| tabler-outline/circle-check | Completed payment, confirmation |
| tabler-outline/phone | Follow-up, contact, collection action |

## VII. Visualization Reference List

| Page | Family | Template | Usage |
| --- | --- | --- | --- |
| P03 | chart | horizontal_bar_chart | Compare household count by building |
| P04 | chart | progress_bar_chart | Show current-year comprehensive collection against 100% |
| P05 | chart | horizontal_bar_chart | Compare building-level collection rates and unpaid amounts |
| P06 | chart | donut_chart | Show household collection-status split |
| P07 | chart | pareto_chart | Rank arrears reasons and cumulative share |
| P08 | table | record_table | List the ten largest arrears accounts |
| P09 | chart | stacked_bar_chart | Show 2025 payment-channel mix |

## VIII. Image Resource List

| Filename | Dimensions | Ratio | Purpose | Type | Image pattern | Crop Policy | Acquire Via | Status | Reference | text_policy | page_role |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## IX. Content Outline

### Part 1: 结论与基本盘

#### Slide 01 - 封面

- **Audience move**: 从“又是一份月度报表” → “这次先看结构与优先级”
- **Relationships**: 单一主结论与项目/期间/口径形成主从关系；86.26%与6,240.80形成对比。
- **Composition**: 86.26%以超大字号压在左侧网格，标题与副标题沿严格轴线向右展开，一条金色细线作为唯一强调。
- **Title**: 86.26%：收缴差距不在总量，而在少数户与两栋楼
- **Core message**: 综合收缴率86.26%，欠收6,240.80元，问题集中在49户零缴纳与5栋/6栋。
- **Content**:
  - 鸣X园｜2026年1-5月物业收缴经营分析
  - 口径：综合收缴率 = 当年实收 ÷（当年应收 + 往年转入）
  - 数据源：房产客户信息、应收信息、缴费数据、欠款明细、房产收缴率明细表

#### Slide 02 - 执行摘要

- **Audience move**: 从“知道有这个数” → “接受三个数字就是问题全貌”
- **Relationships**: 收缴率、欠收金额、零缴纳户数三个同级指标并列；按“率 → 金额 → 户数”顺序阅读。
- **Composition**: 三列不等宽网格，每列一个主数字；金色细线只置于中列下方，形成视觉落点。
- **Title**: 三个数字定义当前收缴问题
- **Core message**: 86.26%的综合收缴率背后，是6,240.80元欠收，其中49户零缴纳贡献64.3%。
- **Content**:
  - 86.26%｜综合收缴率（当年实收39,191.90 ÷（当年应收44,501.50 + 往年转入931.20））
  - 6,240.80元｜欠收；仅按当年应收口径收缴率为88.07%
  - 49户｜零缴纳；贡献4,015.50元，占欠收64.3%
  - 结论：先抓49户与5栋/6栋，再做全量提醒
  - 来源：5、房产收缴率明细表；4、欠款明细报表

#### Slide 03 - 项目基本盘

- **Audience move**: 从“只看欠费” → “先理解285户、2.75万m²的管理底盘”
- **Relationships**: 总量285户/27,534.59m²与楼栋户数构成整体-部分关系；3栋/6栋户数最多。
- **Composition**: 左侧纵向大数字，右侧楼栋横向条形；底部一行说明入住与房产类型。
- **Title**: 285户、2.75万m²：成熟社区的收缴基本盘
- **Core message**: 收缴管理面对285户、2.75万m²、280户已入住的成熟社区，3栋和6栋户数最多。
- **Content**:
  - 285套房产，27,534.59m²收费面积；280套已入住
  - 281套为多层住宅，4套虚拟房产；全部已启用
  - 楼栋户数：1栋43、2栋42、3栋56、4栋42、5栋42、6栋56、其他4
  - 3栋/6栋各56户，是收缴工作量的主要承载
  - 来源：1、房产客户信息
- **Visualization**: `households-by-building` — horizontal bar chart of household count by building.
- **Native-ready**: households-by-building=yes

### Part 2: 收缴差距

#### Slide 04 - 当前收缴

- **Audience move**: 从“感觉收缴还行” → “看到连续下行与当前差距”
- **Relationships**: 2021-2025年度收缴率形成连续下行序列；2026年1-5月综合口径与当年应收口径形成并列解释。
- **Composition**: 左侧86.26%与进度条；右侧五年数值带；底部两句口径说明。
- **Title**: 收缴率连续下行，2026年1-5月综合口径86.26%
- **Core message**: 年度收缴率从2021年的96.92%降至2025年的90.96%，2026年1-5月综合口径86.26%，差距需要结构性解释。
- **Content**:
  - 2026年1-5月：当年应收44,501.50，往年转入931.20，当年实收39,191.90，欠收6,240.80
  - 综合收缴率86.26%；仅按当年应收口径88.07%
  - 2021→2025年度收缴率：96.92% → 95.84% → 93.64% → 93.72% → 90.96%
  - 口径：综合口径包含往年转入；年度值来自应收信息表
  - 来源：2、应收信息；5、房产收缴率明细表
- **Visualization**: `collection-progress` — progress bar against a 100% domain.
- **Native-ready**: collection-progress=yes

#### Slide 05 - 楼栋对比

- **Audience move**: 从“全项目一起抓” → “把资源压到5栋/6栋”
- **Relationships**: 六个楼栋的收缴率与欠收金额并列比较；5栋/6栋为最低组，合计欠收3,385.20元。
- **Composition**: 全宽楼栋横向条形，5栋/6栋用金色标注；右侧窄列显示欠收金额。
- **Title**: 4栋接近95.8%，5栋、6栋停在81%区间
- **Core message**: 5栋与6栋合计欠收3,385.20元，占全项目欠收54.2%，是最明确的楼栋优先级。
- **Content**:
  - 1栋83.40%，欠收993.00
  - 2栋90.06%，欠收655.50
  - 3栋88.99%，欠收953.20
  - 4栋95.78%，欠收253.90
  - 5栋81.28%，欠收1,557.90
  - 6栋81.46%，欠收1,827.30
  - 来源：5、房产收缴率明细表
- **Visualization**: `building-collection` — horizontal bar chart sorted by building order with rate labels and unpaid amounts.
- **Native-ready**: building-collection=yes

#### Slide 06 - 户数结构

- **Audience move**: 从“普遍催缴” → “锁定49户零缴纳”
- **Relationships**: 297户当年收缴状态分为242户全额、49户零缴纳、6户部分缴纳；零缴纳户贡献4,015.50元。
- **Composition**: 左侧环形图，右侧三行解释；底部小字说明“户数按当年状态、金额按综合口径”。
- **Title**: 242户当年全额缴纳，49户零缴纳，欠收高度集中
- **Core message**: 49户零缴纳贡献4,015.50元，占全部欠收64.3%；催缴应从广覆盖转向精准到户。
- **Content**:
  - 当年收缴状态：242户全额、49户零缴纳、6户部分缴纳
  - 零缴纳户欠收4,015.50元，占6,240.80元的64.3%
  - 剩余欠收分散在部分缴纳、往年转入与退款/抵扣情形
  - 口径：户数按当年收缴率状态；金额按综合口径
  - 来源：5、房产收缴率明细表
- **Visualization**: `household-status` — donut chart of the three household states.
- **Native-ready**: household-status=yes

### Part 3: 欠费结构

#### Slide 07 - 欠费原因

- **Audience move**: 从“欠费就是态度问题” → “按原因分流，质量遗留与恶意/困难各占一大块”
- **Relationships**: 欠费原因按金额降序排列，前两类合计87.4%；其余原因合计12.6%。
- **Composition**: 帕累托图横贯页面；右侧两条处理路径（质量销项/清欠处置）；底部来源行。
- **Title**: 欠费不是单一原因：质量遗留与恶意/困难合计87.4%
- **Core message**: 房屋质量/地产遗留16,903.43元（46.4%）与恶意拒交/经济困难14,922.65元（41.0%）构成欠费主体，必须分流处理。
- **Content**:
  - 房屋质量问题/地产遗留设备问题：16,903.43元，46.4%
  - 无理由、恶意拒交、经济困难：14,922.65元，41.0%
  - 纠纷1,574.40、空置要求打折1,521.10、服务不满735.70、失联513.00、设施问题165.50、未填66.50
  - 合计36,402.28元，47户
  - 来源：4、欠款明细报表
- **Visualization**: `arrears-reasons` — pareto chart of reason amounts and cumulative share.
- **Native-ready**: arrears-reasons=yes

#### Slide 08 - 重点欠费户

- **Audience move**: 从“欠费很多户” → “先处理前10户，覆盖56.9%的欠费”
- **Relationships**: 10个欠费账户按金额降序排列；每行包含房产、业主、金额、原因、区间五个字段。
- **Composition**: 全宽记录表；表头下方一条金色细线；金额列右对齐并使用等宽数字。
- **Title**: 前10户欠费2.07万元，占全部欠费56.9%
- **Core message**: 前10户合计欠费20,705.69元，占全部欠费56.9%；其中6栋5户、质量遗留7户。
- **Content**:
  - 6-2-602｜徐X萍｜3,175.63｜无理由、恶意拒交、经济困难｜2017-01-01~2025-05-31
  - 1-3-501｜崔X伟｜3,082.36｜房屋质量问题/地产遗留设备问题｜2017-01-01~2025-05-31
  - 3-2-601｜王X｜2,809.87｜房屋质量问题/地产遗留设备问题｜2017-01-01~2025-05-31
  - 4-2-202｜张X文｜1,984.50｜无理由、恶意拒交、经济困难｜2018-01-01~2023-08-31
  - 6-3-602｜何X｜1,922.60｜房屋质量问题/地产遗留设备问题｜2019-01-01~2025-05-31
  - 6-4-602｜党X荣｜1,892.10｜房屋质量问题/地产遗留设备问题｜2021-01-01~2025-05-31
  - 6-4-601｜马X华｜1,881.50｜房屋质量问题/地产遗留设备问题｜2021-01-01~2025-05-31
  - 6-2-102｜周X涛｜1,384.23｜无理由、恶意拒交、经济困难｜2017-01-01~2020-12-31
  - 3-3-602｜张X科｜1,348.90｜房屋质量问题/地产遗留设备问题｜2022-01-01~2025-05-31
  - 5-3-602｜崔X军｜1,224.00｜房屋质量问题/地产遗留设备问题｜2020-01-01~2022-12-31
  - 来源：4、欠款明细报表
- **Visualization**: `top-debtors` — record table of the ten largest arrears accounts.
- **Native-ready**: top-debtors=yes

#### Slide 09 - 收款渠道

- **Audience move**: 从“收款只是财务动作” → “渠道结构决定对账与催缴效率”
- **Relationships**: 2025年收款总额48,957.50元按渠道构成整体-部分关系；微信为绝对主渠道，现金为第二渠道。
- **Composition**: 单条100%堆叠横条横贯页面；下方四个渠道标签；右侧三条运营含义。
- **Title**: 2025年86.4%的收款来自微信，现金仍占11.9%
- **Core message**: 微信是主渠道，现金仍是第二大渠道；收款运营应优先保障线上对账，同时把现金日结纳入清欠管理。
- **Content**:
  - 2025年收款48,957.50元：微信42,291.50（86.4%）、现金5,838.50（11.9%）、支付宝499.00（1.0%）、刷卡328.50（0.7%）
  - 2024年收款91,641.92元，渠道结构同样以微信为主（69,978.90），仅作运营参照
  - 现金收款当日对账；退款/预收抵扣单独核对
  - 来源：3、缴费数据
- **Visualization**: `payment-channels` — 100% stacked bar of the four channels.
- **Native-ready**: payment-channels=yes

### Part 4: 行动

#### Slide 10 - 分层清欠行动

- **Audience move**: 从“知道问题” → “带着按楼栋/原因/户分层的行动清单离场”
- **Relationships**: 四条行动线并列，按优先级形成顺序：5/6栋 → 49户/前10户 → 质量遗留 → 收款对账。
- **Composition**: 四条纵向行动带，编号01-04；一条金色横线贯穿；结尾用一句话收束“先抓结构，再抓总量”。
- **Title**: 下一步：按楼栋、按原因、按户分层清欠
- **Core message**: 先锁定5栋/6栋与49户零缴纳，再按质量遗留、恶意/困难、失联分流；收款对账同步收紧。
- **Content**:
  - 01 楼栋：5栋/6栋逐户核对欠费、退款、预收抵扣；建立周度清欠看板（项目经理/客服）
  - 02 重点户：前10户与49户零缴纳清单化；质量遗留先销项，恶意/困难分类处置，失联户补联系方式（项目经理/法务/客服）
  - 03 质量遗留：与地产/工程建立问题销项清单，修复与催缴同步推进，避免“带病催收”（工程/地产接口人）
  - 04 收款运营：微信主渠道保持，现金当日对账；退款/预收抵扣月度复核（财务/收费员）
  - 建议节奏：立即启动重点户核对；本月完成5/6栋清单；本季度复盘质量遗留销项

## X. Speaker Notes Requirements

- **Generation**: enabled
- **Filename**: match each SVG filename under `notes/`
- **Content**: Ground each page's narration in the final SVG; lead with the page conclusion, then one supporting fact and the audience move; cite the source table name where a figure appears.
- **Total duration**: 8–10 minutes
- **Notes style**: formal, conclusion-first, concise
- **Presentation purpose**: 复盘2026年1-5月收缴表现，识别欠费结构与重点对象，推动下一阶段分层清欠
