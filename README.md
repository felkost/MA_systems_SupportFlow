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
| ⬜ | 1 — Core | Pydantic schemas, LangGraph StateGraph, Supervisor, Router Agent, input filter |
| ⬜ | 2 — Data | Docs Agent + RAG, real Silpo MCP client, Web Search Agent |
| ⬜ | 3 — Escalation | Escalation Agent, file report, Telegram notification |
| ⬜ | 4 — Evaluation & observability | Langfuse tracing, DeepEval, golden dataset, thresholds |
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

## Gate

```bash
black --check . tests/*.py && flake8 && mypy src && pytest --cov=src
```

## Known limitations

Nothing is implemented yet. `config/models.yaml` carries placeholder model
names pending a model-scout pass (see `docs/model-prices-2026-08-25.md`).
