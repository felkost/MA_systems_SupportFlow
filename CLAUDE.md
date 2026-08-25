# CLAUDE.md

## 1. What this project is

SupportFlow is a read-only, multi-agent customer-support assistant for a
retail knowledge base and a real external MCP server (Silpo MCP). A
LangGraph Supervisor classifies a customer message, routes it to one of
three specialist agents (Docs / Web Search / Escalation), and returns a
sourced answer or hands the case to a human operator via Telegram.

Relative to the donor research projects this repository studied
(`MA_systems_hl10/11/12`, outside this repo): those projects build a
research-report pipeline (Planner → Researcher → Critic) with either no
real inter-process protocol or a fully networked one. SupportFlow adopts a
**hybrid transport** instead of either extreme: Router and Escalation run
in-process with the Supervisor (per the task's own definition of the
in-process agent contract), while Docs and Web Search run as separate A2A
servers, because they carry the tool-using, isolatable work. This is a
deliberate deviation from the task's literal "everything in one process"
architecture description, recorded because a fully networked topology
(the donor project that has one) measurably failed the task's own
single-trace-with-cost requirement for its sub-agent processes, and a
fully in-process one gives up the chance to demonstrate the A2A protocol
at all.

Full requirement source: `docs/task-supportflow.md`. Acceptance checklist:
`docs/requirements-checklist.md`. Neither is duplicated here — read them,
don't paraphrase them.

## 2. Architecture table

Layer is a property of the file, enforced by `tests/test_layering.py`
(an AST import walk), not by directory nesting alone.

| Layer | Contains | May import | Build state |
|---|---|---|---|
| kernel | `src/kernel/settings.py`, paths, constants | nothing project-local | not built |
| domain | `src/domain/schemas.py` (4 mandatory Pydantic models), state, routing rules | kernel | not built |
| infra | `src/infrastructure/` — Silpo MCP client, OpenRouter, Tavily, DuckDuckGo, Telegram, file system, Langfuse, retriever | kernel, domain | not built |
| application | `src/application/` — Supervisor, case-handling scenarios | kernel, domain, infra | not built |
| interface | `src/interfaces/` — FastAPI app, A2A server entrypoints for Docs/Web Search, launcher | everything | not built |
| obs | `src/infrastructure/observability.py` (tracing/logging live in infra, listed separately because the invariant below singles it out) | kernel, domain, infra | not built |

**The single most important rule this table exists to pin:** `application`
(the Supervisor) never imports the Docs/Web Search agent modules directly.
Every call to them goes through the A2A client. Importing them would let a
network delegation silently degrade into a local function call while every
test still passes.

## 3. Development commands

