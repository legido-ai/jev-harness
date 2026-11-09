"""Command line entry point: decide once, serve the API, read the ledger."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .answers import Decision
from .cascade import Cascade
from .deciders.base import DeciderError
from .deciders.llm import LLMDecider
from .deciders.mock import KeywordDecider
from .deciders.typesafe import TypeSafeDecider
from .evals import band_report
from .ledger import Ledger
from .policy import Policy
from .spec import load_question_set


def _read_state(args: argparse.Namespace) -> Any:
    if args.state:
        return args.state
    if args.state_file:
        with open(args.state_file, "r", encoding="utf-8") as fh:
            text = fh.read()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("provide --state, --state-file, or pipe the state on stdin")


def _policy(args: argparse.Namespace) -> Policy:
    if getattr(args, "policy", None):
        raw = json.loads(open(args.policy, "r", encoding="utf-8").read())
        return Policy(
            thresholds={str(k): float(v) for k, v in (raw.get("thresholds") or {}).items()},
            default_threshold=float(raw.get("default_threshold", 0.90)),
            review_floor=float(raw.get("review_floor", 0.50)),
            destructive=raw.get("destructive") or (),
            destructive_threshold=float(raw.get("destructive_threshold", 0.95)),
        )
    return Policy(
        default_threshold=float(getattr(args, "threshold", 0.90)),
        destructive=("refund", "approve_transfer", "delete"),
    )


def _decider(name: str):
    if name == "jev":
        return TypeSafeDecider()
    if name == "llm":
        return LLMDecider()
    return KeywordDecider()


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_decide(args: argparse.Namespace) -> int:
    questions, primary = load_question_set(args.questions)
    ledger = Ledger(args.ledger) if args.ledger else Ledger()
    cascade = Cascade(
        questions=questions,
        policy=_policy(args),
        decision_model=_decider(args.decider),
        fallback_model=LLMDecider() if args.fallback == "llm" else None,
        ledger=ledger,
        primary_question=primary,
        shadow=args.shadow,
    )
    state = _read_state(args)
    decision: Decision = cascade.decide(state, action=args.action)
    _print(decision.to_dict())
    return 0 if not decision.escalated else 2


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    os.environ.setdefault("QUESTIONS_PATH", args.questions)
    if args.ledger:
        os.environ.setdefault("LEDGER_PATH", args.ledger)
    uvicorn.run("jev_harness.api:app", host=args.host, port=args.port, log_level=args.log_level)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    _print(Ledger(args.ledger).stats())
    return 0


def cmd_bands(args: argparse.Namespace) -> int:
    _print({"bands": band_report(Ledger(args.ledger).decisions())})
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    questions, primary = load_question_set(args.questions)
    _print(
        {
            "name": questions.name,
            "version": questions.version,
            "primary": primary,
            "questions": list(questions.with_companions().questions),
            "payload": questions.payload(),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jev-harness", description=__doc__)
    parser.add_argument("--version", action="version", version="jev-harness 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)

    decide = sub.add_parser("decide", help="run one decision through the cascade")
    decide.add_argument("--questions", required=True, help="question-set spec (JSON)")
    decide.add_argument("--state")
    decide.add_argument("--state-file")
    decide.add_argument("--action", help="policy key for the threshold")
    decide.add_argument("--decider", choices=["jev", "llm", "keyword"], default="jev")
    decide.add_argument("--fallback", choices=["llm", "none"], default="llm")
    decide.add_argument("--policy")
    decide.add_argument("--threshold", type=float, default=0.90)
    decide.add_argument("--shadow", action="store_true", help="label only; never authorise")
    decide.add_argument("--ledger", help="append decisions to this JSONL file")
    decide.set_defaults(func=cmd_decide)

    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--questions", required=True)
    serve.add_argument("--ledger")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=cmd_serve)

    stats = sub.add_parser("stats", help="ledger summary")
    stats.add_argument("--ledger", required=True)
    stats.set_defaults(func=cmd_stats)

    bands = sub.add_parser("bands", help="agreement per confidence band")
    bands.add_argument("--ledger", required=True)
    bands.set_defaults(func=cmd_bands)

    validate = sub.add_parser("validate", help="validate and print a question-set spec")
    validate.add_argument("--questions", required=True)
    validate.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except DeciderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
