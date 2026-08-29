"""LangGraph node functions and graph assembly — split out of
`supervisor.py` as a same-PR file-size prerequisite: `supervisor.py` grew
past its file-size ceiling once tracing instrumentation landed.
`supervisor.py` keeps the request-handling entrypoint
(`handle_request`/`build_initial_state`); this module owns the graph's
own nodes, conditional edges, and compilation — a genuine responsibility
split (request orchestration vs. graph definition), not a
constants-only extraction.

The graph wired here is the real one — real Router node, real
conditional edges dispatching on `decide_route()`.
"""

import logging
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Any

from a2a.client.errors import A2AClientTimeoutError, AgentCardResolutionError
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from src.application.escalation_agent import EscalationContext, run_escalation_agent
from src.application.router_agent import run_router
from src.domain.filters import mask_pii
from src.domain.routing import decide_route
from src.domain.state import (
    ErrorType,
    NextAction,
    SupportFlowState,
    format_conversation_history,
)
from src.infrastructure.a2a_transport import A2ATimeoutError
from src.infrastructure.docs_client import (
    DocsInvalidResponseError,
    DocsUnavailableError,
    call_docs_agent,
)
from src.infrastructure.observability import get_langfuse_client
from src.infrastructure.web_search_client import (
    WebSearchInvalidResponseError,
    WebSearchUnavailableError,
    call_web_search,
)
from src.kernel.settings import load_agent_config

# Without this, every A2A failure reached the customer as a bare
# `*_unavailable` tag with its cause discarded — unreproducible by
# design. The text goes to the process log only, never to Langfuse or
# to the customer-facing answer.
logger = logging.getLogger(__name__)

# Module-level, process-lifetime, single instance — the checkpointed
# state lives in this object, not in the compiled graph `build_graph()`
# returns fresh on every request, so it must outlive any one request the
# same way `escalation_agent.py`'s own `_session_store` does (decision
# #28). In-memory only, unbounded, no eviction: correct for this
# project's single-process demo topology, wrong the moment a second
# worker process or a restart-survival requirement exists — same
# `# ponytail` ceiling as `_session_store`, upgrade to a shared store
# (Redis, a DB row) if that changes. See `docs/decisions.md` #77.
_checkpointer = InMemorySaver()


def reset_checkpointer() -> None:
    """Test-only: replace the module-level checkpointer with a fresh one.

    Without this, every test that calls `handle_request` shares one
    `session_id`'s worth of accumulated `errors`/`conversation_history`
    with every other test using the same id — an autouse fixture in
    `tests/conftest.py` calls this between tests so the suite stays
    order-independent.
    """
    global _checkpointer
    _checkpointer = InMemorySaver()


def _current_observation_id() -> str | None:
    """The active Langfuse observation id, or `None` when tracing is
    disabled — read fresh at each A2A call site rather than threaded
    through `SupportFlowState`.
    """
    client = get_langfuse_client()
    return client.get_current_observation_id() if client is not None else None


def router_node(state: SupportFlowState) -> dict[str, Any]:
    """Runs `router_agent.run_router`, then either routes via
    `decide_route()` on success or fails closed to Escalation on
    exhaustion.
    """
    client = get_langfuse_client()
    span_cm = (
        client.start_as_current_observation(name="supervisor.routing", as_type="span")
        if client is not None
        else nullcontext()
    )
    with span_cm as span:
        result = run_router(
            state["original_request_masked"],
            state["request_id"],
            state["session_id"],
            state["trace_id"],
            conversation_history=format_conversation_history(
                state["conversation_history"]
            ),
        )
        if result.classification is None:
            if span is not None:
                span.update(metadata={"next_action": "escalate"})
            return {
                "next_action": "escalate",
                "errors": result.errors,
                "retry_count": result.retry_count,
            }
        next_action = decide_route(result.classification)
        if span is not None:
            span.update(
                metadata={
                    "category": result.classification.category,
                    "urgency": result.classification.urgency,
                    "next_action": next_action,
                }
            )
        return {
            "classification": result.classification,
            "router_prompt_version": result.prompt_version,
            "next_action": next_action,
            "errors": result.errors,
            "retry_count": result.retry_count,
        }


