"""Shared A2A plumbing for both A2A-hosted agents (Docs, Web Search —
docs/decisions.md #1). One module, reused by both `interfaces/*_a2a_server.py`
entrypoints and by `application`'s node functions, so the client/server
wiring is written once, not duplicated per agent.

Confirmed against the installed `a2a-sdk==1.1.2` package by direct
`inspect`/`dir()` probing (see docs/decisions.md #23, insights.md
2026-08-25) — this version has no `A2AStarletteApplication`/
`DefaultRequestHandler`/built-in trace propagation; a server is
hand-assembled from `FastAPI()` + `add_a2a_routes_to_fastapi()`, and
`request_id`/`session_id`/`trace_id`/`deadline` travel in
`SendMessageRequest.metadata` (a plain `dict[str, str]` on the protobuf
message), read back server-side from `RequestContext.request.metadata`.
"""

import asyncio
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone

import httpx
from a2a.client.client_factory import ClientConfig, ClientFactory
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.request_handlers.default_request_handler_v2 import (
    DefaultRequestHandlerV2,
)
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import (
    AgentCard,
    AgentInterface,
    Message,
    Part,
    Role,
    SendMessageRequest,
)
from fastapi import FastAPI

from src.infrastructure.observability import get_langfuse_client

# docs/decisions.md #23: only "1.0" is negotiated by this SDK version.
_PROTOCOL_VERSION = "1.0"


class A2ATimeoutError(TimeoutError):
    """`deadline` had already passed before the call was made — the same
    fail-fast contract as `src.infrastructure.acp.call_router`.
    """


class A2AInvalidResponseError(Exception):
    """The remote agent's response carried no text part to read."""


def build_agent_card(name: str, description: str, url: str) -> AgentCard:
    """One `AgentCard` describing a single JSON-RPC interface at `url`.

    Parameters
    ----------
    name, description : str
    url : str
        This process's own base URL, e.g. `"http://localhost:8802"`.

    Returns
    -------
    AgentCard
        A protobuf message (`a2a.types.a2a_pb2.AgentCard`), not Pydantic —
        confirmed by SDK probe, docs/decisions.md #23.
    """
    return AgentCard(
        name=name,
        description=description,
        version="1.0",
        supported_interfaces=[
            AgentInterface(
                url=url,
                protocol_binding="JSONRPC",
                protocol_version=_PROTOCOL_VERSION,
            )
        ],
    )


def build_server_app(agent_executor: AgentExecutor, agent_card: AgentCard) -> FastAPI:
    """Assemble the FastAPI app hosting one A2A-executed agent.

    Parameters
    ----------
    agent_executor : AgentExecutor
        Implements `execute`/`cancel` — the agent's own request handling.
    agent_card : AgentCard

    Returns
    -------
    FastAPI
    """
    handler = DefaultRequestHandlerV2(
        agent_executor=agent_executor,
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    )
    return app


def read_request_metadata(context: RequestContext) -> dict[str, str]:
    """The `request_id`/`session_id`/`trace_id`/`deadline` the client sent
    (docs/decisions.md #23). `RequestContext.metadata` (confirmed by
    reading the installed SDK's source, not its `__init__` signature —
    `RequestContext` exposes `metadata`/`message` properties, not a
    `.request` attribute) already returns a plain `dict[str, str]`.
    """
    return context.metadata


def read_request_text(context: RequestContext) -> str:
    """The first text part of the incoming message — this project's
    payload is always a single text turn, never multi-part.
    """
    if context.message is None:
        return ""
    for part in context.message.parts:
        if part.WhichOneof("content") == "text":
            return part.text
    return ""


def _build_request(
    text: str,
    request_id: str,
    session_id: str,
    trace_id: str,
    deadline: datetime,
    parent_span_id: str | None = None,
) -> SendMessageRequest:
    message = Message(
        message_id=str(uuid.uuid4()), role=Role.ROLE_USER, parts=[Part(text=text)]
    )
    request = SendMessageRequest(message=message)
    request.metadata["request_id"] = request_id
    request.metadata["session_id"] = session_id
    request.metadata["trace_id"] = trace_id
    request.metadata["deadline"] = deadline.isoformat()
    if parent_span_id is not None:
        request.metadata["parent_span_id"] = parent_span_id
    return request


