"""`call_web_search` against the real Web Search A2A server app, in-process
(ASGI transport, no socket) — proves the full client → server → agent
round trip, including the tool-unavailable error path.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.application.web_search_agent import WebSearchAgentResult
from src.domain.schemas import Source, WebSearchResponse
from src.infrastructure.web_search_client import (
    WebSearchUnavailableError,
    call_web_search,
)
from src.interfaces.web_search_a2a_server import build_app


def _asgi_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8802"
    )


def test_call_web_search_returns_validated_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_result = WebSearchAgentResult(
        response=WebSearchResponse(
            answer="Так, є акція на хліб.",
            sources=[
                Source(
                    ref="https://example.com", retrieved_at=datetime.now(timezone.utc)
                )
            ],
            confidence=0.9,
        ),
        retrieval_context=["хліб зі знижкою 20%"],
        tools_called=["tavily"],
    )
    monkeypatch.setattr(
        "src.interfaces.web_search_a2a_server.run_web_search",
        lambda query: fake_result,
    )

    result = call_web_search(
        "Чи є у вас акції на хліб?",
        request_id="r1",
        session_id="s1",
        trace_id="0123456789abcdef0123456789abcdef",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
        httpx_client=_asgi_client(build_app()),
    )

    assert result.response.answer == "Так, є акція на хліб."
    assert result.response.confidence == 0.9
    assert result.retrieval_context == ["хліб зі знижкою 20%"]
    assert result.tools_called == ["tavily"]


def test_call_web_search_forwards_parent_span_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_send_a2a_message(
        base_url, text, request_id, session_id, trace_id, deadline, **kwargs
    ):
        captured.update(kwargs)
        return '{"response": {"answer": "x", "sources": [], "confidence": 1.0}}'

    monkeypatch.setattr(
        "src.infrastructure.web_search_client.send_a2a_message", fake_send_a2a_message
    )

    call_web_search(
        "query",
        request_id="r1",
        session_id="s1",
        trace_id="0123456789abcdef0123456789abcdef",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
        parent_span_id="deadbeefdeadbeef",
    )

    assert captured["parent_span_id"] == "deadbeefdeadbeef"


def test_call_web_search_raises_when_tool_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infrastructure.web_search import SearchUnavailableError

    def failing(_query: str) -> WebSearchResponse:
        raise SearchUnavailableError("both providers down")

    monkeypatch.setattr("src.interfaces.web_search_a2a_server.run_web_search", failing)

    with pytest.raises(WebSearchUnavailableError):
        call_web_search(
            "query",
            request_id="r1",
            session_id="s1",
            trace_id="0123456789abcdef0123456789abcdef",
            deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
            httpx_client=_asgi_client(build_app()),
        )