Not yet defined — no virtualenv, no `requirements.txt` installed. Recorded
here as soon as the kickoff gate exists:

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
black --check . tests/*.py && flake8 && mypy src && pytest --cov=src
```

## 4. Invariants

- **Router has no tools**, because giving it any breaks the task's own
  test-coverage rule (§10: "Router Agent calls no tools") and adds latency
  to the one node every single request passes through.
- **Escalation never crosses a network hop**, because it is the fallback
  for "tool unavailable" (§7); a networked fallback can fail by the same
  mechanism it exists to catch.
- **`application` never imports `interfaces/docs_a2a_server.py` or
  `interfaces/web_search_a2a_server.py` directly** — see the architecture
  table above. Enforced by `tests/test_layering.py`.
- **Every process that makes an LLM call carries its own Langfuse span
  exporter**, because the one reference project that ran multiple agent
  processes over A2A (`MA_systems_hl10`) shipped without this and its UI
  showed supervisor-only cost — a direct failure of the task's §9
  requirement that every step's tokens and cost are recorded.
- **Trace context is propagated across every A2A hop** (`baggage` on the
  client, `extract` on the server), because the task calls disconnected
  traces for one request an error (§9), and OpenTelemetry does not do this
  automatically across a process boundary.
- **Prompts are never hardcoded in agent code.** They are fetched from
  Langfuse Prompt Management by name + `production` label, because §9
  requires prompts to be versioned there, and a hardcoded fallback that
  silently diverges from the tracked version defeats that requirement.
- **`should_export_span` is composed, never replaced**, because Langfuse's
  default filter silently drops any span without a `gen_ai.*` attribute or
  a recognized instrumentor scope — replacing it (instead of `or`-ing onto
  it) would also drop Langfuse's own default coverage.
- **DeepEval runs offline only** (`evaluate()` / `assert_test(test_case=...)`),
  never via `@observe`, because DeepEval's own OTel instrumentation and
  Langfuse's both attach to the global `TracerProvider` and will split one
  trace into two.
- **Guardrails sit at every sink, not one place**: input (language/domain/PII
  filter before Router, §7), tool (Silpo MCP read-only allowlist,
  `ToolCallLimitMiddleware`/`ModelCallLimitMiddleware` on Docs/Web Search),
  output (PII stripped before web search — or web search is skipped
  entirely, §9 — plus the Privacy Safety evaluator and the no-leaked-data
  test on tracked reports).
- **Escalation's Telegram send and file write go through a human-in-the-loop
  gate in interactive/demo mode, and through an explicit bypass flag in
  automated runs.** Escalation is the task's own HITL mechanism (§7); gating
  the moment it actually sends something external keeps a live demo
  controllable, while an ungated golden-dataset run (18 cases via
  `deepeval test run tests/`) cannot block on a human click. The flag's
  default and location is a Stage 3 decision — see `docs/decisions.md`.
- **Silpo MCP tool arguments are always composed in Ukrainian; the final
  customer-facing answer is composed in the customer's detected
  language.** Silpo's catalogue is Ukrainian-language data — a query
  passed through untranslated for a non-Ukrainian customer message
  silently returns nothing, which looks like a tool failure but is a
  language mismatch. `docs/decisions.md` §6.
- **Heavy ML imports (`sentence-transformers`, the embedding model,
  Chroma) live inside the function that uses them, never at module top
  level**, and only in Docs Agent's own module tree. Router, Supervisor,
  and Escalation must never load the retriever — with four processes,
  a top-level heavy import costs memory and startup time four times, not
  once. `docs/decisions.md` §7.
- **The Silpo MCP client persists its OAuth token to disk and refreshes it
  on startup**, because the process (and the machine) can restart, and a
  refresh_token grant is how Silpo's own docs say to recover a session
  without a fresh phone+OTP login. `docs/decisions.md` §5. Never logged,
  never sent to Langfuse, lives in `.cache/` (gitignored). A revoked/expired
  refresh token fails loudly rather than silently degrading.
- **Docs Agent's Silpo MCP allowlist is exactly the 17 tools in
  `docs/silpo_mcp_allowlist.md`**, derived from the real `tools/list`
  response (`docs/silpo_mcp_tools.json`) by each tool's own description,
  not by name pattern — two personal tools (`silpo_get_loyalty_info`,
  `silpo_get_promo_codes`) do not match the `silpo_get_my_*` prefix and
  would be missed by a naive filter. No code adds a Silpo tool to this
  list without updating that file first; calling a personal tool
  prematurely would put a real person's data through Langfuse, which §9
  forbids outright.

## 5. Code style

See `agentic-code-and-comments` skill and `CONTRIBUTING.md`. Short version:
numpydoc-style docstrings (Parameters → Returns → See Also → Notes →
Examples), comments explain *why* not *what*, ponytail discipline (no
speculative abstraction), files ≤250 lines preferred (§8 of the task),
250–320 allowed for a single well-scoped responsibility.

**Never in a tracked file:** a reference to a gitignored file
(`insights.md`, `handoff.md`, `.claude/`) by name or content. A commit
message or code comment describes the change, not its stage number.

**Exception, explicit author request:** `README.md` carries a visible
progress checklist (stage name, ✅/⬜ status) — this is the one place a
stage number is allowed in a tracked file, because a reader deciding
whether to pick this project up needs to know what's actually done without
reading every commit. Keep it updated at the close of every stage.

## 6. Forbidden

- Never call a write operation on Silpo MCP (cart, favorites, certificates)
  — the system is read-only end to end (task §1).
- Never log a raw OAuth token, API key, or unmasked personal field to
  Langfuse or to a file.
- Never run `git commit`, `git push`, or `gh pr create` — commands are
  printed, the author runs them.
- Never invent a numeric threshold not present in `docs/task-supportflow.md`.
- Never replace `should_export_span` instead of composing onto it.

## 7. Tests

`tests/` — component, tool-correctness, and layering tests, run by
`pytest`. `evals/golden_dataset.json` + `deepeval test run tests/` is the
end-to-end evaluation gate, separate from the pytest gate. Coverage target:
≥80% for `src/domain` and `src/application`, 100% for critical routes and
data-protection code (task §10).

## 8. Session protocol

**Every session — this one and every future one — is conducted in
Ukrainian from the first message, no exception and no reminder needed.**
This applies regardless of what language the request arrives in.

Read order at the start of a session: `handoff.md` (if present — it is
gitignored, local only), then `git log --oneline -5` and
`git status --short --branch` to verify `handoff.md`'s snapshot rather than
trust it, then this file, then `docs/requirements-checklist.md`.

Standing rules: session and plan language is Ukrainian; all other tracked
documentation, code, comments, and commits are English; stage boundaries
are visible in `insights.md`/`handoff.md` (both local) and, since the
author's explicit exception, in `README.md`'s progress checklist — never
elsewhere in a tracked file.
