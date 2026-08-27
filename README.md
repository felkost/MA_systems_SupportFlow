# SupportFlow

SupportFlow is a customer-support chat assistant. It only reads data — it
never changes an order, a cart, or a customer account.

A "Supervisor" reads each customer message and decides what to do:

- send it to the **Docs Agent** (answers from a knowledge base and the
  Silpo product catalog),
- send it to the **Web Search Agent** (answers from a live web search),
- or **escalate** it to a human operator on Telegram.

The Supervisor uses LangGraph to run this decision as a small graph of
steps.

## How the parts talk to each other

Router and Escalation run inside the same process as the Supervisor. Docs
Agent and Web Search Agent run as separate processes and talk over a
protocol called A2A ("agent to agent"). The browser only talks to the
FastAPI backend — never straight to an agent or to Silpo, Tavily, or
Telegram.

```mermaid
flowchart LR
    Browser["React chat UI\n(Vite dev server)"] -->|"POST /chat"| API["FastAPI\nsrc/interfaces/api.py"]
    API -->|"handle_request()"| Supervisor["Supervisor\nLangGraph StateGraph"]
    Supervisor --> Router["Router Agent\n(in-process, no tools)"]
    Supervisor -->|"A2A"| Docs["Docs Agent\n(A2A server :8801)"]
    Supervisor -->|"A2A"| WebSearch["Web Search Agent\n(A2A server :8802)"]
    Supervisor --> Escalation["Escalation Agent\n(in-process)"]
    Docs --> SilpoMCP["Silpo MCP"]
    Docs --> KB["Knowledge base\n(Chroma+BM25)"]
    WebSearch --> Tavily["Tavily / DuckDuckGo"]
    Escalation --> Telegram["Telegram"]
    Escalation --> Files["output/escalations/*.json"]
```

A case escalates when: the category is critical, the answer's confidence
is too low, or a tool failed. When that happens, Escalation Agent writes
a report file and sends one message to a human operator on Telegram. This
is one-way — nothing comes back from Telegram into the system.

## How one chat message flows through the app

Open the URL the Vite dev server prints (usually
`http://localhost:5173`). This is the chat page. Each message you send
becomes one `POST /chat` call:

```mermaid
sequenceDiagram
    participant Browser
    participant API as FastAPI (/chat)
    participant Supervisor
    participant Graph as LangGraph (docs | web_search | escalate)

    Browser->>API: POST /chat {message, session_id}
    API->>API: bypass_hitl scoped True for this call
    API->>Supervisor: handle_request(message, request_id, session_id, trace_id)
    Supervisor->>Graph: run_input_filter -> graph.invoke
    Graph-->>Supervisor: final SupportFlowState (answer, sources, next_action)
    Supervisor-->>API: SupportFlowState
    API-->>Browser: ChatResponse {answer, sources, confidence, escalated}
```

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env   # fill in OpenRouter, Silpo MCP, Tavily, Telegram, Langfuse keys
git config core.hooksPath hooks   # after every commit, sends the eval data to Langfuse
```

Do this once, by hand — Silpo's login needs a real phone number and a
one-time code, so a script cannot do it for you:

```bash
python scripts/silpo_mcp_login.py
```

Do this once, by hand, to check your Telegram bot works
(`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` in `.env`):

```bash
python -m scripts.telegram_bot_setup_check
```

Then start three things, each in its own terminal:

```bash
python -m src.interfaces.launcher
```

```bash
.venv/Scripts/uvicorn src.interfaces.api:app --reload --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

**Restarting.** The API (`uvicorn`) does not auto-reload every kind of
change. If you edit code under `src/application/` or `src/interfaces/`,
or you change `.env`, stop and restart the API terminal. If you edit
`src/application/docs_agent.py`, `src/application/web_search_agent.py`,
or the A2A server files, restart the launcher terminal too — it can take
about 80 seconds to be ready again, because Docs Agent loads its search
index at startup.

## Gate

Run this before you say a change is done:

```bash
black --check . tests/*.py && flake8 && mypy src && pytest --cov=src
```

This command never calls a real service (no Silpo, Tavily, OpenRouter, or
Telegram call). To test against the real services, run one of these by
hand: `scripts/docs_agent_smoke.py`, `scripts/run_router_gate.py`,
`scripts/escalation_agent_smoke.py`.

Escalation Agent only sends a real Telegram message when
`ALLOW_REAL_SEND=true` is set.

## Data

`data/knowledge_base/` holds three small JSON files. Docs Agent reads
them to answer questions — this is its "knowledge base".

| File | What is inside |
|---|---|
| `faq.json` | Common questions and answers (returns, delivery, loyalty program, and so on). |
| `services.json` | Short descriptions of Silpo's services, like the loyalty program. |
| `dialogues.json` | Example conversations between a customer and a human operator. |

Every entry has an `id`, the text, a source, and a date. Docs Agent turns
these files into a search index (Chroma + BM25) when it starts, and
searches it for every customer question. If you add or edit an entry,
restart the launcher so it rebuilds the index.