def docs_node(state: SupportFlowState) -> dict[str, Any]:
    """Calls Docs Agent over A2A and routes on the result: a model failure
    or a below-threshold confidence both escalate.
    Mirrors `web_search_node` exactly — same no-retry rationale.
    """
    config = load_agent_config("docs")
    deadline = datetime.now(timezone.utc) + timedelta(seconds=config.timeout_seconds)
    errors: list[ErrorType] = []
    try:
        result = call_docs_agent(
            state["original_request_masked"],
            state["request_id"],
            state["session_id"],
            state["trace_id"],
            deadline,
            parent_span_id=_current_observation_id(),
            conversation_history=format_conversation_history(
                state["conversation_history"]
            ),
        )
    except (A2ATimeoutError, A2AClientTimeoutError) as exc:
        logger.warning("docs_timeout after %ss: %s", config.timeout_seconds, exc)
        # A2ATimeoutError: this project's own pre-flight deadline check.
        # A2AClientTimeoutError: the a2a-sdk's own runtime network
        # read-timeout — found live during a smoke test (Docs Agent's
        # first-request retriever cold-start exceeded
        # config/models.yaml's docs.timeout_seconds), never triggered
        # before because no prior live run hit a slow-enough first call.
        return {"next_action": "escalate", "errors": ["docs_timeout"]}
    except (DocsUnavailableError, AgentCardResolutionError) as exc:
        logger.warning("docs_unavailable: %s", exc)
        # AgentCardResolutionError: the agent's process never answered the
        # `/.well-known/agent-card.json` probe — literally "agent
        # unavailable", the same escalation a tool failure already
        # takes. Found live 2026-08-26: uncaught, it propagated out of
        # the graph and surfaced as an HTTP 500 from `/chat` (a browser-
        # visible crash) instead of the graceful escalation every other
        # transport failure on this path already produces.
        return {"next_action": "escalate", "errors": ["docs_unavailable"]}
    except DocsInvalidResponseError as exc:
        logger.warning("docs_invalid_response: %s", exc)
        return {"next_action": "escalate", "errors": ["docs_invalid_response"]}

    if (
        config.confidence_threshold is not None
        and result.response.confidence < config.confidence_threshold
    ):
        errors.append("docs_low_confidence")
        return {
            "docs_response": result.response,
            "retrieval_context": result.retrieval_context,
            "tools_called": result.tools_called,
            "confidence": result.response.confidence,
            "next_action": "escalate",
            "errors": errors,
        }

    return {
        "docs_response": result.response,
        "retrieval_context": result.retrieval_context,
        "tools_called": result.tools_called,
        "answer": result.response.answer,
        "answer_prompt_version": result.prompt_version,
        "confidence": result.response.confidence,
        "next_action": "respond",
        "errors": errors,
        "conversation_history": [
            {
                "customer": state["original_request_masked"],
                "answer": result.response.answer,
            }
        ],
    }


