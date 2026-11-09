# Changelog

## 0.1.0 - 2026-11-08

First release.

- Typed primitives (`Choice`, `Score`, `Noul`) with derived companion Nouls and
  versioned question sets.
- Four-layer cascade: code rules, decision model, chat-model fallback, human review.
- Per-action thresholds with an irreducible floor for destructive actions.
- Append-only JSONL ledger with outcome attachment, cost accounting and band analysis.
- FastAPI service (`/healthz`, `/v1/decide`, `/v1/feedback`, `/v1/stats`, `/v1/bands`)
  with optional bearer auth, plus a CLI (`decide`, `serve`, `stats`, `bands`, `validate`).
- Three deciders: TypeSafe/Jev over plain HTTP, any OpenAI-compatible chat endpoint,
  and a deterministic offline stand-in for tests and demos.
- 55 tests, no network required; CI on Python 3.10 and 3.12; multi-arch image published by `.github/workflows/docker.yml`.
