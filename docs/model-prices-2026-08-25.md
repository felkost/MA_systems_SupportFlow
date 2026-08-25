# OpenRouter price snapshot — 2026-08-25

Queried live via `https://openrouter.ai/api/v1/models`. This file is a
snapshot for cost estimation, not a pin — refresh with a new dated file when
it goes stale, never edit this one in place.

## Finding: the reference projects' model names are gone from the catalogue

`openai/gpt-4.1-mini` (hl10's shared default) and `google/gemini-2.5-pro`
(hl10/hl12's judge default) were **not found** in today's catalogue query.
This is evidence, not proof of removal — a full unpaginated listing was not
inspected — but it means **do not carry those model names forward without
re-checking**. The per-role model choice for `config/models.yaml` is an open
item for Stage 1, not a decision made here.

## What the query did surface — cheap current candidates

| Model id | Prompt price | Completion price |
|---|---|---|
| `google/gemini-3.5-flash-lite` | $0.0000003/token | $0.0000025/token |
| `deepseek/deepseek-v4-flash-0731` | $0.0000000616/token | $0.0000001232/token |
| `upstage/solar-pro4` | $0.00000003/token | $0.00000012/token |

These are candidates for a cheap agent role (Router, Web Search), not
selections — no judge-family-independence check has been run against them.

## Visible gap, recorded rather than guessed

No embeddings model price and no full per-role recommendation are in this
file. Before Stage 1 designs `config/models.yaml`, run the model-scout agent
(`agentic-model-selection`) for a proper per-role stack: supervisor, Router,
Docs, Web Search, Escalation, and a judge model from a different family than
whichever chat model the agents end up on.
