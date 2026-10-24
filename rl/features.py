"""Stable, anonymized feature construction for the contextual bandit."""

from __future__ import annotations

import math
from typing import Dict, List

import numpy as np

from .types import ActionCode, CaseState

REASON_CATEGORIES = [
    "hardship_or_refusal",
    "property_defect",
    "dispute",
    "service_dissatisfaction",
    "vacancy_discount",
    "facility_issue",
    "unreachable",
    "unknown",
]

LAST_ACTIONS = [
    ActionCode.REMINDER,
    ActionCode.ACCOUNT_REVIEW,
    ActionCode.PHONE_CALL,
    ActionCode.FORMAL_NOTICE,
    ActionCode.LEGAL_REVIEW,
]

FEATURE_NAMES = [
    "bias",#偏置项
    "aging_known",
    "aging_months_capped",
    "amount_log",
    *[f"reason_{name}" for name in REASON_CATEGORIES],
    "property_vacant",
    "payment_count_log",
    "average_delay_norm",
    "late_payment_rate",
    "recent_payment_count_log",
    "prior_contact_count_log",
    "days_since_contact_norm",
    *[f"last_{action.value}" for action in LAST_ACTIONS],
]


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return float(min(high, max(low, value)))


def context_features(case: CaseState) -> np.ndarray:
    aging_known = case.aging_days is not None
    aging_days = max(0, case.aging_days or 0)
    reason = case.reason_category if case.reason_category in REASON_CATEGORIES else "unknown"

    values: List[float] = [
        1.0,
        1.0 if aging_known else 0.0,
        _clip(aging_days / 91.0, 0.0, 4.0),
        math.log1p(max(0.0, case.amount)) / 10.0,
    ]
    values.extend(1.0 if reason == name else 0.0 for name in REASON_CATEGORIES)
    values.extend(
        [
            1.0 if "空置" in case.property_status else 0.0,
            math.log1p(case.historical_payment_count) / 10.0,
            _clip(case.average_payment_delay_days / 180.0, 0.0, 2.0),
            _clip(case.late_payment_rate),
            math.log1p(case.recent_payment_count) / 10.0,
            math.log1p(case.prior_contact_count) / 5.0,
            _clip((case.days_since_last_contact or 365) / 90.0, 0.0, 2.0),
        ]
    )
    values.extend(1.0 if case.last_action == action else 0.0 for action in LAST_ACTIONS)
    return np.asarray(values, dtype=float)


def context_dict(case: CaseState) -> Dict[str, float]:
    return {
        name: float(value)
        for name, value in zip(FEATURE_NAMES, context_features(case))
    }
