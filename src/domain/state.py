"""LangGraph state shape: "original request, classification,
search results, answer, confidence, errors, session/trace ids, next action".

A `TypedDict`, not a Pydantic model — LangGraph's `StateGraph` is built
around `TypedDict` plus reducers (`Annotated[list, operator.add]`), and
wrapping it in Pydantic buys nothing here while complicating that pattern.
"""

from typing import Annotated, Literal, TypedDict

from src.domain.schemas import (
    ClassificationOutput,
    DocsResponse,
    EscalationOutput,
    WebSearchResponse,
)
from src.kernel.constants import MAX_HISTORY_TURNS

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

# A checkpointed thread's `graph.invoke` input is combined with whatever
# the checkpointer already holds through the field's own reducer, same as
# any node's return value — confirmed by direct probe against the
# installed `langgraph==1.2.11`: an empty-list seed on turn 2 still
# produced `existing + [] == existing`, i.e. a plain `errors=[]` seed
# never resets anything once a checkpointer is attached. This marker,
# passed only by `build_initial_state`, is how `_errors_reducer` tells
# "a new turn is starting" apart from a node's own legitimate (possibly
# empty) error list — see `docs/decisions.md` #77. Must be JSON/msgpack
# serializable (a custom sentinel object is not — `InMemorySaver` persists
# every pending write, not just the reduced result, and rejected one live).
RESET_ERRORS_MARKER: list[str] = ["__reset_turn__"]


def _errors_reducer(existing: list[ErrorType], new: list[ErrorType]) -> list[ErrorType]:
    """Accumulates within one graph run like `operator.add` (Router and
    Docs/Web Search can each contribute an entry in the same turn — e.g.
    a recovered Router retry followed by a Docs low-confidence escalation,
    both worth keeping in the escalation report), but a fresh turn's own
    `RESET_ERRORS_MARKER` seed discards whatever a prior turn in this
    session's checkpointed thread left behind instead of adding to it.
    """
    if new == RESET_ERRORS_MARKER:
        return []
    return existing + new


class ConversationTurn(TypedDict):
    """One resolved turn, kept only for the *next* turn's prompts to read
    as context — never a transcript feature in its own right. `customer`
    is always `original_request_masked`, already PII-masked; `answer` is
    Supervisor's composed final answer for that turn, `None` on the rare
    turn a rejection short-circuits before the graph runs (never added to
    history — see `build_initial_state`).
    """

    customer: str
    answer: str


def _keep_last_n_turns(
    existing: list[ConversationTurn], new: list[ConversationTurn]
) -> list[ConversationTurn]:
    """Appends like `operator.add`, then trims to the newest
    `MAX_HISTORY_TURNS` — bounding growth here, once, rather than at each
    of the three prompt-assembly call sites that read this field.
    """
    return (existing + new)[-MAX_HISTORY_TURNS:]


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
        `Annotated` with `_errors_reducer` so parallel branches (if any
        exist later) merge rather than overwrite *within one turn*, while
        a new turn's `RESET_ERRORS_MARKER` seed starts the list over
        instead of inheriting the previous turn's entries from the
        session's checkpointed thread.
    conversation_history : list[ConversationTurn]
        The session's last `MAX_HISTORY_TURNS` resolved turns, read by
        Router/Docs/Web Search's prompts as context for a follow-up
        message — see `docs/decisions.md` #77. `Annotated` with
        `_keep_last_n_turns`: `docs_node`/`web_search_node`/`escalate_node`
        each append their own turn's `{customer, answer}` pair;
        `router_node` never does (it does not resolve the case). Empty on
        `build_initial_state`'s own seed so the reducer only ever adds to
        what the checkpointer already holds, never duplicates it.
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
    escalation_capped : bool
        `EscalationAgentResult.capped` — `telegram_sent=False` on its own
        does not say *why*: a deduplicated case, a disabled
        `ALLOW_REAL_SEND`, and a capped send cap all look identical
        without this field (docs/decisions.md #80/#82). `False` on every
        non-escalated request, same rule as `report_written`.
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
    errors: Annotated[list[ErrorType], _errors_reducer]
    conversation_history: Annotated[list[ConversationTurn], _keep_last_n_turns]
    retry_count: int
    escalation_count: int
    router_prompt_version: int | None
    answer_prompt_version: int | None
    retrieval_context: list[str]
    tools_called: list[str]
    report_written: bool
    telegram_sent: bool
    escalation_capped: bool
    next_action: NextAction


def format_conversation_history(turns: list[ConversationTurn]) -> str:
    """Render prior turns for a prompt's `{{conversation_history}}` slot.

    Parameters
    ----------
    turns : list of ConversationTurn

    Returns
    -------
    str
        Empty when there is no history yet — a session's first turn, or a
        prompt that predates this field being seeded. Never `None`: the
        prompt template's placeholder is always replaced with something.
    """
    if not turns:
        return ""
    return "\n".join(
        f"Клієнт: {turn['customer']}\nАсистент: {turn['answer']}" for turn in turns
    )
