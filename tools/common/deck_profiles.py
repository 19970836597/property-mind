# -*- coding: utf-8 -*-
"""演示文稿叙事 profile —— 把「领域知识」从代码里抽出来

背景
----
原实现把行业知识**写死在渲染脚本里**：

    page_count = 11
    sections = ["封面", "路线图", "一页纸结论", "收入", "成本", ...]
    SECTION_KEYWORDS = {"收入": ["收入", "公共收益"], ...}

于是换个业务就要改代码。本模块把这些知识变成**声明式数据**：

    知识（本节）     讲什么、几页、什么顺序、用哪些维度
    工具（deck_*）   怎么画

技能文档（SKILL.md）负责说明「为什么这样讲」，profile 负责机读的落地参数。

内置 profile
------------
* ``general``   通用经营分析（收入→成本→质量→效能→异常→行动）
* ``property``  物业项目月度经营分析（六段式，20 分钟 11 页）
* ``sales``     销售/业绩分析（目标→达成→结构→趋势→异常→行动）

自定义
------
业务方可以自己写 profile 字典并注册，不需要改任何代码::

    from tools.common.deck_profiles import register_profile
    register_profile("my_biz", {
        "name": "我的业务",
        "sections": [...],
        "page_rules": [...],
    })
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "SectionSpec",
    "PageRule",
    "DeckProfile",
    "PROFILES",
    "register_profile",
    "get_profile",
    "list_profiles",
    "plan_pages",
]


class SectionSpec:
    """一个叙事段落。"""

    __slots__ = ("key", "label", "order", "layout", "chart", "keywords", "purpose")

    def __init__(
        self,
        key: str,
        label: str,
        order: int,
        layout: str = "text",
        chart: str = "",
        keywords: Optional[List[str]] = None,
        purpose: str = "",
    ):
        self.key = key
        self.label = label
        self.order = order
        self.layout = layout
        self.chart = chart
        self.keywords = list(keywords or [])
        self.purpose = purpose

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "order": self.order,
            "layout": self.layout, "chart": self.chart,
            "keywords": self.keywords, "purpose": self.purpose,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"SectionSpec({self.key!r}, order={self.order}, layout={self.layout!r})"


class PageRule:
    """时长 → 页数与段落组合 的规则。"""

    __slots__ = ("max_minutes", "sections")

    def __init__(self, max_minutes: int, sections: List[str]):
        self.max_minutes = max_minutes
        self.sections = list(sections)

    def to_dict(self) -> Dict[str, Any]:
        return {"max_minutes": self.max_minutes,
                "sections": self.sections,
                "page_count": len(self.sections)}


class DeckProfile:
    """一套演示文稿的叙事知识。"""

    def __init__(
        self,
        key: str,
        name: str,
        sections: List[SectionSpec],
        page_rules: List[PageRule],
        notes: Optional[Dict[str, str]] = None,
    ):
        self.key = key
        self.name = name
        # 按 order 排序，保证叙事顺序不会因为声明顺序而乱
        self.sections = sorted(sections, key=lambda s: s.order)
        self.page_rules = sorted(page_rules, key=lambda r: r.max_minutes)
        self.notes = dict(notes or {})

    # ---------------- 查询 ----------------

    def section(self, key: str) -> Optional[SectionSpec]:
        for s in self.sections:
            if s.key == key:
                return s
        return None

    def rules_for(self, minutes: int) -> PageRule:
        """按汇报时长选页数规则。"""
        for rule in self.page_rules:
            if minutes <= rule.max_minutes:
                return rule
        return self.page_rules[-1]

    def plan(self, minutes: int = 20) -> List[SectionSpec]:
        """按时长给出本次的段落序列（展开为逐页）。"""
        rule = self.rules_for(minutes)
        planned: List[SectionSpec] = []
        for key in rule.sections:
            spec = self.section(key)
            if spec is not None:
                planned.append(spec)
        return planned

    def keywords_map(self) -> Dict[str, List[str]]:
        return {s.key: list(s.keywords) for s in self.sections if s.keywords}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "sections": [s.to_dict() for s in self.sections],
            "page_rules": [r.to_dict() for r in self.page_rules],
            "notes": self.notes,
        }

    def describe(self, minutes: int = 20) -> str:
        rule = self.rules_for(minutes)
        lines = [
            f"叙事 profile: {self.name}（{self.key}）",
            f"  {minutes} 分钟 → {len(rule.sections)} 页",
            "  逐页:",
        ]
        for i, spec in enumerate(self.plan(minutes), 1):
            chart = f" 图表={spec.chart}" if spec.chart else ""
            lines.append(f"    {i:02d}. {spec.label}  [布局={spec.layout}{chart}]")
            if spec.purpose:
                lines.append(f"        {spec.purpose}")
        return "\n".join(lines)

    def __repr__(self) -> str:  # pragma: no cover
        return f"DeckProfile({self.key!r}, {len(self.sections)} sections)"


# ===================================================================
# 内置 profile
# ===================================================================

_GENERAL_SECTIONS = [
    SectionSpec("cover", "封面", 10, "cover",
                purpose="项目 / 期间 / 汇报人"),
    SectionSpec("agenda", "路线图", 20, "agenda",
                purpose="让听众知道要讲几段"),
    SectionSpec("summary", "一页纸结论", 30, "kpi", chart="KPI 卡片",
                keywords=["结论"], purpose="30 秒决策：好坏 + 最大问题 + 第一动作"),
    SectionSpec("primary", "主指标", 40, "chart", chart="双系列柱状图",
                keywords=["收入", "达成"], purpose="核心目标完成情况 vs 参照系"),
    SectionSpec("secondary", "成本/投入", 50, "chart", chart="条形图（偏差排序）",
                keywords=["成本"], purpose="按科目偏差从大到小排，标出 TOP"),
    SectionSpec("quality", "质量指标", 60, "chart", chart="分层柱状图",
                keywords=["质量", "率"], purpose="把结果指标挂到过程指标上"),
    SectionSpec("efficiency", "效能指标", 70, "chart", chart="柱+折线组合",
                keywords=["效能", "人均"], purpose="效率与投入产出"),
    SectionSpec("anomaly", "异常归因", 80, "cards", chart="归因卡片",
                keywords=["异常", "归因"], purpose="三层穿透：偏差→事件→责任与时限"),
    SectionSpec("actions", "总结行动", 90, "cards", chart="行动卡片",
                keywords=["行动"], purpose="每项带责任人 + 时限 + 量化验收"),
    SectionSpec("appendix", "附录", 100, "appendix",
                purpose="明细台账，正文只放结论图"),
]

_GENERAL_RULES = [
    PageRule(10, ["cover", "summary", "primary", "secondary", "anomaly",
                  "actions", "appendix"]),
    PageRule(20, ["cover", "agenda", "summary", "primary", "secondary",
                  "quality", "efficiency", "anomaly", "actions",
                  "appendix", "appendix"]),
    PageRule(40, ["cover", "agenda", "summary", "primary", "secondary",
                  "secondary", "quality", "efficiency", "efficiency",
                  "anomaly", "anomaly", "actions", "appendix", "appendix",
                  "appendix", "appendix"]),
]

# ---- 物业项目月度经营分析（六段式，与原 SKILL.md 一致）----
_PROPERTY_SECTIONS = [
    SectionSpec("cover", "封面", 10, "cover",
                purpose="项目 / 期间 / 数据口径"),
    SectionSpec("agenda", "路线图", 20, "agenda",
                purpose="六段式主线"),
    SectionSpec("summary", "一页纸结论", 30, "kpi", chart="KPI 卡片（6 个）",
                keywords=["一页纸结论"],
                purpose="三句话讲完：经营结果 / 最大异常 / 下月第一动作"),
    SectionSpec("revenue", "收入", 40, "chart", chart="双系列柱状图",
                keywords=["收入", "公共收益"],
                purpose="物业费 / 公共收益 / 多种经营 / 其他；公共收益单独列示"),
    SectionSpec("cost", "成本", 50, "chart", chart="条形图（红黄绿语义色）",
                keywords=["成本", "人力"],
                purpose="人力→能耗→外包→维保→物料→管理费，按偏差排序"),
    SectionSpec("collection", "收缴率", 60, "chart", chart="分层柱状图 + 指标卡片",
                keywords=["收缴率", "账龄"],
                purpose="当期 / 累计 / 陈欠 / 账龄分层；收缴率是服务质量的果"),
    SectionSpec("efficiency", "人均效能", 70, "chart", chart="柱+折线组合",
                keywords=["人均", "效能"],
                purpose="人均面积 / 人均产值 / 人工成本率 / 人均工单"),
    SectionSpec("anomaly", "异常科目归因", 80, "cards", chart="条形图 + 归因表格",
                keywords=["异常", "归因"],
                purpose="三层穿透，必须落到责任部门与时限"),
    SectionSpec("actions", "总结行动", 90, "cards", chart="行动卡片",
                keywords=["行动项"],
                purpose="3~5 项，每项责任人 + 时限 + 量化验收"),
    SectionSpec("appendix", "附录", 100, "appendix",
                purpose="数据溯源与明细台账"),
]

_PROPERTY_RULES = [
    PageRule(10, ["cover", "summary", "revenue", "cost", "anomaly",
                  "actions", "appendix"]),
    PageRule(20, ["cover", "agenda", "summary", "revenue", "cost",
                  "collection", "efficiency", "anomaly", "actions",
                  "appendix", "appendix"]),
    PageRule(40, ["cover", "agenda", "summary", "revenue", "revenue",
                  "cost", "cost", "collection", "efficiency", "efficiency",
                  "anomaly", "anomaly", "actions", "appendix", "appendix",
                  "appendix"]),
]

# ---- 销售 / 业绩分析 ----
_SALES_SECTIONS = [
    SectionSpec("cover", "封面", 10, "cover"),
    SectionSpec("agenda", "路线图", 20, "agenda"),
    SectionSpec("summary", "业绩总览", 30, "kpi", chart="KPI 卡片",
                keywords=["业绩", "目标"], purpose="目标达成 + 最大缺口"),
    SectionSpec("target", "目标达成", 40, "chart", chart="目标 vs 实际",
                keywords=["目标", "达成"], purpose="完成率与时间进度对比"),
    SectionSpec("structure", "业绩结构", 50, "chart", chart="双系列柱状图",
                keywords=["产品", "区域"], purpose="按产品/区域拆解贡献"),
    SectionSpec("trend", "趋势", 60, "chart", chart="折线图",
                keywords=["趋势", "环比"], purpose="多月走势，识别拐点"),
    SectionSpec("anomaly", "异常/缺口", 80, "cards", chart="归因卡片",
                keywords=["异常", "缺口"], purpose="缺口定位到人、客户或产品"),
    SectionSpec("actions", "下期行动", 90, "cards",
                keywords=["行动"], purpose="责任人 + 时限 + 量化验收"),
    SectionSpec("appendix", "附录", 100, "appendix"),
]

_SALES_RULES = [
    PageRule(10, ["cover", "summary", "target", "structure", "anomaly",
                  "actions", "appendix"]),
    PageRule(20, ["cover", "agenda", "summary", "target", "structure",
                  "trend", "anomaly", "actions", "appendix", "appendix",
                  "appendix"]),
    PageRule(40, ["cover", "agenda", "summary", "target", "structure",
                  "structure", "trend", "trend", "anomaly", "anomaly",
                  "actions", "actions", "appendix", "appendix", "appendix",
                  "appendix"]),
]


PROFILES: Dict[str, DeckProfile] = {
    "general": DeckProfile(
        "general", "通用经营分析",
        _GENERAL_SECTIONS, _GENERAL_RULES,
        notes={"结构": "目标→投入→质量→效能→异常→行动",
               "纪律": "标题写结论；每页一句 so what；异常必须闭环"},
    ),
    "property": DeckProfile(
        "property", "物业项目月度经营分析",
        _PROPERTY_SECTIONS, _PROPERTY_RULES,
        notes={"结构": "收入→成本→收缴率→人均效能→异常归因→总结行动",
               "顺序理由": "收缴率是服务质量的果，放收入成本后才讲得通因果",
               "红线": "公共收益属全体业主共有，必须单独列示，不计入经营利润"},
    ),
    "sales": DeckProfile(
        "sales", "销售业绩分析",
        _SALES_SECTIONS, _SALES_RULES,
        notes={"结构": "目标→达成→结构→趋势→缺口→行动",
               "纪律": "缺口必须落到人/客户/产品，不接受『市场原因』兜底"},
    ),
}


def register_profile(key: str, spec: Dict[str, Any]) -> DeckProfile:
    """注册自定义 profile（业务方无需改代码）。"""
    sections = [
        SectionSpec(
            key=s["key"],
            label=s.get("label", s["key"]),
            order=int(s.get("order", i * 10)),
            layout=s.get("layout", "text"),
            chart=s.get("chart", ""),
            keywords=s.get("keywords"),
            purpose=s.get("purpose", ""),
        )
        for i, s in enumerate(spec.get("sections") or [], start=1)
    ]
    rules = [
        PageRule(int(r.get("max_minutes", 20)), r.get("sections") or [])
        for r in (spec.get("page_rules") or [])
    ]
    if not rules:
        rules = [PageRule(20, [s.key for s in sections])]
    profile = DeckProfile(key, spec.get("name", key), sections, rules,
                          spec.get("notes"))
    PROFILES[key] = profile
    return profile


def get_profile(key: Optional[str]) -> DeckProfile:
    """取 profile；未知 key 退回 general 并保留原 key 便于排查。"""
    if key and key in PROFILES:
        return PROFILES[key]
    return PROFILES["general"]


def list_profiles() -> List[Dict[str, str]]:
    return [{"key": p.key, "name": p.name} for p in PROFILES.values()]


def plan_pages(profile: str = "general", minutes: int = 20) -> List[Dict[str, Any]]:
    """给出「几页 + 每页讲什么」的页面规划（机读）。"""
    p = get_profile(profile)
    return [
        {**spec.to_dict(), "page": i}
        for i, spec in enumerate(p.plan(minutes), start=1)
    ]
