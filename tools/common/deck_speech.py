# -*- coding: utf-8 -*-
"""逐页话术生成（Speech）—— 从 IR 反推汇报逐字稿

为什么要它
----------
旧脚本三大交付物之一是「汇报话术」（PPT 用来"看"，话术用来"说"）。
切到通用链路后这个能力不能丢。

好在 IR 里已经有足够信息：每页的 ``so_what``（结论）、``blocks``（数据）、
``kicker``（阶段定位）。缺的只有"主因与动作"—— 那是领域判断，
由 spec 的 ``action_narrative`` 模板提供（同一个叙事引擎）。

三段式（按 references/03-汇报话术.md）
------------------------------------
    结论先行  10 秒   这页的结论是什么          ← slide.so_what
    数据支撑  30 秒   关键数字 + 口径           ← 从 blocks 自动提取
    主因与动作 30 秒  为什么 + 谁的 + 什么时候   ← spec.action_narrative
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "build_speech",
    "format_speech_markdown",
    "write_speech_files",
    "extract_data_support",
]


def extract_data_support(slide: Dict[str, Any], max_items: int = 6) -> str:
    """从一页的 blocks 里提炼「数据支撑」句子。

    纯机制：按块类型把关键数字拼成一句可念的话，不含任何行业判断。
    """
    parts: List[str] = []

    for block in slide.get("blocks") or []:
        btype = block.get("type")

        if btype == "kpi_grid":
            items = block.get("items") or []
            pairs = [
                f"{it.get('label')} {it.get('value')}"
                + (f"（{it.get('delta')}）" if it.get("delta") else "")
                for it in items[:max_items] if it.get("label")
            ]
            if pairs:
                parts.append("；".join(pairs))

        elif btype == "chart":
            categories = list(block.get("categories") or [])
            series = list(block.get("series") or [])
            unit = block.get("unit") or ""
            for s in series[:2]:
                values = list(s.get("values") or [])
                pairs = [
                    f"{cat} {val:,.2f}{unit}" if isinstance(val, (int, float))
                    else f"{cat} {val}{unit}"
                    for cat, val in zip(categories, values)
                ][:max_items]
                if pairs:
                    parts.append(f"{s.get('name') or '数值'}：" + "、".join(pairs))

        elif btype == "table":
            headers = [str(h) for h in (block.get("headers") or [])]
            rows = list(block.get("rows") or [])
            if headers and rows:
                summary = []
                for row in rows[:3]:
                    cells = [str(c) for c in row]
                    summary.append(" / ".join(
                        f"{h}={c}" for h, c in zip(headers, cells) if c
                    ))
                if summary:
                    parts.append("；".join(summary))

        elif btype in ("text", "bullets"):
            text = block.get("text") or ""
            items = block.get("items") or []
            if text:
                parts.append(str(text))
            elif items:
                parts.append("；".join(str(i) for i in items[:max_items]))

    if not parts:
        return ""
    merged = "；".join(parts)
    return merged if len(merged) <= 400 else merged[:397] + "…"


def build_speech(
    deck: Dict[str, Any],
    spec: Optional[Dict[str, Any]] = None,
    actions_by_section: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """从 IR 生成逐页话术。

    Args:
        deck: 演示文稿 IR。
        spec: 技能知识（可选），用于按段落匹配 action_narrative。
        actions_by_section: 已渲染好的「主因与动作」文本，键是段落标签。

    Returns:
        ``{"项目","期间","页数","so_whats","scripts"}``
    """
    meta = deck.get("meta") or {}
    actions_by_section = actions_by_section or {}

    # spec 里按 label / key 建索引，便于用 kicker 反查
    section_by_label: Dict[str, Dict[str, Any]] = {}
    for section in (spec or {}).get("sections") or []:
        if not isinstance(section, dict):
            continue
        for alias in (section.get("label"), section.get("key")):
            if alias:
                section_by_label[str(alias)] = section

    so_whats: Dict[str, str] = {}
    scripts: Dict[str, Dict[str, str]] = {}

    for slide in deck.get("slides") or []:
        layout = str(slide.get("layout") or "")
        if layout in ("cover", "agenda", "appendix"):
            continue

        label = str(slide.get("kicker") or slide.get("title") or "").strip()
        if not label:
            continue
        so_what = str(slide.get("so_what") or "").strip()
        so_whats[label] = so_what

        section = section_by_label.get(label) or {}
        # 「主因与动作」优先级：
        #   ① IR 上渲染好的 _action（由 spec 的 action_narrative 生成）
        #   ② 调用方显式传入的 actions_by_section
        #   ③ spec 里的静态 action 文本
        #   ④ 通用兜底
        action = (
            str(slide.get("_action") or "").strip()
            or actions_by_section.get(label)
            or str(section.get("action") or "").strip()
            or "按本页结论明确责任人、时限与量化验收标准，下期复盘逐项对账。"
        )
        if "{" in action:
            # 未替换的占位符说明缺事实，退化为通用动作，避免把花括号念出来
            action = "按本页结论明确责任人、时限与量化验收标准，下期复盘逐项对账。"

        scripts[label] = {
            "结论先行": so_what or "（本页结论待补）",
            "数据支撑": extract_data_support(slide) or "（本页数据见图表）",
            "主因与动作": action,
        }

    return {
        "项目": meta.get("project", ""),
        "期间": meta.get("period", ""),
        "页数": len(deck.get("slides") or []),
        "so_whats": so_whats,
        "scripts": scripts,
    }


def format_speech_markdown(payload: Dict[str, Any]) -> str:
    """把话术渲染成可直接贴进聊天正文的 Markdown。"""
    lines = [
        f"# {payload.get('项目', '')} · {payload.get('期间', '')} 汇报话术",
        "",
        "> 每页三段式：结论先行（10 秒）→ 数据支撑（30 秒）→ 主因与动作（30 秒）",
        "",
    ]
    for label, parts in (payload.get("scripts") or {}).items():
        lines.append(f"## {label}")
        lines.append("")
        if parts.get("结论先行"):
            lines.append(f"**本页结论**：{parts['结论先行']}")
            lines.append("")
        lines.append("| 段落 | 话术 |")
        lines.append("|---|---|")
        for key in ("结论先行", "数据支撑", "主因与动作"):
            if key in parts:
                lines.append(f"| {key} | {parts[key]} |")
        lines.append("")
    return "\n".join(lines)


def write_speech_files(payload: Dict[str, Any], base_path: str) -> Dict[str, str]:
    """把话术写成 ``<base>.speech.json`` 与 ``<base>.speech.md``。

    Args:
        payload: :func:`build_speech` 的结果。
        base_path: 不含扩展名的基础路径（通常与 pptx 同名）。

    Returns:
        ``{"json": ..., "md": ...}``
    """
    out_dir = os.path.dirname(os.path.abspath(base_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    json_path = base_path + ".speech.json"
    md_path = base_path + ".speech.md"

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(format_speech_markdown(payload))

    return {"json": json_path, "md": md_path}
