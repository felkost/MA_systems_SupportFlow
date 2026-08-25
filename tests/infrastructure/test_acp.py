"""`AcpEnvelope.deadline` is enforced, not merely carried
(docs/decisions.md #19: an unenforced field reads as a control during
review while providing none).
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.infrastructure.acp import AcpEnvelope, call_router


def test_call_router_raises_timeout_when_deadline_already_passed() -> None:
    envelope = AcpEnvelope(
        request_id="r1",
        session_id="s1",
        task="classify",
        deadline=datetime.now(timezone.utc) - timedelta(seconds=1),
        trace_id="t1",
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
        trace_id="t1",
        payload="text",
    )
    with pytest.raises(ValueError, match="classify"):
        call_router(envelope)
