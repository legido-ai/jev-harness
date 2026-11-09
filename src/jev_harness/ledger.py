"""Append-only decision ledger.

Every decision writes one JSON line: which questions (and which *version* of them), the
full probability distribution, the threshold, which layer answered, latency, tokens and
cost. Later, an outcome can be attached with :meth:`Ledger.payoff`.

That log is three things at once: the audit trail, the cost report, and - once outcomes
accumulate - the labelled evaluation set for tuning thresholds.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

__all__ = ["Ledger"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    """JSONL ledger. ``path=None`` keeps records in memory (tests, one-shot runs)."""

    def __init__(self, path: str | Path | None = None, preview_chars: int = 280) -> None:
        self.path = Path(path) if path else None
        self.preview_chars = preview_chars
        self._records: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- write ------------------------------------------------------------
    def record(
        self,
        *,
        decision: Any,
        state: Any,
        questions: Any,
        shadow: bool = False,
        meta: dict[str, Any] | None = None,
    ) -> str:
        state_text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, sort_keys=True)
        entry = {
            "id": uuid.uuid4().hex[:16],
            "ts": _now(),
            "kind": "decision",
            "question_set": questions.name,
            "question_set_version": questions.version,
            "questions": list(questions.with_companions().questions),
            "state_sha256": hashlib.sha256(state_text.encode("utf-8")).hexdigest()[:32],
            "state_chars": len(state_text),
            "state_preview": state_text[: self.preview_chars],
            "model": decision.model,
            "shadow": shadow,
            "action": decision.action,
            "value": decision.value,
            "confidence": decision.confidence,
            "threshold": decision.threshold,
            "layer": decision.layer,
            "escalated": decision.escalated,
            "authoritative": decision.authoritative,
            "may_act": decision.may_act,
            "reasons": list(decision.reasons),
            "answers": {qid: a.to_dict() for qid, a in decision.answers.items()},
            "secondary": decision.secondary,
            "latency_ms": round(decision.latency_ms, 1),
            "usage": decision.usage.to_dict(),
            "cost_usd": round(decision.usage.cost_usd(), 10),
            "meta": meta or {},
            "outcome": None,
        }
        self._append(entry)
        return entry["id"]

    def payoff(self, ledger_id: str, outcome: str | bool | None, *, note: str = "") -> dict[str, Any]:
        """Attach ground truth to an earlier decision (append-only, latest wins)."""
        if isinstance(outcome, bool):
            outcome = "correct" if outcome else "wrong"
        entry = {
            "id": ledger_id,
            "ts": _now(),
            "kind": "outcome",
            "outcome": outcome,
            "note": note,
        }
        self._append(entry)
        return entry

    def _append(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._records.append(entry)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- read -------------------------------------------------------------
    def records(self) -> list[dict[str, Any]]:
        if self.path and self.path.exists():
            loaded: list[dict[str, Any]] = []
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        loaded.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # a torn final line must not make the ledger unreadable
            return loaded
        return list(self._records)

    def decisions(self) -> list[dict[str, Any]]:
        """Decisions joined with the latest outcome recorded for each."""
        outcomes: dict[str, str] = {}
        decisions: dict[str, dict[str, Any]] = {}
        for entry in self.records():
            if entry.get("kind") == "outcome":
                outcomes[entry["id"]] = str(entry.get("outcome"))
            elif entry.get("kind") == "decision":
                decisions[entry["id"]] = dict(entry)
        for did, record in decisions.items():
            record["outcome"] = outcomes.get(did)
        return list(decisions.values())

    def stats(self) -> dict[str, Any]:
        decisions = self.decisions()
        total = len(decisions)
        by_layer: dict[str, int] = {}
        cost = 0.0
        latency = 0.0
        scored = [d for d in decisions if d.get("outcome") in {"correct", "wrong"}]
        for record in decisions:
            by_layer[record.get("layer", "?")] = by_layer.get(record.get("layer", "?"), 0) + 1
            cost += float(record.get("cost_usd") or 0.0)
            latency += float(record.get("latency_ms") or 0.0)
        correct = sum(1 for d in scored if d["outcome"] == "correct")
        return {
            "decisions": total,
            "by_layer": by_layer,
            "auto_acted": sum(1 for d in decisions if d.get("may_act")),
            "escalated": sum(1 for d in decisions if d.get("escalated")),
            "reviewed": sum(1 for d in decisions if d.get("outcome")),
            "accuracy_on_reviewed": (correct / len(scored)) if scored else None,
            "total_cost_usd": round(cost, 8),
            "mean_latency_ms": round(latency / total, 1) if total else None,
        }
