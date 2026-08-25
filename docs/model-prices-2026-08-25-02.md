# OpenRouter price snapshot — 2026-08-25 (model-scout resolution)

Queried live via `https://openrouter.ai/api/v1/models`, 418 models total,
286 with both `tools` and `structured_outputs` in `supported_parameters`
(the filter every role below needs — router/docs/web_search/escalation/
supervisor for tool-using agent behaviour, judge for GEval scoring).
`openrouter/auto` and every `:free` id were dropped per the skill's rule.
This file resolves the gap left by `docs/model-prices-2026-08-25.md` —
that file found the donor projects' two model names gone from the
catalogue and stopped there; this one runs the per-role stack it called
for. Superseding it, not editing it in place.

## Token profile — ASSUMED, not measured

No Langfuse traces or `evals/golden_dataset.json` token counts exist yet
(pre-Stage-1 repo). Every total below rests on an assumed profile and is
therefore provisional — costs scale linearly with it, so re-run this once
real traces exist.

| Role | Assumed input tokens | Assumed output tokens | Basis |
|---|---|---|---|
| router | 300 | 256 | small schema-instruction system prompt + short customer message in; task gave the 256-out figure directly (max_tokens) |
| docs | 2000 | 600 | retrieved RAG context + question in; grounded answer out |
| web_search | 1500 | 600 | search snippets in; grounded answer out |
| escalation | 800 | 300 | case context in; empathetic summary out |
| supervisor | 500 | 200 | routing state in; short structured decision out |
| judge | 1500 | 300 | case + response + rubric in; GEval score+reason out |

## Per-role candidates (price/token, context, qualifying flags)

All rows below carry `tools` + `structured_outputs`.

**router** — cheap, fast, strict structured output, 100% of traffic
| Model id | Prompt | Completion | Context |
|---|---|---|---|
| `google/gemini-3.5-flash-lite` | $0.0000003 | $0.0000025 | 1,048,576 |
| `deepseek/deepseek-v4-flash` | $0.0000000826 | $0.0000001652 | 1,048,576 |
| `qwen/qwen3-30b-a3b-instruct-2507` | $0.00000004815 | $0.00000019305 | 262,144 |

**docs** — instruction-following for RAG grounding, moderate context
| Model id | Prompt | Completion | Context |
|---|---|---|---|
| `openai/gpt-5.6-luna` | $0.0000002 | $0.0000012 | 1,050,000 |
| `google/gemini-3.5-flash` | $0.0000015 | $0.000009 | 1,048,576 |
| `mistralai/mistral-medium-3.1` | $0.0000004 | $0.000002 | 131,072 |

**web_search** — same profile as docs, needs long context for snippets
| Model id | Prompt | Completion | Context |
|---|---|---|---|
| `deepseek/deepseek-v4-flash` | $0.0000000826 | $0.0000001652 | 1,048,576 |
| `google/gemini-3.5-flash-lite` | $0.0000003 | $0.0000025 | 1,048,576 |
| `openai/gpt-5.6-luna` | $0.0000002 | $0.0000012 | 1,050,000 |

**escalation** — concise, empathetic case summary
| Model id | Prompt | Completion | Context |
|---|---|---|---|
| `openai/gpt-5.6-luna` | $0.0000002 | $0.0000012 | 1,050,000 |
| `mistralai/mistral-medium-3.1` | $0.0000004 | $0.000002 | 131,072 |
| `qwen/qwen3-max` | $0.00000078 | $0.0000039 | 262,144 |

**supervisor** — short structured delegation decisions
| Model id | Prompt | Completion | Context |
|---|---|---|---|
| `google/gemini-3.5-flash-lite` | $0.0000003 | $0.0000025 | 1,048,576 |
| `deepseek/deepseek-v4-flash` | $0.0000000826 | $0.0000001652 | 1,048,576 |
| `openai/gpt-5.6-luna` | $0.0000002 | $0.0000012 | 1,050,000 |

**judge** — must be a family absent from every agent role above, strict
structured output for DeepEval GEval
| Model id | Prompt | Completion | Context |
|---|---|---|---|
| `anthropic/claude-haiku-4.5` | $0.000001 | $0.000005 | 200,000 |
| `anthropic/claude-sonnet-5` | $0.000002 | $0.00001 | 1,000,000 |
| `x-ai/grok-4.5` | $0.000002 | $0.000006 | 500,000 |

## Ranked stack table (uniform-agent tiers, most expensive first)

Assumed profile above, all 5 agent roles on one model + judge on a
different-family model.

| Tier | Agent model (all 5 roles) | Judge model | Run cost | Judge cost | Total |
|---|---|---|---|---|---|
| Premium | `openai/gpt-5.6-terra` | `anthropic/claude-opus-4.5` | $0.033000 | $0.015000 | **$0.048000** |
| Mid | `google/gemini-3.5-flash-lite` | `anthropic/claude-haiku-4.5` | $0.006280 | $0.003000 | **$0.009280** |
| Cheap | `deepseek/deepseek-v4-flash` | `anthropic/claude-haiku-4.5` | $0.000735 | $0.003000 | **$0.003735** |

