"""Load property collection data into anonymized RL cases."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from core.paths import DATA_COMMON_DIR, PROJECT_ROOT

from .config import load_config
from .safety import classify_reason
from .timeutils import business_today
from .types import CaseState, ResponseModelParams

_INTERVAL_END = re.compile(
    r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*[~～]\s*"
    r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"
)


@dataclass
class CollectionDataset:
    cases: List[CaseState]
    response_params: ResponseModelParams
    metadata: Dict[str, Any] = field(default_factory=dict)


def _col(df: pd.DataFrame, *keywords: str, exclude: Sequence[str] = ()) -> Optional[str]:
    for name in df.columns:
        normalized = str(name).replace(" ", "")
        if any(item in normalized for item in exclude):
            continue
        if all(keyword in normalized for keyword in keywords):
            return name
    return None


def _read_arrears(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path, header=None, nrows=3)
    header_row = 1 if raw.iloc[0].notna().sum() <= 2 else 0
    frame = pd.read_excel(path, header=header_row)
    frame.columns = [str(name).strip() for name in frame.columns]
    return frame


def _parse_interval_end(value: Any) -> Optional[date]:
    if value is None or pd.isna(value):
        return None
    match = _INTERVAL_END.search(str(value))
    if match:
        try:
            return date(
                int(match.group(4)),
                int(match.group(5)),
                int(match.group(6)),
            )
        except ValueError:
            return None
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", str(value))
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _find_data_file(data_dir: Path, prefix: str) -> Path:
    candidates = sorted(
        path for path in data_dir.glob(f"{prefix}*.xlsx") if not path.name.startswith("~$")
    )
    if not candidates:
        raise FileNotFoundError(f"{data_dir} 下未找到以 {prefix} 开头的 xlsx 文件")
    return candidates[0]


def _normalized_property(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _anonymous_id(property_key: str, project: str) -> str:
    payload = f"{project}|{property_key}".encode("utf-8")
    return "case_" + hashlib.sha256(payload).hexdigest()[:16]


def _fraction_quantiles(ratios: pd.Series) -> List[float]:
    clean = pd.to_numeric(ratios, errors="coerce")
    clean = clean[(clean >= 0) & (clean <= 1)].dropna()
    if clean.empty or (clean < 0.999).mean() < 0.02:
        # The source file usually records full payments. Keep partial recovery
        # possible in the simulator but label it as an assumption in policy data.
        return [0.4, 0.6, 0.8, 1.0]
    quantiles = clean.quantile([0.1, 0.25, 0.5, 0.75, 0.9]).tolist()
    return [round(float(np.clip(value, 0.01, 1.0)), 4) for value in quantiles]


def _estimate_weekly_hazards(
    receivables: pd.DataFrame,
    payments: pd.DataFrame,
    as_of: date,
    config: Dict[str, Any],
) -> List[float]:
    rec_id = _col(receivables, "应收编号")
    due_col = _col(receivables, "应缴费日期")
    pay_id = _col(payments, "应收编号")
    paid_at_col = _col(payments, "收款时间")
    if not all((rec_id, due_col, pay_id, paid_at_col)):
        return [
            0.07,
            0.055,
            0.05,
            0.045,
            0.04,
            0.035,
            0.032,
            0.03,
            0.028,
            0.026,
            0.024,
            0.022,
            0.02,
        ]

    due = receivables[[rec_id, due_col]].copy()
    due = due.rename(columns={rec_id: "receivable_id", due_col: "due_at"})
    due["due_at"] = pd.to_datetime(due["due_at"], errors="coerce")
    due = due.dropna(subset=["due_at"]).drop_duplicates("receivable_id")

    pay = payments[[pay_id, paid_at_col]].copy()
    pay = pay.rename(columns={pay_id: "receivable_id", paid_at_col: "paid_at"})
    pay["paid_at"] = pd.to_datetime(pay["paid_at"], errors="coerce")
    pay = pay.dropna(subset=["paid_at"]).sort_values("paid_at")
    first_pay = pay.groupby("receivable_id", as_index=False).first()

    joined = due.merge(first_pay, on="receivable_id", how="left")
    if joined.empty:
        return [0.05] * 13

    paid = joined["paid_at"].notna()
    delay = (joined["paid_at"] - joined["due_at"]).dt.days
    # Payments beyond the 90-day simulation horizon are treated as censored,
    # not as events in the final week. Otherwise every old payment is forced
    # into week 12 and creates an artificial spike in the hazard curve.
    event = paid & (delay.fillna(9999) <= 90)
    event_week = np.floor(np.maximum(delay.fillna(0), 0) / 7).astype(int)
    event_week = np.clip(event_week, 0, 12)
    censor_week = np.floor(
        np.maximum((pd.Timestamp(as_of) - joined["due_at"]).dt.days, 0) / 7
    ).astype(int)
    censor_week = np.clip(censor_week, 0, 12)
    censor_week = np.where(paid & ~event, 12, censor_week)

    smoothing = config.get("hazard_smoothing", {})
    smooth_events = float(smoothing.get("events", 1.0))
    smooth_risk = float(smoothing.get("at_risk", 20.0))
    hazards: List[float] = []
    for week in range(13):
        at_risk = np.where(event, event_week >= week, censor_week >= week)
        events = np.where(event, event_week == week, False)
        risk_count = int(at_risk.sum())
        event_count = int(events.sum())
        hazard = (event_count + smooth_events) / (risk_count + smooth_risk)
        hazards.append(round(float(np.clip(hazard, 0.001, 0.8)), 6))
    return hazards


def _property_history(
    receivables: pd.DataFrame,
    payments: pd.DataFrame,
    as_of: date,
) -> Dict[str, Dict[str, float]]:
    pay_property = _col(payments, "房产")
    pay_id = _col(payments, "应收编号")
    paid_at = _col(payments, "收款时间")
    rec_id = _col(receivables, "应收编号")
    due_at = _col(receivables, "应缴费日期")
    if not all((pay_property, pay_id, paid_at, rec_id, due_at)):
        return {}

    due = receivables[[rec_id, due_at]].rename(
        columns={rec_id: "receivable_id", due_at: "due_at"}
    )
    due["due_at"] = pd.to_datetime(due["due_at"], errors="coerce")
    due = due.dropna(subset=["due_at"]).drop_duplicates("receivable_id")

    frame = payments[[pay_property, pay_id, paid_at]].rename(
        columns={
            pay_property: "property",
            pay_id: "receivable_id",
            paid_at: "paid_at",
        }
    )
    frame["property"] = frame["property"].map(_normalized_property)
    frame["paid_at"] = pd.to_datetime(frame["paid_at"], errors="coerce")
    frame = frame.dropna(subset=["paid_at"])
    frame = frame.merge(due, on="receivable_id", how="left")
    frame["delay"] = (frame["paid_at"] - frame["due_at"]).dt.days

    result: Dict[str, Dict[str, float]] = {}
    cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=180)
    for property_key, group in frame.groupby("property"):
        delays = pd.to_numeric(group["delay"], errors="coerce").dropna()
        result[str(property_key)] = {
            "payment_count": float(len(group)),
            "avg_delay": float(delays.mean()) if not delays.empty else 0.0,
            "late_rate": float((delays > 0).mean()) if not delays.empty else 0.0,
            "recent_payment_count": float((group["paid_at"] >= cutoff).sum()),
        }
    return result


def _data_fingerprint(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.name):
        stat = path.stat()
        digest.update(path.name.encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return digest.hexdigest()


def load_collection_dataset(
    data_dir: str | Path | None = None,
    *,
    arrears_path: str | Path | None = None,
    payment_path: str | Path | None = None,
    receivable_path: str | Path | None = None,
    as_of: date | None = None,
    config: Dict[str, Any] | None = None,
) -> CollectionDataset:
    """Load local Excel files and return only anonymized model inputs."""

    root = Path(data_dir or DATA_COMMON_DIR)
    arrears_file = Path(arrears_path) if arrears_path else _find_data_file(root, "4")
    payment_file = Path(payment_path) if payment_path else _find_data_file(root, "3")
    receivable_file = (
        Path(receivable_path) if receivable_path else _find_data_file(root, "2")
    )
    reference_date = as_of or business_today()
    cfg = config or load_config()

    arrears = _read_arrears(arrears_file)
    payments = pd.read_excel(payment_file)
    receivables = pd.read_excel(receivable_file)

    property_col = _col(arrears, "房产全称") or _col(arrears, "房产")
    amount_col = _col(arrears, "总欠款") or _col(arrears, "欠款")
    interval_col = (
        _col(arrears, "物业服务费", "区间")
        or _col(arrears, "欠款区间")
        or _col(arrears, "物业服务费")
    )
    reason_col = _col(arrears, "欠费原因") or _col(arrears, "原因")
    status_col = _col(arrears, "房产状态")
    project_col = _col(arrears, "分期")
    if property_col is None or amount_col is None:
        raise ValueError("欠款明细缺少房产全称或总欠款金额列")

    arrears = arrears[
        arrears[property_col].notna()
        & (arrears[property_col].astype(str).str.strip() != "")
        & arrears[amount_col].notna()
    ].copy()
    arrears["_property"] = arrears[property_col].map(_normalized_property)
    arrears["_amount"] = pd.to_numeric(arrears[amount_col], errors="coerce").fillna(0)
    arrears["_reason"] = (
        arrears[reason_col].fillna("").astype(str) if reason_col else ""
    )
    arrears["_interval"] = (
        arrears[interval_col].fillna("").astype(str) if interval_col else ""
    )
    arrears["_status"] = (
        arrears[status_col].fillna("").astype(str) if status_col else ""
    )
    arrears["_project"] = (
        arrears[project_col].fillna("").astype(str) if project_col else "物业项目"
    )

    history = _property_history(receivables, payments, reference_date)
    cases: List[CaseState] = []
    for property_key, group in arrears.groupby("_property", sort=False):
        amount = float(group["_amount"].sum())
        if amount <= 0:
            continue
        end_dates = [_parse_interval_end(value) for value in group["_interval"]]
        valid_dates = [value for value in end_dates if value is not None]
        aging_days = (
            max(0, (reference_date - min(valid_dates)).days) if valid_dates else None
        )
        reasons = [
            str(value).strip()
            for value in group["_reason"].tolist()
            if str(value).strip()
        ]
        reason_text = max(reasons, key=reasons.count) if reasons else ""
        project = str(group["_project"].iloc[0]).strip()
        property_stats = history.get(property_key, {})
        cases.append(
            CaseState(
                case_id=_anonymous_id(property_key, project),
                aging_days=aging_days,
                amount=round(amount, 2),
                reason_category=classify_reason(
                    reason_text, cfg.get("reason_keywords", {})
                ),
                property_status=(
                    "vacant"
                    if "空置" in str(group["_status"].iloc[0])
                    else "occupied"
                ),
                historical_payment_count=int(
                    property_stats.get("payment_count", 0)
                ),
                average_payment_delay_days=float(
                    property_stats.get("avg_delay", 0.0)
                ),
                late_payment_rate=float(
                    np.clip(property_stats.get("late_rate", 0.0), 0, 1)
                ),
                recent_payment_count=int(
                    property_stats.get("recent_payment_count", 0)
                ),
            )
        )

    if not cases:
        raise ValueError("欠款明细没有产生有效案例")

    hazards = _estimate_weekly_hazards(
        receivables, payments, reference_date, cfg
    )
    ratio_col = _col(payments, "当前账面金额") or _col(payments, "原金额")
    original_col = _col(payments, "原金额")
    if ratio_col and original_col and ratio_col != original_col:
        ratios = pd.to_numeric(payments[ratio_col], errors="coerce") / pd.to_numeric(
            payments[original_col], errors="coerce"
        )
    else:
        ratios = pd.Series(dtype=float)

    response_params = ResponseModelParams(
        weekly_hazards=hazards,
        fraction_quantiles=_fraction_quantiles(ratios),
        reason_factors={
            key: float(value) for key, value in cfg.get("reason_factors", {}).items()
        },
        effect_profiles={
            name: {
                key: float(value)
                for key, value in profile.get("multipliers", {}).items()
            }
            for name, profile in cfg.get("effect_profiles", {}).items()
        },
        max_sample_fraction=float(cfg.get("max_sample_fraction", 0.95)),
    )
    metadata = {
        "as_of": reference_date.isoformat(),
        "source_files": [
            arrears_file.name,
            payment_file.name,
            receivable_file.name,
        ],
        "data_fingerprint": _data_fingerprint(
            [arrears_file, payment_file, receivable_file]
        ),
        "case_count": len(cases),
        "project_root": str(PROJECT_ROOT),
        "contains_pii": False,
    }
    return CollectionDataset(
        cases=cases,
        response_params=response_params,
        metadata=metadata,
    )
