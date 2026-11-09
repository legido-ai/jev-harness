"""Per-action thresholds.

A confidence threshold is not one number for the whole system: the same model needs a
higher bar to approve a transfer than to file a newsletter. Thresholds therefore live
per action, and irreversible actions get a floor that policy cannot lower.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

__all__ = ["Policy", "PolicyError"]


class PolicyError(ValueError):
    pass


@dataclass
class Policy:
    """Threshold table for the actions a harness can take."""

    thresholds: Mapping[str, float] = field(default_factory=dict)
    default_threshold: float = 0.90
    review_floor: float = 0.50
    destructive: Iterable[str] = field(default_factory=tuple)
    destructive_threshold: float = 0.95

    def __post_init__(self) -> None:
        self.destructive = frozenset(self.destructive)
        for name, value in self.thresholds.items():
            self._check_range(name, value)
        self._check_range("default_threshold", self.default_threshold)
        self._check_range("review_floor", self.review_floor)
        self._check_range("destructive_threshold", self.destructive_threshold)
        if self.review_floor > self.destructive_threshold:
            raise PolicyError("review_floor cannot exceed destructive_threshold")
        for action in self.destructive:
            self.threshold_for(action)

    @staticmethod
    def _check_range(name: str, value: float) -> None:
        if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
            raise PolicyError(f"threshold for {name!r} must be within [0, 1], got {value!r}")

    def threshold_for(self, action: str) -> float:
        """Threshold that must be met before ``action`` may run unreviewed."""
        if action in self.destructive:
            configured = float(self.thresholds.get(action, self.destructive_threshold))
            return max(configured, self.destructive_threshold)
        if action in self.thresholds:
            return float(self.thresholds[action])
        return float(self.default_threshold)

    def may_act(self, action: str, confidence: float | None) -> bool:
        if confidence is None:
            return False
        return float(confidence) >= self.threshold_for(action)

    def needs_review(self, confidence: float | None) -> bool:
        if confidence is None:
            return True
        return float(confidence) < self.review_floor