The knee sits at the Mid tier: below it (Cheap tier), the agent model
itself gets cheap enough that the judge cost (fixed at $0.003 regardless of
which agent tier it's judging) stops being a rounding error and becomes
most of the bill — a sign that further agent-side savings buy less than
they used to, and the next lever is a cheaper judge, which is exactly the
downgrade that needs a before/after on the golden dataset, not a
substitution here.

## Recommended mixed-tier stack

| Role | Model | Why not uniform |
|---|---|---|
| router | `google/gemini-3.5-flash-lite` | highest-traffic role; Google's structured-output path is the one most exercised in the wild for strict small schemas — worth the small premium over DeepSeek here specifically |
| docs | `openai/gpt-5.6-luna` | RAG-grounding needs "don't state what isn't in context" instruction adherence; kept distinct from router's provider to get an independent read during measurement |
| web_search | `deepseek/deepseek-v4-flash` | cheapest capable candidate with the largest context window of the three (relevant when search snippets run long) |
| escalation | `openai/gpt-5.6-luna` | same instruction-following need as docs, low traffic role so reuse costs nothing extra to measure |
| supervisor | `google/gemini-3.5-flash-lite` | short structured decisions, same reliability argument as router |
| judge | `anthropic/claude-haiku-4.5` | only family in the stack not otherwise used (agents span Google/OpenAI/DeepSeek); cheapest Anthropic model with confirmed `structured_outputs` |

Mixed-tier total (assumed profile): run cost $0.003103 + judge $0.003000 =
**$0.006103**.

**Family-independence check**: agent families in use — Google
(`gemini-3.5-flash-lite`), OpenAI (`gpt-5.6-luna`), DeepSeek
(`deepseek-v4-flash`). Judge family — Anthropic (`claude-haiku-4.5`).
No overlap.

## The ratio

Premium ($0.048000) ÷ Cheap ($0.003735) ≈ **12.85×**. For an 18-case golden
run (`evals/golden_dataset.json`), that's roughly $0.86 (Premium) vs $0.067
(Cheap) per full pass — a difference worth measuring against, not one worth
picking blind, since the assumed profile above hasn't been checked against
a real trace yet.

## Router-specific cost (as asked)

Given the task's own router profile (~300 input tokens: small schema
system prompt + short customer message; 256 output tokens, i.e. the
`max_tokens` ceiling, used as the worst case):

| Candidate | Cost per request |
|---|---|
| `google/gemini-3.5-flash-lite` (chosen) | **$0.00073** |
| `deepseek/deepseek-v4-flash` | $0.0000671 |
| `qwen/qwen3-30b-a3b-instruct-2507` | $0.0000639 |

At 100% of traffic this is the role where the ~11x gap between the chosen
model and the cheapest structurally-qualified candidate matters most on a
per-request basis — flagged here, not resolved, because router reliability
(few-shot-free zero-shot JSON, temperature 0) is exactly the dimension a
price table cannot measure.

## What must be verified before committing

- Strict structured output on the real Pydantic schemas — router's
  3-field enum schema, docs/web_search/escalation/supervisor's actual
  response models, and DeepEval's GEval schema for the judge — not just
  the presence of `structured_outputs` in `supported_parameters`.
- Router's zero-shot reliability at temperature 0 specifically, since that
  is the highest-traffic, most schema-strict role and the one this survey
  could not measure from pricing data alone.
- DeepEval GEval agreement with human labels for `anthropic/claude-haiku-4.5`
  against at least a handful of golden-dataset cases before trusting it as
  the eval gate — per CLAUDE.md's own recorded lesson, a cheap judge's
  agreement can go negative even when its price looks fine.

## Diff against `docs/model-prices-2026-08-25.md`

- Confirms that file's finding: `openai/gpt-4.1-mini` and
  `google/gemini-2.5-pro` are absent from today's catalogue.
- Fills the gap that file left open: full per-role candidates, a judge
  pick with confirmed family independence, uniform-tier and mixed-tier
  stack costs, and the router-specific per-request figure.
- One correction: that file's three "cheap candidates" table listed
  `google/gemini-3.5-flash-lite` at $0.0000003/$0.0000025 and
  `deepseek/deepseek-v4-flash-0731` at $0.0000000616/$0.0000001232 — both
  confirmed unchanged in today's re-query. `upstage/solar-pro4` also still
  present at $0.00000003/$0.00000012 but was not selected for any role
  since a `deepseek/deepseek-v4-flash` and `qwen/qwen3-30b-a3b-instruct-2507`
  both undercut or match it while carrying wider context.