**Why the index is built at every start, and not saved to disk.** Docs
Agent takes about 57 seconds to become ready. Measured, that time is:

| Step | Time |
|---|---|
| Load the AI model that turns text into vectors | ~52 s |
| Build the search index from the 27 knowledge-base entries | ~1 s |

Saving the index to disk would only save that ~1 second. The model itself
must still be loaded into memory every time, because Docs Agent needs it
to turn each *customer question* into a vector before it can search. So a
separate "build the index once" script would not make startup faster
here.

This answer depends on the size of the knowledge base. With 27 entries,
building the index is free. With tens of thousands of documents, it would
not be — and then saving the index to disk would be worth doing.

## Scripts

`scripts/` holds small programs you run by hand. The test command above
(the "gate") never runs them. The one exception is
`scripts/experiment_stats.py` — that file is a helper library, not a
program you run directly; `compare_prompt_versions.py` uses it. For every
other file, run it like this:

```bash
.venv/Scripts/python scripts/<name>.py
```

Some of these scripts cost real money (they call a paid AI model). Always
check the script's own message about cost before you type "yes".

| Script | What it does |
|---|---|
| `silpo_mcp_login.py` | Logs in to the real Silpo MCP service. Needs a phone and a one-time code, so you do this by hand. |
| `silpo_mcp_healthcheck.py` | Checks that the saved Silpo MCP login still works. |
| `probe_silpo_mcp.py` | Lists the tools Silpo MCP offers right now. Used to build `docs/silpo_mcp_allowlist.md`. |
| `telegram_bot_setup_check.py` | Checks that your Telegram bot and chat settings are correct. |
| `seed_prompts.py` | Uploads the four agent prompts to Langfuse, under the "production" label. |
| `configure_langfuse_evaluator.py` | Sets up two automatic quality checks in Langfuse: one for Docs/Web Search answers, one for Escalation handoffs. |
| `sync_dataset.py` | Copies the three test-case files (below) to Langfuse. Runs itself after every commit. |
| `docs_agent_smoke.py`, `escalation_agent_smoke.py`, `observability_smoke.py`, `golden_dataset_smoke.py` | Quick one-case checks against the real services. Run one of these first, before a bigger (and more expensive) run. |
| `run_golden_dataset_baseline.py` | Runs the full 18-case test set for real and saves the score. This score becomes the pass/fail line for future test runs. |
| `run_router_gate.py` | Runs Router three times on 12 held-out test messages and reports its accuracy. |
| `seed_candidate_prompts.py` | Builds a new, "candidate" version of one prompt (with extra examples) and uploads it to Langfuse — without changing the live "production" version. |
| `compare_prompt_versions.py` | Runs both the old ("production") and new ("candidate") prompt on the same test cases and reports which one scored better, with statistics. Asks you to type "run" before it spends money. |
| `meta_prompt_docs.py`, `chart_meta_prompt_comparison.py` | An older way of testing a new Docs prompt (an AI model rewrote the prompt itself). Kept for its saved results; the newer way is `seed_candidate_prompts.py` + `compare_prompt_versions.py`. |
| `experiment_smoke.py` | Sends one real chat message and shows you what Langfuse recorded for it — the fast check before running a big, paid experiment. |

## `output/`

Scripts save their results here. Git ignores everything in this folder,
except two files:

| File | Saved in Git? | Made by |
|---|---|---|
| `deepeval_baseline.json` | yes | `run_golden_dataset_baseline.py`. This is the score every future test run is compared against. |
| `router_gate_result.json` | yes | `run_router_gate.py`. Router's saved accuracy score. |
| `escalations/*.json` | no | Escalation Agent. One file per real escalated case. |
| Other files (`*-comparison.json`, `*-baseline.json`, …) | no | `compare_prompt_versions.py`, `meta_prompt_docs.py`. One file per manual run — you can delete these any time and make them again by re-running the script. |

## Diagrams

`report/` holds the project's diagrams. Each file is one `.html` page you
can open in a browser — it has a picture plus text explaining it. Most
also have a matching `.svg` file.

- `architecture.html` — the whole system in one picture: browser → API →
  Supervisor → Docs/Web Search agents (or straight to Escalation).
- `supervisor-graph.html` — the steps inside the Supervisor's LangGraph.
- `router-sequence.html` — how Router classifies one message, and what
  happens if it fails.
- `escalation-sequence.html` — Escalation's steps: write the report, hide
  private data, ask for confirmation, send it, and save the file.
- `docs-agent-sequence.html` — how Docs Agent searches the knowledge base
  and the Silpo catalog.
- `web-search-agent-sequence.html` — how Web Search Agent tries Tavily
  first, then DuckDuckGo if that fails.
- `langfuse-observability.html` — what SupportFlow sends to Langfuse, and
  how one chat message becomes one trace across three processes.
- `prompt-architecture.html` (+ `prompt-*.svg`) — the four agent prompts,
  their structure, and the two prompt experiments run against them (both
  came back "inconclusive" — no prompt was changed because of them).
