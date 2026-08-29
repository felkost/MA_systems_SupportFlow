"""Web Search Agent orchestration: call the search tool, fence the
retrieved content as untrusted data, mirroring the customer-message
fencing, then compose a `WebSearchResponse`.

Runs inside the Web Search A2A server process —
`src/interfaces/web_search_a2a_server.py`'s `AgentExecutor` is the only
caller. `src/application/supervisor.py` must never import this module
directly (it reaches Web Search Agent only through the A2A client,
`src.infrastructure.a2a_transport`) — enforced by
`tests/test_layering.py::test_application_never_imports_agent_a2a_servers_directly`.
"""

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

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
        The mandatory schema — this alone crosses back to
        Supervisor's process as the A2A reply's primary payload.
    retrieval_context : list[str]
        The raw search-result snippets actually used, carried alongside
        `response` in the A2A reply so `SupportFlowState.retrieval_context`
        can be populated even though Web Search Agent runs in its own
        process — without this, the `FaithfulnessMetric` would have
        nothing to score the Web Search route against.
    tools_called : list[str]
        Which provider actually served the result — `["tavily"]` or
        `["duckduckgo"]`, populated only once `search()` has actually
        returned.
    prompt_version : int
        The resolved Langfuse version of `supportflow/web_search` this
        answer was actually composed with — see `DocsAgentResult`'s
        identical field for why it travels alongside `response` rather
        than inside it.
    """

    response: WebSearchResponse
    retrieval_context: list[str]
    tools_called: list[str]
    prompt_version: int


def run_web_search(
    masked_query: str,
    *,
    conversation_history: str = "",
    search_fn: SearchFn = _default_search,
) -> WebSearchAgentResult:
    """Search, then compose a sourced, confidence-scored answer.

    Parameters
    ----------
    masked_query : str
        Already PII-masked — Web Search Agent must never receive raw
        personal data.
    conversation_history : str, default=""
        Prior turns in this session, pre-formatted and PII-masked by the
        caller (`graph_nodes.web_search_node` — Web Search "gets no
        personal user data", same rule `masked_query` already follows),
        received from `WebSearchExecutor` over the A2A hop's metadata.
        Empty for a session's first turn or against a live prompt seeded
        before this parameter existed (`docs/decisions.md` #77).
    search_fn : SearchFn, default=`src.infrastructure.web_search.search`
        Injected for testing the tool-unavailable path without a network
        call; production callers use the default.

    Returns
    -------
    WebSearchAgentResult
        `response.sources[].retrieved_at` is stamped with this call's own
        fetch time, not left to the model to guess.

    Raises
    ------
    SearchUnavailableError
        Both Tavily and `ddgs` failed — an unavailable tool, which
        escalates; propagated unchanged so the caller
        (the A2A executor, then Supervisor) can record it.
    WebSearchInvalidOutputError
        The model's output failed `WebSearchResponse` validation.
    """
    outcome = search_fn(masked_query)
    fetched_at = datetime.now(timezone.utc)
    retrieval_context = [r.content for r in outcome.results]
    retrieved_block = "\n\n".join(
        f"[{r.title}]({r.url})\n{r.content}" for r in outcome.results
    )

    prompt_text, prompt_version = get_prompt("supportflow/web_search")
    compiled_prompt = (
        prompt_text.replace("{{customer_message}}", masked_query)
        .replace("{{retrieved_content}}", retrieved_block)
        .replace("{{conversation_history}}", conversation_history)
    )

    model = get_chat_model("web_search")
    structured_model = model.with_structured_output(WebSearchResponse, include_raw=True)
    client = get_langfuse_client()
    span_cm = (
        client.start_as_current_observation(
            name="web_search_agent.compose",
            as_type="generation",
            prompt=get_prompt_client("supportflow/web_search"),
            model=model.model_name,
        )
        if client is not None
        else nullcontext()
    )
    try:
        with span_cm as generation:
            raw = structured_model.invoke(compiled_prompt)
            result = raw.get("parsed")
            # See docs_agent.py's identical fix — validated inside the
            # `with` so a parse failure is marked `level="ERROR"` instead
            # of leaving a `level=DEFAULT` span the judge evaluator would
            # otherwise score.
            if generation is not None:
                usage = getattr(raw.get("raw"), "usage_metadata", None) or {}
                update_kwargs: dict[str, Any] = dict(
                    usage_details=dict(usage),
                    input=compiled_prompt,
                    output=str(result),
                )
                if isinstance(result, WebSearchResponse):
                    update_kwargs["metadata"] = {
                        "customer_message": masked_query,
                        "agent_answer": result.answer,
                    }
                else:
                    update_kwargs["level"] = "ERROR"
                generation.update(**update_kwargs)
    except Exception as exc:  # noqa: BLE001 — provider errors vary
        raise WebSearchInvalidOutputError(str(exc)) from exc
    if not isinstance(result, WebSearchResponse):
        raise WebSearchInvalidOutputError(
            f"model returned no valid WebSearchResponse: {raw.get('parsing_error')}"
        )

    # This call's own fetch time is authoritative, not whatever timestamp
    # the model guessed at.
    result.sources = [
        source.model_copy(update={"retrieved_at": fetched_at})
        for source in result.sources
    ]
    return WebSearchAgentResult(
        response=result,
        retrieval_context=retrieval_context,
        tools_called=[outcome.provider],
        prompt_version=prompt_version,
    )
