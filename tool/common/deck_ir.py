# -*- coding: utf-8 -*-
"""演示文稿中间表示（Deck IR）—— 业务无关的幻灯片契约

为什么需要它
------------
原实现把「物业经营分析」的页面逻辑和「画 PPT」的渲染逻辑混在一个
2000 行脚本里（150+ 处物业专有名词）。结果是：

* 换个业务（销售/财务/生产）就要重写整个脚本
* 技能无法复用，工具无法独立演进

本模块定义一层**中间表示**，把两者彻底分开：

    业务数据(任意表)  ──[技能+领域知识]──▶  Deck IR  ──[deck_build 工具]──▶  .pptx
                                            业务无关

技能负责「讲什么、分几页、按什么顺序」（知识）；
工具负责「怎么画」（机制）。

IR 结构
-------
::

    {
      "meta": {
        "title": "演示文稿标题",
        "subtitle": "副标题",
        "project": "项目/主体名",
        "period": "期间",
        "duration_minutes": 20,
        "theme": "default"
      },
      "slides": [
        {
          "layout": "cover|section|kpi|chart|table|text|cards|appendix",
          "title": "结论型标题（写结论，不写栏目名）",
          "kicker": "阶段标签，如「第 2 段 · 成本」",
          "blocks": [ ...见下... ],
          "so_what": "本页结论句",
          "footnote": "口径说明"
        }
      ]
    }

块类型（blocks）
----------------
::

    {"type": "kpi_grid", "items": [
        {"label": "营业收入", "value": "108.0 万", "delta": "预算 113.5 万",
         "status": "ok|warn|bad|info"}]}

    {"type": "chart", "chart": "bar|column|line|pie|combo",
     "categories": ["产品A", "产品B"],
     "series": [{"name": "实际", "values": [92, 8.5]},
                {"name": "预算", "values": [95, 8]}],
     "unit": "万元", "number_format": "#,##0.0"}

    {"type": "text", "text": "一段说明文字", "style": "body|bullet|quote"}

    {"type": "bullets", "items": ["要点一", "要点二"]}

    {"type": "table", "headers": ["科目", "实际", "预算"],
     "rows": [["人力成本", "48.0", "45.0"]],
     "weights": [2, 1, 1]}

    {"type": "cards", "items": [
        {"title": "行动项 1", "body": "责任：工程部 · 时限：9/15",
         "status": "warn"}]}

设计原则
--------
1. **业务无关**：IR 里不应出现任何行业专有名词，只有 label/value 字符串。
2. **可序列化**：纯 JSON，便于 Agent 生成、落盘、审阅、版本对比。
3. **渲染可降级**：未知 layout/block 不崩溃，标注后跳过。
4. **结论驱动**：每页强制 `title`（结论型）+ `so_what`（一句话结论）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "LAYOUTS",
    "BLOCK_TYPES",
    "STATUSES",
    "empty_deck",
    "make_slide",
    "validate_deck",
    "deck_page_count",
]

#: 支持的页面布局
LAYOUTS = (
    "cover",      # 封面
    "agenda",     # 路线图 / 目录
    "kpi",        # KPI 卡片页（一页纸结论）
    "chart",      # 图表页
    "table",      # 表格页
    "text",       # 文字页
    "cards",      # 卡片页（行动项 / 异常归因）
    "appendix",   # 附录
)

#: 支持的块类型
BLOCK_TYPES = (
    "kpi_grid",
    "chart",
    "text",
    "bullets",
    "table",
    "cards",
)

#: 状态语义色（渲染器映射到具体颜色）
STATUSES = ("ok", "warn", "bad", "info")


def empty_deck(title: str = "", **meta: Any) -> Dict[str, Any]:
    """创建一个空演示文稿 IR。"""
    meta_block: Dict[str, Any] = {"title": title, "theme": "default"}
    meta_block.update({k: v for k, v in meta.items() if v is not None})
    return {"meta": meta_block, "slides": []}


def make_slide(
    layout: str,
    title: str,
    blocks: Optional[List[Dict[str, Any]]] = None,
    kicker: str = "",
    so_what: str = "",
    footnote: str = "",
    **extra: Any,
) -> Dict[str, Any]:
    """构造一页 IR。未知 layout 会被标记但保留，便于排查。"""
    slide: Dict[str, Any] = {
        "layout": layout if layout in LAYOUTS else "text",
        "title": title or "",
        "kicker": kicker or "",
        "blocks": list(blocks or []),
        "so_what": so_what or "",
        "footnote": footnote or "",
    }
    if layout not in LAYOUTS:
        slide["_unknown_layout"] = layout
    slide.update(extra)
    return slide


def validate_deck(deck: Dict[str, Any]) -> List[str]:
    """校验 IR 结构，返回问题列表（空列表表示没问题）。

    渲染器会容忍问题，但技能应当先自查 —— 早点发现比画歪了再改便宜。
    """
    problems: List[str] = []

    if not isinstance(deck, dict):
        return ["IR 不是字典"]
    if "slides" not in deck or not isinstance(deck["slides"], list):
        return ["缺少 slides 数组"]

    meta = deck.get("meta") or {}
    if not meta.get("title"):
        problems.append("meta.title 为空（封面会没有主标题）")

    if not deck["slides"]:
        problems.append("没有任何页面")

    for i, slide in enumerate(deck["slides"], 1):
        where = f"第 {i} 页"
        if not isinstance(slide, dict):
            problems.append(f"{where}: 不是字典")
            continue

        layout = slide.get("layout")
        if layout not in LAYOUTS:
            problems.append(f"{where}: 未知 layout {layout!r}")
        if slide.get("_unknown_layout"):
            problems.append(
                f"{where}: layout {slide['_unknown_layout']!r} 不存在，已按 text 渲染"
            )

        if not slide.get("title"):
            problems.append(f"{where}: 缺少 title（技能要求标题写结论）")
        if not slide.get("so_what") and layout not in ("cover", "agenda", "appendix"):
            problems.append(f"{where}: 缺少 so_what 结论句")

        blocks = slide.get("blocks")
        if not isinstance(blocks, list):
            problems.append(f"{where}: blocks 不是数组")
            continue
        # cover/agenda/appendix 可以没有内容块（由渲染器按布局自己填）
        if not blocks and layout not in ("cover", "agenda", "appendix"):
            problems.append(f"{where}: 没有任何 blocks")

        for j, block in enumerate(blocks, 1):
            if not isinstance(block, dict):
                problems.append(f"{where} 块{j}: 不是字典")
                continue
            btype = block.get("type")
            if btype not in BLOCK_TYPES:
                problems.append(f"{where} 块{j}: 未知类型 {btype!r}")
                continue

            if btype == "kpi_grid":
                if not block.get("items"):
                    problems.append(f"{where} 块{j}: kpi_grid 缺 items")
            elif btype == "chart":
                categories = block.get("categories") or []
                if not categories:
                    problems.append(f"{where} 块{j}: chart 缺 categories")
                series = block.get("series") or []
                if not series:
                    problems.append(f"{where} 块{j}: chart 缺 series")
                elif categories:
                    n = len(categories)
                    for s in series:
                        if len(s.get("values") or []) != n:
                            problems.append(
                                f"{where} 块{j}: series {s.get('name')!r} 点数 "
                                f"{len(s.get('values') or [])} ≠ 分类数 {n}"
                            )
            elif btype == "table":
                headers = block.get("headers") or []
                for row in block.get("rows") or []:
                    if len(row) != len(headers):
                        problems.append(
                            f"{where} 块{j}: 表格行列数 {len(row)} ≠ 表头 {len(headers)}"
                        )
                        break
                if not headers:
                    problems.append(f"{where} 块{j}: table 缺 headers")
            elif btype in ("text", "bullets"):
                # ⚠ 这里必须显式判断类型，不能用 else 兜底：
                # 否则以后新增块类型会静默落进这个分支，
                # 报出「text 内容为空」这种误导性错误。
                if not (block.get("text") or block.get("items")):
                    problems.append(f"{where} 块{j}: {btype} 内容为空")
            elif btype == "cards":
                if not block.get("items"):
                    problems.append(f"{where} 块{j}: cards 缺 items")

    return problems


def deck_page_count(deck: Dict[str, Any]) -> int:
    """页面数（含封面）。"""
    return len(deck.get("slides") or [])
