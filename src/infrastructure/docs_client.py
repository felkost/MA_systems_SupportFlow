"""The A2A client side of Docs Agent — `application/supervisor.py`'s only
path to it. Mirrors `src.infrastructure.web_search_client.call_web_search`'s
shape.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from pydantic import ValidationError

from src.domain.schemas import DocsResponse
from src.infrastructure.a2a_transport import send_a2a_message
from src.kernel.settings import load_agent_config


class DocsUnavailableError(Exception):
    """The remote Docs Agent reported an invalid-output failure, which
    escalates — carries the remote `error_type`/`error`, never the raw
    exception text.
    """


class DocsInvalidResponseError(Exception):
    """The remote agent's reply was not a valid `DocsResponse` and not a
    recognised error payload either.
    """


@dataclass(frozen=True)
class DocsCallResult:
    """`response` plus `retrieval_context`, `tools_called`, and the
    resolved `prompt_version` Docs Agent actually composed with — carried
    across the A2A hop so a live case logged from this answer can be
    traced back to its prompt.
    """

    response: DocsResponse
    retrieval_context: list[str]
    prompt_version: int
    tools_called: list[str] = field(default_factory=list)


def call_docs_agent(
    masked_text: str,
    request_id: str,
    session_id: str,
    trace_id: str,
    deadline: datetime,
    *,
    parent_span_id: str | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    conversation_history: str = "",
) -> DocsCallResult:
    """One A2A call to Docs Agent.

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
        Injected for testing against an in-process ASGI app.
    conversation_history : str, default=""
        Forwarded to `send_a2a_message` unchanged — see its own docstring
        (`docs/decisions.md` #77).

    Returns
    -------
    DocsCallResult

    Raises
    ------
    A2ATimeoutError
        `deadline` already passed.
    DocsUnavailableError
        The remote agent's own model call failed.
    DocsInvalidResponseError
        The reply was neither a valid response nor a recognised error.
    """
    config = load_agent_config("docs")
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
        raise DocsInvalidResponseError(f"reply was not JSON: {exc}") from exc

    if "error_type" in payload:
        raise DocsUnavailableError(f"{payload['error_type']}: {payload['error']}")

    try:
        response = DocsResponse.model_validate(payload["response"])
    except (ValidationError, KeyError, TypeError) as exc:
        raise DocsInvalidResponseError(str(exc)) from exc
    return DocsCallResult(
        response=response,
        retrieval_context=payload.get("retrieval_context", []),
        tools_called=payload.get("tools_called", []),
        prompt_version=payload["prompt_version"],
    )
