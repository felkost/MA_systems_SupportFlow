"""Session memory: does a follow-up `handle_request` call in the same
`session_id` actually see the prior turn, and does a different
`session_id` stay isolated from it. The
checkpointer itself is reset between tests by `tests/conftest.py`'s
autouse fixture — without it these tests would leak into each other.
"""

from datetime import datetime, timezone

import pytest

from src.application import graph_nodes, supervisor
from src.domain.schemas import ClassificationOutput, DocsResponse, Source
from src.infrastructure.docs_client import DocsCallResult

_TRACE_ID = "0123456789abcdef0123456789abcdef"


def _fake_router_result():
    from src.application.router_agent import RouterResult

    return RouterResult(
        classification=ClassificationOutput(  # type: ignore[arg-type]
            category="product", urgency="low", language="uk"
        ),
        prompt_version=1,
        errors=[],
        retry_count=0,
    )


def _fake_docs_result(answer: str) -> DocsCallResult:
    return DocsCallResult(
        response=DocsResponse(
            answer=answer,
            sources=[
                Source(
                    ref="internal_policy/assortment_v1.md",
                    retrieved_at=datetime.now(timezone.utc),
                )
            ],
            confidence=0.9,
        ),
        retrieval_context=["контекст"],
        prompt_version=9,
    )


def test_second_call_in_the_same_session_carries_the_first_turn_in_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        graph_nodes, "run_router", lambda *a, **kw: _fake_router_result()
    )
    monkeypatch.setattr(
        graph_nodes,
        "call_docs_agent",
        lambda *a, **kw: _fake_docs_result("Мене звати Фелікс."),
    )

    first = supervisor.handle_request("Мене звати Фелікс", "r1", "s-shared", _TRACE_ID)
    assert first["conversation_history"] == [
        {"customer": "Мене звати Фелікс", "answer": "Мене звати Фелікс."}
    ]

    monkeypatch.setattr(
        graph_nodes,
        "call_docs_agent",
        lambda *a, **kw: _fake_docs_result("Вас звати Фелікс."),
    )
    second = supervisor.handle_request("як мене звати", "r2", "s-shared", _TRACE_ID)

    assert second["conversation_history"] == [
        {"customer": "Мене звати Фелікс", "answer": "Мене звати Фелікс."},
        {"customer": "як мене звати", "answer": "Вас звати Фелікс."},
    ]


def test_a_different_session_id_never_sees_another_sessions_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        graph_nodes, "run_router", lambda *a, **kw: _fake_router_result()
    )
    monkeypatch.setattr(
        graph_nodes,
        "call_docs_agent",
        lambda *a, **kw: _fake_docs_result("Мене звати Фелікс."),
    )
    supervisor.handle_request("Мене звати Фелікс", "r1", "s-first", _TRACE_ID)

    monkeypatch.setattr(
        graph_nodes,
        "call_docs_agent",
        lambda *a, **kw: _fake_docs_result("Не можемо визначити."),
    )
    other_session = supervisor.handle_request(
        "як мене звати", "r2", "s-second", _TRACE_ID
    )

    assert other_session["conversation_history"] == [
        {"customer": "як мене звати", "answer": "Не можемо визначити."}
    ]


def test_errors_do_not_bleed_across_turns_in_the_same_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`errors` still accumulates *within* one turn (see
    `tests/domain/test_state.py`'s reducer unit tests) but must not
    inherit a prior turn's entries from the checkpointed thread — the
    defect a plain `operator.add` reducer had, confirmed by direct probe
    against the installed langgraph before this fix.
    """
    from src.application.escalation_agent import EscalationAgentResult
    from src.domain.schemas import EscalationOutput
    from src.infrastructure.docs_client import DocsUnavailableError

    monkeypatch.setattr(
        graph_nodes, "run_router", lambda *a, **kw: _fake_router_result()
    )

    def _raise(*_a, **_kw):
        raise DocsUnavailableError("boom")

    monkeypatch.setattr(graph_nodes, "call_docs_agent", _raise)
    monkeypatch.setattr(
        graph_nodes,
        "run_escalation_agent",
        lambda *a, **kw: EscalationAgentResult(
            output=EscalationOutput(
                summary="s",
                category="product",
                customer_message="Передано оператору.",
                attempted_resolution="Спроба через Docs Agent.",
            ),
            written=True,
            sent=False,
            deduplicated=False,
            capped=False,
        ),
    )

    first = supervisor.handle_request("питання 1", "r1", "s-errors", _TRACE_ID)
    assert first["errors"] == ["docs_unavailable"]

    monkeypatch.setattr(
        graph_nodes,
        "call_docs_agent",
        lambda *a, **kw: _fake_docs_result("Відповідь без помилок."),
    )
    second = supervisor.handle_request("питання 2", "r2", "s-errors", _TRACE_ID)

    assert second["errors"] == []
