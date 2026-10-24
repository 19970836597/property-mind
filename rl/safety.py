"""Business safety constraints for collection action candidates."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .types import ActionCode, CaseState


def classify_reason(text: str, keywords: Dict[str, List[str]]) -> str:
    normalized = str(text or "").strip()
    for category, words in keywords.items():
        if any(word in normalized for word in words):
            return category
    return "unknown"


def _cooldown_blocked(case: CaseState, action: ActionCode, config: Dict[str, Any]) -> bool:
    days = case.days_since_last_contact
    if days is None or action == ActionCode.NO_ACTION:
        return False
    base_cooldown = int(config.get("baseline_contact_cooldown_days", 7))
    if days < base_cooldown:
        return True
    action_cooldown = int(
        config.get("actions", {}).get(action.value, {}).get("review_days", 7)
    )
    return case.last_action == action and days < action_cooldown


def eligible_actions(
    case: CaseState,
    config: Dict[str, Any],
    include_no_action: bool = True,
) -> List[ActionCode]:
    """Return legal actions for a case, before the global budget filter."""

    if case.amount <= 0:
        return [ActionCode.NO_ACTION]

    if case.aging_days is None or case.is_dispute:
        base = [ActionCode.ACCOUNT_REVIEW]
    elif case.aging_days <= 90:
        base = [ActionCode.REMINDER, ActionCode.PHONE_CALL]
    elif case.aging_days <= 180:
        base = [ActionCode.PHONE_CALL, ActionCode.FORMAL_NOTICE]
    else:
        base = [
            ActionCode.PHONE_CALL,
            ActionCode.FORMAL_NOTICE,
            ActionCode.LEGAL_REVIEW,
        ]

    allowed = [
        action
        for action in base
        if not _cooldown_blocked(case, action, config)
    ]
    if include_no_action:
        allowed.insert(0, ActionCode.NO_ACTION)
    return allowed


def is_action_allowed(case: CaseState, action: ActionCode, config: Dict[str, Any]) -> bool:
    return action in eligible_actions(case, config, include_no_action=True)


def safety_reason_for(case: CaseState, action: ActionCode, config: Dict[str, Any]) -> str:
    if case.aging_days is None:
        return "账龄未知，强制人工核账"
    if case.is_dispute:
        return "存在费用争议，强制人工核账"
    if action == ActionCode.LEGAL_REVIEW and (case.aging_days or 0) <= 180:
        return "账龄不足 180 天，禁止法务评审"
    if not is_action_allowed(case, action, config):
        return "动作不满足账龄、升级或冷却规则"
    return "通过安全校验"


def action_set(cases: Iterable[CaseState], config: Dict[str, Any]) -> set[ActionCode]:
    result: set[ActionCode] = set()
    for case in cases:
        result.update(eligible_actions(case, config, include_no_action=True))
    return result
