# jev-harness

An open-source decision harness for **System One** models: ask typed questions about a
state, get answers **with calibrated probabilities**, and route on confidence through a
four-layer cascade instead of paying a frontier model to answer everything.

The model is the cheap part. The harness is what makes it trustworthy: per-action
thresholds, an invariant that stops a confident guess, an audit ledger that doubles as
your evaluation set, and a shadow mode for the week before you let it act.

```
code rule  ->  decision model  ->  chat model  ->  human
 instant        ~100 ms, $/1M      seconds        whenever you look
```

## Why

Most of an agent's calls are not creation. Which tool next. Is this source relevant. Is
this command safe. Is the task done. A decision model answers those with a *typed value
and a probability* instead of a paragraph, for a fraction of the cost - and the
probability is the part that lets software act safely: it tells you which answers to act
on and which to send somewhere else.

That only works if the harness is disciplined about three things, all implemented here:

| Risk | What this harness does |
| --- | --- |
| A choice model always returns *something* | every Choice carries a derived **companion Noul** ("does any option fit?"); a confident guess is blocked when it says no |
| One threshold for every action | thresholds live **per action**, and irreversible actions have a floor the config cannot lower |
| Quiet confidence drift | the model id and the **question-set version are pinned** into every ledger record; thresholds tuned against version N mean nothing against N+1 |
| Reasoning models returning nothing | the chat fallback treats *empty visible content* as a failed turn, retries with a bigger budget, then raises - it never sends silence downstream |

## The cascade

1. **Code** - exact rules. Free and instant. A regex, a permission check, an amount cap.
2. **Decision model** - one request, every question that shares the state, evaluated in
   parallel. Answers carry `choice` / `score` / `noul` plus `probabilities` and
   `confidence`.
3. **Chat model** - only the cases under the threshold, and it is told the first model's
   guess so it second-guesses instead of starting blind.
4. **Human** - both answers side by side, so review takes seconds.

`may_act` is true only when the deciding layer is authoritative, not escalated, and its
confidence clears the threshold for that action.

## Quickstart (no credentials)

```bash
uv venv .venv && . .venv/bin/activate
uv pip install -e ".[dev]"
pytest -q                                  # 54 tests, no network
python examples/email_triage.py            # offline keyword stand-in
jev-harness validate --questions examples/email_triage.questions.json
```

## Quickstart (real models)

```bash
export TYPESAFE_API_KEY=...                # decision model (pin the id, see below)
export LLM_API_KEY=...                     # any OpenAI-compatible chat endpoint
export LLM_BASE_URL=https://opencode.ai/zen/go/v1
export LLM_MODEL=deepseek-v4.1-flash

jev-harness decide --questions examples/email_triage.questions.json   --state "Our API returns 500 on every order since 20 minutes ago"   --decider jev --fallback llm --ledger triage.jsonl
```

Exit code is `0` when a layer acted, `2` when the decision went to review, `3` on a
decider error - so a shell or a CI job can branch on it.

## Service

```bash
jev-harness serve --questions examples/email_triage.questions.json --ledger triage.jsonl
```

```
GET  /healthz        wiring, and which layers are actually configured
GET  /v1/questions   the question set as sent, companions included
POST /v1/decide      {"state": ..., "action": ..., "meta": {...}} -> typed decision
POST /v1/feedback    {"ledger_id": ..., "outcome": "correct"|"wrong"} -> attach ground truth
GET  /v1/stats       decisions, layers, cost, accuracy on reviewed cases
GET  /v1/bands       agreement per confidence band
```

Set `HARNESS_TOKEN` to require `Authorization: Bearer <token>` on `/v1/*`
(`/healthz` stays open for probes).

## Docker

```bash
docker build -t jev-harness:local .
docker run --rm -p 8080:8080 \
  -e QUESTIONS_PATH=/app/examples/email_triage.questions.json \
  -e TYPESAFE_API_KEY -e LLM_API_KEY \
  -v "$PWD/data:/data" -e LEDGER_PATH=/data/ledger.jsonl \
  jev-harness:local
curl -s localhost:8080/healthz
```

`docker compose up` does the same with the bundled compose file.

## Question sets are data, and they are versioned

