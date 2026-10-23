"""Evaluate the bandit against the current collection guide."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

import numpy as np

from .common import action_cost
from .config import load_config
from .heuristic import HeuristicPolicyService
from .loader import CollectionDataset
from .policy import CollectionPolicyService
from .response import CollectionResponseModel
from .safety import is_action_allowed
from .timeutils import business_today
from .types import (
    ActionCode,
    EvaluationReport,
    ProfileMetrics,
    Recommendation,
)


def _case_episode_seed(base_seed: int, episode: int, case_id: str) -> int:
    payload = f"{base_seed}|{episode}|{case_id}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


class CollectionPolicyEvaluator:
    def __init__(
        self,
        dataset: CollectionDataset,
        config: Optional[dict] = None,
    ):
        self.dataset = dataset
        self.config = config or load_config()

    @staticmethod
    def _actions_by_case(
        recommendations: Iterable[Recommendation],
    ) -> Dict[str, ActionCode]:
        return {item.case_id: item.action for item in recommendations}

    def _validate_plan(
        self,
        recommendations: List[Recommendation],
        budget: float,
    ) -> tuple[int, int]:
        by_id = {case.case_id: case for case in self.dataset.cases}
        unsafe = 0
        for item in recommendations:
            case = by_id[item.case_id]
            if not is_action_allowed(case, item.action, self.config):
                unsafe += 1
        spent = sum(action_cost(item.action, self.config) for item in recommendations)
        breaches = 1 if spent > budget + 1e-9 else 0
        return unsafe, breaches

    def compare(
        self,
        candidate: CollectionPolicyService,
        baseline: Optional[HeuristicPolicyService] = None,
        scenarios: Iterable[str] = ("conservative", "base", "optimistic"),
        episodes: int = 1000,
        seed: int = 42,
        budget_units: Optional[float] = None,
        horizon_days: int = 90,
    ) -> EvaluationReport:
        if episodes < 1:
            raise ValueError("episodes 必须大于 0")
        baseline = baseline or HeuristicPolicyService(self.config)
        budget = float(
            self.config.get("default_budget_units", 20)
            if budget_units is None
            else budget_units
        )
        reference = self.dataset.metadata.get("as_of")
        as_of = (
            datetime.fromisoformat(reference).date()
            if reference
            else business_today()
        )
        candidate_plan = candidate.recommend(
            self.dataset.cases, budget_units=budget, as_of=as_of
        )
        baseline_plan = baseline.recommend(
            self.dataset.cases, budget_units=budget, as_of=as_of
        )
        candidate_actions = self._actions_by_case(candidate_plan)
        baseline_actions = self._actions_by_case(baseline_plan)
        candidate_unsafe, candidate_breach = self._validate_plan(
            candidate_plan, budget
        )

        profile_metrics: List[ProfileMetrics] = []
        for profile in scenarios:
            response = CollectionResponseModel(
                candidate.artifact.response_model,
                self.config,
                profile=profile,
            )
            candidate_recoveries = np.zeros(episodes, dtype=float)
            baseline_recoveries = np.zeros(episodes, dtype=float)
            for episode in range(episodes):
                for case in self.dataset.cases:
                    case_seed = _case_episode_seed(seed, episode, case.case_id)
                    candidate_recoveries[episode] += response.sample_recovery(
                        case,
                        candidate_actions.get(case.case_id, ActionCode.NO_ACTION),
                        horizon_days=horizon_days,
                        seed=case_seed,
                    )
                    baseline_recoveries[episode] += response.sample_recovery(
                        case,
                        baseline_actions.get(case.case_id, ActionCode.NO_ACTION),
                        horizon_days=horizon_days,
                        seed=case_seed,
                    )

            differences = candidate_recoveries - baseline_recoveries
            rng = np.random.default_rng(seed + len(profile_metrics))
            bootstrap_indices = rng.integers(
                0, episodes, size=(1000, episodes)
            )
            bootstrap_means = differences[bootstrap_indices].mean(axis=1)
            ci_lower, ci_upper = np.quantile(bootstrap_means, [0.025, 0.975])
            candidate_mean = float(candidate_recoveries.mean())
            baseline_mean = float(baseline_recoveries.mean())
            passed = (
                candidate_mean >= baseline_mean
                and float(ci_lower) >= 0
                and candidate_unsafe == 0
                and candidate_breach == 0
            )
            counts: Dict[str, int] = {}
            for action in candidate_actions.values():
                counts[action.value] = counts.get(action.value, 0) + 1
            profile_metrics.append(
                ProfileMetrics(
                    profile=profile,
                    episodes=episodes,
                    baseline_mean_recovery=round(baseline_mean, 2),
                    candidate_mean_recovery=round(candidate_mean, 2),
                    mean_difference=round(float(differences.mean()), 2),
                    difference_ci_lower=round(float(ci_lower), 2),
                    difference_ci_upper=round(float(ci_upper), 2),
                    unsafe_action_count=candidate_unsafe,
                    budget_breach_count=candidate_breach,
                    candidate_action_counts=counts,
                    passed=passed,
                )
            )

        limitations = [
            "动作效果包含业务规则先验，历史数据主要校准回款基线。",
            "模拟通过不等于真实回款提升，正式启用前仍需受控试点。",
        ]
        if candidate.last_fallback_reason:
            limitations.append(f"本次评测发生安全回退：{candidate.last_fallback_reason}")
        return EvaluationReport(
            policy_version=candidate.artifact.version,
            baseline_version=baseline.version,
            episodes=episodes,
            seed=seed,
            horizon_days=horizon_days,
            generated_at=datetime.now(timezone.utc),
            profiles=profile_metrics,
            passed=all(profile.passed for profile in profile_metrics),
            limitations=limitations,
        )
