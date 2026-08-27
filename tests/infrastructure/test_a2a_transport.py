"""End-to-end A2A round trip against an in-process server (ASGI transport,
no real socket) — proves the hand-assembled server/client wiring actually
works against the installed `a2a-sdk==1.1.2`, not just that it imports.
Also proves `request_id`/`session_id`/`trace_id` travel across the hop
and that `send_a2a_message` fails fast on an
already-passed deadline, mirroring `call_router`'s contract
(`tests/infrastructure/test_acp.py`).
"""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import Message, Part, Role

from src.infrastructure.a2a_transport import (
    A2ATimeoutError,
    build_agent_card,
    build_server_app,
    read_request_metadata,
    read_request_text,
    send_a2a_message,
)


class _EchoExecutor(AgentExecutor):
    """Replies with the received text and metadata as JSON — a stand-in
    for a real agent's `AgentExecutor`, exercising only the transport.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        reply = json.dumps(
            {
                "text": read_request_text(context),
                "metadata": read_request_metadata(context),
            }
        )
        await event_queue.enqueue_event(
            Message(
                role=Role.ROLE_AGENT, message_id="reply-1", parts=[Part(text=reply)]
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("not exercised by this project's agents")


def _asgi_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def test_send_a2a_message_round_trips_text_and_metadata() -> None:
    agent_card = build_agent_card("echo", "test agent", "http://testserver")
    app = build_server_app(_EchoExecutor(), agent_card)

    reply = send_a2a_message(
        "http://testserver",
        "Чи є у вас акції на хліб?",
        request_id="r1",
        session_id="s1",
        trace_id="0123456789abcdef0123456789abcdef",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
        httpx_client=_asgi_client(app),
    )

    payload = json.loads(reply)
    assert payload["text"] == "Чи є у вас акції на хліб?"
    assert payload["metadata"]["request_id"] == "r1"
    assert payload["metadata"]["session_id"] == "s1"
    assert payload["metadata"]["trace_id"] == "0123456789abcdef0123456789abcdef"


def test_send_a2a_message_carries_parent_span_id_when_given() -> None:
    agent_card = build_agent_card("echo", "test agent", "http://testserver")
    app = build_server_app(_EchoExecutor(), agent_card)

    reply = send_a2a_message(
        "http://testserver",
        "text",
        request_id="r1",
        session_id="s1",
        trace_id="0123456789abcdef0123456789abcdef",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
        httpx_client=_asgi_client(app),
        parent_span_id="deadbeefdeadbeef",
    )

    payload = json.loads(reply)
    assert payload["metadata"]["parent_span_id"] == "deadbeefdeadbeef"


def test_send_a2a_message_omits_parent_span_id_when_not_given() -> None:
    agent_card = build_agent_card("echo", "test agent", "http://testserver")
    app = build_server_app(_EchoExecutor(), agent_card)

    reply = send_a2a_message(
        "http://testserver",
        "text",
        request_id="r1",
        session_id="s1",
        trace_id="0123456789abcdef0123456789abcdef",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
        httpx_client=_asgi_client(app),
    )

    payload = json.loads(reply)
    assert "parent_span_id" not in payload["metadata"]


def test_send_a2a_message_client_side_span_does_not_swallow_connection_failure() -> (
    None
):
    """A refused connection (task §7's escalation trigger) must still
    raise, not be absorbed by the new client-side span wrapper.
    """
    unreachable_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=lambda *a, **kw: None),
        base_url="http://testserver",
    )

    with pytest.raises(Exception):
        send_a2a_message(
            "http://testserver",
            "text",
            request_id="r1",
            session_id="s1",
            trace_id="0123456789abcdef0123456789abcdef",
            deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
            httpx_client=unreachable_client,
        )


def test_send_a2a_message_raises_when_deadline_already_passed() -> None:
    agent_card = build_agent_card("echo", "test agent", "http://testserver")
    app = build_server_app(_EchoExecutor(), agent_card)

    with pytest.raises(A2ATimeoutError):
        send_a2a_message(
            "http://testserver",
            "text",
            request_id="r1",
            session_id="s1",
            trace_id="0123456789abcdef0123456789abcdef",
            deadline=datetime.now(timezone.utc) - timedelta(seconds=1),
            httpx_client=_asgi_client(app),
        )
