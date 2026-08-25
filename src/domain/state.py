"""LangGraph state shape (task §6): "original request, classification,
search results, answer, confidence, errors, session/trace ids, next action".

A `TypedDict`, not a Pydantic model — LangGraph's `StateGraph` is built
around `TypedDict` plus reducers (`Annotated[list, operator.add]`), and
wrapping it in Pydantic buys nothing here while complicating that pattern.
"""

import operator
from typing import Annotated, Literal, TypedDict

from src.domain.schemas import (
    ClassificationOutput,
    DocsResponse,
    EscalationOutput,
    WebSearchResponse,
)

NextAction = Literal["router", "docs", "web_search", "escalate", "reject", "respond"]

# docs/decisions.md #14: a fixed vocabulary, never a raw exception string —
# `errors` must never carry the offending customer text (e.g. a
# `ValidationError` repr contains its input), which graph-boundary masking
# does not cover.
ErrorType = Literal[
    "empty_input",
    "input_too_long",
    "unsupported_language",
    "out_of_domain",
    "forbidden_content",
    "router_invalid_output",
    "router_timeout",
    "router_retries_exhausted",
    "prompt_fetch_failed",
    "web_search_unavailable",
    "web_search_timeout",
    "web_search_invalid_response",
    "web_search_low_confidence",
]


class SupportFlowState(TypedDict):
    """One request's state as it moves through the graph.

    Parameters
    ----------
    request_id, session_id, trace_id : str
        docs/decisions.md #19: `session_id` travels in
        `AcpEnvelope` too — task §9 requires it in observation metadata.
    original_request_masked : str
        docs/decisions.md #14: masking happens before this state is built,
        never as a node inside the traced graph. The unmasked text, if a
        node genuinely needs it, is never stored here.
    classification : ClassificationOutput or None
    docs_response, web_search_response, escalation_output : ... or None
        Populated by their respective agents once built (Stage 2/3); `None`
        while a route is unimplemented in Stage 1
        (docs/decisions.md #16).
    answer : str or None
        Supervisor's composed final answer (task §7 step 7).
    confidence : float or None
        Mirrors whichever downstream response's confidence drove the
        current routing decision.
    errors : list[ErrorType]
        `Annotated` with `operator.add` so parallel branches (if any exist
        later) merge rather than overwrite.
    retry_count : int
        Compared against `AgentModelConfig.max_retries`
        (docs/decisions.md #12).
    escalation_count : int
        Per-session cap on real escalations, incremented by Supervisor —
        never by an agent prompt, since a prompt instruction is bypassable
        by the same injection it would defend against
        (docs/decisions.md #19, F2).
    router_prompt_version : int or None
        The resolved Langfuse prompt version actually used for this
        request's Router call (docs/decisions.md #13) — `label="production"`
        is mutable, so this is what makes a later before/after comparison
        attributable to a specific prompt version.
    retrieval_context : list[str]
        The retrieved chunk texts Docs/Web Search Agent actually used
        (docs/decisions.md #22) — state-only, not part of either mandatory
        response model (task §6 freezes their shape). Stage 4's DeepEval
        `FaithfulnessMetric` scores against this.
    next_action : NextAction
    """

    request_id: str
    session_id: str
    trace_id: str
    original_request_masked: str
    classification: ClassificationOutput | None
    docs_response: DocsResponse | None
    web_search_response: WebSearchResponse | None
    escalation_output: EscalationOutput | None
    answer: str | None
    confidence: float | None
    errors: Annotated[list[ErrorType], operator.add]
    retry_count: int
    escalation_count: int
    router_prompt_version: int | None
    retrieval_context: list[str]
    next_action: NextAction
