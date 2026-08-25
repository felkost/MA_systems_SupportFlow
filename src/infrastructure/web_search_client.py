"""The A2A client side of Web Search Agent — `application/supervisor.py`'s
only path to it (docs/decisions.md #1/#23). Mirrors
`src.infrastructure.acp.call_router`'s "one call attempt, deadline
enforced, typed failure" shape, but over the network instead of in-process.
"""

import json
from dataclasses import dataclass
from datetime import datetime

import httpx
from pydantic import ValidationError

from src.domain.schemas import WebSearchResponse
from src.infrastructure.a2a_transport import send_a2a_message
from src.kernel.settings import load_agent_config


class WebSearchUnavailableError(Exception):
    """The remote agent reported its search tool as unavailable (task §7
    step 6) — carries the remote `error_type`/`error` for `errors`
    logging, never the raw exception text (docs/decisions.md #14).
    """


class WebSearchInvalidResponseError(Exception):
    """The remote agent's reply was not a valid `WebSearchResponse` and
    was not a recognised tool-unavailable error payload either.
    """


@dataclass(frozen=True)
class WebSearchCallResult:
    """`response` plus `retrieval_context` (docs/decisions.md #22) — both
    cross the A2A hop in the same reply payload.
    """

    response: WebSearchResponse
    retrieval_context: list[str]


def call_web_search(
    masked_text: str,
    request_id: str,
    session_id: str,
    trace_id: str,
    deadline: datetime,
    *,
    httpx_client: httpx.AsyncClient | None = None,
) -> WebSearchCallResult:
    """One A2A call to Web Search Agent.

    Parameters
    ----------
    masked_text : str
        Already PII-masked (docs/decisions.md #14).
    request_id, session_id, trace_id : str
    deadline : datetime
        Enforced by `send_a2a_message` (docs/decisions.md #19's pattern).
    httpx_client : httpx.AsyncClient, optional
        Injected for testing against an in-process ASGI app — production
        callers use the default (a real network client).

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
        response=response, retrieval_context=payload.get("retrieval_context", [])
    )
