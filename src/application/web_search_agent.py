"""Web Search Agent orchestration: call the search tool, fence the
retrieved content as untrusted data (docs/decisions.md #24 — F3, mirrors
Decision 18's customer-message fencing), then compose a `WebSearchResponse`.

Runs inside the Web Search A2A server process (docs/decisions.md #1) —
`src/interfaces/web_search_a2a_server.py`'s `AgentExecutor` is the only
caller. `src/application/supervisor.py` must never import this module
directly (it reaches Web Search Agent only through the A2A client,
`src.infrastructure.a2a_transport`) — enforced by
`tests/test_layering.py::test_application_never_imports_agent_a2a_servers_directly`.
"""

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone

from src.domain.schemas import WebSearchResponse
from src.infrastructure.llm import get_chat_model
from src.infrastructure.observability import get_langfuse_client
from src.infrastructure.prompts import get_prompt, get_prompt_client
from src.infrastructure.web_search import SearchFn
from src.infrastructure.web_search import search as _default_search


class WebSearchInvalidOutputError(Exception):
    """The model call succeeded but did not produce a valid
    `WebSearchResponse` — refusal, prose, or an out-of-schema value.
    """


@dataclass(frozen=True)
class WebSearchAgentResult:
    """Parameters
    ----------
    response : WebSearchResponse
        The mandatory schema (task §6) — this alone crosses back to
        Supervisor's process as the A2A reply's primary payload.
    retrieval_context : list[str]
        The raw search-result snippets actually used, carried alongside
        `response` in the A2A reply so `SupportFlowState.retrieval_context`
        (docs/decisions.md #22) can be populated even though Web Search
        Agent runs in its own process (docs/decisions.md #1) — without
        this, Stage 4's `FaithfulnessMetric` would have nothing to score
        the Web Search route against.
    """

    response: WebSearchResponse
    retrieval_context: list[str]


def run_web_search(
    masked_query: str, *, search_fn: SearchFn = _default_search
) -> WebSearchAgentResult:
    """Search, then compose a sourced, confidence-scored answer.

    Parameters
    ----------
    masked_query : str
        Already PII-masked (docs/decisions.md #14) — Web Search Agent must
        never receive raw personal data (task §4/§9).
    search_fn : SearchFn, default=`src.infrastructure.web_search.search`
        Injected for testing the tool-unavailable path without a network
        call; production callers use the default.

    Returns
    -------
    WebSearchAgentResult
        `response.sources[].retrieved_at` is stamped with this call's own
        fetch time, not left to the model to guess (docs/decisions.md #15).

    Raises
    ------
    SearchUnavailableError
        Both Tavily and `ddgs` failed — task §7 step 6's "unavailable
        tool" escalation trigger; propagated unchanged so the caller
        (the A2A executor, then Supervisor) can record it.
    WebSearchInvalidOutputError
        The model's output failed `WebSearchResponse` validation.
    """
    results = search_fn(masked_query)
    fetched_at = datetime.now(timezone.utc)
    retrieval_context = [r.content for r in results]
    retrieved_block = "\n\n".join(f"[{r.title}]({r.url})\n{r.content}" for r in results)

    prompt_text, _prompt_version = get_prompt("supportflow/web_search")
    compiled_prompt = prompt_text.replace("{{customer_message}}", masked_query).replace(
        "{{retrieved_content}}", retrieved_block
    )

    model = get_chat_model("web_search")
    structured_model = model.with_structured_output(WebSearchResponse, include_raw=True)
    client = get_langfuse_client()
    span_cm = (
        client.start_as_current_observation(
            name="web_search_agent.compose",
            as_type="generation",
            prompt=get_prompt_client("supportflow/web_search"),
        )
        if client is not None
        else nullcontext()
    )
    try:
        with span_cm as generation:
            raw = structured_model.invoke(compiled_prompt)
            if generation is not None:
                usage = getattr(raw.get("raw"), "usage_metadata", None) or {}
                generation.update(usage_details=dict(usage))
    except Exception as exc:  # noqa: BLE001 — provider errors vary
        raise WebSearchInvalidOutputError(str(exc)) from exc
    result = raw.get("parsed")
    if not isinstance(result, WebSearchResponse):
        raise WebSearchInvalidOutputError(
            f"model returned no valid WebSearchResponse: {raw.get('parsing_error')}"
        )

    # docs/decisions.md #15: this call's own fetch time is authoritative,
    # not whatever timestamp the model guessed at.
    result.sources = [
        source.model_copy(update={"retrieved_at": fetched_at})
        for source in result.sources
    ]
    return WebSearchAgentResult(response=result, retrieval_context=retrieval_context)
