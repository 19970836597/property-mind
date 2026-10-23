"""Shared action metadata helpers."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Dict

from .types import ActionCode


def action_cost(action: ActionCode, config: Dict[str, Any]) -> float:
    return float(
        config.get("actions", {}).get(action.value, {}).get("cost", 0)
    )


def review_days(action: ActionCode, config: Dict[str, Any]) -> int:
    return int(
        config.get("actions", {}).get(action.value, {}).get("review_days", 7)
    )


def recommendation_id(
    case_id: str,
    action: ActionCode,
    policy_version: str,
    as_of: date,
) -> str:
    payload = (
        f"{case_id}|{action.value}|{policy_version}|{as_of.isoformat()}"
    ).encode("utf-8")
    return "rec_" + hashlib.sha256(payload).hexdigest()[:20]
