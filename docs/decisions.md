# Numbered decisions

Every point where the plan review diverges from a literal reading of
`docs/task-supportflow.md`, or resolves something the task leaves open, is
recorded here with its reasoning — never as a silent choice made while
writing code.

## 1. Hybrid transport instead of a single ACP process

Task §2/§4/§12 describes ACP as an entirely in-process contract. This
project splits it: Router and Escalation stay in-process with the
Supervisor; Docs and Web Search run as separate A2A servers
(`a2a-sdk==1.1.2`).

**Why:** Router sits on every request's hot path and has no tools (§4) — a
network hop would add latency and a failure point for no benefit. Escalation
is the designated fallback when a tool is unavailable (§7) — making it
networked means the fallback can fail the same way it exists to catch. Docs
and Web Search are long-running, tool-using, and isolatable — their
separation is where A2A actually demonstrates something, and it is the only
place in this project where the real Agent2Agent protocol is exercised.

**Consequence carried forward:** every process that makes an LLM call must
run its own Langfuse span exporter, and trace context must be explicitly
propagated across each A2A hop — the one reference project with a fully
networked topology (`MA_systems_hl10`, outside this repo) shipped without
either and its UI showed supervisor-only cost, a direct miss of task §9.

**Status:** not a violation of §13 (success criteria) — §13 never mentions
ACP by name. It is a deviation from §2/§4/§12's literal architecture
description, recorded rather than silently made.

## 2. Personal Silpo MCP data — deferred

Task §9 requires synthetic users in the demo, knowledge base, and golden
dataset, and forbids raw personal data in Langfuse. Silpo MCP
authentication is phone + OTP against a real account, and its personal
tools (`*_my_orders`, `*_my_profile`, loyalty) return a real person's data.
No synthetic account to log in as exists.

**Default until resolved:** Docs Agent's Silpo MCP allowlist contains only
non-personal, read-only tools (products, prices, promotions, categories,
stores). Extending to personal tools requires working PII masking plus a
Privacy Safety evaluator check on that masking, and is itself a numbered
decision to be made at Stage 2 once the real `tools/list` response is on
disk.

## 3. Silpo MCP OAuth and tool count — confirmed from the author's own prior work

The author's Colab notebook (`Silpo_scenario_lab4.ipynb`, real experiments
against `https://mcp.silpo.ua/mcp` from 2026-08-13) confirms real numbers
that supersede the prose-documentation estimate used in early planning:
**39 tools total, 33 with `readOnlyHint: true`, 6 write tools.** The 6
write tools, verbatim from a live run: `silpo_add_or_update_cart_products`,
`silpo_add_or_update_certificates`, `silpo_add_or_update_favorite_products`,
`silpo_clear_shopping_cart`, `silpo_remove_cart_products`,
`silpo_update_shopping_cart`.

Confirmed protocol detail: `PROTOCOL_VERSION = "2025-06-18"` (not the
"2026-07-28" revision found in general MCP-SDK research — Silpo's server
answers the older revision). OAuth flow works via Dynamic Client
Registration with `token_endpoint_auth_method: "none"` (public client, no
secret) — confirmed live (`client_id: vhnmN8q4F5AQahsJ` from a real run).

The full 33-name read-only list is not embedded in the notebook's own
outputs — it only prints counts. `scripts/probe_silpo_mcp.py`
(hand-rolled `requests`, ported from this notebook rather than the `mcp`
SDK, since the notebook is proven working and an SDK-based guess is not)
writes the complete `tools/list` response, names included, to
`docs/silpo_mcp_tools.json` when the author runs it.

**Allowlist boundary this sets:** `readOnlyHint` from the server itself is
the allowlist source (§9's "allowed list of read operations"), not a
list this project writes by hand — the same discovery-vs-authorization
split used by the notebook's own `READ_ALLOWLIST` construction.

**Update, same day, after running the probe live:** real `tools/list`
confirms 33 read-only tools (`docs/silpo_mcp_tools.json`). Split further by
description into 17 non-personal (default Docs Agent allowlist) and 16
personal (excluded per Decision 2) — full reasoning and the exact list in
`docs/silpo_mcp_allowlist.md`. Two tools (`silpo_get_loyalty_info`,
`silpo_get_promo_codes`) are personal despite not matching the
`silpo_get_my_*` name prefix; classification used each tool's own
description text, not a name-pattern heuristic.

## 4. `project_support.md` — missing input

Task §12.1 asks to preserve a copy of `project_support.md`; the Sources
section lists it as the course specification. No such file exists on this
machine (`docs/requirements-checklist.md`, "Open gap"). Recorded as a
blocker for whatever step needs that file's content specifically — not
filled with an invented specification.

## 5. Token persistence across process/machine restarts

`scripts/probe_silpo_mcp.py`'s in-memory `TokenStorage` is correct for a
one-off probe and wrong for the production Docs Agent MCP client — a
restart (process crash, computer reboot) would otherwise force a fresh
phone+OTP login every time, which is not viable for an unattended agent
process and contradicts the hackathon requirement that tokens are stored
server-side rather than re-obtained interactively.

**Required for Stage 2, not yet built:** a persistent `TokenStorage`
implementation (task's own `mcp.client.auth.TokenStorage` interface) that
writes the access + refresh token to local disk (encrypted or at minimum
outside the repo — `.cache/silpo_mcp_token.json`, gitignored, never
logged, never sent to Langfuse per §9) and, on startup, tries
`refresh_token` before falling back to a fresh interactive OAuth flow.
Silpo's own docs name `401 invalid_token` as the expected refresh trigger.

**Interactive login stays a manual, human step** — Silpo's OAuth is
phone+OTP against a real account, so no code may automate the login itself
(the redirect_handler cannot skip a human). What must be automated is
*not needing that human step again* after the first successful login, for
as long as the refresh token stays valid. If the refresh token itself
expires or is revoked, the system should fail loudly (escalate / alert),
not silently degrade to "Silpo MCP unavailable" the way a golden-dataset
failure case (§10: "Silpo MCP unavailability") expects.

## 6. Human-in-the-loop scope

Escalation's real Telegram send and file write are gated behind
human-in-the-loop confirmation in interactive/demo mode, and behind an
explicit bypass flag during automated runs (golden-dataset evaluation,
`deepeval test run tests/`). The flag's exact name, default, and where it
lives (`config/models.yaml` vs. a separate runtime setting) is deferred to
Stage 3, when Escalation Agent is actually built.
