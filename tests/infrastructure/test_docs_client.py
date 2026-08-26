"""`call_docs_agent` against the real Docs A2A server app, in-process
(ASGI transport, no socket) — proves the full client -> server -> agent
round trip, including the invalid-output error path.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.application.docs_agent import DocsAgentResult, DocsInvalidOutputError
from src.domain.schemas import DocsResponse, Source
from src.infrastructure.docs_client import DocsUnavailableError, call_docs_agent
from src.interfaces.docs_a2a_server import build_app


def _asgi_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8801"
    )


def test_call_docs_agent_returns_validated_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_result = DocsAgentResult(
        response=DocsResponse(
            answer="Бонусна картка не має терміну дії.",
            sources=[
                Source(
                    ref="internal_policy/loyalty_v5.md",
                    retrieved_at=datetime.now(timezone.utc),
                )
            ],
            confidence=0.9,
        ),
        retrieval_context=["Бонусна картка не має терміну дії."],
        tools_called=["silpo_find_products_batch"],
    )

    async def fake_run_docs_agent(_query: str) -> DocsAgentResult:
        return fake_result

    monkeypatch.setattr(
        "src.interfaces.docs_a2a_server.run_docs_agent", fake_run_docs_agent
    )

    result = call_docs_agent(
        "Скільки діє бонусна картка?",
        request_id="r1",
        session_id="s1",
        trace_id="0123456789abcdef0123456789abcdef",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
        httpx_client=_asgi_client(build_app()),
    )

    assert result.response.answer == "Бонусна картка не має терміну дії."
    assert result.retrieval_context == ["Бонусна картка не має терміну дії."]
    assert result.tools_called == ["silpo_find_products_batch"]  # Stage 4 Wave B D-B7


def test_call_docs_agent_forwards_parent_span_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_send_a2a_message(
        base_url, text, request_id, session_id, trace_id, deadline, **kwargs
    ):
        captured.update(kwargs)
        return '{"response": {"answer": "x", "sources": [], "confidence": 1.0}}'

    monkeypatch.setattr(
        "src.infrastructure.docs_client.send_a2a_message", fake_send_a2a_message
    )

    call_docs_agent(
        "query",
        request_id="r1",
        session_id="s1",
        trace_id="0123456789abcdef0123456789abcdef",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
        parent_span_id="deadbeefdeadbeef",
    )

    assert captured["parent_span_id"] == "deadbeefdeadbeef"


def test_call_docs_agent_raises_when_model_output_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_run_docs_agent(_query: str) -> DocsAgentResult:
        raise DocsInvalidOutputError("refused to answer")

    monkeypatch.setattr(
        "src.interfaces.docs_a2a_server.run_docs_agent", failing_run_docs_agent
    )

    with pytest.raises(DocsUnavailableError):
        call_docs_agent(
            "query",
            request_id="r1",
            session_id="s1",
            trace_id="0123456789abcdef0123456789abcdef",
            deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
            httpx_client=_asgi_client(build_app()),
        )