async def _send_async(
    base_url: str,
    text: str,
    request_id: str,
    session_id: str,
    trace_id: str,
    deadline: datetime,
    httpx_client: httpx.AsyncClient | None,
    parent_span_id: str | None,
) -> str:
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise A2ATimeoutError(f"deadline already passed for request {request_id}")
    # a2a-sdk's own httpx client defaults to a short timeout meant for a
    # simple network round trip — too short once the callee's own
    # processing (search + LLM call) is what actually takes the time.
    # Sizing it to our own remaining deadline keeps one deadline
    # contract instead of two independent ones racing each other.
    client_for_call = httpx_client or httpx.AsyncClient(timeout=remaining)
    # streaming=False: this project's agents return one final answer, never
    # incremental updates — streaming mode left the client waiting on a
    # status/completion event this project's simple Message-only executors
    # never emit (confirmed by a hang during this module's own testing).
    config = ClientConfig(streaming=False, httpx_client=client_for_call)
    client = await ClientFactory(config).create_from_url(base_url)
    request = _build_request(
        text, request_id, session_id, trace_id, deadline, parent_span_id
    )
    async for event in client.send_message(request):
        if event.WhichOneof("payload") == "message":
            for part in event.message.parts:
                if part.WhichOneof("content") == "text":
                    return part.text
    raise A2AInvalidResponseError(f"no text response for request {request_id}")


def send_a2a_message(
    base_url: str,
    text: str,
    request_id: str,
    session_id: str,
    trace_id: str,
    deadline: datetime,
    *,
    httpx_client: httpx.AsyncClient | None = None,
    parent_span_id: str | None = None,
) -> str:
    """One A2A call: enforce `deadline`, send `text`, return the remote
    agent's reply text.

    Parameters
    ----------
    base_url : str
        The target agent's A2A server base URL.
    text : str
        Already-masked payload (docs/decisions.md #14) — never raw
        customer text.
    request_id, session_id, trace_id : str
        Carried in `SendMessageRequest.metadata` (docs/decisions.md #23).
    deadline : datetime
        Timezone-aware. Raises `A2ATimeoutError` if already passed.
    httpx_client : httpx.AsyncClient, optional
        Injected for testing (an ASGI-transport client against an in-process
        app, no real socket) — defaults to a real network client.
    parent_span_id : str, optional
        The caller's current Langfuse observation id (Stage 4 decision 39)
        — carried in `SendMessageRequest.metadata` alongside `trace_id` so
        the callee's own root span parents onto this trace. `None` when
        tracing is disabled.

    Returns
    -------
    str
        The remote agent's reply text (typically a JSON-encoded response
        model — the caller validates it against the matching Pydantic
        model, this function only carries text).

    Raises
    ------
    A2ATimeoutError
    A2AInvalidResponseError

    Notes
    -----
    Opens its own client-side Langfuse span around the network call
    (Stage 4 decision 39) — a connection failure before the callee ever
    opens its own root span (refused connection, timeout, DNS failure —
    precisely task §7's escalation trigger) would otherwise leave no trace
    record at all. The exception always propagates unchanged; Langfuse's
    own span context manager records it as an error observation.
    """
    client = get_langfuse_client()
    span_cm = (
        client.start_as_current_observation(
            name="a2a.send_message",
            as_type="span",
            metadata={"base_url": base_url, "request_id": request_id},
        )
        if client is not None
        else nullcontext()
    )
    with span_cm:
        return asyncio.run(
            _send_async(
                base_url,
                text,
                request_id,
                session_id,
                trace_id,
                deadline,
                httpx_client,
                parent_span_id,
            )
        )
