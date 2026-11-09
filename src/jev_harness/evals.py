"""Confidence-band analysis: the shadow week, in code.

The rollout rule is to compare agreement *by confidence band* and automate the highest
band first, rather than trusting one aggregate accuracy number. These helpers compute
that table from the ledger.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

__all__ = ["DEFAULT_BANDS", "band_report", "suggest_band"]

DEFAULT_BANDS: Sequence[tuple[float, float]] = (
    (0.0, 0.5),
    (0.5, 0.9),
    (0.9, 0.95),
    (0.95, 1.0001),
)


def _band_label(low: float, high: float) -> str:
    upper = "1.0" if high > 1.0 else f"{high:g}"
    return f"[{low:g}, {upper})"


def band_report(
    records: Iterable[dict[str, Any]],
    bands: Sequence[tuple[float, float]] = DEFAULT_BANDS,
) -> list[dict[str, Any]]:
    """Agreement per confidence band for records that carry an outcome."""
    decisions = [r for r in records if r.get("kind", "decision") == "decision"]
    report: list[dict[str, Any]] = []
    for low, high in bands:
        members = [
            r
            for r in decisions
            if r.get("confidence") is not None and low <= float(r["confidence"]) < high
        ]
        labelled = [r for r in members if r.get("outcome") in {"correct", "wrong"}]
        correct = sum(1 for r in labelled if r["outcome"] == "correct")
        escalated = sum(1 for r in members if r.get("escalated") or not r.get("may_act"))
        report.append(
            {
                "band": _band_label(low, high),
                "decisions": len(members),
                "labelled": len(labelled),
                "accuracy": round(correct / len(labelled), 4) if labelled else None,
                "auto_rate": round((len(members) - escalated) / len(members), 4) if members else None,
                "escalated": escalated,
            }
        )
    return report


def suggest_band(
    records: Iterable[dict[str, Any]],
    min_accuracy: float = 0.98,
    min_samples: int = 20,
) -> float | None:
    """Lowest band floor whose measured accuracy clears ``min_accuracy``.

    Returns ``None`` when no band has enough labelled decisions, which is the honest
    answer for a young ledger.
    """
    for row in reversed(band_report(records)):
        if row["labelled"] >= min_samples and row["accuracy"] is not None and row["accuracy"] >= min_accuracy:
            return float(row["band"].strip("[)").split(",")[0])
    return None
