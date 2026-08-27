"""Router Agent orchestration: retry policy and the fail-closed default.
`src.infrastructure.acp.call_router` makes one attempt; this module
decides what happens when that attempt fails and counts the retries
against `config/models.yaml`'s `max_retries`.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.domain.schemas import ClassificationOutput
from src.domain.state import ErrorType
from src.infrastructure.acp import AcpEnvelope, RouterInvalidOutputError, call_router
from src.kernel.settings import load_agent_config


@dataclass(frozen=True)
class RouterResult:
    """Parameters
    ----------
    classification : ClassificationOutput or None
        `None` means every attempt failed — the caller must route to
        Escalation (the fail-closed default).
    prompt_version : int or None
        Set only when `classification` is set.
    errors : list[ErrorType]
        One entry per failed attempt, plus `"router_retries_exhausted"`
        if every attempt failed.
    retry_count : int
        Number of retries actually used (0 means it succeeded on the
        first attempt).
    """

    classification: ClassificationOutput | None
    prompt_version: int | None
    errors: list[ErrorType] = field(default_factory=list)
    retry_count: int = 0


def run_router(
    masked_text: str,
    request_id: str,
    session_id: str,
    trace_id: str,
    *,
    prompt_label: str = "production",
) -> RouterResult:
    """Classify `masked_text`, retrying once on failure before giving up.

    Parameters
    ----------
    masked_text : str
        Already PII-masked — never the raw customer message.
    request_id, session_id, trace_id : str
    prompt_label : str, default="production"
        Forwarded to `call_router`; only a prompt-comparison run passes
        anything else.

    Returns
    -------
    RouterResult
        `classification is None` on exhaustion — the caller (Supervisor)
        must fail closed to Escalation, never fail open to a default
        category.
    """
    config = load_agent_config("router")
    errors: list[ErrorType] = []
    attempt = 0
    while attempt <= config.max_retries:
        envelope = AcpEnvelope(
            request_id=request_id,
            session_id=session_id,
            task="classify",
            deadline=datetime.now(timezone.utc)
            + timedelta(seconds=config.timeout_seconds),
            trace_id=trace_id,
            payload=masked_text,
        )
        try:
            classification, prompt_version = call_router(
                envelope, prompt_label=prompt_label
            )
            return RouterResult(classification, prompt_version, errors, attempt)
        except TimeoutError:
            errors.append("router_timeout")
        except RouterInvalidOutputError:
            errors.append("router_invalid_output")
        attempt += 1

    errors.append("router_retries_exhausted")
    return RouterResult(
        classification=None, prompt_version=None, errors=errors, retry_count=attempt
    )
