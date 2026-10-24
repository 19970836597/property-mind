"""Policy artifact and anonymized outcome persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, List

from .types import Outcome, PolicyArtifact


class PolicyStore:
    @staticmethod
    def save(artifact: PolicyArtifact, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            artifact.model_dump_json(indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return target

    @staticmethod
    def load(path: str | Path) -> PolicyArtifact:
        return PolicyArtifact.model_validate_json(
            Path(path).read_text(encoding="utf-8")
        )


class OutcomeStore:
    """Append-only JSONL store for human-approved outcomes.

    No raw owner or property data is accepted by the schema.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, outcome: Outcome) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(outcome.model_dump_json() + "\n")

    def load(self) -> List[Outcome]:
        if not self.path.exists():
            return []
        outcomes: List[Outcome] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if text:
                    outcomes.append(Outcome.model_validate_json(text))
        return outcomes

    def ready_for_retraining(self, minimum: int = 20) -> bool:
        valid = [
            outcome
            for outcome in self.load()
            if outcome.accepted
            and outcome.observation_days > 0
            and outcome.recovered_amount >= 0
        ]
        return len(valid) >= minimum
