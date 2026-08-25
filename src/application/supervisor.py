"""Supervisor's request-handling entrypoint: runs the input filter ahead
of the graph (docs/decisions.md #14 — masking is a precondition of
entering the graph, never a node inside it, so there is no
`input_filter` node), then builds and invokes it. The graph's own nodes,
conditional edges, and compilation live in `src.application.graph_nodes`
(Stage 4 Wave A split — this file grew past CLAUDE.md's 320-line ceiling
once tracing instrumentation landed; request orchestration and graph
definition are a genuine responsibility split, not a constants-only
extraction).
"""

from contextlib import nullcontext

from src.application.graph_nodes import build_graph
from src.domain.filters import run_input_filter
from src.domain.state import SupportFlowState
from src.infrastructure.observability import build_callback_handler, get_langfuse_client
from src.kernel.constants import GRAPH_RECURSION_LIMIT


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
        retrieval_context=[],
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
    # docs/decisions.md #36: the guardrail span wraps this call site, not
    # the inside of `src.domain.filters` — that module is `domain`-layer
    # and may not import `infra` (`tests/test_layering.py`).
    client = get_langfuse_client()
    span_cm = (
        client.start_as_current_observation(
            name="input_filter.run", as_type="guardrail"
        )
        if client is not None
        else nullcontext()
    )
    with span_cm as span:
        filtered = run_input_filter(raw_text)
        if span is not None:
            span.update(metadata={"triggered": filtered.error is not None})

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
    callbacks = [build_callback_handler()] if client is not None else []
    result: SupportFlowState = graph.invoke(
        initial_state,
        config={"recursion_limit": GRAPH_RECURSION_LIMIT, "callbacks": callbacks},
    )
    return result
