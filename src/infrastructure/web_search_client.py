"""The A2A client side of Web Search Agent — `application/supervisor.py`'s
only path to it. Mirrors
`src.infrastructure.acp.call_router`'s "one call attempt, deadline
enforced, typed failure" shape, but over the network instead of in-process.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from pydantic import ValidationError

from src.domain.schemas import WebSearchResponse
from src.infrastructure.a2a_transport import send_a2a_message
from src.kernel.settings import load_agent_config


class WebSearchUnavailableError(Exception):
    """The remote agent reported its search tool as unavailable, which
    escalates — carries the remote `error_type`/`error` for `errors`
    logging, never the raw exception text.
    """


class WebSearchInvalidResponseError(Exception):
    """The remote agent's reply was not a valid `WebSearchResponse` and
    was not a recognised tool-unavailable error payload either.
    """


@dataclass(frozen=True)
class WebSearchCallResult:
    """`response` plus `retrieval_context`, `tools_called`, and the
    resolved `prompt_version` Web Search Agent actually composed with —
    all cross the A2A hop in the same reply payload.
    """

    response: WebSearchResponse
    retrieval_context: list[str]
    prompt_version: int
    tools_called: list[str] = field(default_factory=list)


def call_web_search(
    masked_text: str,
    request_id: str,
    session_id: str,
    trace_id: str,
    deadline: datetime,
    *,
    parent_span_id: str | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    conversation_history: str = "",
) -> WebSearchCallResult:
    """One A2A call to Web Search Agent.

    Parameters
    ----------
    masked_text : str
        Already PII-masked.
    request_id, session_id, trace_id : str
    deadline : datetime
        Enforced by `send_a2a_message`.
    parent_span_id : str, optional
        The caller's current Langfuse observation id, forwarded unchanged
        to `send_a2a_message`. `None` when tracing is disabled.
    httpx_client : httpx.AsyncClient, optional
        Injected for testing against an in-process ASGI app — production
        callers use the default (a real network client).
    conversation_history : str, default=""
        Forwarded to `send_a2a_message` unchanged. The caller
        (`graph_nodes.web_search_node`) is responsible for masking prior
        answers before this crosses the hop — Web Search Agent gets no
        personal user data, same rule `masked_text` already follows.

    Returns
    -------
    WebSearchCallResult

    Raises
    ------
    A2ATimeoutError
        `deadline` already passed.
    WebSearchUnavailableError
        The remote tool call failed.
    WebSearchInvalidResponseError
        The reply was neither a valid response nor a recognised error.
    """
    config = load_agent_config("web_search")
    reply_text = send_a2a_message(
        f"http://localhost:{config.port}",
        masked_text,
        request_id,
        session_id,
        trace_id,
        deadline,
        httpx_client=httpx_client,
        parent_span_id=parent_span_id,
        conversation_history=conversation_history,
    )
    try:
        payload = json.loads(reply_text)
    except json.JSONDecodeError as exc:
        raise WebSearchInvalidResponseError(f"reply was not JSON: {exc}") from exc

    if "error_type" in payload:
        raise WebSearchUnavailableError(f"{payload['error_type']}: {payload['error']}")

    try:
        response = WebSearchResponse.model_validate(payload["response"])
    except (ValidationError, KeyError, TypeError) as exc:
        raise WebSearchInvalidResponseError(str(exc)) from exc
    return WebSearchCallResult(
        response=response,
        retrieval_context=payload.get("retrieval_context", []),
        tools_called=payload.get("tools_called", []),
        prompt_version=payload["prompt_version"],
    )
