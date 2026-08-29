"""LangGraph state shape: "original request, classification,
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

# A fixed vocabulary, never a raw exception string — `errors` must never
# carry the offending customer text (e.g. a `ValidationError` repr contains
# its input), which graph-boundary masking does not cover.
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
    "docs_unavailable",
    "docs_timeout",
    "docs_invalid_response",
    "docs_low_confidence",
]


class SupportFlowState(TypedDict):
    """One request's state as it moves through the graph.

    Parameters
    ----------
    request_id, session_id, trace_id : str
        `session_id` travels in `AcpEnvelope` too — it is required in
        observation metadata.
    original_request_masked : str
        Masking happens before this state is built, never as a node inside
        the traced graph. The unmasked text, if a node genuinely needs it,
        is never stored here.
    classification : ClassificationOutput or None
    docs_response, web_search_response, escalation_output : ... or None
        Populated by their respective agents once built; `None` while a
        route is unimplemented.
    answer : str or None
        Supervisor's composed final answer.
    confidence : float or None
        Mirrors whichever downstream response's confidence drove the
        current routing decision.
    errors : list[ErrorType]
        `Annotated` with `operator.add` so parallel branches (if any exist
        later) merge rather than overwrite.
    retry_count : int
        Compared against `AgentModelConfig.max_retries`.
    escalation_count : int
        Per-session cap on real escalations, incremented by Supervisor —
        never by an agent prompt, since a prompt instruction is bypassable
        by the same injection it would defend against.
    router_prompt_version : int or None
        The resolved Langfuse prompt version actually used for this
        request's Router call — `label="production"` is mutable, so this
        is what makes a later before/after comparison attributable to a
        specific prompt version.
    answer_prompt_version : int or None
        The resolved Langfuse version of whichever prompt (`supportflow/
        docs` or `supportflow/web_search`) actually composed
        `answer` — `None` whenever the case escalated instead, since an
        escalated request's customer-facing text was not composed by
        either of those prompts even when `docs_response`/
        `web_search_response` is still set (the low-confidence path).
        Set only on the two "respond" branches in `graph_nodes.py`, so it
        travels alongside `answer` rather than alongside a response
        object that may be orphaned by a later escalation.
    retrieval_context : list[str]
        The retrieved chunk texts Docs/Web Search Agent actually used —
        state-only, not part of either mandatory response model (their
        shape is frozen). Scores DeepEval's `FaithfulnessMetric`.
    tools_called : list[str]
        Names of the tools Docs/Web Search Agent actually invoked
        successfully — same sibling-field pattern as `retrieval_context`.
        A name is appended only once its call has returned; a mid-call
        timeout or a swallowed exception never adds one. Scores DeepEval's
        `ToolCorrectnessMetric`.
    report_written, telegram_sent : bool
        `escalate_node`'s own `EscalationAgentResult.written`/`.sent`,
        surfaced so a caller (the API's `ChatResponse`) can show whether
        the operator report/Telegram send actually happened, rather than
        only that the case escalated. `False` on every non-escalated
        request — never inferred from `next_action` alone, since a
        deduplicated or capped escalation still sets
        `next_action="escalate"` without writing or sending anything.
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
    answer_prompt_version: int | None
    retrieval_context: list[str]
    tools_called: list[str]
    report_written: bool
    telegram_sent: bool
    next_action: NextAction
