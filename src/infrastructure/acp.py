"""The in-process delegation channel for Router and Escalation
(docs/decisions.md #1, #19). `infrastructure`, not `application` — this is
a transport client, and `tests/test_layering.py` allows
application-to-application imports, so misplacing it there would pass
silently (docs/decisions.md #9's lane-1 finding).

`call_router` is one call attempt: it enforces `envelope.deadline`, fetches
the versioned prompt, calls the model, and validates the result. Retry and
fail-closed policy (docs/decisions.md #12) is `application/router_agent.py`'s
job, not this module's — this module has no opinion about what happens when
it fails, only about failing loudly and on time.
"""

import time
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from src.domain.schemas import ClassificationOutput
from src.infrastructure.llm import get_chat_model
from src.infrastructure.observability import get_langfuse_client
from src.infrastructure.prompts import get_prompt_client

AcpTask = Literal["classify", "escalate"]


class AcpEnvelope(BaseModel):
    """One in-process delegation call (task §4: "request id, task,
    deadline, Langfuse trace ids").

    Parameters
    ----------
    request_id : str
    session_id : str
        docs/decisions.md #19 — task §9 requires it in observation
        metadata; the draft envelope had no path for it.
    task : {"classify", "escalate"}
    deadline : datetime
        Timezone-aware. `call_router` raises `TimeoutError` if this has
        already passed by the time the model call would return.
    trace_id : str
    payload : str
        The masked customer message (docs/decisions.md #14) — never the
        raw one.
    """

    request_id: str
    session_id: str
    task: AcpTask
    deadline: datetime
    trace_id: str
    payload: str


class RouterInvalidOutputError(Exception):
    """The model call succeeded but did not produce a valid
    `ClassificationOutput` — refusal, prose, or an out-of-schema value.
    """


def call_router(envelope: AcpEnvelope) -> tuple[ClassificationOutput, int]:
    """One attempt to classify `envelope.payload` via the Router Agent.

    Parameters
    ----------
    envelope : AcpEnvelope
        `envelope.task` must be `"classify"`.

    Returns
    -------
    (ClassificationOutput, int)
        The validated classification and the resolved Langfuse prompt
        version actually used (docs/decisions.md #13 — captured here so
        `application/router_agent.py` can put it in
        `SupportFlowState.router_prompt_version`).

    Raises
    ------
    TimeoutError
        `envelope.deadline` has already passed.
    RouterInvalidOutputError
        The model's output failed `ClassificationOutput` validation.

    Notes
    -----
    docs/decisions.md #19: `deadline` is enforced here, not merely carried
    — a field nothing checks reads as a control during review while
    providing none.
    """
    if envelope.task != "classify":
        raise ValueError(f"call_router got task={envelope.task!r}, expected 'classify'")
    if datetime.now(timezone.utc) >= envelope.deadline:
        raise TimeoutError(f"AcpEnvelope {envelope.request_id} deadline already passed")

    prompt_client = get_prompt_client("supportflow/router")
    prompt_version = prompt_client.version
    remaining = (envelope.deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise TimeoutError(
            f"AcpEnvelope {envelope.request_id} deadline passed before model call"
        )

    model = get_chat_model("router", timeout_override=remaining)
    start = time.monotonic()
    # include_raw=True: only way to reach the raw AIMessage's usage_metadata
    # (task §9's "кількість токенів") — with_structured_output's default
    # returns just the parsed schema, discarding it.
    structured_model = model.with_structured_output(
        ClassificationOutput, include_raw=True
    )
    compiled_prompt = prompt_client.prompt.replace(
        "{{customer_message}}", envelope.payload
    )

    langfuse = get_langfuse_client()
    span_cm = (
        langfuse.start_as_current_observation(
            name="acp.call_router",
            as_type="generation",
            prompt=prompt_client,
        )
        if langfuse is not None
        else nullcontext()
    )
    try:
        with span_cm as generation:
            raw = structured_model.invoke(compiled_prompt)
            if generation is not None:
                usage = getattr(raw.get("raw"), "usage_metadata", None) or {}
                generation.update(usage_details=dict(usage))
    except Exception as exc:  # noqa: BLE001 — re-raised as a typed error below
        if datetime.now(timezone.utc) >= envelope.deadline:
            raise TimeoutError(
                f"AcpEnvelope {envelope.request_id} timed out after "
                f"{time.monotonic() - start:.1f}s"
            ) from exc
        raise RouterInvalidOutputError(str(exc)) from exc

    result = raw.get("parsed")
    if not isinstance(result, ClassificationOutput):
        raise RouterInvalidOutputError(
            f"model returned no valid ClassificationOutput: {raw.get('parsing_error')}"
        )
    return result, prompt_version
