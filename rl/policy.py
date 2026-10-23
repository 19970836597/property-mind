"""Budget-aware LinUCB policy for property collection recommendations."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .allocation import BudgetOption, allocate_budget
from .common import action_cost, recommendation_id, review_days
from .config import config_hash, load_config
from .features import FEATURE_NAMES, context_features
from .heuristic import HeuristicPolicyService
from .response import CollectionResponseModel
from .safety import eligible_actions, safety_reason_for
from .storage import PolicyStore
from .timeutils import business_today
from .types import ActionCode, CaseState, PolicyArtifact, Recommendation


class CollectionPolicyService:
    """LinUCB scoring combined with a hard safety and budget layer."""

    def __init__(
        self,
        artifact: PolicyArtifact,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.artifact = artifact
        self.config = config or load_config()
        if config_hash(self.config) != artifact.config_hash:
            raise ValueError("策略文件与当前 RL 配置版本不一致")
        if artifact.feature_names != FEATURE_NAMES:
            raise ValueError("策略特征版本与当前代码不一致")
        self._inverse_cache: Dict[str, np.ndarray] = {}
        self.last_fallback_reason = ""

    @classmethod
    def from_path(
        cls,
        path: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> "CollectionPolicyService":
        return cls(PolicyStore.load(path), config=config)

    def _inverse(self, action: ActionCode, arm) -> np.ndarray:
        key = action.value
        if key not in self._inverse_cache:
            matrix = np.asarray(arm.A, dtype=float)
            self._inverse_cache[key] = np.linalg.inv(
                matrix + np.eye(matrix.shape[0]) * 1e-8
            )
        return self._inverse_cache[key]

    def _score(
        self,
        case: CaseState,
        action: ActionCode,
    ) -> Tuple[float, float, float]:
        arm = self.artifact.arms.get(action.value)
        if arm is None:
            return 0.0, 0.0, 0.0
        x = context_features(case)
        inverse = self._inverse(action, arm)
        theta = inverse @ np.asarray(arm.b, dtype=float)
        mean = float(np.clip(theta @ x, 0.0, 1.0))
        uncertainty = float(
            np.sqrt(max(0.0, x @ inverse @ x))
        )
        ucb = float(np.clip(mean + self.artifact.alpha * uncertainty, 0.0, 1.0))
        values = np.asarray(arm.b, dtype=float)
        confidence = float(
            np.clip(
                np.count_nonzero(values) / max(1, len(values)),
                0.0,
                1.0,
            )
        )
        return mean, ucb, confidence

    def _artifact_ready(self) -> bool:
        minimum = int(self.config.get("min_policy_samples_per_arm", 6))
        for arm in self.artifact.arms.values():
            if self.artifact.sample_counts.get(arm.action.value, 0) < minimum:
                return False
            if len(arm.A) != len(FEATURE_NAMES):
                return False
        return bool(self.artifact.arms)

    def _fallback(
        self,
        cases: List[CaseState],
        budget_units: float,
        as_of: date,
    ) -> List[Recommendation]:
        fallback = HeuristicPolicyService(self.config)
        return fallback.recommend(cases, budget_units=budget_units, as_of=as_of)

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
        if not cases:
            return []
        if not self._artifact_ready():
            self.last_fallback_reason = "策略样本不足，已回退到现行催缴规则"
            return self._fallback(cases, budget, reference)

        scores: Dict[Tuple[str, ActionCode], Tuple[float, float, float]] = {}
        options_by_case: List[List[BudgetOption]] = []
        ordered_cases: List[CaseState] = []
        for case in cases:
            ordered_cases.append(case)
            allowed = eligible_actions(case, self.config, include_no_action=True)
            no_action_score = self._score(case, ActionCode.NO_ACTION)
            scores[(case.case_id, ActionCode.NO_ACTION)] = no_action_score
            options: List[BudgetOption] = [
                (
                    ActionCode.NO_ACTION,
                    0,
                    no_action_score[1] * case.amount,
                )
            ]
            for action in allowed:
                if action == ActionCode.NO_ACTION:
                    continue
                mean, ucb, confidence = self._score(case, action)
                scores[(case.case_id, action)] = (mean, ucb, confidence)
                cost = action_cost(action, self.config)
                expected_amount = ucb * case.amount
                value = expected_amount
                if action == ActionCode.ACCOUNT_REVIEW:
                    # Safety review receives priority but still cannot breach
                    # the configured budget.
                    value += 1_000_000 + case.amount
                options.append((action, cost, value))
            options_by_case.append(options)

        selected_indices = allocate_budget(options_by_case, budget)
        recommendations: List[Recommendation] = []
        for index, case in enumerate(ordered_cases):
            action = selected_indices.get(index, ActionCode.NO_ACTION)
            mean, ucb, confidence = scores.get(
                (case.case_id, action),
                self._score(case, action),
            )
            expected_amount = ucb * case.amount if action != ActionCode.NO_ACTION else mean * case.amount
            cost = action_cost(action, self.config)
            rationale = self._rationale(case, action, mean, ucb, cost)
            recommendations.append(
                Recommendation(
                    recommendation_id=recommendation_id(
                        case.case_id, action, self.artifact.version, reference
                    ),
                    case_id=case.case_id,
                    action=action,
                    as_of=reference,
                    review_after_days=review_days(action, self.config),
                    expected_recovered_amount=round(expected_amount, 2),
                    expected_recovery_fraction=round(mean, 4),
                    effort_cost=cost,
                    confidence=round(confidence, 4),
                    rationale=rationale,
                    safety_reason=safety_reason_for(case, action, self.config),
                    policy_version=self.artifact.version,
                )
            )
        recommendations.sort(
            key=lambda item: (
                item.action == ActionCode.NO_ACTION,
                -item.expected_recovered_amount,
                item.case_id,
            )
        )
        if not self._noninferior_to_heuristic(
            ordered_cases, recommendations, budget, reference
        ):
            # The learned model may only override the manual guide when it is
            # non-inferior under every declared response scenario.
            self.last_fallback_reason = (
                "学习策略未通过三场景非劣性检查，已回退到现行催缴规则"
            )
            return HeuristicPolicyService(self.config).recommend(
                cases, budget_units=budget, as_of=reference
            )
        self.last_fallback_reason = ""
        return recommendations

    def _noninferior_to_heuristic(
        self,
        cases: List[CaseState],
        candidate: List[Recommendation],
        budget: float,
        as_of: date,
    ) -> bool:
        baseline = HeuristicPolicyService(self.config).recommend(
            cases, budget_units=budget, as_of=as_of
        )
        by_id = {case.case_id: case for case in cases}
        candidate_actions = {item.case_id: item.action for item in candidate}
        baseline_actions = {item.case_id: item.action for item in baseline}
        for profile in self.artifact.response_model.effect_profiles:
            response = CollectionResponseModel(
                self.artifact.response_model,
                self.config,
                profile=profile,
            )
            candidate_value = sum(
                response.predict_distribution(
                    by_id[case_id], candidate_actions[case_id]
                ).expected_recovered_fraction
                * by_id[case_id].amount
                for case_id in by_id
            )
            baseline_value = sum(
                response.predict_distribution(
                    by_id[case_id], baseline_actions[case_id]
                ).expected_recovered_fraction
                * by_id[case_id].amount
                for case_id in by_id
            )
            if candidate_value + 1e-9 < baseline_value:
                return False
        return True

    @staticmethod
    def _rationale(
        case: CaseState,
        action: ActionCode,
        mean: float,
        ucb: float,
        cost: float,
    ) -> List[str]:
        aging = "未知" if case.aging_days is None else f"{case.aging_days}天"
        return [
            f"账龄 {aging}，欠款 {case.amount:.2f} 元",
            f"欠费类别 {case.reason_category}",
            f"预测回款比例 {mean:.1%}，决策上界 {ucb:.1%}，努力成本 {cost:g}",
            f"建议动作 {action.value}",
        ]
