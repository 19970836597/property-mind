"""Configuration loading and deterministic hashing for the collection policy."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config") / "collection_policy_v1.json"


@lru_cache(maxsize=4)
def _load_cached(path_text: str) -> Dict[str, Any]:
    path = Path(path_text)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"RL 配置必须是 JSON 对象: {path}")
    return data


def load_config(path: str | Path | None = None) -> Dict[str, Any]:
    """Load and validate the minimal shape used by the policy.

    A fresh dictionary is returned so callers cannot mutate the cached config.
    """

    config_path = Path(path or DEFAULT_CONFIG_PATH).resolve()
    data = _load_cached(str(config_path))
    copied = json.loads(json.dumps(data, ensure_ascii=False))
    required = {
        "feature_version",
        "alpha",
        "ridge",
        "default_budget_units",
        "actions",
        "effect_profiles",
        "reason_factors",
        "hazard_smoothing",
    }
    missing = sorted(required - set(copied))
    if missing:
        raise ValueError(f"RL 配置缺少字段: {', '.join(missing)}")
    return copied


def config_hash(config: Dict[str, Any]) -> str:
    payload = json.dumps(
        config, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
