# -*- coding: utf-8 -*-
"""@ 解析：从用户输入里认领被点名的群成员。

支持中英文 alias（``@collection_agent`` / ``@催费助手`` / ``@催费``），
以及 ``@all`` / ``@所有人``（全体成员，按注册顺序）。

实现是两段式（不用单个贪婪正则是为了避坑）：

1. **已知 alias 交替匹配**（按长度从长到短，如 ``催费助手`` 排在
   ``催费`` 前面）：``@法务助手 联合出个方案`` 不会把正文吞进名字；
   ``@催费欠款多少``（没空格）也能认出 ``@催费``。
2. **剩余 @ 扫 unknown**：认领不上的 @（写错名字）不抛错，返回到
   unknown，调用方降级走自荐流程。

@ 只是点名方式，不是硬闸门。
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

#: 全体成员的写法
ALL_ALIASES = {"all", "所有人"}

#: 第二段 unknown 扫描：@ 后跟英文名 / 2~12 个汉字。
#: 注意这里只扫**第一段没吃掉**的 @，所以贪婪吞正文的问题只影响
#: 本来就写错了的名字（可接受：unknown 截断到 12 字照样能提示）。
MENTION_RE = re.compile(
    r"@([A-Za-z_][A-Za-z0-9_]*|[一-龥]{2,12}|所有人)"
)


def build_alias_lookup(
    alias_map: Dict[str, List[str]],
) -> Dict[str, str]:
    """alias（大小写不敏感）→ 成员 canonical 名。"""
    lookup: Dict[str, str] = {}
    for member_name, aliases in alias_map.items():
        lookup[member_name.lower()] = member_name
        for alias in aliases or []:
            key = str(alias).strip().lower()
            if key and key not in lookup:
                lookup[key] = member_name
    return lookup


def _known_alias_pattern(alias_map: Dict[str, List[str]]) -> "re.Pattern | None":
    """已知 alias 的 @ 交替匹配（最长优先），大小写不敏感。"""
    lookup = build_alias_lookup(alias_map)
    if not lookup:
        return None
    # 最长优先：催费助手 必须排在 催费 前面，否则 @催费助手 只吃掉一半
    alts = sorted(lookup.keys(), key=len, reverse=True)
    body = "|".join(re.escape(a) for a in alts if a not in ALL_ALIASES)
    if not body:
        return None
    return re.compile(rf"@({body}|all|所有人)", re.IGNORECASE)


def extract_mentions(
    text: str,
    alias_map: Dict[str, List[str]],
) -> Tuple[List[str], str, List[str]]:
    """解析用户输入中的 @。

    Args:
        text: 用户原始输入。
        alias_map: 成员 canonical 名 → alias 列表（canonical 名本身可 @）。

    Returns:
        (mentioned, clean_text, unknown)：
        - mentioned：被点名的成员 canonical 名（按 @ 出现顺序去重；
          ``@all`` 展开为注册顺序的全体成员）；
        - clean_text：去掉 @ 段后的文本（喂给成员，避免污染意图识别），
          连续空白已压成单个空格；
        - unknown：认领不上的 @ 原文（不含 @ 符号）。
    """
    text = text or ""
    lookup = build_alias_lookup(alias_map)
    order = list(alias_map.keys())

    mentioned: List[str] = []
    unknown: List[str] = []
    seen = set()

    def _claim_known(match: re.Match) -> str:
        raw = match.group(1).strip()
        key = raw.lower()
        if key in ALL_ALIASES:
            for name in order:
                if name not in seen:
                    seen.add(name)
                    mentioned.append(name)
            return " "
        target = lookup.get(key)
        if target is not None and target not in seen:
            seen.add(target)
            mentioned.append(target)
        return " "

    pattern = _known_alias_pattern(alias_map)
    rest = pattern.sub(_claim_known, text) if pattern else text

    def _claim_unknown(match: re.Match) -> str:
        unknown.append(match.group(1).strip())
        return " "

    clean = MENTION_RE.sub(_claim_unknown, rest)
    clean = re.sub(r"\s+", " ", clean).strip()
    return mentioned, clean, unknown
