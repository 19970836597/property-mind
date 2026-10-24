"""Existing collection guide expressed as a deterministic baseline policy."""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

import numpy as np

from .common import action_cost, recommendation_id, review_days
from .safety import eligible_actions, safety_reason_for
from .timeutils import business_today
from .types import ActionCode, CaseState, Recommendation


class HeuristicPolicyService:
    """Fallback and comparison policy based on priority_guide.md."""

    def __init__(self, config: Dict[str, object]):
        self.config = config
        self.version = "priority-guide-v1"

    def _score(self, case: CaseState, amount_percentile: float, max_contacts: int) -> float:
        if case.aging_days is None:
            bucket_index = 0
        elif case.aging_days <= 30:
            bucket_index = 1
        elif case.aging_days <= 90:
            bucket_index = 2
        elif case.aging_days <= 180:
            bucket_index = 3
        else:
            bucket_index = 4
        contact_share = case.prior_contact_count / max(1, max_contacts)
        return 40 * bucket_index / 4 + 40 * amount_percentile + 20 * contact_share

    def _preferred(self, case: CaseState, score: float) -> ActionCode:
        allowed = eligible_actions(case, self.config, include_no_action=False)
        if not allowed:
            return ActionCode.NO_ACTION
        if case.aging_days is None or case.is_dispute:
            return ActionCode.ACCOUNT_REVIEW
        if score >= 70:
            preferred = (
                ActionCode.LEGAL_REVIEW
                if (case.aging_days or 0) > 180
                else ActionCode.FORMAL_NOTICE
                if (case.aging_days or 0) > 90
                else ActionCode.PHONE_CALL
            )
        elif score >= 40:
            preferred = ActionCode.PHONE_CALL
        else:
            preferred = ActionCode.REMINDER
        if preferred in allowed:
            return preferred
        rank = {
            ActionCode.REMINDER: 1,
            ActionCode.PHONE_CALL: 2,
            ActionCode.FORMAL_NOTICE: 3,
            ActionCode.LEGAL_REVIEW: 4,
            ActionCode.ACCOUNT_REVIEW: 5,
        }
        return max(allowed, key=lambda action: rank.get(action, 0))

    def recommend(
        self,
        cases: List[CaseState],
        budget_units: Optional[float] = None,
        as_of: Optional[date] = None,
    ) -> List[Recommendation]:
        reference = as_of or business_today()
        budget = float(
            self.config.get("default_budget_units", 20)
            if budget_units is None
            else budget_units
        )
        if budget < 0:
            raise ValueError("budget_units 不能为负数")
        amounts = np.asarray([case.amount for case in cases], dtype=float)
        max_contacts = max((case.prior_contact_count for case in cases), default=0)
        options = []
        for index, case in enumerate(cases):
            percentile = (
                float(np.mean(amounts <= case.amount)) if amounts.size else 0.0
            )
            score = self._score(case, percentile, max_contacts)
            action = self._preferred(case, score)
            cost = action_cost(action, self.config)
            age_weight = 1.0 + min(4.0, max(0, case.aging_days or 0) / 365.0)
            priority = case.amount * age_weight / max(cost, 1e-9)
            options.append((priority, case.case_id, index, case, action, cost))

        options.sort(key=lambda item: (-item[0], item[1]))
        selected = {}
        spent = 0.0
        for _priority, case_id, index, case, action, cost in options:
            if action == ActionCode.NO_ACTION:
                continue
            if spent + cost > budget + 1e-9:
                continue
            selected[index] = action
            spent += cost

        recommendations: List[Recommendation] = []
        for index, case in enumerate(cases):
            action = selected.get(index, ActionCode.NO_ACTION)
            cost = action_cost(action, self.config)
            amount_share = (
                float(np.mean(amounts <= case.amount)) if amounts.size else 0.0
            )
            expected_fraction = min(
                0.9,
                0.05 + 0.15 * min(4, max(0, case.aging_days or 0) // 91),
            )
            recommendations.append(
                Recommendation(
                    recommendation_id=recommendation_id(
                        case.case_id, action, self.version, reference
                    ),
                    case_id=case.case_id,
                    action=action,
                    as_of=reference,
                    review_after_days=review_days(action, self.config),
                    expected_recovered_amount=round(
                        expected_fraction * case.amount, 2
                    ),
                    expected_recovery_fraction=round(expected_fraction, 4),
                    effort_cost=cost,
                    confidence=0.5,
                    rationale=[
                        f"现行规则金额分位 {amount_share:.0%}",
                        f"账龄档优先策略选择 {action.value}",
                    ],
                    safety_reason=safety_reason_for(case, action, self.config),
                    policy_version=self.version,
                )
            )
        recommendations.sort(
            key=lambda item: (
                item.action == ActionCode.NO_ACTION,
                -item.expected_recovered_amount,
                item.case_id,
            )
        )
        return recommendations
