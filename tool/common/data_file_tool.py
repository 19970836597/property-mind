# -*- coding: utf-8 -*-
"""常用文件定位工具（能力名 ``data_file``）

解决什么
--------
用户说「用上个月的销售表做个 PPT」但**不给路径**。
本工具负责：从用户这句话里解析线索 → 去常用文件目录里把文件找出来。

它不是数据读取工具（那是 ``data_analysis``），只负责**定位**：
返回候选文件路径、大小、修改时间、命中理由，让 Agent 决定用哪个。

典型用法
--------
    router.execute("data_file", {"query": "上个月的销售表"})
    router.execute("data_file", {"action": "index"})
    router.execute("data_file", {"file_name": "销售成交汇总_2026-09.xlsx"})
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..tools import Tool, ToolParameter
from .commons import (
    CommonFile,
    describe_index,
    find_files,
    index_common_files,
    resolve_common_dirs,
)

__all__ = ["DataFileTool"]


#: "上个月 / 最近7天 / 本季度" 之类的时间线索 → 天数
RECENT_PATTERNS = (
    (r"今天|当天|本日", 1),
    (r"昨天", 2),
    (r"这周|本周|本星期", 7),
    (r"上周|上星期", 14),
    (r"这个?月|本月|当月", 31),
    (r"上个?月|上月", 62),
    (r"本季度|这季度", 92),
    (r"上个?季度|上季度", 185),
    (r"今年|本年度", 366),
    (r"最近", 30),
)


def _infer_recent_days(text: str) -> Optional[int]:
    """从自然语言里推断"最近多少天"。"""
    for pattern, days in RECENT_PATTERNS:
        if re.search(pattern, text):
            return days
    # "最近 7 天" / "近 30 天"
    match = re.search(r"(?:最近|近|过去)\s*(\d{1,3})\s*(?:天|日)", text)
    if match:
        return int(match.group(1))
    # "2026年9月" / "2026-09" → 从该月至今的天数
    match = re.search(r"(\d{4})\s*[-年/]\s*(\d{1,2})", text)
    if match:
        try:
            target = datetime(int(match.group(1)), int(match.group(2)), 1)
            delta = (datetime.now() - target).days
            if 0 <= delta <= 730:
                return max(delta, 1)
        except ValueError:
            pass
    return None


def _extract_file_hint(query: str) -> str:
    """从请求里抽更像文件名的片段（带扩展名或书名号）。"""
    quoted = re.findall(r"[《「\"']([^》」\"']{2,60})[》」\"']", query)
    if quoted:
        return quoted[0]
    with_ext = re.findall(r"[\w\u4e00-\u9fff\-（）()]+\.(?:xlsx|xlsm|csv|tsv|json)",
                          query, flags=re.IGNORECASE)
    if with_ext:
        return with_ext[0]
    return ""


class DataFileTool(Tool):
    """常用文件定位工具（``data_file``）。"""

    def __init__(self, expandable: bool = False):
        super().__init__(
            name="data_file",
            description=(
                "在常用文件目录（data/common、data/inbox）里按文件名或关键词查找数据文件。"
                "当用户提到某个表格/数据但没给出完整路径时使用，例如"
                "「上个月的销售表」「用物业经营分析那个文件」。"
                "返回候选文件路径与匹配理由，随后可用 data_analysis 读取内容。"
            ),
            expandable=expandable,
        )

    def get_params(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type="string",
                description=(
                    "用户请求里的线索，原样传入即可，例如"
                    "「上个月的销售成交汇总」或「物业经营分析 2026年9月」"
                ),
                required=False,
                default="",
            ),
            ToolParameter(
                name="action",
                type="string",
                description=(
                    "list=列出常用目录所有文件（了解有哪些可用）; "
                    "find=按 query 匹配（默认）; "
                    "info=查看单文件信息"
                ),
                required=False,
                default="find",
            ),
            ToolParameter(
                name="file_name",
                type="string",
                description="精确文件名（action=info 时使用）",
                required=False,
                default=None,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="最多返回几个候选，默认 5",
                required=False,
                default=5,
            ),
        ]

    def run(self, params: Dict[str, Any]) -> str:
        action = str(params.get("action") or "find").strip().lower()
        limit = params.get("limit") or 5
        try:
            limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            limit = 5

        if action in ("list", "index", "ls"):
            return describe_index(limit=limit)

        if action == "info":
            return self._info(str(params.get("file_name") or params.get("query") or ""))

        query = str(params.get("query") or "").strip()
        return self._find(query, limit)

    # ---------------- 具体动作 ----------------

    def _find(self, query: str, limit: int) -> str:
        roots = resolve_common_dirs()
        if not roots:
            return (
                "未找到常用文件目录。请创建 data/common 目录并把常用表格放进去，"
                "或设置环境变量 SKILL_COMMON_DIRS 指向你的目录。"
            )

        hint = _extract_file_hint(query)
        search_text = hint or query
        recent_days = _infer_recent_days(query)

        candidates = find_files(
            query=search_text, dirs=roots, limit=limit, recent_days=recent_days
        )

        if not candidates:
            return (
                f"常用目录里没有可用数据文件。\n{describe_index(limit=limit)}"
            )

        lines = [
            f"[常用文件定位] 线索: {query!r}"
            + (f"  文件名线索: {hint!r}" if hint else "")
            + (f"  时间范围: 最近 {recent_days} 天" if recent_days else "")
        ]

        best = candidates[0]
        lines.append(f"推荐使用: {best.path}")
        lines.append("")
        lines.append(f"候选（{len(candidates)} 个）:")
        for i, item in enumerate(candidates, 1):
            matched = "、".join(item.matched) or "-"
            lines.append(
                f"  {i}. {item.name}  [{item.size_kb}KB, "
                f"{item.modified:%Y-%m-%d %H:%M}, 得分 {item.score}]"
            )
            lines.append(f"     路径: {item.path}")
            lines.append(f"     命中: {matched}")

        lines.append("")
        lines.append(
            "下一步: 用 data_analysis 读取该文件（file=<上面的路径>）获取数据并画像。"
        )
        return "\n".join(lines)

    def _info(self, target: str) -> str:
        if not target:
            return "请提供 file_name。"
        items = index_common_files()
        exact = [i for i in items if i["name"] == target]
        partial = [i for i in items if target.lower() in i["name"].lower()]
        hits = exact or partial
        if not hits:
            return f"常用目录里没有匹配 {target!r} 的文件。\n{describe_index(limit=10)}"
        lines = [f"[文件信息] 匹配 {len(hits)} 个:"]
        for item in hits:
            lines.append(
                f"  - {item['name']}  {item['size_kb']}KB  {item['modified']}"
            )
            lines.append(f"    路径: {item['path']}")
        return "\n".join(lines)