def web_search_node(state: SupportFlowState) -> dict[str, Any]:
    """Calls Web Search Agent over A2A and routes on the result: a tool
    failure or a below-threshold confidence both escalate. No retry
    loop here — unlike Router, Web Search does not sit on
    every request, so one failed attempt escalating directly is the lazy,
    sufficient default until measurement says otherwise.
    """
    config = load_agent_config("web_search")
    deadline = datetime.now(timezone.utc) + timedelta(seconds=config.timeout_seconds)
    errors: list[ErrorType] = []
    try:
        result = call_web_search(
            state["original_request_masked"],
            state["request_id"],
            state["session_id"],
            state["trace_id"],
            deadline,
            parent_span_id=_current_observation_id(),
            # Masked, not just the plain formatter Router/Docs use: Web
            # Search "gets no personal user data" (CLAUDE.md invariant) —
            # `original_request_masked` already covers each turn's
            # customer half, but a prior turn's `answer` was never masked
            # (Supervisor composes it from grounded, non-personal
            # sources, but nothing guarantees that structurally). Masking
            # the whole rendered block is defense in depth, not a
            # per-field split, and idempotent on the already-masked half.
            conversation_history=mask_pii(
                format_conversation_history(state["conversation_history"])
            ),
        )
    except (A2ATimeoutError, A2AClientTimeoutError) as exc:
        logger.warning("web_search_timeout after %ss: %s", config.timeout_seconds, exc)
        return {"next_action": "escalate", "errors": ["web_search_timeout"]}
    except (WebSearchUnavailableError, AgentCardResolutionError) as exc:
        logger.warning("web_search_unavailable: %s", exc)
        # See `docs_node`'s own note — same live-found HTTP-500 path.
        return {"next_action": "escalate", "errors": ["web_search_unavailable"]}
    except WebSearchInvalidResponseError as exc:
        logger.warning("web_search_invalid_response: %s", exc)
        return {"next_action": "escalate", "errors": ["web_search_invalid_response"]}

    if (
        config.confidence_threshold is not None
        and result.response.confidence < config.confidence_threshold
    ):
        errors.append("web_search_low_confidence")
        return {
            "web_search_response": result.response,
            "retrieval_context": result.retrieval_context,
            "tools_called": result.tools_called,
            "confidence": result.response.confidence,
            "next_action": "escalate",
            "errors": errors,
        }

    return {
        "web_search_response": result.response,
        "retrieval_context": result.retrieval_context,
        "tools_called": result.tools_called,
        "answer": result.response.answer,
        "answer_prompt_version": result.prompt_version,
        "confidence": result.response.confidence,
        "next_action": "respond",
        "errors": errors,
        "conversation_history": [
            {
                "customer": state["original_request_masked"],
                "answer": result.response.answer,
            }
        ],
    }


def escalate_node(state: SupportFlowState) -> dict[str, Any]:
    """The last step for a critical request, a request Supervisor could
    not resolve confidently, or a request where a tool was unavailable.
    Runs in-process — no A2A hop, so this fallback does not itself depend
    on the network path it exists to catch a failure of.
    """
    context = EscalationContext(
        masked_text=state["original_request_masked"],
        classification=state["classification"],
        confidence=state["confidence"],
        errors=state["errors"],
        docs_response=state["docs_response"],
        web_search_response=state["web_search_response"],
    )
    result = run_escalation_agent(
        context, request_id=state["request_id"], session_id=state["session_id"]
    )
    return {
        "escalation_output": result.output,
        "answer": result.output.customer_message,
        "escalation_count": state["escalation_count"] + 1,
        "report_written": result.written,
        "telegram_sent": result.sent,
        "conversation_history": [
            {
                "customer": state["original_request_masked"],
                "answer": result.output.customer_message,
            }
        ],
    }


def route_after_router(state: SupportFlowState) -> NextAction:
    """The conditional-edge selector. `router_node` always sets
    `next_action` to one of `"docs"`, `"web_search"`, or `"escalate"` —
    never leaves it at its initial `"router"` value.
    """
    return state["next_action"]


def route_after_web_search(state: SupportFlowState) -> NextAction:
    """`web_search_node` always sets `next_action` to `"respond"` or
    `"escalate"` — never leaves it at `"web_search"`, since a
    below-threshold confidence or a tool failure both fall back to
    escalation.
    """
    return state["next_action"]


def route_after_docs(state: SupportFlowState) -> NextAction:
    """`docs_node` always sets `next_action` to `"respond"` or
    `"escalate"` — same contract as `route_after_web_search`.
    """
    return state["next_action"]


def build_graph() -> Any:
    """Compile the graph: `router` → conditional edge → one of
    `docs` / `web_search` / `escalate`.

    Returns
    -------
    CompiledStateGraph
    """
    graph = StateGraph(SupportFlowState)
    graph.add_node("router", router_node)
    graph.add_node("docs", docs_node)
    graph.add_node("web_search", web_search_node)
    graph.add_node("escalate", escalate_node)
    graph.set_entry_point("router")
    graph.add_conditional_edges(
        "router",
        route_after_router,
        {"docs": "docs", "web_search": "web_search", "escalate": "escalate"},
    )
    graph.add_conditional_edges(
        "docs",
        route_after_docs,
        {"respond": END, "escalate": "escalate"},
    )
    graph.add_conditional_edges(
        "web_search",
        route_after_web_search,
        {"respond": END, "escalate": "escalate"},
    )
    graph.add_edge("escalate", END)
    return graph.compile(checkpointer=_checkpointer)
