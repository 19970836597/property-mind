from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from rl.allocation import allocate_budget
from rl.config import load_config
from rl.features import FEATURE_NAMES, context_features
from rl.response import CollectionResponseModel
from rl.safety import eligible_actions, is_action_allowed
from rl.storage import OutcomeStore
from rl.types import (
    ActionCode,
    CaseState,
    Outcome,
    ResponseModelParams,
)


def make_case(**updates) -> CaseState:
    data = {
        "case_id": "case_test",
        "aging_days": 120,
        "amount": 1000.0,
        "reason_category": "hardship_or_refusal",
        "property_status": "occupied",
        "historical_payment_count": 3,
        "average_payment_delay_days": 20,
        "late_payment_rate": 0.5,
        "recent_payment_count": 1,
        "prior_contact_count": 1,
    }
    data.update(updates)
    return CaseState(**data)


def response_params() -> ResponseModelParams:
    config = load_config()
    return ResponseModelParams(
        weekly_hazards=[0.03] * 13,
        fraction_quantiles=[0.5, 0.75, 1.0],
        reason_factors={
            key: float(value)
            for key, value in config["reason_factors"].items()
        },
        effect_profiles={
            name: {
                key: float(value)
                for key, value in profile["multipliers"].items()
            }
            for name, profile in config["effect_profiles"].items()
        },
    )


def test_features_are_stable_and_bounded():
    case = make_case()
    first = context_features(case)
    second = context_features(case.model_copy())
    assert first.shape == (len(FEATURE_NAMES),)
    assert np.array_equal(first, second)
    assert np.isfinite(first).all()
    assert first[0] == 1.0


def test_safety_forces_review_for_dispute_and_unknown_aging():
    config = load_config()
    dispute = make_case(aging_days=240, reason_category="dispute")
    unknown = make_case(aging_days=None)
    assert eligible_actions(dispute, config, include_no_action=False) == [
        ActionCode.ACCOUNT_REVIEW
    ]
    assert eligible_actions(unknown, config, include_no_action=False) == [
        ActionCode.ACCOUNT_REVIEW
    ]
    assert not is_action_allowed(
        make_case(aging_days=60), ActionCode.LEGAL_REVIEW, config
    )
    assert not is_action_allowed(
        make_case(aging_days=240), ActionCode.REMINDER, config
    )


def test_contact_cooldown_blocks_repeated_action():
    config = load_config()
    case = make_case(
        aging_days=60,
        prior_contact_count=1,
        days_since_last_contact=3,
        last_action=ActionCode.REMINDER,
    )
    actions = eligible_actions(case, config, include_no_action=False)
    assert actions == []


def test_response_profile_is_ordered_and_sampling_is_reproducible():
    case = make_case()
    params = response_params()
    conservative = CollectionResponseModel(params, load_config(), "conservative")
    base = CollectionResponseModel(params, load_config(), "base")
    optimistic = CollectionResponseModel(params, load_config(), "optimistic")
    values = [
        model.predict_distribution(case, ActionCode.FORMAL_NOTICE)
        .expected_recovered_fraction
        for model in (conservative, base, optimistic)
    ]
    assert values[0] < values[1] < values[2]
    first = base.sample_recovery(case, ActionCode.PHONE_CALL, seed=101)
    second = base.sample_recovery(case, ActionCode.PHONE_CALL, seed=101)
    assert first == second
    assert 0 <= first <= case.amount


def test_budget_allocator_respects_capacity_and_maximizes_value():
    options = [
        [
            (ActionCode.NO_ACTION, 0, 0.0),
            (ActionCode.REMINDER, 1, 4.0),
            (ActionCode.PHONE_CALL, 2, 9.0),
        ],
        [
            (ActionCode.NO_ACTION, 0, 0.0),
            (ActionCode.FORMAL_NOTICE, 2, 8.0),
        ],
    ]
    selected = allocate_budget(options, 3)
    assert selected[0] == ActionCode.REMINDER
    assert selected[1] == ActionCode.FORMAL_NOTICE
    selected_cost = sum(
        options[index][action_index][1]
        for index, action_index in enumerate(
            _find_indices(options, selected)
        )
    )
    assert selected_cost <= 3


def _find_indices(options, selected):
    indices = []
    for index, choices in enumerate(options):
        target = selected.get(index, ActionCode.NO_ACTION)
        indices.append(next(i for i, choice in enumerate(choices) if choice[0] == target))
    return indices


def test_outcome_store_requires_valid_anonymized_records(tmp_path):
    store = OutcomeStore(tmp_path / "outcomes.jsonl")
    outcome = Outcome(
        recommendation_id="rec_test",
        accepted=True,
        recovered_amount=120.0,
        observation_days=7,
        observed_at=datetime.now(timezone.utc),
    )
    store.append(outcome)
    assert store.load() == [outcome]
    assert not store.ready_for_retraining(minimum=2)