```json
{
  "name": "email_triage",
  "version": 3,
  "companion_nouls": true,
  "primary": "category",
  "questions": [
    {"id": "category", "type": "choice", "instructions": "What does the sender want?",
     "options": {"customer": "asking about an order or a bill", "fraud": "payment scam"}},
    {"id": "urgent", "type": "noul", "instructions": "Is something broken right now?"},
    {"id": "frustration", "type": "score", "instructions": "How annoyed is the sender?",
     "levels": ["calm", "annoyed", "angry"]}
  ]
}
```

Rules the harness assumes, because the docs say violating them degrades accuracy instead
of raising:

- one judgment per question - "is this urgent and from a paying customer" is two questions;
- describe situations, not degrees - "mentions cancelling or a chargeback" is checkable;
- every level of a Score stands alone, since levels are judged independently;
- a Noul is phrased so a high number means **yes**;
- **no `other` option**: use the companion Noul. An `other` option competes with the real
  options for probability, and in testing it swallowed the correct answer;
- point at the exact field (`email.body`, `order.charges`) so the state is unambiguous.

## Rollout: shadow first, then bands

```bash
jev-harness decide ... --shadow          # label only; never authorises anything
jev-harness bands --ledger triage.jsonl  # accuracy per confidence band
jev-harness stats --ledger triage.jsonl
```

Automate the highest band first. `suggest_band()` returns the lowest band floor whose
measured accuracy clears your target **and** has enough labelled decisions - otherwise it
returns `None`, which is the honest answer for a young ledger.

## What is verified, and what is not

Verified on an ARM64, 2-core, CPU-only box:

- **54 tests pass** (`pytest -q`), no network: cascade routing, companion-Noul blocking,
  shadow mode, decider failure -> review, ledger join/stats, band maths, auth, spec
  round-trip, reasoning-only retry, invalid-option rejection.
- **Live cascade run** with the chat leg on a real hosted endpoint
  (`deepseek-v4.1-flash`): rule -> stand-in -> chat model, ending with
  `layer=llm value=customer conf=0.90 act=true`. The escalation path, the hint to the
  second model and the JSON validation are exercised against a real API, not a mock.
- **The decision-model leg is wired but was not exercised end to end here**: the
  credential available to this build returns `401 authentication_error` from
  `api.typesafe.ai`. The harness treats that as a non-retryable `AuthError` and degrades
  to review rather than crashing - that behaviour *is* covered by tests. Put a valid
  `TYPESAFE_API_KEY` in place to light up layer 1.

Not claimed: any accuracy number of our own. The reference document quotes the vendor's
evals (193.6x faster / 444.6x cheaper, self-described as the high end); this repository
ships no benchmark of its own and its offline decider is a keyword stand-in, **not a
model**. Measure on your own labelled set before you trust a threshold.

## Layout

```
src/jev_harness/
  primitives.py   Choice / Score / Noul, companion Noul, versioned question sets
  policy.py       per-action thresholds, destructive floors, review floor
  cascade.py      the four layers and the invariants that stop a confident guess
  answers.py      typed answers, usage, cost, Decision
  ledger.py       append-only JSONL: audit trail, cost report, eval set
  evals.py        confidence-band agreement, suggested band floor
  spec.py         JSON question-set specs
  api.py          FastAPI service          cli.py  decide / serve / stats / bands
  deciders/       typesafe.py (HTTP)  llm.py (OpenAI-compatible)  mock.py (offline)
docs/reference/   the reference playbook, as markdown and PDF, plus its renderer
examples/         email triage with rules, policy and a question-set spec
```

## Sources

- `docs/reference/jev-playbook.pdf` - *Jev engineering*, Mr. Buzzoni, 29 Sep 2026
  ([original post](https://x.com/mr_buzzoni/status/2104783226833186920)), the document this
  harness follows.
- TypeSafe API docs - `https://docs.typesafe.ai` (request shape, primitives, confidence,
  fan-out, jaggedness). The primitive and confidence rules above are taken from there, not
  guessed.
- Vendor claims are labelled as vendor claims throughout.

Licence: MIT. The reference PDF is third-party material, reproduced for reference with
attribution; it is not covered by this repository's MIT licence.
