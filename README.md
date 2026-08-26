# SupportFlow

A read-only, multi-agent customer-support assistant. A LangGraph Supervisor
classifies an incoming message, routes it to a Docs, Web Search, or
Escalation agent, and returns a sourced answer or hands the case to a human
operator via Telegram.

See `docs/task-supportflow.md` for the full task statement and
`docs/requirements-checklist.md` for what "done" means.

## Progress

| | Stage | What it delivers |
|---|---|---|
| ✅ | 0 — Kickoff | Conventions, layer table, dependency pins, real Silpo MCP tool list + allowlist, seeded prompts |
| ✅ | 1 — Core | Pydantic schemas, LangGraph StateGraph, Supervisor, Router Agent, input filter, fail-closed retry policy, Router gate ≥10/12 |
| ✅ | 2 — Data | Docs Agent + RAG (Chroma+BM25), real Silpo MCP client (persistent token, 17-tool allowlist), Web Search Agent (Tavily+ddgs), A2A server/client, launcher |
| ✅ | 3 — Escalation | Escalation Agent, file report, Telegram notification |
| ✅ | 4 — Evaluation & observability | Wave A (Observability) live-verified: Langfuse/OTel tracing across the full path, `CallbackHandler`, per-observation metadata, cross-process trace propagation via Langfuse's own `TraceContext`. Known gap: Telegram's own observation unconfirmed (needs an `ALLOW_REAL_SEND=true` run). Wave B done and live-verified, 2026-08-26: 18-case golden dataset (`evals/golden_dataset.json`), DeepEval metrics (Answer Relevancy, Faithfulness, Route/Tool Correctness, Privacy Safety, custom GEval Support Resolution Quality), thresholds set from a real baseline, a Docs Agent meta-prompting cycle (production→candidate, +0.112 mean, promoted), and a full `deepeval test run tests/test_golden_dataset.py -m eval` gate — see `docs/decisions.md` #51-60 for every defect found and fixed along the way |
| ⬜ | 5 — Product & docs | FastAPI, React chat UI, final README, diagrams |

Updated at the close of every stage — see `docs/decisions.md` for the
reasoning behind each stage's scope.

## Architecture

Router and Escalation agents run in-process with the Supervisor. Docs and
Web Search agents run as separate A2A servers. See `docs/decisions.md` §1
for why, and `CLAUDE.md` for the Clean Architecture layer table this
repository follows.

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env   # fill in OpenRouter, Silpo MCP, Tavily, Telegram, Langfuse keys
```

One-time, manual (Silpo's OAuth is phone+OTP against a real account, not
automatable — docs/decisions.md #5):

```bash
python scripts/silpo_mcp_login.py
```

One-time, manual — Escalation Agent's Telegram bot and test channel
(`docs/telegram_bot_setup.md` walks through creating the bot, adding it to
a test group, and finding `TELEGRAM_CHAT_ID`):

```bash
python -m scripts.telegram_bot_setup_check
```

Then, to run Docs and Web Search Agent as their own processes:

```bash
python -m src.interfaces.launcher
```

## Gate

```bash
black --check . tests/*.py && flake8 && mypy src && pytest --cov=src
```

`pytest --cov=src` never makes a live call to Silpo MCP, Tavily,
OpenRouter, or Telegram (docs/decisions.md #21) — `scripts/docs_agent_smoke.py`,
`scripts/run_router_gate.py`, and `scripts/escalation_agent_smoke.py` are
the manual, live-verification paths.

Escalation Agent sends a real Telegram message only with
`ALLOW_REAL_SEND=true` — see `docs/telegram_bot_setup.md` for the one-time
bot/test-channel setup needed before that flag means anything.

## Known risks

- Telegram's own Langfuse observation is unconfirmed — no live run has
  set `ALLOW_REAL_SEND=true` yet.
- Generation-span cost is never populated — Langfuse doesn't recognise
  the OpenRouter-routed model id for its automatic cost calculation;
  token counts are real.
- PII masking at the Langfuse export barrier covers email/phone/card
  shapes only, not OAuth tokens, API keys, or free-text addresses.
- Escalation's send-dedup/cap store is a single-process, in-memory
  dict — correct for this project's single-process demo topology, not a
  multi-worker deployment.
- The final golden-dataset gate is not fully green yet (Stage 4 Wave
  B): known route-boundary flakiness and two below-floor answer-quality
  findings remain, tracked and not silently dropped.
