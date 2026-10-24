"""Discrete-time collection response model and Monte Carlo simulator."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np

from .types import ActionCode, CaseState, PaymentDistribution, ResponseModelParams


class CollectionResponseModel:
    """Estimate or sample recovery under a collection action.

    The model executes weekly hazard steps. Historical payment data supplies
    the baseline hazard, while action effects are explicit scenario priors.
    """

    def __init__(
        self,
        params: ResponseModelParams,
        config: Dict[str, Any],
        profile: str = "base",
    ):
        if profile not in params.effect_profiles:
            raise ValueError(f"未知效果场景: {profile}")
        if not params.weekly_hazards:
            raise ValueError("weekly_hazards 不能为空")
        self.params = params
        self.config = config
        self.profile = profile

    def _action_coefficient(self, action: ActionCode) -> float:
        return float(
            self.params.effect_profiles[self.profile].get(action.value, 1.0)
        )

    def _history_factor(self, case: CaseState) -> float:
        factor = 1.0
        if case.average_payment_delay_days >= 90:
            factor *= 0.8
        elif case.average_payment_delay_days >= 30:
            factor *= 0.9
        if case.recent_payment_count > 0:
            factor *= 1.05
        return factor

    def _weekly_hazard(
        self,
        case: CaseState,
        action: ActionCode,
        week: int,
    ) -> float:
        start = min(
            len(self.params.weekly_hazards) - 1,
            max(0, (case.aging_days or 0) // 7),
        )
        base = self.params.weekly_hazards[start]
        reason_factor = self.params.reason_factors.get(case.reason_category, 0.8)
        hazard = base * reason_factor * self._history_factor(case)

        review_days = int(
            self.config.get("actions", {})
            .get(action.value, {})
            .get("review_days", 7)
        )
        active_weeks = max(1, int(np.ceil(review_days / 7)))
        if week < active_weeks:
            hazard *= self._action_coefficient(action)
        return float(np.clip(hazard, 0.001, 0.95))

    def _review_days(self, action: ActionCode) -> int:
        return int(
            self.config.get("actions", {})
            .get(action.value, {})
            .get("review_days", 7)
        )

    def predict_distribution(
        self,
        case: CaseState,
        action: ActionCode,
        horizon_days: int = 90,
    ) -> PaymentDistribution:
        weeks = max(1, int(np.ceil(horizon_days / 7)))
        mean_fraction = float(np.mean(self.params.fraction_quantiles))
        survival = 1.0
        expected_fraction = 0.0
        any_payment = 0.0
        for week in range(weeks):
            hazard = self._weekly_hazard(case, action, week)
            expected_fraction += survival * hazard * mean_fraction
            any_payment += survival * hazard
            survival *= 1.0 - hazard
        return PaymentDistribution(
            expected_recovered_fraction=float(np.clip(expected_fraction, 0, 1)),
            probability_any_payment=float(np.clip(any_payment, 0, 1)),
            review_after_days=self._review_days(action),
        )

    def sample_recovery(
        self,
        case: CaseState,
        action: ActionCode,
        horizon_days: int = 90,
        seed: int = 0,
    ) -> float:
        """Sample cumulative recovery for one case/action pair."""

        rng = np.random.default_rng(seed)
        weeks = max(1, int(np.ceil(horizon_days / 7)))
        remaining = float(case.amount)
        recovered = 0.0
        fractions = np.asarray(self.params.fraction_quantiles, dtype=float)
        fractions = np.clip(fractions, 0.01, self.params.max_sample_fraction)

        for week in range(weeks):
            if remaining <= 0:
                break
            hazard = self._weekly_hazard(case, action, week)
            if rng.random() >= hazard:
                continue
            fraction = float(rng.choice(fractions))
            paid = min(remaining, remaining * fraction)
            recovered += paid
            remaining -= paid
            if paid <= 0:
                break
        return round(float(recovered), 2)
