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
| 🟨 | 4 — Evaluation & observability | Wave A (Observability) done: Langfuse/OTel tracing across the full path, `CallbackHandler`, per-observation metadata, cross-process trace propagation — code-complete and unit-tested, live end-to-end verification (`scripts/observability_smoke.py`) still to be run. Wave B (DeepEval, golden dataset, thresholds, meta-prompting) not started |
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

## Known limitations

Router, Docs, Web Search, and Escalation are all real. Docs and Web
Search run as separate A2A server processes
(`src/interfaces/{docs,web_search}_a2a_server.py`), started together by
`src/interfaces/launcher.py`; Router and Escalation run in-process with
the Supervisor. There is no FastAPI app or web UI yet (Stage 5). Langfuse
tracing across the full call tree (`CallbackHandler` on `graph.invoke`,
per-observation metadata for Silpo MCP/ACP/A2A/Telegram/File System/PII
scrubbing/Supervisor routing, cross-process trace propagation via
Langfuse's own `TraceContext`) is code-complete and unit-tested
(Stage 4 Wave A) but not yet live-verified end to end —
`scripts/observability_smoke.py` is written but has not been run against
real Langfuse/OpenRouter credentials this session. PII masking at the
Langfuse export barrier (`mask_otel_spans`) reuses the existing
email/phone/card-Luhn patterns only — it does not detect OAuth
tokens, API keys, or free-text addresses by shape; the only control for
those categories today is code review at each span's `metadata=` call
site (docs/decisions.md Stage 4 decision 34's Known Risks). DeepEval, the
golden dataset, thresholds, and the meta-prompting cycle are Stage 4 Wave
B, not started. Retrieval/prompt relevance quality (Docs/Web Search can
lose track of the actual question among noisy retrieved content) is
unmeasured and unfixed — in scope for Wave B, not treated as done.
Escalation's session-scoped send-dedup/cap store is a module-level,
single-process dict (docs/decisions.md #28) — correct for this project's
single-process demo topology, not a multi-worker deployment.
`docs/decisions.md` #9–39 record every Stage 1–4 design decision,
including ones this list doesn't repeat here (personal-data masking,
prompt-fetch failure behaviour, the self-reported-confidence gate's
unvalidated status, the Silpo MCP branch/delivery/timeslot bootstrap
chain, the Telegram send-safety flags).
