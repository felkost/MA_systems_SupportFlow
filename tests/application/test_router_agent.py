"""Router's fail-closed paths: invalid output, timeout, and retry
exhaustion each end in a `RouterResult` with `classification is None`,
never an unhandled exception — proven without a live model call by
faking `call_router`.
"""

from datetime import datetime, timezone

import pytest

from src.application import router_agent
from src.domain.schemas import ClassificationOutput
from src.infrastructure.acp import RouterInvalidOutputError


@pytest.fixture(autouse=True)
def _stub_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.kernel.settings import AgentModelConfig

    monkeypatch.setattr(
        router_agent,
        "load_agent_config",
        lambda role: AgentModelConfig(
            model="stub/model",
            temperature=0,
            max_tokens=256,
            timeout_seconds=10,
            confidence_threshold=None,
            max_retries=1,
        ),
    )


def test_router_succeeds_on_first_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = ClassificationOutput(category="product", urgency="low", language="uk")

    def fake_call_router(
        envelope: object, *, prompt_label: str = "production"
    ) -> tuple[ClassificationOutput, int]:
        return expected, 3

    monkeypatch.setattr(router_agent, "call_router", fake_call_router)
    result = router_agent.run_router("text", "req-1", "sess-1", "trace-1")

    assert result.classification == expected
    assert result.prompt_version == 3
    assert result.retry_count == 0
    assert result.errors == []


def test_router_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = ClassificationOutput(category="general", urgency="low", language="en")
    calls = {"n": 0}

    def fake_call_router(
        envelope: object, *, prompt_label: str = "production"
    ) -> tuple[ClassificationOutput, int]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RouterInvalidOutputError("prose instead of JSON")
        return expected, 3

    monkeypatch.setattr(router_agent, "call_router", fake_call_router)
    result = router_agent.run_router("text", "req-1", "sess-1", "trace-1")

    assert result.classification == expected
    assert result.retry_count == 1
    assert result.errors == ["router_invalid_output"]


def test_router_fails_closed_on_invalid_output_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def always_invalid(
        envelope: object, *, prompt_label: str = "production"
    ) -> tuple[ClassificationOutput, int]:
        raise RouterInvalidOutputError("model refused")

    monkeypatch.setattr(router_agent, "call_router", always_invalid)
    result = router_agent.run_router("text", "req-1", "sess-1", "trace-1")

    assert result.classification is None
    assert result.prompt_version is None
    assert "router_invalid_output" in result.errors
    assert "router_retries_exhausted" in result.errors
    assert result.retry_count == 2  # max_retries=1 → attempts 0 and 1, both failed


def test_router_fails_closed_on_timeout_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def always_times_out(
        envelope: object, *, prompt_label: str = "production"
    ) -> tuple[ClassificationOutput, int]:
        raise TimeoutError("deadline passed")

    monkeypatch.setattr(router_agent, "call_router", always_times_out)
    result = router_agent.run_router("text", "req-1", "sess-1", "trace-1")

    assert result.classification is None
    assert result.errors.count("router_timeout") == 2  # both attempts timed out
    assert "router_retries_exhausted" in result.errors


def test_router_mixed_failure_then_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def mixed(
        envelope: object, *, prompt_label: str = "production"
    ) -> tuple[ClassificationOutput, int]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("deadline passed")
        raise RouterInvalidOutputError("still bad")

    monkeypatch.setattr(router_agent, "call_router", mixed)
    result = router_agent.run_router("text", "req-1", "sess-1", "trace-1")

    assert result.classification is None
    assert result.errors == [
        "router_timeout",
        "router_invalid_output",
        "router_retries_exhausted",
    ]


def test_deadline_already_passed_raises_timeout_before_model_call() -> None:
    # infra/acp.py's own contract, exercised directly: an envelope whose
    # deadline is already in the past must never reach the model call.
    from src.infrastructure.acp import AcpEnvelope, call_router

    envelope = AcpEnvelope(
        request_id="r",
        session_id="s",
        task="classify",
        deadline=datetime(2020, 1, 1, tzinfo=timezone.utc),
        trace_id="0123456789abcdef0123456789abcdef",
        payload="text",
    )
    with pytest.raises(TimeoutError):
        call_router(envelope)


def test_prompt_label_reaches_call_router(monkeypatch: pytest.MonkeyPatch) -> None:
    """A comparison run must actually reach the label it asked for.

    Without forwarding, a `candidate` prompt could be seeded and never
    measured — the comparison would run production against itself and
    report no difference, which reads as a real (negative) result.
    """
    seen: list[str] = []

    def record(
        envelope: object, *, prompt_label: str = "production"
    ) -> tuple[ClassificationOutput, int]:
        seen.append(prompt_label)
        return ClassificationOutput(category="general", urgency="low", language="uk"), 1

    monkeypatch.setattr(router_agent, "call_router", record)
    router_agent.run_router("text", "r", "s", "t", prompt_label="candidate")

    assert seen == ["candidate"]


def test_prompt_label_defaults_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def record(
        envelope: object, *, prompt_label: str = "production"
    ) -> tuple[ClassificationOutput, int]:
        seen.append(prompt_label)
        return ClassificationOutput(category="general", urgency="low", language="uk"), 1

    monkeypatch.setattr(router_agent, "call_router", record)
    router_agent.run_router("text", "r", "s", "t")

    assert seen == ["production"]
