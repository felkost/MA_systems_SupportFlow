# SupportFlow

A read-only, multi-agent customer-support assistant. A LangGraph Supervisor
classifies an incoming message, routes it to a Docs, Web Search, or
Escalation agent, and returns a sourced answer or hands the case to a human
operator via Telegram.

**Status: kickoff only.** No application code exists yet — this repository
currently holds the scaffold, conventions, and requirement checklist. See
`docs/task-supportflow.md` for the full task statement and
`docs/requirements-checklist.md` for what "done" means.

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
`project_support.md`, referenced by the task statement, was not found on
this machine (`docs/decisions.md` §3).
