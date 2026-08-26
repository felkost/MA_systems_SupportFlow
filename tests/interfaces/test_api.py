"""FastAPI `/chat` endpoint tests (Stage 5, task §8) — `supervisor.handle_request`
mocked at the *importing* module (`src.interfaces.api.supervisor`, not
`src.application.supervisor`), same pattern `test_supervisor.py` already
uses for `graph_nodes`. No live LLM/A2A/Silpo/Telegram call.
"""

import pytest
from fastapi.testclient import TestClient

from src.application.supervisor import build_initial_state
from src.domain.schemas import DocsResponse, EscalationOutput, Source
from src.interfaces.api import app, supervisor

client = TestClient(app)


def test_chat_bypasses_hitl_during_the_call_and_restores_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision 6: an unattended web request must never block on
    Escalation's interactive `input()` confirmation — scoped exactly like
    `tests/evaluation/harness.py`'s own `_bypass_hitl()`, never a permanent
    mutation of the shared `settings` singleton."""
    from src.kernel.settings import settings

    assert settings.bypass_hitl is False
    seen = []

    def fake_handle_request(*args: object, **kwargs: object) -> dict:
        seen.append(settings.bypass_hitl)
        state = build_initial_state("q", "r", "s", "t" * 32)
        state["next_action"] = "respond"
        state["answer"] = "ok"
        return state

    monkeypatch.setattr(supervisor, "handle_request", fake_handle_request)

    client.post("/chat", json={"message": "q"})

    assert seen == [True]
    assert settings.bypass_hitl is False


def test_docs_route_returns_answer_and_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    state = build_initial_state("Де знайти безлактозне молоко?", "r1", "s1", "t1" * 8)
    state["next_action"] = "respond"
    state["answer"] = "Безлактозне молоко є у відділі молочних продуктів."
    state["confidence"] = 0.9
    state["docs_response"] = DocsResponse(
        answer=state["answer"],
        sources=[Source(ref="faq-03", retrieved_at="2026-08-26T00:00:00Z")],
        confidence=0.9,
    )

    monkeypatch.setattr(supervisor, "handle_request", lambda *a, **k: state)

    response = client.post("/chat", json={"message": "Де знайти безлактозне молоко?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == state["answer"]
    assert body["sources"] == [
        {"ref": "faq-03", "retrieved_at": "2026-08-26T00:00:00Z", "version": ""}
    ]
    assert body["escalated"] is False
    assert body["confidence"] == 0.9
    assert body["session_id"]


def test_escalate_route_returns_customer_message_no_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = build_initial_state("Термінова проблема з оплатою", "r2", "s2", "t2" * 8)
    state["next_action"] = "escalate"
    state["escalation_output"] = EscalationOutput(
        summary="Критичний випадок",
        category="critical",
        customer_message="Оператор зв'яжеться з вами найближчим часом.",
        attempted_resolution="Класифіковано, передано оператору.",
    )
    state["answer"] = state["escalation_output"].customer_message
    state["report_written"] = True
    state["telegram_sent"] = False

    monkeypatch.setattr(supervisor, "handle_request", lambda *a, **k: state)

    response = client.post("/chat", json={"message": "Термінова проблема з оплатою"})

    assert response.status_code == 200
    body = response.json()
    assert body["escalated"] is True
    assert body["sources"] == []
    assert body["answer"] == "Оператор зв'яжеться з вами найближчим часом."
    assert body["report_written"] is True
    assert body["telegram_sent"] is False


def test_reject_route_returns_fallback_answer_no_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = build_initial_state("", "r3", "s3", "t3" * 8)
    state["next_action"] = "reject"
    state["errors"] = ["empty_input"]

    monkeypatch.setattr(supervisor, "handle_request", lambda *a, **k: state)

    response = client.post("/chat", json={"message": ""})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"]
    assert "empty_input" not in body["answer"]


def test_admin_real_send_toggle_flips_settings_and_health_reflects_it() -> None:
    from src.kernel.settings import settings

    original = settings.allow_real_send
    try:
        response = client.post("/admin/real-send", json={"enabled": True})
        assert response.status_code == 200
        assert response.json() == {"allow_real_send": True}
        assert settings.allow_real_send is True
        assert client.get("/health").json()["allow_real_send"] is True

        client.post("/admin/real-send", json={"enabled": False})
        assert settings.allow_real_send is False
    finally:
        settings.allow_real_send = original


def test_cors_allows_configured_frontend_origin_only() -> None:
    allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"

    other = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers
