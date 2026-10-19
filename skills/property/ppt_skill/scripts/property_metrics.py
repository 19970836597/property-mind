# -*- coding: utf-8 -*-
"""Deterministic metrics for the fixed property collection deck."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Iterable

from openpyxl import load_workbook


DEFAULT_FILES = {
    "properties": "1、房产客户信息.xlsx",
    "receivables": "2、应收信息.xlsx",
    "payments": "3、缴费数据.xlsx",
    "arrears": "4、欠款明细报表.xlsx",
    "collection": "5、房产收缴率明细表.xlsx",
}

DEFAULT_PROJECT = "物业管理项目"
DEFAULT_PERIOD = "2026年1-5月"

BUILDING_ORDER = ["1栋", "2栋", "3栋", "4栋", "5栋", "6栋"]


class DataValidationError(RuntimeError):
    """Raised when a source workbook is missing or does not match its contract."""


@dataclass
class BuildingMetric:
    name: str
    rate: float = 0.0
    unpaid: float = 0.0
    due: float = 0.0
    paid: float = 0.0


@dataclass
class ReasonMetric:
    name: str
    amount: float = 0.0
    share: float = 0.0


@dataclass
class DebtorMetric:
    property: str
    owner: str
    amount: float
    reason: str
    period: str

    @property
    def owner_display(self) -> str:
        name = (self.owner or "").strip()
        return name[:1] if name else ""


@dataclass
class ChannelMetric:
    name: str
    amount: float = 0.0
    share: float = 0.0


@dataclass
class PropertyMetrics:
    project_name: str
    period: str
    data_dir: Path

    property_count: int = 0
    total_area: float = 0.0
    occupied_count: int = 0
    residential_count: int = 0
    virtual_count: int = 0
    building_counts: dict[str, int] = field(default_factory=dict)
    other_count: int = 0

    current_due: float = 0.0
    prior_due: float = 0.0
    current_paid: float = 0.0
    current_unpaid: float = 0.0
    comprehensive_rate: float = 0.0
    current_rate: float = 0.0
    annual_rates: list[tuple[str, float]] = field(default_factory=list)

    building_metrics: list[BuildingMetric] = field(default_factory=list)
    highest_building: BuildingMetric = field(default_factory=lambda: BuildingMetric(""))
    low_group_label: str = "5/6栋"
    building_56_unpaid: float = 0.0
    building_56_share: float = 0.0

    full_count: int = 0
    zero_count: int = 0
    partial_count: int = 0
    zero_unpaid: float = 0.0
    zero_share: float = 0.0

    arrears_total: float = 0.0
    arrears_fee: float = 0.0
    arrears_garbage: float = 0.0
    arrears_reasons: list[ReasonMetric] = field(default_factory=list)
    top_debtors: list[DebtorMetric] = field(default_factory=list)
    top10_total: float = 0.0
    top10_share: float = 0.0
    top10_building_count: int = 0
    top10_quality_count: int = 0

    channel_total: float = 0.0
    channels: list[ChannelMetric] = field(default_factory=list)

    @property
    def low_group_names(self) -> list[str]:
        return [b.name for b in self.building_metrics if b.name in self.low_group_label.split("/")]

    @property
    def low_group_compact(self) -> str:
        return self.low_group_label.replace("栋/", "/")

    @property
    def top_reason(self) -> ReasonMetric:
        return self.arrears_reasons[0] if self.arrears_reasons else ReasonMetric("未填")

    @property
    def second_reason(self) -> ReasonMetric:
        return self.arrears_reasons[1] if len(self.arrears_reasons) > 1 else ReasonMetric("未填")

    @property
    def top_channel(self) -> ChannelMetric:
        return self.channels[0] if self.channels else ChannelMetric("微信")


def _num(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _norm(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _read_workbook(path: Path, header_row: int) -> tuple[list[str], list[tuple[Any, ...]]]:
    if not path.is_file():
        raise DataValidationError(f"缺少数据文件：{path}")
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        headers = [_norm(c.value) for c in ws[header_row]]
        rows = list(ws.iter_rows(min_row=header_row + 1, values_only=True))
    finally:
        wb.close()
    return headers, rows


def _column(headers: list[str], name: str) -> int:
    for idx, header in enumerate(headers):
        if header == name or header.startswith(name):
            return idx
    raise DataValidationError(f"缺少列：{name}（现有列：{', '.join(h for h in headers if h)}）")


def _building_name(value: Any, allow_plain: bool = False) -> str:
    raw = _norm(value)
    if raw in {"1栋", "2栋", "3栋", "4栋", "5栋", "6栋"}:
        return raw
    if allow_plain and raw in {"1", "2", "3", "4", "5", "6"}:
        return f"{raw}栋"
    return raw or "其他"


def _parse_project(request: str) -> str:
    match = re.search(
        r"([\u4e00-\u9fa5A-Za-z0-9]{2,12}(?:小区|花园|家园|公寓|广场|大厦|社区|项目|园))",
        request or "",
    )
    if not match:
        return DEFAULT_PROJECT
    project = re.sub(
        r"^(?:帮我|请|生成|制作|做|用|为|关于|一份|一个)+",
        "",
        match.group(1),
    ).strip()
    project = _normalize_project_name(project)
    return project or DEFAULT_PROJECT


def _normalize_project_name(name: str) -> str:
    return re.sub(r"(小区|花园|家园|公寓|广场|大厦|社区|园)项目$", r"\1", (name or "").strip())


def _parse_period(request: str) -> str:
    match = re.search(r"(\d{4}\s*年\s*\d{1,2}\s*[-—~至]\s*\d{1,2}\s*月)", request or "")
    if match:
        return re.sub(r"\s+", "", match.group(1)).replace("—", "-").replace("~", "-").replace("至", "-")
    return DEFAULT_PERIOD


def compute_metrics(
    data_dir: str | Path,
    project_name: str | None = None,
    period: str | None = None,
    request: str = "",
) -> PropertyMetrics:
    data_dir = Path(data_dir)
    metrics = PropertyMetrics(
        project_name=_normalize_project_name(project_name) if project_name else _parse_project(request),
        period=period or _parse_period(request),
        data_dir=data_dir,
    )

    # 1. Property base
    headers, rows = _read_workbook(data_dir / DEFAULT_FILES["properties"], 1)
    c_building = _column(headers, "楼栋")
    c_area = _column(headers, "实测收费面积")
    c_status = _column(headers, "房产状态")
    c_type = _column(headers, "房产类型")
    metrics.property_count = len(rows)
    metrics.total_area = round(sum(_num(r[c_area]) for r in rows), 2)
    metrics.occupied_count = sum(1 for r in rows if _norm(r[c_status]) == "已入住")
    metrics.residential_count = sum(1 for r in rows if _norm(r[c_type]) == "居住-多层住宅")
    metrics.virtual_count = sum(1 for r in rows if _norm(r[c_type]) == "虚拟房产")
    counts: dict[str, int] = {name: 0 for name in BUILDING_ORDER}
    for row in rows:
        name = _building_name(row[c_building])
        if name in counts:
            counts[name] += 1
    metrics.building_counts = counts
    metrics.other_count = max(0, metrics.property_count - sum(counts.values()))

    # 2. Annual receivables
    headers, rows = _read_workbook(data_dir / DEFAULT_FILES["receivables"], 1)
    c_period = _column(headers, "费用期间")
    c_bill = _column(headers, "账单金额")
    c_unpaid = _column(headers, "未缴金额")
    annual: dict[str, list[float]] = {}
    for row in rows:
        year = _norm(row[c_period])[:4]
        if not year.isdigit():
            continue
        item = annual.setdefault(year, [0.0, 0.0])
        item[0] += _num(row[c_bill])
        item[1] += _num(row[c_unpaid])
    metrics.annual_rates = []
    for year in ("2021", "2022", "2023", "2024", "2025"):
        bill, unpaid = annual.get(year, [0.0, 0.0])
        rate = ((bill - unpaid) / bill * 100.0) if bill else 0.0
        metrics.annual_rates.append((year, round(rate, 2)))

    # 3. 2025 payment channels
    headers, rows = _read_workbook(data_dir / DEFAULT_FILES["payments"], 1)
    c_pay_period = _column(headers, "收款期间")
    c_channel = _column(headers, "收款方式")
    c_amount = _column(headers, "当前账面金额")
    channel_map = {
        "POS - 微信": "微信",
        "微信": "微信",
        "现金": "现金",
        "POS - 支付宝": "支付宝",
        "支付宝": "支付宝",
        "POS - 刷卡": "刷卡",
        "刷卡": "刷卡",
    }
    channel_totals: dict[str, float] = {"微信": 0.0, "现金": 0.0, "支付宝": 0.0, "刷卡": 0.0}
    for row in rows:
        if not _norm(row[c_pay_period]).startswith("2025"):
            continue
        amount = _num(row[c_amount])
        name = channel_map.get(_norm(row[c_channel]), "其他")
        if name not in channel_totals:
            channel_totals[name] = 0.0
        channel_totals[name] += amount
    metrics.channel_total = round(sum(channel_totals.values()), 2)
    metrics.channels = [
        ChannelMetric(
            name=name,
            amount=round(amount, 2),
            share=round((amount / metrics.channel_total * 100.0) if metrics.channel_total else 0.0, 2),
        )
        for name, amount in sorted(channel_totals.items(), key=lambda item: item[1], reverse=True)
    ]

    # 4. Arrears detail, excluding summary rows
    headers, rows = _read_workbook(data_dir / DEFAULT_FILES["arrears"], 2)
    c_property = _column(headers, "房产全称")
    c_building = _column(headers, "楼栋")
    c_owner = _column(headers, "业X名称")
    c_total = _column(headers, "总欠款金额")
    c_fee = _column(headers, "物业服务费欠款")
    c_garbage = _column(headers, "代收代付生活垃圾处理费欠款")
    c_reason = _column(headers, "欠费原因")
    c_range = _column(headers, "物业服务费欠款区间")
    detail = [
        r for r in rows
        if _norm(r[c_property]) and _norm(r[c_building])
    ]
    metrics.arrears_total = round(sum(_num(r[c_total]) for r in detail), 2)
    metrics.arrears_fee = round(sum(_num(r[c_fee]) for r in detail), 2)
    metrics.arrears_garbage = round(sum(_num(r[c_garbage]) for r in detail), 2)
    reason_totals: dict[str, float] = {}
    for row in detail:
        reason = _norm(row[c_reason]) or "未填"
        reason_totals[reason] = reason_totals.get(reason, 0.0) + _num(row[c_total])
    metrics.arrears_reasons = [
        ReasonMetric(
            name=name,
            amount=round(amount, 2),
            share=round((amount / metrics.arrears_total * 100.0) if metrics.arrears_total else 0.0, 2),
        )
        for name, amount in sorted(reason_totals.items(), key=lambda item: item[1], reverse=True)
    ]
    top_rows = sorted(detail, key=lambda r: _num(r[c_total]), reverse=True)[:10]
    metrics.top_debtors = [
        DebtorMetric(
            property=_norm(r[c_property]),
            owner=_norm(r[c_owner]),
            amount=round(_num(r[c_total]), 2),
            reason=_norm(r[c_reason]) or "未填",
            period=_norm(r[c_range]),
        )
        for r in top_rows
    ]
    metrics.top10_total = round(sum(d.amount for d in metrics.top_debtors), 2)
    metrics.top10_share = round(
        (metrics.top10_total / metrics.arrears_total * 100.0) if metrics.arrears_total else 0.0,
        2,
    )
    metrics.top10_building_count = sum(
        1 for r in top_rows if _norm(r[c_building]).startswith("6")
    )
    metrics.top10_quality_count = sum(
        1 for r in top_rows
        if "房屋质量" in _norm(r[c_reason]) or "地产遗留" in _norm(r[c_reason])
    )

    # 5. Collection detail, excluding the grand-total row
    headers, rows = _read_workbook(data_dir / DEFAULT_FILES["collection"], 3)
    detail = [r for r in rows if _norm(r[1]) and _norm(r[2])]
    metrics.current_due = round(sum(_num(r[7]) for r in detail), 2)
    metrics.prior_due = round(sum(_num(r[8]) for r in detail), 2)
    metrics.current_paid = round(sum(_num(r[9]) for r in detail), 2)
    denom = metrics.current_due + metrics.prior_due
    metrics.current_unpaid = round(denom - metrics.current_paid, 2)
    metrics.comprehensive_rate = round(
        (metrics.current_paid / denom * 100.0) if denom else 0.0, 2
    )
    metrics.current_rate = round(
        (metrics.current_paid / metrics.current_due * 100.0) if metrics.current_due else 0.0,
        2,
    )

    building_acc: dict[str, list[float]] = {name: [0.0, 0.0, 0.0] for name in BUILDING_ORDER}
    for row in detail:
        name = _building_name(row[1], allow_plain=True)
        if name not in building_acc:
            continue
        building_acc[name][0] += _num(row[7])
        building_acc[name][1] += _num(row[8])
        building_acc[name][2] += _num(row[9])
    metrics.building_metrics = []
    for name in BUILDING_ORDER:
        due, prior, paid = building_acc[name]
        total = due + prior
        metrics.building_metrics.append(
            BuildingMetric(
                name=name,
                rate=round((paid / total * 100.0) if total else 0.0, 2),
                unpaid=round(total - paid, 2),
                due=round(total, 2),
                paid=round(paid, 2),
            )
        )
    metrics.highest_building = max(metrics.building_metrics, key=lambda b: b.rate)
    low_two = sorted(metrics.building_metrics, key=lambda b: b.rate)[:2]
    metrics.low_group_label = "/".join(b.name for b in low_two)
    metrics.building_56_unpaid = round(
        sum(b.unpaid for b in metrics.building_metrics if b.name in ("5栋", "6栋")), 2
    )
    metrics.building_56_share = round(
        (metrics.building_56_unpaid / metrics.current_unpaid * 100.0)
        if metrics.current_unpaid else 0.0,
        2,
    )

    for row in detail:
        ratio = _num(row[11])
        total = _num(row[7]) + _num(row[8])
        paid = _num(row[9])
        if ratio >= 0.999:
            metrics.full_count += 1
        elif ratio <= 0:
            metrics.zero_count += 1
            metrics.zero_unpaid += total - paid
        else:
            metrics.partial_count += 1
    metrics.zero_unpaid = round(metrics.zero_unpaid, 2)
    metrics.zero_share = round(
        (metrics.zero_unpaid / metrics.current_unpaid * 100.0)
        if metrics.current_unpaid else 0.0,
        2,
    )

    return metrics


def _iter_years(metrics: PropertyMetrics) -> Iterable[tuple[str, float]]:
    return metrics.annual_rates
