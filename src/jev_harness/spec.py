"""Question-set specifications, loadable from JSON.

Keeping the question set in data (not in code) is what lets the same service serve
different decisions, and it makes the version explicit: a spec file has a ``version``,
and changing criteria means bumping it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .primitives import Choice, Noul, Question, QuestionError, QuestionSet, Score

__all__ = ["load_question_set", "dump_question_set", "question_set_from_dict"]


def question_set_from_dict(spec: Mapping[str, Any]) -> tuple[QuestionSet, str | None]:
    """Build a :class:`QuestionSet` from a dict; returns it with the primary question id."""
    name = spec.get("name")
    if not name:
        raise QuestionError("spec needs a 'name'")
    questions = QuestionSet(
        name=str(name),
        version=int(spec.get("version", 1)),
        add_companion_nouls=bool(spec.get("companion_nouls", True)),
    )
    for raw in spec.get("questions") or []:
        qtype = str(raw.get("type", "")).lower()
        try:
            if qtype == "choice":
                question: Question = Choice(
                    id=str(raw["id"]),
                    instructions=str(raw["instructions"]),
                    options={str(k): str(v) for k, v in raw["options"].items()},
                )
            elif qtype == "score":
                question = Score(
                    id=str(raw["id"]),
                    instructions=str(raw["instructions"]),
                    levels=[str(x) for x in raw["levels"]],
                )
            elif qtype == "noul":
                question = Noul(
                    id=str(raw["id"]),
                    instructions=str(raw["instructions"]),
                    criteria=raw.get("criteria"),
                )
            else:
                raise QuestionError(f"unknown question type {qtype!r}")
        except KeyError as exc:
            raise QuestionError(f"question {raw.get('id')!r} is missing {exc}") from exc
        questions.add(question)
    if not questions.questions:
        raise QuestionError("spec has no questions")
    primary = spec.get("primary")
    if primary is not None and primary not in questions.questions:
        raise QuestionError(f"primary question {primary!r} is not in the set")
    return questions, (str(primary) if primary else None)


def load_question_set(path: str | Path) -> tuple[QuestionSet, str | None]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return question_set_from_dict(data)


def dump_question_set(questions: QuestionSet, primary: str | None = None) -> dict[str, Any]:
    """Round-trip form. Companion Nouls are derived, never written out."""
    out: list[dict[str, Any]] = []
    for qid, question in questions.questions.items():
        entry: dict[str, Any] = {
            "id": qid,
            "type": question.type,
            "instructions": question.instructions,
        }
        if isinstance(question, Choice):
            entry["options"] = dict(question.options)
        elif isinstance(question, Score):
            entry["levels"] = list(question.levels)
        elif isinstance(question, Noul) and question.criteria:
            entry["criteria"] = question.criteria
        out.append(entry)
    return {
        "name": questions.name,
        "version": questions.version,
        "companion_nouls": questions.add_companion_nouls,
        "primary": primary,
        "questions": out,
    }
