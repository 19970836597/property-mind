# -*- coding: utf-8 -*-
"""演示文稿内容生成工具（能力名 ``deck_content``）

把「数据集 + 叙事知识（profile）」变成 ``deck_ir`` 定义的 IR。

    data_file → 找到文件
    data_analysis → 读懂数据
    deck_content → **数据 + 领域知识 → 演示文稿 IR**   ← 本模块
    deck_build   → IR → .pptx
    pptx_check   → 结构校验

设计要点
--------
1. **知识在 profile 里，不在代码里。** 页数、顺序、每页讲什么都由
   ``deck_profiles`` 提供；本模块只做「把数据摆到对应位置」。
2. **两段式输入**：先 ``plan`` 给出页面规划供人/Agent 审阅，
   再 ``build`` 产出 IR。避免一上来就生成一堆错页。
3. **可覆盖**：技能（Agent）可以用 ``overrides`` 覆盖任意页的
   title / so_what / 块内容 —— 领域判断由模型给，摆放由工具给。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from ..tools import Tool, ToolParameter
from .commons import find_files, resolve_common_dirs
from .deck_ir import make_slide, validate_deck
from .deck_narrative import extract_facts, pick_narrative
from .deck_profiles import get_profile, list_profiles, plan_pages
from .table_derive import compute_derived
from .table_io import HAS_PANDAS, pd, read_table
from .table_join import apply_joins

# ---------------------------------------------------------------------
# 段落内容生成已抽到 deck_sections（一个段落一个函数）
# 这里保留同名私有别名作为**向后兼容出口**。
# ---------------------------------------------------------------------
from .deck_sections import (
    aggregate as _aggregate,
    build_appendix,
    build_cards,
    build_cover,
    build_grouped_chart,
    build_kpi,
    build_metrics_chart,
    build_summary,
    build_table,
    build_text,
    resolve_metric as _resolve_metric,
    resolve_section_data,
    single_metric_card as _single_metric_card,
)

# ---------------------------------------------------------------------
# 依赖抽出的通用能力
#   · 表格探查   → table_profile（列识别：日期/金额/分组/伪数值）
#   · 数值格式化 → deck_format
# 这里保留同名私有别名，作为**向后兼容出口**；实现的归属在各自模块。
# ---------------------------------------------------------------------
from .table_profile import (
    detect_date_column as _detect_date_column,
    is_date_like as _is_date_like,
    looks_like_period as _looks_like_period,
    numeric_columns as _numeric_columns,
    pick_group_column as _pick_group_column,
    pick_metrics as _pick_metrics,
    pick_value_column as _pick_value_column,
    primary_value_column as _primary_value_column,
    text_columns as _text_columns,
    to_datetime as _to_datetime,
)
from .deck_format import (
    fmt_money as _fmt_money,
    fmt_wan as _fmt_wan,
    short_label as _short_label,
    wan as _wan,
)

__all__ = ["DeckContentTool"]


# 每页最多展示多少组
MAX_GROUPS = 8


def _resolve_target(params: Dict[str, Any]):
    """定位数据文件：file 优先，其次 file_name 去常用目录找。"""
    explicit = params.get("file") or params.get("path")
    if explicit:
        path = os.path.abspath(str(explicit))
        return (path, f"指定文件: {path}") if os.path.isfile(path) else (None, f"文件不存在: {path}")

    name = str(params.get("file_name") or "").strip()
    if not name:
        return None, "未提供 file，也未提供 file_name"
    hits = find_files(query=name, dirs=resolve_common_dirs(), limit=1)
    if not hits:
        return None, f"常用目录里找不到 {name!r}"
    return hits[0].path, f"匹配到 {hits[0].name}"




class DeckContentTool(Tool):
    """演示文稿内容组织工具（``deck_content``）。"""

    def __init__(self, expandable: bool = False):
        super().__init__(
            name="deck_content",
            description=(
                "把数据集按指定 narrative spec（页数规则 + 结论句模板 + 配色）组织成演示文稿 IR："
                "决定分几页、每页讲什么、放什么图表。"
                "action=plan 只给页面规划供审阅；action=build 产出可渲染的 IR。"
                "IR 随后交给 deck_build 渲染成 .pptx。"
            ),
            expandable=expandable,
        )

    def get_params(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="action", type="string",
                          description="plan=只出页面规划; build=产出 IR; profiles=列出可用 profile",
                          required=False, default="plan"),
            ToolParameter(name="file", type="string",
                          description="数据文件完整路径", required=False, default=None),
            ToolParameter(name="file_name", type="string",
                          description="文件名/关键词；会自动去常用目录匹配",
                          required=False, default=None),
            ToolParameter(name="profile", type="string",
                          description="叙事模板：general / property / sales",
                          required=False, default="general"),
            ToolParameter(name="minutes", type="integer",
                          description="汇报时长（分钟），决定页数：10→7页, 20→11页, 40→16页",
                          required=False, default=20),
            ToolParameter(name="project", type="string",
                          description="项目 / 主体名称", required=False, default=None),
            ToolParameter(name="period", type="string",
                          description="报告期间，如 2026-09；留空则用数据里最近一期",
                          required=False, default=None),
            ToolParameter(name="group_column", type="string",
                          description="分组维度列（如产品、科目、区域）", required=False, default=None),
            ToolParameter(name="value_column", type="string",
                          description="金额/主指标列", required=False, default=None),
            ToolParameter(name="date_column", type="string",
                          description="日期列", required=False, default=None),
            ToolParameter(name="overrides", type="object",
                          description=(
                              "按页码覆盖内容，形如 "
                              '{"2": {"title": "...", "so_what": "..."}} —— '
                              "领域判断（结论句）由 Agent 提供，摆放由工具负责"
                          ),
                          required=False, default=None),
            ToolParameter(name="out", type="string",
                          description="IR 落盘路径（可选，便于审阅/复用）",
                          required=False, default=None),
            ToolParameter(name="spec", type="object",
                          description=(
                              "技能的叙事知识（deck_spec）：含 page_rules / sections"
                              " / narrative 结论句模板。给了它就用它，否则用内置 profile。"
                              "narrative 让结论句从「念数字」变成「给判断」。"
                          ),
                          required=False, default=None),
            ToolParameter(name="spec_path", type="string",
                          description="deck_spec.json 路径（与 spec 二选一）",
                          required=False, default=None),
            ToolParameter(name="extra_facts", type="object",
                          description=(
                              "额外事实，供 narrative 模板占位符使用，"
                              "如 {\"rate\": 92.5, \"labor_rate\": \"41.5%\"}"
                          ),
                          required=False, default=None),
        ]

    # ---------------- spec 加载 ----------------

    @staticmethod
    def _load_spec(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """加载技能的叙事知识。支持 dict / JSON 字符串 / 文件路径。"""
        raw = params.get("spec")
        path = params.get("spec_path")

        if isinstance(raw, str):
            text = raw.strip()
            if text.startswith("{"):
                try:
                    raw = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"spec 不是合法 JSON: {exc}") from exc
            elif os.path.isfile(text):
                path = text
                raw = None

        if raw is None and path:
            if not os.path.isfile(str(path)):
                raise ValueError(f"spec_path 不存在: {path}")
            with open(str(path), "r", encoding="utf-8-sig") as handle:
                raw = json.load(handle)

        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("spec 必须是对象")
        return raw

    @staticmethod
    def _plan_from_spec(spec: Dict[str, Any], minutes: int) -> List[Dict[str, Any]]:
        """按 spec 的 page_rules 生成页面规划。"""
        sections = {s["key"]: s for s in (spec.get("sections") or [])
                    if isinstance(s, dict) and s.get("key")}
        rules = [r for r in (spec.get("page_rules") or []) if isinstance(r, dict)]
        if not rules:
            rules = [{"max_minutes": 20, "sections": list(sections)}]

        chosen = None
        for rule in sorted(rules, key=lambda r: int(r.get("max_minutes", 10 ** 6))):
            if minutes <= int(rule.get("max_minutes", 10 ** 6)):
                chosen = rule
                break
        if chosen is None:
            chosen = sorted(rules, key=lambda r: int(r.get("max_minutes", 0)))[-1]

        plan: List[Dict[str, Any]] = []
        for i, key in enumerate(chosen.get("sections") or [], start=1):
            spec_section = sections.get(key)
            if not spec_section:
                continue
            plan.append({**spec_section, "page": i})
        return plan

    def run(self, params: Dict[str, Any]) -> str:
        action = str(params.get("action") or "plan").lower()

        if action in ("profiles", "list"):
            lines = ["可用叙事 profile（内置）:"]
            for p in list_profiles():
                lines.append(f"  - {p['key']}: {p['name']}")
            lines.append("")
            lines.append("也可以传 spec（技能的 deck_spec.json）覆盖内置 profile。")
            return "\n".join(lines)

        profile_key = str(params.get("profile") or "general")
        try:
            minutes = int(params.get("minutes") or 20)
        except (TypeError, ValueError):
            minutes = 20

        # 技能 spec 优先；没有才用内置 profile
        try:
            spec = self._load_spec(params)
        except ValueError as exc:
            return f"spec 加载失败: {exc}"

        if spec:
            profile = get_profile(spec.get("profile") or profile_key)
            spec_name = spec.get("name") or profile.name
            plan = self._plan_from_spec(spec, minutes)
        else:
            profile = get_profile(profile_key)
            spec_name = profile.name
            plan = plan_pages(profile_key, minutes)

        # plan 动作不需要读数据
        if action == "plan":
            suffix = "（来自技能 spec）" if spec else "（内置 profile）"
            lines = [
                f"[deck_content] 页面规划 — {spec_name}{suffix}"
                f"（{minutes} 分钟 → {len(plan)} 页）",
                "",
            ]
            for item in plan:
                chart = f" · {item['chart']}" if item.get("chart") else ""
                lines.append(
                    f"  {item['page']:02d}. {item['label']}  "
                    f"[{item['layout']}{chart}]"
                )
                if item.get("purpose"):
                    lines.append(f"      目的: {item['purpose']}")
                if item.get("narrative"):
                    lines.append(f"      结论句模板: {len(item['narrative'])} 条规则")
            if spec and spec.get("check"):
                lines.append("")
                lines.append(f"校验要求: {spec['check'].get('must_contain')}")
            if not spec and profile.notes:
                lines.append("")
                lines.append("叙事约束:")
                for k, v in profile.notes.items():
                    lines.append(f"  - {k}: {v}")
            return "\n".join(lines)

        # ---- build：需要数据 ----
        path, note = _resolve_target(params)
        if path is None:
            return f"{note}\n（可用 action=plan 先看页面规划；或用 file 指定路径）"

        table, engine = read_table(path)
        if table is None:
            return f"无法读取数据: {engine}"
        if not (HAS_PANDAS and isinstance(table, pd.DataFrame)):
            return "内容生成需要 pandas（当前环境不可用）"

        # ---- 跨表关联：把维表（面积/房产状态等）拼到主表上 ----
        # 顺序很重要：**先关联再算派生** —— 否则派生指标引用的
        # 维度列（如"实测收费面积"）还不存在。
        join_notes: List[str] = []
        if spec and spec.get("join"):
            try:
                table, join_notes = apply_joins(
                    table, spec, resolve_common_dirs())
            except Exception as exc:
                join_notes = [f"关联整体失败: {type(exc).__name__}: {exc}"]

        # ---- 派生指标：从已有列算出业务指标（知识在 spec 的 derived 段）----
        derived_summary: Dict[str, Any] = {}
        derived_notes: List[str] = []
        if spec and spec.get("derived"):
            try:
                table, derived_notes, derived_summary = compute_derived(
                    table, spec["derived"])
            except Exception as exc:
                derived_notes = [f"派生指标整体失败: {type(exc).__name__}: {exc}"]

        deck = self._build_deck(table, spec, profile, spec_name, plan, params,
                                note, engine, derived_summary, join_notes)

        problems = validate_deck(deck)
        out = params.get("out")
        if out:
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            with open(out, "w", encoding="utf-8") as handle:
                json.dump(deck, handle, ensure_ascii=False, indent=2)

        lines = [
            f"[deck_content] 已生成 IR — {spec_name}，{len(deck['slides'])} 页",
            f"  数据: {note}（引擎 {engine}）",
        ]
        if spec:
            narrated = sum(1 for s in deck["slides"] if s.get("_narrative_applied"))
            lines.append(f"  叙事知识: 来自技能 spec，{narrated} 页结论句由模板生成")
        if derived_summary:
            lines.append(f"  派生指标（{len(derived_summary)} 项，从数据算出而非外部传入）:")
            for item in derived_notes:
                lines.append(f"    - {item}")
        elif derived_notes:
            lines.append("  ⚠ 派生指标未生效:")
            for item in derived_notes:
                lines.append(f"    - {item}")
        if join_notes:
            lines.append("  跨表关联:")
            for item in join_notes:
                lines.append(f"    - {item}")
        if out:
            lines.append(f"  IR 已落盘: {out}")
        if problems:
            lines.append(f"  ⚠ IR 校验 {len(problems)} 条提示:")
            for p in problems[:8]:
                lines.append(f"    - {p}")
        lines.append("")
        lines.append("下一步: 交给 deck_build 渲染（theme 通常也来自同一份 spec）。")
        return "\n".join(lines)

    # ---------------- 构建 IR ----------------

    def _build_deck(self, df, spec, profile, spec_name, plan, params,
                    note, engine, derived_summary=None,
                    join_notes=None) -> Dict[str, Any]:
        df = df.copy()
        project = str(params.get("project") or "").strip()
        title = f"{project} 经营分析" if project else "经营分析报告"

        metrics = _pick_metrics(df)
        nums = _numeric_columns(df)
        texts = _text_columns(df)

        value_column = params.get("value_column") or _pick_value_column(nums, df)
        group_column = params.get("group_column") or _pick_group_column(texts)
        # 时间列：显式传参优先，否则自动识别（用于推断期间）
        date_column = params.get("date_column") or _detect_date_column(df)

        # 期间
        period = str(params.get("period") or "").strip()
        if not period and date_column and date_column in df.columns:
            parsed = _to_datetime(df[date_column])
            months = parsed.dt.strftime("%Y-%m").dropna()
            counts = months.value_counts().sort_index()
            if len(counts) == 0:
                period = ""
            elif len(counts) == 1:
                period = str(counts.index[0])
            else:
                # 不能无脑取「最新月」：实测 2025-12 只有 9 行，
                # 拿它当汇报期间会严重误导（其他月动辄上千行）。
                latest = str(counts.index[-1])
                median = float(counts.median())
                if counts.iloc[-1] < max(1.0, median * 0.5):
                    period = (f"{counts.index[0]} ~ {counts.index[-1]}"
                              f"（全周期 {len(counts)} 个月）")
                else:
                    period = latest

        # 分组聚合（供未声明 data 的段落复用）
        grouped: List[Dict[str, Any]] = _aggregate(df, group_column, value_column)

        overrides = params.get("overrides") or {}
        if isinstance(overrides, str):
            try:
                overrides = json.loads(overrides)
            except json.JSONDecodeError:
                overrides = {}

        extra_facts = params.get("extra_facts") or {}
        if isinstance(extra_facts, str):
            try:
                extra_facts = json.loads(extra_facts)
            except json.JSONDecodeError:
                extra_facts = {}

        # 派生指标并入事实表：**优先于**外部传入的 extra_facts。
        # 从数据算出来的值比人工传的可信 —— 人工传错了没人会发现。
        merged_facts: Dict[str, Any] = dict(extra_facts)
        merged_facts.update(derived_summary or {})

        # 主题：spec 里带的优先（样式知识也在技能里）
        theme = None
        if spec and isinstance(spec.get("theme"), dict):
            theme = spec["theme"]

        deck = {
            "meta": {
                "title": title,
                "subtitle": " · ".join(x for x in [
                    spec_name,
                    f"数据源 {os.path.basename(note.split('（')[0].strip())}" if note else "",
                ] if x),
                "project": project,
                "period": period,
                "profile": (spec or {}).get("profile") or profile.key,
                "footnote": f"口径：{engine}；共 {metrics['rows']} 条记录",
            },
            "slides": [],
        }
        if theme:
            deck["meta"]["theme_spec"] = theme

        for item in plan:
            page = item["page"]
            key = item["key"]
            override = overrides.get(str(page)) or overrides.get(key) or {}
            slide = self._build_slide(
                item, override, df=df, metrics=metrics, grouped=grouped,
                value_column=value_column, group_column=group_column,
                period=period, profile=profile,
                extra_facts=merged_facts, derived=derived_summary or {},
                use_narrative=bool(spec),
            )
            deck["slides"].append(slide)

        # ---- 内容健康度（必须在 slides 填完之后算）----
        # 为什么需要：一次实测里，脚本报 success=True、结构校验 PASS，
        # 但产物 11 页里 7 页写着"数据缺失，待补" —— 因为自动取数
        # 匹到了**报表格式**的表（多行表头），列名全是 Unnamed: N。
        # 结构校验只看"每页有没有结论句"，看不出那句话是"待补数据"。
        #
        # 所以把内容层面的信号显式记下来，由编排器决定要不要报失败。
        # 注意：早期版本把这段写在 slides 循环之前 → pages 恒为 0，
        # 门禁因此失效（0/0 永远不触发）。
        degraded = sum(
            1 for s in deck["slides"]
            if "数据缺失" in str(s.get("title") or "")
        )
        derived_expected = bool((spec or {}).get("derived"))
        deck["meta"]["content_health"] = {
            "pages": len(deck["slides"]),
            "degraded_pages": degraded,
            "derived_expected": derived_expected,
            "derived_ok": bool(derived_summary) if derived_expected else None,
            "derived_count": len(derived_summary or {}),
            "join_attempted": bool((spec or {}).get("join")),
            "join_notes": list(join_notes or []),
        }
        return deck

    def _build_slide(self, item, override, **ctx) -> Dict[str, Any]:
        """调度器：解包上下文 → 解析数据 → 生成 blocks → 收尾。

        具体每个段落长什么样，都在 ``deck_sections`` 里。
        这里只负责"按 key / layout 找对那个函数"。
        """
        key = item["key"]
        layout = item["layout"]
        label = item["label"]
        df = ctx["df"]
        metrics = ctx["metrics"]
        derived = ctx.get("derived") or {}
        grouped = ctx["grouped"]
        value_column = ctx["value_column"]
        group_column = ctx["group_column"]
        period = ctx["period"]

        # 段落可以声明自己的数据来源（不同段落用不同的列）
        value_column, group_column, grouped, data_missing = (
            resolve_section_data(item, df, value_column, group_column, grouped))

        blocks: List[Dict[str, Any]] = []
        default_title = label
        default_so_what = ""

        total = sum(g["value"] for g in grouped) if grouped else 0.0

        if data_missing:
            # 数据缺失时**诚实降级**，绝不拿别的列冒充
            blocks.append({
                "type": "text",
                "style": "lead",
                "text": f"缺少所需的{'、'.join(data_missing)}，本页无法生成。"
                        f"请在数据里补充对应列，或在本段 data 里改指正确的列名。",
            })
            blocks.append({
                "type": "bullets",
                "items": [f"数据现有列：{', '.join(str(c) for c in df.columns[:10])}"],
            })
            return self._finish_slide(
                item, override, layout, label,
                f"{label}（数据缺失，待补）",
                f"未找到{'、'.join(data_missing)}，本页结论待补数据后生成。",
                blocks, narrative_applied=False)

        # 指标对比图（从派生标量画，而不是按列分组）
        chart_blocks, chart_title, metrics_chart_used = build_metrics_chart(
            item.get("metrics_chart"), derived, metrics, df)
        blocks.extend(chart_blocks)
        if chart_title:
            default_title = chart_title

        # ---- 按 key / layout 分派 ----
        if key == "cover":
            cover_blocks, default_title = build_cover(
                metrics, grouped, total, ctx["profile"].name)
            blocks.extend(cover_blocks)

        elif key == "summary":
            summary_blocks, default_so_what = build_summary(
                metrics, grouped, total)
            blocks.extend(summary_blocks)

        elif layout == "chart":
            chart2, so_what2 = build_grouped_chart(
                grouped, value_column, total, metrics_chart_used)
            blocks.extend(chart2)
            if so_what2:
                default_so_what = so_what2

        elif layout == "cards":
            card_blocks, default_so_what = build_cards(grouped, total)
            blocks.extend(card_blocks)

        elif layout == "kpi":
            blocks.extend(build_kpi(metrics))

        elif layout == "table":
            blocks.extend(build_table(grouped, value_column, total))

        elif layout == "agenda":
            pass          # 由渲染器按后续页面自动生成

        elif layout == "appendix":
            blocks.extend(build_appendix(df))

        elif layout == "text":
            blocks.extend(build_text(metrics, period))

        return self._finish_slide(
            item, override, layout, label, default_title, default_so_what,
            blocks,
            # 这里必须是 False：``_narrative_applied`` 的含义是
            # **"结论句由 spec 的叙事模板生成"**，不是"有文案"。
            # 何时置 True 由 _finish_slide 在模板真正命中时决定 ——
            # 传 bool(default_so_what) 会把内置兜底文案也算成"套了模板"。
            narrative_applied=False,
            metrics=metrics, grouped=grouped,
            extra_facts=ctx.get("extra_facts"),
            use_narrative=ctx.get("use_narrative"))

    def _finish_slide(self, item, override, layout, label, default_title,
                      default_so_what, blocks, narrative_applied,
                      metrics=None, grouped=None, extra_facts=None,
                      use_narrative=False) -> Dict[str, Any]:
        """统一收尾：套叙事模板 → 应用 overrides → 组装 IR 页。"""
        extra: Dict[str, Any] = {}

        # ---- 叙事模板：把「事实」变成「判断」----
        # 这是「念数字」和「给判断」的分界线：
        # 没有 narrative 时，结论句只是把聚合结果复述一遍；
        # 有 narrative 时，按数据条件套用行业知识写好的句式。
        #
        # 注意：只要段落带**任何一种** narrative 规则（so_what / title / action），
        # 就需要算事实。早先把整块锁在 `if item.get("narrative")` 里，
        # 导致只配了 action_narrative 的段落永远拿不到「主因与动作」。
        has_any_narrative = any(
            item.get(k) for k in ("narrative", "title_narrative", "action_narrative")
        )
        if use_narrative and has_any_narrative:
            total = sum(g["value"] for g in (grouped or [])) if grouped else 0.0
            facts = extract_facts(
                total=total,
                count=(metrics or {}).get("rows", 0),
                groups=grouped,
                extra=extra_facts or {},
            )
            narrated = pick_narrative(item.get("narrative") or [], facts, key="so_what")
            if narrated:
                default_so_what = narrated
                narrative_applied = True
            narrated_title = pick_narrative(
                item.get("title_narrative") or [], facts, key="title")
            if narrated_title:
                default_title = narrated_title
            # 「主因与动作」也由知识驱动（话术第 3 段）
            narrated_action = pick_narrative(
                item.get("action_narrative") or [], facts, key="action")
            if narrated_action:
                extra["_action"] = narrated_action

        slide = make_slide(
            layout=layout,
            title=str(override.get("title") or default_title),
            kicker=str(override.get("kicker") or label),
            blocks=override.get("blocks") or blocks,
            so_what=str(override.get("so_what") or default_so_what),
            footnote=str(override.get("footnote") or ""),
        )
        if narrative_applied and not override.get("so_what"):
            slide["_narrative_applied"] = True
        if extra:
            slide.update(extra)
        return slide


