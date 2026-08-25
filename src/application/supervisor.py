"""Supervisor: builds the LangGraph `StateGraph` and runs the input filter
ahead of it (docs/decisions.md #14 — masking is a precondition of
entering the graph, never a node inside it, so there is no
`input_filter` node here).

docs/decisions.md #16: the graph wired here is the real one — real Router
node, real conditional edges dispatching on `decide_route()` — but
Docs/Web Search/Escalation are not built until Stage 2/3, so their nodes
raise `NotImplementedError` naming the owning stage rather than returning
a fabricated result. A graph test can then assert an edge reaches the
correct (still-unimplemented) node without any stub silently "passing".
"""

from typing import Any

from langgraph.graph import END, StateGraph

from src.application.router_agent import run_router
from src.domain.filters import run_input_filter
from src.domain.routing import decide_route
from src.domain.state import NextAction, SupportFlowState
from src.kernel.constants import GRAPH_RECURSION_LIMIT


def router_node(state: SupportFlowState) -> dict[str, Any]:
    """The one real agent node in Stage 1.

    Runs `router_agent.run_router`, then either routes via
    `decide_route()` on success or fails closed to Escalation on
    exhaustion (docs/decisions.md #12).
    """
    result = run_router(
        state["original_request_masked"],
        state["request_id"],
        state["session_id"],
        state["trace_id"],
    )
    if result.classification is None:
        return {
            "next_action": "escalate",
            "errors": result.errors,
            "retry_count": result.retry_count,
        }
    return {
        "classification": result.classification,
        "router_prompt_version": result.prompt_version,
        "next_action": decide_route(result.classification),
        "errors": result.errors,
        "retry_count": result.retry_count,
    }


def docs_node(state: SupportFlowState) -> dict[str, Any]:
    raise NotImplementedError("Docs Agent — Stage 2, not built yet")


def web_search_node(state: SupportFlowState) -> dict[str, Any]:
    raise NotImplementedError("Web Search Agent — Stage 2, not built yet")


def escalate_node(state: SupportFlowState) -> dict[str, Any]:
    raise NotImplementedError("Escalation Agent — Stage 3, not built yet")


def route_after_router(state: SupportFlowState) -> NextAction:
    """The conditional-edge selector. `router_node` always sets
    `next_action` to one of `"docs"`, `"web_search"`, or `"escalate"` —
    never leaves it at its initial `"router"` value.
    """
    return state["next_action"]


def build_graph() -> Any:
    """Compile the Stage 1 graph: `router` → conditional edge → one of
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
    graph.add_edge("docs", END)
    graph.add_edge("web_search", END)
    graph.add_edge("escalate", END)
    return graph.compile()


def build_initial_state(
    masked_text: str, request_id: str, session_id: str, trace_id: str
) -> SupportFlowState:
    """The state a filtered, accepted request enters the graph with."""
    return SupportFlowState(
        request_id=request_id,
        session_id=session_id,
        trace_id=trace_id,
        original_request_masked=masked_text,
        classification=None,
        docs_response=None,
        web_search_response=None,
        escalation_output=None,
        answer=None,
        confidence=None,
        errors=[],
        retry_count=0,
        escalation_count=0,
        router_prompt_version=None,
        next_action="router",
    )


def handle_request(
    raw_text: str, request_id: str, session_id: str, trace_id: str
) -> SupportFlowState:
    """Task §7's full entry point: input filter (step 1), then the graph.

    Parameters
    ----------
    raw_text : str
        The unmasked customer message. Never stored — `run_input_filter`
        produces the masked version that alone enters state
        (docs/decisions.md #14).
    request_id, session_id, trace_id : str

    Returns
    -------
    SupportFlowState
        On a filter rejection, a terminal state with `next_action="reject"`
        and the rejection's `ErrorType` in `errors` — the graph is never
        invoked for a rejected request, so no LLM call and no trace are
        spent on it.

    Raises
    ------
    NotImplementedError
        The route reached is Docs, Web Search, or Escalation — not yet
        built (docs/decisions.md #16). Expected in Stage 1; a later
        stage's build removes this.
    """
    filtered = run_input_filter(raw_text)
    if filtered.error is not None:
        state = build_initial_state(
            filtered.masked_text, request_id, session_id, trace_id
        )
        state["next_action"] = "reject"
        state["errors"] = [filtered.error]
        return state

    graph = build_graph()
    initial_state = build_initial_state(
        filtered.masked_text, request_id, session_id, trace_id
    )
    result: SupportFlowState = graph.invoke(
        initial_state, config={"recursion_limit": GRAPH_RECURSION_LIMIT}
    )
    return result
