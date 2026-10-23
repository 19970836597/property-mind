from __future__ import annotations

from datetime import date

import numpy as np

from rl.config import load_config
from rl.evaluator import CollectionPolicyEvaluator
from rl.heuristic import HeuristicPolicyService
from rl.loader import CollectionDataset, load_collection_dataset
from rl.policy import CollectionPolicyService
from rl.safety import is_action_allowed
from rl.storage import PolicyStore
from rl.trainer import CollectionPolicyTrainer
from rl.types import ActionCode


def mini_dataset():
    full = load_collection_dataset(as_of=date(2026, 10, 24))
    return CollectionDataset(
        cases=full.cases[:8],
        response_params=full.response_params,
        metadata=full.metadata,
    )


def test_loader_produces_anonymized_cases_without_customer_fields():
    dataset = load_collection_dataset(as_of=date(2026, 10, 24))
    assert len(dataset.cases) >= 40
    assert len(dataset.response_params.weekly_hazards) == 13
    assert all(case.case_id.startswith("case_") for case in dataset.cases)
    payload = dataset.cases[0].model_dump()
    assert "owner" not in payload
    assert "phone" not in payload
    assert "property_address" not in payload


def test_training_is_reproducible_and_round_trips(tmp_path):
    dataset = mini_dataset()
    trainer = CollectionPolicyTrainer(load_config())
    first = trainer.fit(dataset, seed=7, replicates=2)
    second = trainer.fit(dataset, seed=7, replicates=2)
    assert first.sample_counts == second.sample_counts
    for action in ActionCode:
        left = first.arms[action.value]
        right = second.arms[action.value]
        assert np.allclose(left.A, right.A)
        assert np.allclose(left.b, right.b)

    path = PolicyStore.save(first, tmp_path / "policy.json")
    loaded = PolicyStore.load(path)
    assert loaded.config_hash == first.config_hash
    text = path.read_text(encoding="utf-8")
    for sensitive in ("业主姓名", "身份证", "移动电话", "property_address"):
        assert sensitive not in text


def test_policy_respects_budget_and_safety():
    dataset = mini_dataset()
    artifact = CollectionPolicyTrainer(load_config()).fit(
        dataset, seed=9, replicates=2
    )
    service = CollectionPolicyService(artifact)
    recommendations = service.recommend(
        dataset.cases,
        budget_units=20,
        as_of=date(2026, 10, 24),
    )
    assert len(recommendations) == len(dataset.cases)
    spent = sum(item.effort_cost for item in recommendations)
    assert spent <= 20
    by_id = {case.case_id: case for case in dataset.cases}
    for item in recommendations:
        assert is_action_allowed(by_id[item.case_id], item.action, service.config)
        assert item.review_after_days in {7, 14, 30}


def test_evaluation_passes_with_safe_noninferiority_fallback():
    dataset = mini_dataset()
    artifact = CollectionPolicyTrainer(load_config()).fit(
        dataset, seed=11, replicates=2
    )
    candidate = CollectionPolicyService(artifact)
    evaluator = CollectionPolicyEvaluator(dataset)
    report = evaluator.compare(
        candidate,
        baseline=HeuristicPolicyService(load_config()),
        episodes=20,
        seed=11,
        budget_units=20,
    )
    assert report.passed
    assert all(profile.unsafe_action_count == 0 for profile in report.profiles)
    assert all(profile.budget_breach_count == 0 for profile in report.profiles)


def test_policy_falls_back_without_usable_artifact():
    dataset = mini_dataset()
    artifact = CollectionPolicyTrainer(load_config()).fit(
        dataset, seed=13, replicates=1
    )
    for arm in artifact.arms.values():
        artifact.sample_counts[arm.action.value] = 0
    service = CollectionPolicyService(artifact)
    recommendations = service.recommend(dataset.cases)
    assert service.last_fallback_reason
    assert all(
        item.policy_version == "priority-guide-v1"
        for item in recommendations
    )
