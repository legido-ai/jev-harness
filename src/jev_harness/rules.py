"""Layer zero: code rules.

The cheapest decision is the one that never reaches a model. Anything that can be
computed exactly belongs here: a regex, a permission check, an amount limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

__all__ = ["Rule"]


@dataclass(frozen=True)
class Rule:
    name: str
    action: str
    value: str
    when: Callable[[Any], bool]
    reason: str = ""

    def matches(self, state: Any) -> bool:
        try:
            return bool(self.when(state))
        except Exception:  # a broken rule must not take the service down
            return False
