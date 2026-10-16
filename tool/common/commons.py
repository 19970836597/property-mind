# -*- coding: utf-8 -*-
"""常用文件目录（common files）—— 配置与检索

问题
----
用户说「用上个月的销售表做个 PPT」，但不给路径。
Agent 需要自己去**常用目录**里把文件找出来。

设计
----
* 搜索根默认是项目下的 ``data/common`` 与 ``data/inbox``。
* 可用环境变量 ``SKILL_COMMON_DIRS`` 覆盖（分号/冒号分隔，支持绝对路径）。
* 文件名本身当线索：``销售成交汇总_2026-09.xlsx`` 能匹配"销售""2026-09"。

对外接口
--------
    resolve_common_dirs()   解析出要搜索的目录列表
    iter_data_files()       遍历所有支持格式的文件
    find_files(query, ...)  按文件名/关键词模糊匹配，返回打分排序
    index_common_files()    生成清单（供 Agent 了解有哪些文件）
    describe_index()        人类可读清单
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "CommonFile",
    "PROJECT_ROOT",
    "resolve_common_dirs",
    "iter_data_files",
    "find_files",
    "index_common_files",
    "describe_index",
    "COMMON_DIRS_ENV",
]

#: 项目根。本文件在 tool/common/ 下，因此要上溯三层：
#:     .../tool/common/commons.py → common → tool → 项目根
#: （早先只上溯两层，得到 .../tool，导致 data/common 永远找不到）
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

#: 支持的表格/结构化数据格式
SUPPORTED_EXTENSIONS = (".xlsx", ".xlsm", ".csv", ".tsv", ".json")

#: 环境变量：覆盖搜索根（分号或冒号分隔）
COMMON_DIRS_ENV = "SKILL_COMMON_DIRS"

#: 默认搜索的子目录（相对项目根）
DEFAULT_SUBDIRS = (os.path.join("data", "common"), os.path.join("data", "inbox"))

#: 匹配时需要忽略的噪声词
STOPWORDS = {
    "数据", "表格", "文件", "汇总", "明细", "导出", "副本", "最终", "最新",
    "的", "了", "和", "与", "表",
    "data", "file", "table", "sheet", "final", "copy",
}

#: 文件名与口头请求里都会出现、但**不指向具体文件**的词。
#: 这些词即便在整个候选集里只出现一次，也不能当作强线索 ——
#: 它们稀有只是碰巧，不代表有信息量。
#:
#: 实测踩过的坑：请求「生成上个月**报表**」命中了「欠款明细**报表**.xlsx」，
#: 因为该词在 7 个候选文件里只出现 1 次 → 被区分度算法判为"强线索"。
#: 结果基于**报表格式的表**（多行表头）生成了一份 7/11 页都是
#: "数据缺失"的垃圾 PPT，还报了 success=True。
GENERIC_NAME_TOKENS = {
    # 文档类型
    "报表", "报告", "月报", "季报", "年报", "台账", "清单", "列表",
    "信息", "记录", "情况", "统计", "分析", "经营", "管理",
    "report", "list", "record", "summary", "info",
    # 交付物类型（用户说的是产物，不是数据）
    "ppt", "pptx", "幻灯片", "演示", "汇报", "文档", "excel", "word",
    # 时间泛指（不是文件里的具体期间）
    "上个", "个月", "上月", "本周", "上周", "今天", "昨天", "本月",
    "季度", "年度", "全年", "整年", "近期", "最近",
    # 动作词
    "生成", "制作", "做个", "做一份", "给我", "帮我", "看看", "用这个",
}


def _norm(text: str) -> str:
    """归一化：全角转半角、去空白与标点、小写。"""
    text = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"[\s_\-—－·、,，。.:：;；/\\()（）\[\]【】]+", "", text).lower()


def _tokenize(text: str) -> List[str]:
    """把查询/文件名切成可匹配片段。

    中文没有空格分词，用 2 字滑窗；英文数字按连续串。
    """
    norm = _norm(text)
    tokens: List[str] = []

    for match in re.finditer(r"[a-z0-9]{2,}", norm):
        tokens.append(match.group())

    chinese = re.sub(r"[a-z0-9]+", "", norm)
    for i in range(len(chinese) - 1):
        gram = chinese[i:i + 2]
        if gram and gram not in STOPWORDS:
            tokens.append(gram)

    return tokens


def resolve_common_dirs(extra: Optional[Sequence[str]] = None) -> List[str]:
    """解析「常用文件」搜索根目录（只返回真实存在的）。"""
    candidates: List[str] = []

    env_value = os.environ.get(COMMON_DIRS_ENV, "").strip()
    if env_value:
        candidates.extend(
            part.strip() for part in re.split(r"[;:]", env_value) if part.strip()
        )

    candidates.extend(os.path.join(PROJECT_ROOT, sub) for sub in DEFAULT_SUBDIRS)
    candidates.extend(extra or [])

    resolved: List[str] = []
    for path in candidates:
        absolute = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(absolute) and absolute not in resolved:
            resolved.append(absolute)
    return resolved


@dataclass
class CommonFile:
    """一个候选数据文件。"""

    path: str
    name: str
    size: int
    modified: datetime
    score: int = 0
    matched: List[str] = field(default_factory=list)

    @property
    def size_kb(self) -> float:
        return round(self.size / 1024, 1)

    @property
    def stem(self) -> str:
        return os.path.splitext(self.name)[0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "size_kb": self.size_kb,
            "modified": self.modified.strftime("%Y-%m-%d %H:%M"),
            "score": self.score,
            "matched": self.matched,
        }


def iter_data_files(dirs: Optional[Iterable[str]] = None) -> List[CommonFile]:
    """遍历常用目录下所有支持格式的文件。"""
    roots = list(dirs) if dirs is not None else resolve_common_dirs()
    found: List[CommonFile] = []
    skip = {"__pycache__", ".git", "node_modules", ".idea", ".pytest_cache"}

    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for name in filenames:
                if not name.lower().endswith(SUPPORTED_EXTENSIONS):
                    continue
                full = os.path.join(dirpath, name)
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                found.append(
                    CommonFile(
                        path=full,
                        name=name,
                        size=stat.st_size,
                        modified=datetime.fromtimestamp(stat.st_mtime),
                    )
                )
    return found


def find_files(
    query: str = "",
    dirs: Optional[Sequence[str]] = None,
    limit: int = 5,
    recent_days: Optional[int] = None,
) -> List[CommonFile]:
    """按文件名/关键词模糊匹配常用目录下的文件。

    打分（越高越靠前）：
      * 文件名整体出现在查询里：+12
      * 查询整体出现在文件名里：+10
      * 期间线索命中（2026-09）：+6
      * 每个切片命中：+2

    Args:
        query: 用户请求里的线索，例如"上个月的销售表"。
        dirs: 搜索根，默认 :func:`resolve_common_dirs`。
        limit: 返回条数。
        recent_days: 只看最近 N 天修改过的文件。
    """
    files = iter_data_files(dirs)

    if recent_days is not None:
        cutoff = datetime.now().timestamp() - recent_days * 86400
        files = [f for f in files if f.modified.timestamp() >= cutoff]

    if not query.strip():
        files.sort(key=lambda f: f.modified, reverse=True)
        return files[:limit]

    tokens = _tokenize(query)
    norm_query = _norm(query)

    # ---- 先算每个词的「区分度」----
    # 踩过的坑：请求「生成上个月物业季度报表」命中了「4、欠款明细报表.xlsx」——
    # 因为两者都含「报表」，而旧的打分给每个命中词都记 +2，
    # 于是**通用词和专有词一样重**。结果生成了一份全是"数据缺失"的垃圾 PPT，
    # 还报了 success=True。
    #
    # 一个词出现在越多候选文件里，它的信息量越低。按出现次数反比加权：
    #   只出现在 1 个文件 → 强线索（如「应收」「缴费」）
    #   出现在 2 个文件   → 弱线索
    #   出现在 ≥3 个文件  → 几乎无区分度，不给分（如「报表」「数据」）
    doc_freq: Dict[str, int] = {}
    for item in files:
        norm_name = _norm(item.name)
        for token in set(tokens):
            if token in norm_name:
                doc_freq[token] = doc_freq.get(token, 0) + 1

    def _weight(token: str) -> int:
        # 通用词不给分：它们指示"要什么文档"，不指示"用哪份数据"
        if token in GENERIC_NAME_TOKENS or token in STOPWORDS:
            return 0
        freq = doc_freq.get(token, 0)
        if freq <= 1:
            return 3
        if freq == 2:
            return 1
        return 0

    for item in files:
        norm_name = _norm(item.name)
        norm_stem = _norm(item.stem)

        score = 0
        matched: List[str] = []

        if norm_stem and norm_stem in norm_query:
            score += 12
            matched.append("文件名整体命中")
        elif norm_query and norm_query in norm_name:
            score += 10
            matched.append("查询整体命中")

        for token in tokens:
            if token in norm_name and token not in matched:
                weight = _weight(token)
                if weight:
                    score += weight
                    freq = doc_freq.get(token, 0)
                    matched.append(token if freq <= 1 else f"{token}（{freq}个文件共有）")

        for match in re.finditer(r"\d{4}[-/]?\d{1,2}", norm_query):
            if match.group() and match.group() in norm_name:
                score += 6
                matched.append(f"期间={match.group()}")

        item.score = score
        item.matched = matched

    hits = [f for f in files if f.score > 0]
    hits.sort(key=lambda f: (f.score, f.modified), reverse=True)

    if hits:
        return hits[:limit]

    # 一个都没匹配上时的行为，取决于查询的"意图清晰度"：
    #   * 查询含糊（"用这个数据做个PPT"）→ 退回最近文件，方便用户
    #   * 查询明确指向某个名字（"销售成交汇总"）却找不到
    #     → **不要**静默换文件！否则用户以为用的是 A，实际是 B。
    if _looks_specific(query):
        return []

    files.sort(key=lambda f: f.modified, reverse=True)
    fallback = files[:1] if files else []
    for item in fallback:
        item.matched = ["无匹配，退回最近文件"]
    return fallback


def _looks_specific(query: str) -> bool:
    """判断查询是否明确指向某个文件名。

    有意义的线索（≥2 个非噪声切片）才算明确；否则视为泛指。
    """
    tokens = _tokenize(query)
    meaningful = [t for t in tokens if t not in STOPWORDS and len(t) >= 2]
    return len(set(meaningful)) >= 2


def index_common_files(dirs: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """常用文件清单（按修改时间倒序）。"""
    files = iter_data_files(dirs)
    files.sort(key=lambda f: f.modified, reverse=True)
    return [f.to_dict() for f in files]


def describe_index(dirs: Optional[Sequence[str]] = None, limit: int = 30) -> str:
    """人类可读清单，便于 Agent 判断有哪些可用文件。"""
    roots = list(dirs) if dirs is not None else resolve_common_dirs()
    if not roots:
        return (
            "常用文件目录不存在。请先创建 data/common（或设置 "
            f"{COMMON_DIRS_ENV}），再把常用表格放进去。"
        )

    lines = ["常用文件目录:"]
    lines.extend(f"  • {r}" for r in roots)

    items = index_common_files(roots)
    if not items:
        lines.append("  （目录为空，尚无可用数据文件）")
        return "\n".join(lines)

    lines.append(f"可用文件（{len(items)} 个，按修改时间倒序）:")
    for item in items[:limit]:
        lines.append(f"  - {item['name']}  [{item['size_kb']}KB, {item['modified']}]")
    if len(items) > limit:
        lines.append(f"  ...还有 {len(items) - limit} 个")
    return "\n".join(lines)
