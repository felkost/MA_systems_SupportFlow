# Requirement checklist (verbatim from the task statement)

Source: `docs/task-supportflow.md` (decoded copy of `SupportFlow_task.md`).
This checklist is the acceptance criterion for the whole project. A plan
review may reorder, merge, cut, or deepen stages — it may not add a
requirement this list does not contain.

## Resolved gap

- [x] **`project_support.md`** — found via the source Google Doc's dropped
  hyperlink, fetched from
  `github.com/robot-dreams-code/MULTI-AGENT-SYSTEMS` and saved to
  `docs/project_support.md`. It's the course's generic base spec;
  `SupportFlow_task.md` is the authoritative, Silpo-specific refinement —
  differences catalogued in `docs/decisions.md` §4.

## Mandatory components (§3)

- [x] Router Agent — classifies by category, urgency, language; no tools.
      `src/application/router_agent.py` + `src/infrastructure/acp.py`,
      gated at ≥10/12 on a held-out labelled set across 3 runs
      (`output/router_gate_result.json`).
- [x] Docs Agent — RAG over internal knowledge base + Silpo MCP for domain
      queries. `src/application/docs_agent.py` (hybrid Chroma+BM25
      retriever) + `src/infrastructure/silpo_mcp.py` (persistent-token
      client, 17-tool allowlist, non-personal branch/delivery/timeslot
      bootstrap — docs/decisions.md #27), verified live end-to-end
      (`scripts/docs_agent_smoke.py`).
- [x] Web Search Agent — Tavily primary, DuckDuckGo fallback.
      `src/application/web_search_agent.py` + `src/infrastructure/web_search.py`,
      verified live end-to-end.
- [ ] Escalation Agent — structured report, file save, real Telegram message
      to a test channel.
- [x] LangGraph StateGraph — state, agent sequencing, conditional
      transitions. `src/application/supervisor.py`: real Router node and
      real conditional edges; Docs/Web Search/Escalation nodes raise
      `NotImplementedError` naming their owning stage until built.
- [x] Pydantic — validates data agents pass to each other.
      `src/domain/schemas.py`.
- [ ] Langfuse — traces the full request path, versions system prompts, runs
      automated evaluation.
- [ ] DeepEval + pytest — component, tool, and end-to-end tests.

## Architecture (§4)

- [ ] Flow: web UI → local LangGraph Supervisor → agents via ACP → tools and
      data sources.
- [ ] Supervisor holds state, calls agents via ACP, compares confidence to a
      threshold, runs conditional transitions, limits retries, handles
      errors/timeouts, does not search itself.
- [x] Each ACP call carries: request id, task, deadline, Langfuse trace ids;
      response validated by the matching Pydantic model. `AcpEnvelope`
      also carries `session_id` (docs/decisions.md #19) — task §9's
      observation metadata needs it, beyond §4's own four fields.
- [x] Router has no tools, returns only structured classification.
- [x] Docs Agent returns answer, sources, confidence (0–1). `DocsResponse`
      populated from real KB+MCP retrieval, `Source.retrieved_at` stamped
      at actual fetch time (docs/decisions.md #15).
- [x] Web Search Agent gets no personal user data, does not confirm
      cart/bonus/order state — takes only the already-masked text
      (docs/decisions.md #14), never calls Silpo MCP.
- [ ] Escalation Agent produces an operator-readable report, saves to file,
      sends Telegram notification.

## Data sources and tools (§5)

- [x] Silpo MCP `tools/list` executed before implementation — 39 tools
      (`docs/silpo_mcp_tools.json`, 2026-08-25), allowlist derived in
      `docs/silpo_mcp_allowlist.md`.
- [x] Internal knowledge base: 15–25 FAQ answers, 2–5 pages of service
      descriptions, 5–10 example dialogues. `data/knowledge_base/`: 18
      FAQ, 3 service pages, 6 example dialogues, synthetic, Ukrainian.
- [x] Every knowledge-base document has a source, retrieval date, rule
      version. `src/infrastructure/retriever.py`'s `KnowledgeChunk`.
- [x] Web search used only for current general info absent from internal
      sources — Router routes `general` category to Web Search Agent,
      `product`/service queries to Docs Agent (`src/domain/routing.py`).
- [ ] File system stores escalation reports; Telegram sends to a test
      channel.

## Data contracts (§6) — four mandatory Pydantic models

- [x] `ClassificationOutput`: category (Literal: product, general, critical),
      urgency (Literal: low, medium, critical), language.
- [x] `DocsResponse`: answer, sources, confidence (0–1). `sources` is
      `list[Source]` (ref, retrieved_at, version), not `list[str]` —
      docs/decisions.md #15.
- [x] `WebSearchResponse`: answer, sources, confidence (0–1).
- [x] `EscalationOutput`: summary, category, customer_message,
      attempted_resolution.
- [x] LangGraph state carries: original request, classification, search
      results, answer, confidence, errors, session/trace ids, next action.
      `src/domain/state.py`'s `SupportFlowState` — carries
      `original_request_masked`, never the raw request (docs/decisions.md
      #14).

## Workflow sequence (§7)

- [x] Step 1: input filter — language, domain bounds, personal/forbidden
      data. `src/domain/filters.py`, 100% test coverage
      (docs/decisions.md #10).
- [x] Step 2: Supervisor calls Router Agent via ACP.
- [x] Step 3: critical request → Escalation Agent immediately. Routing
      decision only — Escalation Agent itself is Stage 3
      (docs/decisions.md #16); the graph edge dispatches correctly and
      raises `NotImplementedError` there until built.
- [x] Step 4: product/rules/service request → Docs Agent (knowledge base +
      Silpo MCP). `src/application/supervisor.py`'s `docs_node`, real A2A
      call to `src/interfaces/docs_a2a_server.py`.
- [x] Step 5: general request needing current external info → Web Search
      Agent. `web_search_node`, real A2A call.
- [x] Step 6: low confidence, contradictory sources, or unavailable tool →
      Escalation Agent. Routing decision only — `docs_node`/`web_search_node`
      both route a below-threshold confidence or a tool failure to
      `escalate`; Escalation Agent itself is Stage 3
      (docs/decisions.md #16), the edge raises `NotImplementedError` there
      until built. Contradictory sources: self-reported confidence, no
      separate detector (docs/decisions.md #25).
- [x] Step 7: successful route → Supervisor composes a short final answer
      with sources. `docs_node`/`web_search_node` set `state["answer"]`
      directly from the agent's own composed `answer` field on a
      confident response.
- [ ] Step 8: Langfuse stores the full call tree and auto-eval results.

## Code organization and web UI (§8)

- [ ] Clean Architecture is the **single** architecture for code modules
      (domain / application / infrastructure / interfaces).
- [ ] FastAPI provides the API; CORS open only to the local frontend.
- [ ] Web UI — deliberately simple React 19 app (Vite build): **one chat
      page**.
- [ ] `config/models.yaml` — OpenRouter model, temperature, max tokens,
      timeout, confidence threshold per agent.
- [ ] Settings page — optional extension, only if time remains after the
      mandatory scope.
- [ ] Access keys live server-side in environment variables, never shown in
      the UI.
- [ ] Files ≤250 lines preferred; 250–320 lines allowed if single
      responsibility; larger files split.

## Langfuse and data protection (§9)

- [ ] One trace per run: Supervisor → agent → model → tool.
- [ ] Every step stores duration, error, model used, token count, cost.
- [ ] Agents continue the received trace — no disconnected traces for one
      request (disconnected traces are an error).
- [x] System instructions of all four agents (+ Supervisor) stored and
      versioned in Langfuse Prompt Management — `scripts/seed_prompts.py`,
      zero-shot baseline, label `production`, verified via `auth_check()`
      before seeding.
- [ ] Langfuse auto-evaluates new traces via LLM-as-a-Judge.
- [ ] No access keys, OAuth tokens, full addresses, phones, emails, payment
      data, or raw personal data in Langfuse.
- [ ] Synthetic users in the demo, knowledge base, and golden dataset.
- [ ] Personal data stripped before web search; if impossible without losing
      meaning, web search is not called.
- [x] Silpo MCP access limited to an allowed list of read operations.
      Code-level enforcement, not just a prompt instruction —
      `src/infrastructure/silpo_mcp.py`'s `SILPO_ALLOWLIST`/
      `call_mcp_tool` (docs/decisions.md #24), verified live.
- [ ] LangChain/LangGraph traced via Langfuse `CallbackHandler`; Silpo MCP,
      ACP, Telegram, File System traced via their own Langfuse observations.
- [ ] `CallbackHandler` passed into every `graph.invoke` run config.
- [ ] Separate observations for Silpo MCP, ACP, Telegram, File System,
      PII scrubbing, Supervisor logic — with safe metadata (agent/tool name,
      category, urgency, confidence, session id, status, duration, retry
      count, error type, model, prompt version).
- [ ] Supervisor passes Langfuse trace ids through ACP; agent continues the
      received trace.
- [ ] User ids pseudonymized.
- [ ] Flush/shutdown before process exit in short-lived tests/CLI runs.
- [ ] Readiness criterion: one end-to-end run produces one trace with
      Supervisor, chosen agent, model, tools, final answer, and scores; no
      disconnected records.

## Testing and automated evaluation (§10)

- [ ] Golden dataset: 18 cases in JSON — **6 typical scenarios, 6 edge
      cases, 6 failure scenarios** (not the routing categories from §6).
- [ ] Each case has `input`, `expected_output`, expected route, allowed/
      forbidden tools, personal-data flags.
- [ ] Explicitly covered: Silpo MCP unavailability, OAuth error, timeout
      exceeded, insufficient evidence, out-of-domain request, critical
      escalation.
- [x] Router: ≥10 classification queries with known-correct categories.
      n=12 held-out set, ≥10/12 across 3 runs (10, 10, 11 — mean 0.861),
      `google/gemini-3.5-flash-lite`, `output/router_gate_result.json`
      (docs/decisions.md #17). Not the full 18-case golden dataset —
      that is Stage 4 scope.
- [ ] Docs Agent: answer grounded in knowledge base or Silpo MCP, no
      fabricated facts; out-of-base query → low confidence → escalation.
- [ ] Web Search Agent: every claim backed by a returned web source.
- [ ] Escalation Agent: report has enough context, prior attempted steps,
      and a clear customer message.
- [ ] Supervisor: all conditional routes, confidence threshold, escalation
      transitions tested.
- [ ] Tool-correctness checks: Router calls no tools; Docs calls Silpo MCP
      only for domain queries; Web Search calls Tavily/DuckDuckGo only for
      allowed external queries; Escalation writes a file and sends a real
      test Telegram message.
- [ ] Full golden dataset run via `deepeval test run tests/`.
- [ ] Core metrics (0–1): Answer Relevancy, Groundedness (⇒ DeepEval
      `FaithfulnessMetric`, no metric literally named "Groundedness").
- [ ] Additional metrics: Route Correctness, Tool Correctness, Privacy
      Safety.
- [ ] Custom GEval metric: Support Resolution Quality.
- [ ] **Initial thresholds are set after the first full run.** Orientation
      values: Answer Relevancy ≥0.70, Groundedness ≥0.75, Support Resolution
      Quality ≥0.70. These are NOT a pre-first-run gate.
- [ ] Test coverage for domain + application logic ≥80%; critical routes
      and data protection fully covered.

## Repository structure and deliverables (§11)

- [ ] `src/domain`, `src/application`, `src/infrastructure`, `src/interfaces`.
- [ ] `data/knowledge_base`, `evals/golden_dataset.json`, `tests/`.
- [ ] README: run instructions, architecture diagram, env var setup, known
      limitations, test commands.
- [ ] Defense artefacts: Langfuse dashboard, DeepEval report, coverage
      report, short video or live demo.

## Success criteria (§13)

- [ ] Four mandatory agents. 3/4 real (Router, Docs, Web Search); Escalation
      is Stage 3.
- [x] Classification by category, urgency, language. Stage 1 Router gate.
- [x] RAG over internal knowledge base. Chroma+BM25 `EnsembleRetriever`
      (docs/decisions.md #7).
- [x] DuckDuckGo as fallback for Web Search Agent. `ddgs`, verified live
      fallback path (`src/infrastructure/web_search.py`).
- [ ] File + real Telegram notification for escalation. Stage 3.
- [x] Four mandatory Pydantic models. `src/domain/schemas.py`.
- [x] LangGraph StateGraph, conditional transitions, low-confidence
      fallback. `docs_node`/`web_search_node` both escalate below
      `config/models.yaml`'s `confidence_threshold`.
- [ ] Langfuse across the whole path. Stage 4.
- [ ] LLM-as-a-Judge and ≥10 Router Agent scenarios. Router's ≥10/12 gate
      is done (Stage 1); LLM-as-a-Judge is Stage 4.
- [ ] Repository, README with instructions, diagram, tests, Langfuse
      dashboard, video/live demo.
- [x] Silpo MCP as the primary domain data source. Real persistent-token
      client, 17-tool allowlist, verified live against the real account
      (docs/decisions.md #27).

## Explicitly out of scope / read-only (§1)

- [ ] System is read-only: no order payment, no cart changes, no financial
      operations.
