"""`_compose_escalation_output`'s judge-facing metadata and error-level
fix — same defect class as `_compose_docs_response`'s, see that test's
docstring. Escalation's clean "agent_answer" maps to `customer_message`
(what the customer is told), not `answer` — `EscalationOutput` has no
`answer` field.
"""

from contextlib import contextmanager

import pytest

from src.application import escalation_agent
from src.domain.schemas import EscalationOutput


class _FakeSpan:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)


class _FakeLangfuseClient:
    def __init__(self) -> None:
        self.last_span = _FakeSpan()

    @contextmanager
    def start_as_current_observation(self, *, name: str, as_type: str, **_kw):
        yield self.last_span


class _FakeStructuredModel:
    def __init__(self, raw: dict) -> None:
        self._raw = raw

    def invoke(self, _prompt: str) -> dict:
        return self._raw


class _FakeChatModel:
    model_name = "openai/gpt-5.6-luna"

    def __init__(self, raw: dict) -> None:
        self._raw = raw

    def with_structured_output(
        self, _schema: type, include_raw: bool = False
    ) -> _FakeStructuredModel:
        assert include_raw is True
        return _FakeStructuredModel(self._raw)


def _patch_common(monkeypatch: pytest.MonkeyPatch, fake_model, fake_client) -> None:
    monkeypatch.setattr(escalation_agent, "get_chat_model", lambda _role: fake_model)
    monkeypatch.setattr(escalation_agent, "get_langfuse_client", lambda: fake_client)
    monkeypatch.setattr(escalation_agent, "get_prompt_client", lambda _name: object())


def test_successful_parse_carries_customer_message_as_agent_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeLangfuseClient()
    parsed = EscalationOutput(
        summary="Алергія",
        category="critical",
        customer_message="Оператор зв'яжеться з вами.",
        attempted_resolution="Класифіковано як критичне.",
    )
    fake_model = _FakeChatModel({"raw": object(), "parsed": parsed})
    _patch_common(monkeypatch, fake_model, fake_client)

    result = escalation_agent._compose_escalation_output(
        "full compiled prompt...", "У мене алергія!"
    )

    assert result is parsed
    update = fake_client.last_span.updates[0]
    assert update["metadata"] == {
        "customer_message": "У мене алергія!",
        "agent_answer": "Оператор зв'яжеться з вами.",
    }


def test_parse_failure_marks_the_span_level_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeLangfuseClient()
    fake_model = _FakeChatModel({"raw": object(), "parsed": None, "parsing_error": "x"})
    _patch_common(monkeypatch, fake_model, fake_client)

    with pytest.raises(escalation_agent.EscalationInvalidOutputError):
        escalation_agent._compose_escalation_output("prompt", "У мене алергія!")

    update = fake_client.last_span.updates[0]
    assert update["level"] == "ERROR"
    assert "metadata" not in update
