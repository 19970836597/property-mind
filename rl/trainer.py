"""Train a budget-aware LinUCB policy against the collection simulator"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from .config import config_hash, load_config
from .features import FEATURE_NAMES, context_features
from .loader import CollectionDataset
from .response import CollectionResponseModel
from .safety import eligible_actions
from .types import ActionCode, ArmModel, CaseState, PolicyArtifact


def _sample_seed(
    base_seed: int,
    profile: str,
    case_id: str,
    action: ActionCode,
    replicate: int,
) -> int:
    payload = (
        f"{base_seed}|{profile}|{case_id}|{action.value}|{replicate}"
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


class CollectionPolicyTrainer:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or load_config()

    def fit(
        self,
        dataset: CollectionDataset,
        profiles: Iterable[str] = ("conservative", "base", "optimistic"),
        seed: int = 42,
        replicates: int = 8,
        horizon_days: int = 90,
    ) -> PolicyArtifact:
        profiles = list(profiles)
        if not profiles:
            raise ValueError("至少需要一个效果场景")
        if replicates < 1:
            raise ValueError("replicates 必须大于 0")

        dimension = len(FEATURE_NAMES)
        ridge = float(self.config.get("ridge", 1.0))
        matrices = {
            action.value: (
                np.eye(dimension, dtype=float) * ridge,
                np.zeros(dimension, dtype=float),
            )
            for action in ActionCode
        }
        sample_counts = {action.value: 0 for action in ActionCode}

        training_cases = self._augment_cases(dataset.cases)
        for profile in profiles:
            if profile not in dataset.response_params.effect_profiles:
                raise ValueError(f"数据中不存在效果场景: {profile}")
            response = CollectionResponseModel(
                dataset.response_params, self.config, profile=profile
            )
            for case in training_cases:
                allowed = eligible_actions(
                    case, self.config, include_no_action=True
                )
                x = context_features(case)
                for action in allowed:
                    for replicate in range(replicates):
                        sample_seed = _sample_seed(
                            seed, profile, case.case_id, action, replicate
                        )
                        recovered = response.sample_recovery(
                            case,
                            action,
                            horizon_days=horizon_days,
                            seed=sample_seed,
                        )
                        reward = recovered / max(case.amount, 1e-9)
                        reward = float(np.clip(reward, 0.0, 1.0))
                        A, b = matrices[action.value]
                        A += np.outer(x, x)
                        b += reward * x
                        sample_counts[action.value] += 1

        arms = {
            action.value: ArmModel(
                action=action,
                A=matrices[action.value][0].round(10).tolist(),
                b=matrices[action.value][1].round(10).tolist(),
            )
            for action in ActionCode
        }
        created_at = datetime.now(timezone.utc)
        version = f"collection-linucb-v1-{created_at:%Y%m%dT%H%M%SZ}"
        return PolicyArtifact(
            version=version,
            feature_version=str(self.config["feature_version"]),
            feature_names=list(FEATURE_NAMES),
            alpha=float(self.config.get("alpha", 0.5)),
            ridge=ridge,
            arms=arms,
            sample_counts=sample_counts,
            response_model=dataset.response_params,
            config_hash=config_hash(self.config),
            data_fingerprint=str(dataset.metadata.get("data_fingerprint", "")),
            training_seed=seed,
            created_at=created_at,
        )

    @staticmethod
    def _augment_cases(cases: List[CaseState]) -> List[CaseState]:
        """Add simulated age states so a shared policy covers short-age users.

        The current snapshot contains only old debt. Without this augmentation
        the reminder arm receives no observations and a shared artifact could
        not serve another project with newer arrears.
        """

        augmented = list(cases)
        for case in cases:
            for age in (30, 60, 120, 240):
                augmented.append(
                    case.model_copy(
                        update={
                            "case_id": f"{case.case_id}_cal_{age}",
                            "aging_days": age,
                            "prior_contact_count": 0,
                            "days_since_last_contact": None,
                            "last_action": None,
                        }
                    )
                )
        return augmented
