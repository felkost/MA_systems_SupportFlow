"""`src.application.escalation_agent` — compose, PII-mask, HITL-gate,
write, and conditionally send an escalation report. Every external
boundary (LLM call, Langfuse prompt fetch, HITL prompt, file write,
Telegram send) is injected, per this module's own DI parameters.
"""

from datetime import datetime, timezone
from typing import Any

import pytest

from src.application import escalation_agent
from src.application.escalation_agent import (
    EscalationAgentResult,
    EscalationContext,
    run_escalation_agent,
)
from src.domain.schemas import (
    ClassificationOutput,
    DocsResponse,
    EscalationOutput,
    Source,
    WebSearchResponse,
)
from src.kernel.settings import settings


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate each test from the module-level session/process stores and
    from real `.env` settings (docs/decisions.md #19's send-safety flags).
    """
    monkeypatch.setattr(escalation_agent, "_session_store", {})
    monkeypatch.setattr(escalation_agent, "_process_send_count", 0)
    monkeypatch.setattr(settings, "bypass_hitl", True)
    monkeypatch.setattr(settings, "allow_real_send", False)
    monkeypatch.setattr(settings, "telegram_chat_id", "-100")
    monkeypatch.setattr(settings, "telegram_bot_token", "TOKEN")


def _context(text: str = "У мене алергія на молоко!") -> EscalationContext:
    return EscalationContext(
        masked_text=text,
        classification=ClassificationOutput(
            category="critical", urgency="critical", language="uk"
        ),
        confidence=None,
        errors=[],
    )


def _fake_compose(_prompt: str) -> EscalationOutput:
    return EscalationOutput(
        summary="Алергічна реакція",
        category="critical",
        customer_message="Оператор зв'яжеться з вами найближчим часом.",
        attempted_resolution="Класифіковано як критичне, передано оператору.",
    )


def _prompt_fn(_name: str) -> tuple[str, int]:
    return "customer: {{customer_message}}\ncontext: {{context}}", 1


def _run(**overrides: Any) -> tuple[EscalationAgentResult, list[Any], list[Any]]:
    written: list[Any] = []
    sent: list[Any] = []
    kwargs: dict[str, Any] = dict(
        context=_context(),
        request_id="r1",
        session_id="s1",
        compose_fn=_fake_compose,
        prompt_fn=_prompt_fn,
        write_fn=lambda payload, **kw: written.append((payload, kw)),
        send_fn=lambda text, **kw: sent.append((text, kw)),
    )
    kwargs.update(overrides)
    result = run_escalation_agent(**kwargs)
    return result, written, sent  # type: ignore[return-value]


def test_bypass_hitl_writes_file_without_confirmation() -> None:
    result, written, _sent = _run()

    assert result.written is True
    assert len(written) == 1


def test_hitl_declined_skips_write_and_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "bypass_hitl", False)

    result, written, sent = _run(confirm_fn=lambda output: False)

    assert result.written is False
    assert result.sent is False
    assert written == []
    assert sent == []


def test_hitl_confirmed_writes_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "bypass_hitl", False)

    result, written, _sent = _run(confirm_fn=lambda output: True)

    assert result.written is True
    assert len(written) == 1


def test_allow_real_send_false_writes_but_does_not_send() -> None:
    result, written, sent = _run()

    assert result.written is True
    assert result.sent is False
    assert written != []
    assert sent == []


def test_allow_real_send_true_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "allow_real_send", True)

    result, _written, sent = _run()

    assert result.sent is True
    assert len(sent) == 1


def test_missing_chat_id_refuses_send_even_when_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "allow_real_send", True)
    monkeypatch.setattr(settings, "telegram_chat_id", "")

    result, _written, sent = _run()

    assert result.sent is False
    assert sent == []


def test_pii_is_masked_before_write_or_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "allow_real_send", True)

    def compose_with_pii(_prompt: str) -> EscalationOutput:
        return EscalationOutput(
            summary="Клієнт: test@example.com",
            category="critical",
            customer_message="Зателефонуємо на 0671234567.",
            attempted_resolution="Спроба зв'язку",
        )

    result, written, sent = _run(compose_fn=compose_with_pii)

    assert "test@example.com" not in written[0][0]["customer_message"]
    assert "test@example.com" not in written[0][0]["summary"]
    assert "0671234567" not in written[0][0]["customer_message"]
    assert result.output.customer_message == written[0][0]["customer_message"]
    assert "0671234567" not in sent[0][0]


def test_duplicate_message_in_same_session_is_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "allow_real_send", True)

    written: list[Any] = []
    sent: list[Any] = []
    kwargs = dict(
        request_id="r1",
        session_id="s1",
        compose_fn=_fake_compose,
        prompt_fn=_prompt_fn,
        write_fn=lambda payload, **kw: written.append((payload, kw)),
        send_fn=lambda text, **kw: sent.append((text, kw)),
    )

    first = run_escalation_agent(context=_context("однакове повідомлення"), **kwargs)
    second = run_escalation_agent(context=_context("однакове повідомлення"), **kwargs)

    assert first.deduplicated is False
    assert second.deduplicated is True
    assert len(written) == 1
    assert len(sent) == 1


def test_different_messages_in_same_session_are_not_deduplicated() -> None:
    written: list[Any] = []
    kwargs = dict(
        request_id="r1",
        session_id="s1",
        compose_fn=_fake_compose,
        prompt_fn=_prompt_fn,
        write_fn=lambda payload, **kw: written.append((payload, kw)),
        send_fn=lambda text, **kw: None,
    )

    run_escalation_agent(context=_context("перше повідомлення"), **kwargs)
    run_escalation_agent(context=_context("друге повідомлення"), **kwargs)

    assert len(written) == 2


def test_context_with_router_failure_and_prior_agent_attempts_is_rendered() -> None:
    """Exercises every branch of `_render_context`: no classification
    (Router itself failed, docs/decisions.md #12), a set confidence, a
    non-empty error list, and both prior agents having been tried.
    """
    context = EscalationContext(
        masked_text="Питання без відповіді",
        classification=None,
        confidence=0.3,
        errors=["docs_low_confidence", "web_search_low_confidence"],
        docs_response=DocsResponse(answer="Не впевнений.", confidence=0.3),
        web_search_response=WebSearchResponse(
            answer="Немає джерел.",
            sources=[Source(ref="x", retrieved_at=datetime.now(timezone.utc))],
            confidence=0.3,
        ),
    )
    captured: dict[str, str] = {}

    def capturing_prompt_fn(_name: str) -> tuple[str, int]:
        return "{{customer_message}} | {{context}}", 1

    def capturing_compose_fn(prompt: str) -> EscalationOutput:
        captured["prompt"] = prompt
        return _fake_compose(prompt)

    run_escalation_agent(
        context=context,
        request_id="r1",
        session_id="s1",
        compose_fn=capturing_compose_fn,
        prompt_fn=capturing_prompt_fn,
        write_fn=lambda payload, **kw: None,
        send_fn=lambda text, **kw: None,
    )

    assert "classification: unavailable (Router failed)" in captured["prompt"]
    assert "confidence=0.30" in captured["prompt"]
    assert "docs_low_confidence" in captured["prompt"]
    assert "docs_agent tried, answered: Не впевнений." in captured["prompt"]
    assert "web_search_agent tried, answered: Немає джерел." in captured["prompt"]


def test_session_send_cap_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "allow_real_send", True)
    monkeypatch.setattr(escalation_agent, "MAX_ESCALATION_SENDS_PER_SESSION", 1)

    sent: list[Any] = []
    kwargs = dict(
        request_id="r1",
        session_id="s1",
        compose_fn=_fake_compose,
        prompt_fn=_prompt_fn,
        write_fn=lambda payload, **kw: None,
        send_fn=lambda text, **kw: sent.append(text),
    )

    first = run_escalation_agent(context=_context("msg1"), **kwargs)
    second = run_escalation_agent(context=_context("msg2"), **kwargs)

    assert first.capped is False
    assert second.capped is True
    assert len(sent) == 1
