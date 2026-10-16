# -*- coding: utf-8 -*-
"""表格探查（Table Profiling）—— 从一张表里认出「哪些列是什么」

为什么独立成模块
----------------
这些能力原先长在 ``deck_content_tool`` 里，但它们是**通用的表格理解**，
跟"生成 PPT"没关系：

    · 哪列是日期         → _to_datetime / _is_date_like / _detect_date_column
    · 哪列是金额         → _pick_value_column
    · 哪列是分组维度      → _text_columns / _pick_group_column
    · 哪列是"伪数值"      → _looks_like_period（201801 这种期间码）
    · 哪些指标值得上封面   → _pick_metrics / _primary_value_column

抽出来之后 ``data_analysis`` 之类的能力也能复用，而不是各写一份。

踩过的坑（都固化在实现里）
--------------------------
* ``费用期间`` 值形如 201801，名字里又含「费用」—— 被当成金额列，
  "合计"变成 32 亿。→ 数值**形态**也要筛。
* ``楼栋``/``单元`` 是 ID，``应收金额(元)`` 单行只有几百元 ——
  用``max_abs < 1000`` 区分二者是错的。→ 改用**基数**。
* ``月份`` 列被当成低基数文本选为分组维度 → 图表分类变成一串日期。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "to_datetime",
    "is_date_like",
    "looks_like_period",
    "numeric_columns",
    "text_columns",
    "detect_date_column",
    "pick_group_column",
    "pick_value_column",
    "pick_metrics",
    "primary_value_column",
    "DATE_NAME_HINTS",
    "VALUE_HINTS",
    "VALUE_SKIP_HINTS",
    "GROUP_HINTS",
]


#: 列名里出现这些词 → 基本可判定是日期列
DATE_NAME_HINTS = ("日期", "时间", "date", "Date", "月份", "年月")

#: 金额/主指标列的列名线索（按语义优先，而不是按列位置）
VALUE_HINTS = (
    "金额", "收入", "营收", "销售额", "业绩", "产值", "费用", "成本", "支出",
    "amount", "revenue", "sales", "cost", "value",
)

#: 一般不该当主指标的列（计数类、编号类、期间类）
VALUE_SKIP_HINTS = ("编号", "序号", "id", "ID", "电话", "手机", "编码", "单号",
                    "期间", "年月", "月份", "年度")

#: 分组维度的列名线索（按语义优先）
GROUP_HINTS = (
    "产品", "渠道", "科目", "类别", "分类", "类型", "区域", "部门", "小组",
    "项目", "门店", "客户", "收费项", "业务",
    "channel", "product", "category", "type", "region",
)


def _pd():
    """延迟导入 pandas（环境可能没有）。"""
    from .table_io import pd as _pandas
    return _pandas


def to_datetime(series):
    """安全地尝试把列解析为日期。

    包住 pandas 的 UserWarning：``to_datetime`` 对混合格式会打
    「Could not infer format」警告。本工具链把 stderr 当失败信号看待，
    所以这里显式静默 —— 解析失败本来就用 NaN 表达了。
    """
    import warnings

    pd = _pd()
    if pd is None:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return pd.to_datetime(series, errors="coerce")
    except Exception:
        return pd.Series([None] * len(series))


def is_date_like(series) -> bool:
    """判断列是否「像日期」（列名线索 + 值可解析比例）。"""
    pd = _pd()
    if pd is None:
        return False
    name = str(getattr(series, "name", "") or "")
    if any(k in name for k in DATE_NAME_HINTS):
        return True
    try:
        parsed = to_datetime(series)
        return bool(parsed is not None and parsed.notna().mean() > 0.8)
    except Exception:
        return False


def looks_like_period(series) -> bool:
    """判断数值列是否「其实是期间编码」（如 201801 / 202609）。

    实测踩坑：``费用期间`` 的值是 201801 这种 6 位期间码，
    因为它名字里含「费用」两字命中了金额关键词，被选成金额列 ——
    于是"合计"变成 32 亿。数值形态必须也筛一遍。
    """
    pd = _pd()
    if pd is None:
        return False
    try:
        values = pd.to_numeric(series, errors="coerce").dropna()
        if values.empty:
            return False
        if not bool((values == values.round()).all()):
            return False
        return bool(((values >= 190001) & (values <= 299912)).all())
    except Exception:
        return False


def numeric_columns(df) -> List[str]:
    """数值列（非空率 > 80% 且绝对值之和 > 0）。"""
    pd = _pd()
    if pd is None or df is None:
        return []
    out = []
    for c in df.columns:
        try:
            s = pd.to_numeric(df[c], errors="coerce")
            if s.notna().mean() > 0.8 and s.abs().sum() > 0:
                out.append(c)
        except Exception:
            continue
    return out


def text_columns(df, max_card: int = 30) -> List[str]:
    """低基数文本列（排除日期类），可用作分组维度。"""
    pd = _pd()
    if pd is None or df is None:
        return []
    out = []
    for c in df.columns:
        try:
            nunique = df[c].nunique()
        except Exception:
            continue
        if not (1 < nunique <= max_card):
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            continue
        if is_date_like(df[c]):
            continue
        out.append(c)
    return out


def detect_date_column(df) -> Optional[str]:
    """自动找出时间列（显式传参优先，这里只做兜底）。"""
    if df is None:
        return None
    for c in df.columns:
        if any(k in str(c) for k in DATE_NAME_HINTS):
            return c
    for c in df.columns:
        if is_date_like(df[c]) and df[c].nunique() > 1:
            return c
    return None


def pick_group_column(texts: List[str]) -> Optional[str]:
    """按列名语义挑分组维度，避免选中无意义的列。"""
    if not texts:
        return None
    for c in texts:
        if any(h in str(c) for h in GROUP_HINTS):
            return c
    return texts[0]


def pick_value_column(nums: List[str], df=None) -> Optional[str]:
    """从数值列里挑主指标列。

    规则：**先按列名语义，再按数值形态，最后按量级**。
    踩过的坑：只按列名会选中「订单数」，或者选中「费用期间」这种期间码。
    """
    if not nums:
        return None

    # 0) 形态筛选：排除期间编码这类"伪数值列"
    usable = list(nums)
    if df is not None:
        usable = [c for c in nums if not looks_like_period(df[c])]
        if not usable:
            usable = list(nums)

    # 1) 名称含金额类线索
    for c in usable:
        if any(h in str(c) for h in VALUE_HINTS):
            return c

    # 2) 排除明显不是主指标的（编号/电话/期间等）
    candidates = [c for c in usable
                  if not any(h in str(c) for h in VALUE_SKIP_HINTS)]
    if candidates:
        pd = _pd()
        # 3) 取量级最大的那个（更像金额）
        if df is not None and pd is not None:
            try:
                best, best_total = candidates[0], -1.0
                for c in candidates:
                    total = float(pd.to_numeric(df[c], errors="coerce").abs().sum())
                    if total > best_total:
                        best, best_total = c, total
                return best
            except Exception:
                pass
        return candidates[0]

    return usable[0]


def pick_metrics(df, max_items: int = 6) -> Dict[str, Any]:
    """从数据里抽出可用的核心指标（不假设业务）。

    怎么区分「ID 列」和「金额列」
    ----------------------------
    踩过的坑：原先用 ``max_abs < 1000`` 当门槛，结果
    ``楼栋``(max 6) 和 ``应收金额(元)``(max 562.5) 一起被滤掉了 ——
    因为这是**按户明细表**，单行金额本来就小，而 15876 行的合计有 48 万。

    单行量级根本分不出 ID 和金额。有原则的判据是**基数**：
      · ID / 分类列：不同值很少（楼栋 6 个、单元 4 个）
      · 金额列：不同值很多，且合计有意义
    """
    pd = _pd()
    metrics: Dict[str, Any] = {"rows": int(len(df)) if df is not None else 0}
    if pd is None or df is None:
        return metrics

    for c in numeric_columns(df):
        name = str(c)
        if any(h in name for h in VALUE_SKIP_HINTS):
            continue
        if looks_like_period(df[c]):
            continue
        series = pd.to_numeric(df[c], errors="coerce")
        clean = series.dropna()
        if clean.empty:
            continue
        distinct = int(clean.nunique())
        if distinct <= 10 and distinct < len(clean) * 0.05:
            continue
        metrics[c] = {
            "sum": float(series.sum()),
            "mean": float(series.mean()),
            "max": float(series.abs().max() or 0),
            "non_null": int(series.notna().sum()),
            "distinct": distinct,
        }
        if len(metrics) >= max_items:
            break
    return metrics


def primary_value_column(metrics: Dict[str, Any]) -> Optional[str]:
    """挑「主指标列」—— 合计最大且不同值最多的那个。"""
    candidates = [k for k in (metrics or {}) if k != "rows"]
    if not candidates:
        return None
    return max(candidates,
               key=lambda k: (metrics[k]["sum"], metrics[k]["distinct"]))
