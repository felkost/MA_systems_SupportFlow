"""`AcpEnvelope.deadline` is enforced, not merely carried — an unenforced
field reads as a control during review while providing none.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.infrastructure import acp
from src.infrastructure.acp import AcpEnvelope, RouterInvalidOutputError, call_router


def test_call_router_raises_timeout_when_deadline_already_passed() -> None:
    envelope = AcpEnvelope(
        request_id="r1",
        session_id="s1",
        task="classify",
        deadline=datetime.now(timezone.utc) - timedelta(seconds=1),
        trace_id="0123456789abcdef0123456789abcdef",
        payload="Чи є у вас акції на хліб?",
    )
    with pytest.raises(TimeoutError):
        call_router(envelope)


def test_call_router_rejects_wrong_task() -> None:
    envelope = AcpEnvelope(
        request_id="r1",
        session_id="s1",
        task="escalate",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
        trace_id="0123456789abcdef0123456789abcdef",
        payload="text",
    )
    with pytest.raises(ValueError, match="classify"):
        call_router(envelope)


def test_call_router_langfuse_client_failure_is_not_relabeled_as_invalid_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """decision 33's collision regression: a misconfigured Langfuse client
    must raise as itself, never get caught and relabeled by the model-call
    `except Exception` a few lines below it.
    """

    def raising_get_langfuse_client() -> None:
        raise RuntimeError("TRACING_ENABLED=true but keys are empty")

    def fake_get_prompt_client(name: str, label: str = "production") -> object:
        class _Prompt:
            prompt = "{{customer_message}}"
            version = 1

        return _Prompt()

    monkeypatch.setattr(acp, "get_langfuse_client", raising_get_langfuse_client)
    monkeypatch.setattr(acp, "get_prompt_client", fake_get_prompt_client)

    envelope = AcpEnvelope(
        request_id="r1",
        session_id="s1",
        task="classify",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
        trace_id="0123456789abcdef0123456789abcdef",
        payload="text",
    )
    with pytest.raises(RuntimeError, match="TRACING_ENABLED"):
        call_router(envelope)
    # and specifically not swallowed into RouterInvalidOutputError
    try:
        call_router(envelope)
    except RouterInvalidOutputError:
        pytest.fail("Langfuse client failure was relabeled as RouterInvalidOutputError")
    except RuntimeError:
        pass
