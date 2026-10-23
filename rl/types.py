"""Typed public records used by the collection reinforcement-learning module."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ActionCode(str, Enum):
    NO_ACTION = "no_action"
    REMINDER = "reminder"
    ACCOUNT_REVIEW = "account_review"
    PHONE_CALL = "phone_call"
    FORMAL_NOTICE = "formal_notice"
    LEGAL_REVIEW = "legal_review"


class CaseState(BaseModel):
    """An anonymized collection case.

    The model deliberately does not store owner names, phone numbers, ID
    numbers, or the raw property address.
    """

    case_id: str
    aging_days: Optional[int] = None
    amount: float = Field(ge=0)
    reason_category: str = "unknown"
    property_status: str = "unknown"
    historical_payment_count: int = Field(default=0, ge=0)
    average_payment_delay_days: float = 0.0
    late_payment_rate: float = Field(default=0.0, ge=0, le=1)
    recent_payment_count: int = Field(default=0, ge=0)
    prior_contact_count: int = Field(default=0, ge=0)
    days_since_last_contact: Optional[int] = Field(default=None, ge=0)
    last_action: Optional[ActionCode] = None

    @property
    def is_dispute(self) -> bool:
        return self.reason_category == "dispute"


class Recommendation(BaseModel):
    recommendation_id: str
    case_id: str
    action: ActionCode
    as_of: date
    review_after_days: int
    expected_recovered_amount: float = Field(ge=0)
    expected_recovery_fraction: float = Field(ge=0, le=1)
    effort_cost: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    rationale: List[str] = Field(default_factory=list)
    safety_reason: str = ""
    policy_version: str


class Outcome(BaseModel):
    """A human-approved recommendation and its observed outcome."""

    recommendation_id: str
    accepted: bool
    recovered_amount: float = Field(default=0.0, ge=0)
    observation_days: int = Field(ge=0)
    observed_at: datetime
    notes: str = ""


class PaymentDistribution(BaseModel):
    expected_recovered_fraction: float = Field(ge=0, le=1)
    probability_any_payment: float = Field(ge=0, le=1)
    review_after_days: int = Field(ge=0)


class ResponseModelParams(BaseModel):
    weekly_hazards: List[float]
    fraction_quantiles: List[float]
    reason_factors: Dict[str, float]
    effect_profiles: Dict[str, Dict[str, float]]
    max_sample_fraction: float = 0.95


class ArmModel(BaseModel):
    action: ActionCode
    A: List[List[float]]
    b: List[float]


class PolicyArtifact(BaseModel):
    version: str
    feature_version: str
    feature_names: List[str]
    alpha: float
    ridge: float
    arms: Dict[str, ArmModel]
    sample_counts: Dict[str, int]
    response_model: ResponseModelParams
    config_hash: str
    data_fingerprint: str
    training_seed: int
    created_at: datetime
    model_note: str = (
        "动作效应包含基于业务规则的先验；真实效果需通过受控试点继续校准。"
    )


class ProfileMetrics(BaseModel):
    profile: str
    episodes: int
    baseline_mean_recovery: float
    candidate_mean_recovery: float
    mean_difference: float
    difference_ci_lower: float
    difference_ci_upper: float
    unsafe_action_count: int = 0
    budget_breach_count: int = 0
    candidate_action_counts: Dict[str, int] = Field(default_factory=dict)
    passed: bool


class EvaluationReport(BaseModel):
    policy_version: str
    baseline_version: str
    episodes: int
    seed: int
    horizon_days: int = 90
    generated_at: datetime
    profiles: List[ProfileMetrics]
    passed: bool
    limitations: List[str] = Field(default_factory=list)
